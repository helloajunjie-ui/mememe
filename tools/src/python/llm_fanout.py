"""内置工具：llm_fanout —— 把可并行的机械活外包给 LLM 模型，一次调用拿回全部结果。

设计意图：
- 很多子任务天然可并行（批摘要/批分类/批抽取/批改写/逐条评判），素月自己串行做 = 慢 + 烧自身往返。
- llm_fanout 把一组【互相独立】的子任务分发出去，一次调用拿回全部结果，单条失败不影响其他。

分线模型（2026-10-13 共建者尼可的建议，实测驱动）：
- 同一个 API 端点并发打请求 = 自己撞自己的限流。旧实现单源 8 并发 → 实测 5 成 3 断（RemoteProtocolError）。
- 改为按【服务商】分线：每条线（一个 provider）内部【串行】，线与线之间【并行】。
- N 个源 → N 条并行线。吞吐来自"多源并行"而非"单源并发"→ 同一来源同时只有 1 个在途请求，限流风险降到最低。
- 子任务按 round-robin 分配到各线；失败的任务自动换到另一条线重试一次（跨源容灾）。
- 每条线用该源自己的 model（取 models[0]）——模型名是 per-source 的，跨源复用同一个模型名会 404。

边界：子任务必须互相独立（无先后依赖）；一次上限 20 条，防止误触发巨额计费。
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from tools.base import tool

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_CFG = os.path.join(_ROOT, "config", "llm.json")

_MAX_ITEMS = 20
_MAX_LANES = 8
_FALLBACK_MODEL = "gpt-4o-mini"


def _read_cfg() -> dict:
    try:
        with open(_CFG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _all_lanes(cfg: dict) -> list:
    """全部可用作线路的源：enabled 且 base_url/api_key 齐全。"""
    out = []
    for s in (cfg.get("sources") or []):
        if not s.get("enabled", True):
            continue
        if not (s.get("base_url") and s.get("api_key")):
            continue
        out.append({
            "name": str(s.get("name") or "?"),
            "base_url": s["base_url"],
            "api_key": s["api_key"],
            "models": [m for m in (s.get("models") or []) if m],
        })
    return out


def _pick(cfg: dict, source: str) -> dict:
    """单源模式（兼容旧调用）：按源名取一个。"""
    for s in (cfg.get("sources") or []):
        if str(s.get("name", "")).upper() == (source or "").upper() and s.get("enabled", True):
            return {"base_url": s.get("base_url") or "", "api_key": s.get("api_key") or "", "name": s.get("name")}
    if cfg.get("base_url") and cfg.get("api_key"):
        return {"base_url": cfg["base_url"], "api_key": cfg["api_key"], "name": "default"}
    return {}


def _resolve_lanes(cfg: dict, sources_arg: str, model_override: str) -> list:
    """决定用哪几条线。返回 [{name, base_url, api_key, models, model}]。

    sources_arg: "auto"/"" → 全部启用源；"A,B" → 指定源（按名匹配）。
    """
    want = [x.strip() for x in str(sources_arg or "").replace("，", ",").split(",") if x.strip()]
    if want and want[0].lower() in ("auto", "all", "*"):
        want = []
    lanes = _all_lanes(cfg)
    if want:
        by = {l["name"].upper(): l for l in lanes}
        picked = []
        for n in want:
            l = by.get(n.upper())
            if l is not None and l not in picked:
                picked.append(l)
        if picked:
            lanes = picked
        # 名字全不匹配 → 退回全部启用源（不静默返回空）
    if not lanes:
        s = _pick(cfg, want[0] if want else "")
        if s:
            lanes = [{"name": s.get("name") or "default", "base_url": s.get("base_url") or "",
                      "api_key": s.get("api_key") or "", "models": []}]
    out = []
    for l in lanes[:_MAX_LANES]:
        l = dict(l)
        l["model"] = (model_override or (l["models"][0] if l.get("models") else "") or _FALLBACK_MODEL)
        out.append(l)
    return out


def _endpoint(base: str) -> str:
    b = (base or "").rstrip("/")
    if not b.endswith("/v1"):
        b += "/v1"
    return b + "/chat/completions"


def _one(src: dict, model: str, system: str, prompt: str, max_tokens, temperature, timeout) -> dict:
    import httpx
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    t0 = time.time()
    try:
        r = httpx.post(
            _endpoint(src["base_url"]),
            headers={"Authorization": "Bearer " + src["api_key"], "Content-Type": "application/json"},
            json={"model": model, "messages": msgs,
                  "max_tokens": int(max_tokens), "temperature": float(temperature)},
            timeout=float(timeout),
        )
        secs = round(time.time() - t0, 2)
        if r.status_code != 200:
            return {"ok": False, "secs": secs, "error": "HTTP %s: %s" % (r.status_code, r.text[:200])}
        d = r.json()
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        text, note = msg.get("content"), None
        if not text:
            rc = msg.get("reasoning_content")
            if rc:
                text, note = rc, "正文为空，返回的是推理内容 reasoning_content"
            else:
                note = "content 为空（可能 max_tokens 被推理耗尽，finish_reason=%s）" % ch.get("finish_reason")
        u = d.get("usage") or {}
        out = {"ok": bool(text), "secs": secs, "model": model,
               "tokens": (u.get("total_tokens") if isinstance(u.get("total_tokens"), int) else None),
               "text": text}
        if note:
            out["note"] = note
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "secs": round(time.time() - t0, 2),
                "error": "%s: %s" % (type(e).__name__, e), "model": model}


def _run_lane(lane: dict, idxs: list, items: list, system: str, max_tokens, temperature,
              timeout, inner_conc: int) -> dict:
    """一条线内跑完分配给它的全部子任务。inner_conc=1 时严格串行（推荐）。"""
    res = {}
    if not idxs:
        return res
    if inner_conc <= 1:
        for i in idxs:
            try:
                res[i] = _one(lane, lane["model"], system, items[i], max_tokens, temperature, timeout)
            except Exception as e:  # noqa: BLE001
                res[i] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    else:
        with ThreadPoolExecutor(max_workers=inner_conc) as ex:
            futs = {ex.submit(_one, lane, lane["model"], system, items[i],
                              max_tokens, temperature, timeout): i for i in idxs}
            for f, i in futs.items():
                try:
                    res[i] = f.result()
                except Exception as e:  # noqa: BLE001
                    res[i] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    for i in res:
        res[i]["lane"] = lane["name"]
    return res


@tool(
    name="llm_fanout",
    description=(
        "批量把一个【互相独立】的子任务列表【按服务商分线】并发外包给 LLM：每条线（一个 provider）"
        "内部串行、线与线之间并行——避免单源并发撞自己的限流。子任务按 round-robin 分配到各线，"
        "单条失败不影响其他，失败项会自动换另一条线重试一次（跨源容灾）。返回结果按原顺序对齐。"
        "默认 sources='auto' 用 config/llm.json 里全部启用源。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "description": "子任务列表，每条是一个完整的用户指令（最多 20 条）。返回结果按原顺序对齐。",
                "items": {"type": "string"},
            },
            "system": {"type": "string", "description": "所有子任务共用的 system 提示（可选）"},
            "sources": {"type": "string",
                        "description": "逗号分隔的源名（分线用），默认 'auto'=全部启用源；如 'DEEPSEEK,UIUIHAO'"},
            "model": {"type": "string", "description": "覆盖模型名（留空=各线取自己源的第一个模型，推荐留空）"},
            "max_concurrency": {"type": "number", "description": "每条线【内部】并发数，默认 1（建议保持 1，并发是分线的职责）"},
            "max_tokens": {"type": "number", "description": "单条回复上限，默认 1024"},
            "temperature": {"type": "number", "description": "默认 0.3"},
            "timeout": {"type": "number", "description": "单条超时秒数，默认 120"},
            "retry_other_line": {"type": "boolean", "description": "失败项是否换另一条线重试一次，默认 true"},
            "source": {"type": "string", "description": "（兼容旧调用）单源名；若给了 sources 则忽略此参数"},
        },
        "required": ["prompts"],
    },
)
def run(prompts: list, model: str = "", system: str = "", sources: str = "auto",
        max_concurrency: float = 1, max_tokens: float = 1024, temperature: float = 0.3,
        timeout: float = 120, retry_other_line: bool = True, source: str = "") -> dict:
    if not prompts or not isinstance(prompts, list):
        return {"ok": False, "error": "prompts 不能为空，且需为字符串数组"}
    items = [str(p) for p in prompts][:_MAX_ITEMS]

    cfg = _read_cfg()
    eff_sources = sources
    if (not sources or str(sources).lower() in ("auto", "all", "*")) and source:
        eff_sources = source
    lanes = _resolve_lanes(cfg, eff_sources, model)
    if not lanes:
        return {"ok": False, "error": "未找到可用源（检查 config/llm.json 的 sources / enabled）"}

    inner_conc = max(1, min(int(max_concurrency or 1), 4))
    n = len(items)
    buckets = [[] for _ in lanes]
    for i in range(n):
        buckets[i % len(lanes)].append(i)

    lane_info = [{"name": l["name"], "model": l["model"], "count": len(buckets[k])}
                 for k, l in enumerate(lanes)]

    wall0 = time.time()
    results = [None] * n
    with ThreadPoolExecutor(max_workers=len(lanes)) as ex:
        futs = [ex.submit(_run_lane, l, buckets[k], items, system, max_tokens, temperature,
                          timeout, inner_conc) for k, l in enumerate(lanes)]
        for f in futs:
            try:
                for i, r in (f.result() or {}).items():
                    results[i] = r
            except Exception as e:  # noqa: BLE001
                pass

    # 跨线重试：失败项换另一条线再试一次（串行，不再并发）
    retried = 0
    if retry_other_line and len(lanes) > 1:
        for i in range(n):
            r = results[i]
            if r is None or r.get("ok"):
                continue
            alt = lanes[(i % len(lanes) + 1) % len(lanes)]
            try:
                r2 = _one(alt, alt["model"], system, items[i], max_tokens, temperature, timeout)
            except Exception as e:  # noqa: BLE001
                r2 = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            r2["lane"] = alt["name"]
            r2["retried_from"] = (r or {}).get("lane")
            if r2.get("ok"):
                results[i] = r2
                retried += 1
            else:
                r.setdefault("retry_error", r2.get("error"))

    wall = round(time.time() - wall0, 2)
    out, tot, okc = [], 0, 0
    for i, r in enumerate(results):
        r = dict(r or {"ok": False, "error": "无结果"})
        r["i"] = i
        okc += 1 if r.get("ok") else 0
        if isinstance(r.get("tokens"), int):
            tot += r["tokens"]
        out.append(r)
    return {"ok": okc > 0, "mode": "lanes", "lanes": lane_info,
            "total": n, "ok_count": okc, "retried_ok": retried, "wall_secs": wall,
            "total_tokens": tot, "results": out}
