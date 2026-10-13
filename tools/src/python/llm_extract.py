"""内置工具：llm_extract —— T1 抽取工：把一批文本按固定 schema 抽成结构化 JSON。

设计依据 workspace/tasks/20261013_workers/design.md：
- 外包与否只看一件事——我能否用便宜的代价验证产出。抽取类有 ground truth（对源一查即知），
  是「可外包」里最靠前的一类。
- 本工具是 llm_fanout 之上的固定工序：拼抽取 prompt → 调 fanout（复用分线/重试/截断续写）
  → 解析 JSON → 字段完整性校验。missing 标明漏抽字段，便于抽查对源，不假装完整。
边界：每条文本独立抽取，上限 20 条；模型漏字段/编造由调用方抽查兜底。
"""
from __future__ import annotations

import json
import os
import re

from tools.base import tool

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_FANOUT_PATH = os.path.join(_ROOT, "tools", "src", "python", "llm_fanout.py")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_OBJ_RE = re.compile(r"\{.*\}", re.S)

_FANOUT_CACHE = {"fn": None}


def _fanout_fn():
    """拿 llm_fanout.run：优先包 import，失败则按文件路径 importlib 兜底。"""
    if _FANOUT_CACHE["fn"] is not None:
        return _FANOUT_CACHE["fn"]
    fn = None
    try:
        from tools.src.python.llm_fanout import run as _r
        fn = _r
    except Exception:  # noqa: BLE001
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_llm_fanout_mod", _FANOUT_PATH)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                fn = getattr(mod, "run", None)
        except Exception:  # noqa: BLE001
            fn = None
    _FANOUT_CACHE["fn"] = fn
    return fn


def _norm_fields(fields):
    """fields: dict{name:desc} / list[str] / 'a, b' → {name: desc}。"""
    if isinstance(fields, dict):
        return {str(k): str(v) for k, v in fields.items()}
    if isinstance(fields, (list, tuple)):
        return {str(x): "" for x in fields}
    if isinstance(fields, str):
        return {x.strip(): "" for x in fields.split(",") if x.strip()}
    return {}


def _extract_json(text):
    """从模型回复里抠出 JSON 对象：优先代码块，其次第一个 {...}。"""
    if not text:
        return None
    m = _FENCE_RE.search(text)
    cand = m.group(1) if m else None
    if cand is None:
        m2 = _OBJ_RE.search(text)
        cand = m2.group(0) if m2 else None
    if cand is None:
        return None
    try:
        return json.loads(cand.strip())
    except (json.JSONDecodeError, ValueError):
        return None


def _build_system(fields, instruction):
    lines = ["你是结构化信息抽取工。只做抽取，不解释、不评论、不追问。"]
    if instruction:
        lines.append("任务：" + instruction)
    lines.append("从用户消息的文本中抽取以下字段，只输出一个 JSON 对象：")
    for k, v in fields.items():
        lines.append("  - %s%s" % (k, ("：" + v) if v else ""))
    lines.append("规则：只输出 JSON，不要 markdown 代码块、不要任何多余文字；"
                 "文本里没有的字段值用 null；绝不编造。")
    return "\n".join(lines)


@tool(
    name="llm_extract",
    description=("T1 抽取工：把一批文本按固定 schema 抽成结构化 JSON，复用 llm_fanout 的分线/重试/"
                 "截断续写。返回每条 {ok, data, missing, raw}，missing 列出漏抽字段，便于抽查对源。"),
    parameters={
        "type": "object",
        "properties": {
            "texts": {"type": "array", "items": {"type": "string"},
                      "description": "待抽取文本列表，每条独立抽取，上限 20"},
            "fields": {"type": "object",
                       "description": "要抽的字段 {字段名: 说明}；也接受数组或逗号分隔字符串"},
            "instruction": {"type": "string", "description": "任务说明（可选）"},
            "sources": {"type": "string", "description": "指定源，默认 auto"},
            "max_tokens": {"type": "number", "description": "单条回复上限，默认 1024"},
            "timeout": {"type": "number", "description": "单条超时秒，默认 120"},
            "max_retries": {"type": "number", "description": "失败重试轮数，默认 2"},
            "max_continue": {"type": "number", "description": "截断续写次数，默认 2"},
        },
        "required": ["texts", "fields"],
    },
    group="模型",
)
def run(texts, fields, instruction: str = "", sources: str = "auto",
        max_tokens: float = 1024, timeout: float = 120,
        max_retries: float = 2, max_continue: float = 2) -> dict:
    if not isinstance(texts, list) or not texts:
        return {"ok": False, "error": "texts 不能为空，需为字符串数组"}
    fld = _norm_fields(fields)
    if not fld:
        return {"ok": False, "error": "fields 不能为空"}
    fn = _fanout_fn()
    if fn is None:
        return {"ok": False, "error": "llm_fanout 不可用（import 与 importlib 兜底均失败）"}

    items = ["【待抽取文本】\n" + str(t) for t in texts][:20]
    system = _build_system(fld, instruction or "")
    fr = fn(prompts=items, system=system, sources=sources or "auto",
            max_tokens=max_tokens, timeout=timeout,
            max_retries=max_retries, max_continue=max_continue,
            continue_on_truncate=True, retry_other_line=True)
    raw_results = (fr or {}).get("results") or []

    keys = list(fld.keys())
    out, parsed, miss_n = [], 0, 0
    for i, r in enumerate(raw_results):
        r = dict(r or {})
        text = r.get("text") or ""
        data = _extract_json(text) if r.get("ok") else None
        if isinstance(data, dict):
            parsed += 1
            missing = [k for k in keys if k not in data or data.get(k) in (None, "")]
        else:
            missing = list(keys)
        if missing:
            miss_n += 1
        out.append({
            "i": i,
            "ok": bool(r.get("ok")) and isinstance(data, dict),
            "data": data,
            "missing": missing,
            "truncated": bool(r.get("truncated")),
            "lane": r.get("lane"),
            "raw": text[:2000],
            "error": r.get("error"),
        })

    return {
        "ok": parsed > 0,
        "mode": "extract",
        "total": len(items),
        "parsed_ok": parsed,
        "with_missing": miss_n,
        "fields": keys,
        "wall_secs": (fr or {}).get("wall_secs"),
        "still_truncated": (fr or {}).get("still_truncated", 0),
        "errors": [x["i"] for x in out if x.get("error")],
        "items": out,
    }
