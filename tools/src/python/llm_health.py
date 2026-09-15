"""内置工具：llm_health —— 模型健康与容灾。

素月的 AI 接入容灾工具。中转站/官方的模型列表 ≠ 全部可用（已实测：部分模型 503、
部分首 token 12s+）。本工具负责：
- scan     ：探测各来源所有模型的联通率，更新健康表（config/llm_health.json）与各源可用模型列表
- status   ：查看健康表 + 当前接入配置（不泄漏完整 api_key）
- probe    ：对单个模型做一次快速探测（不写表）
- switch   ：手动切换当前模型/来源（写入 config/llm.json，主循环自动重载生效）

多源配置（可选）：config/llm.json 的 sources 数组
  [{name, base_url, api_key, enabled, models?}]；未配置时按单源（现有 base_url/api_key/model）。
"""
from __future__ import annotations

import json
import os
import time

from tools.base import tool

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_CFG = os.path.join(_ROOT, "config", "llm.json")


def _read_cfg() -> dict:
    try:
        with open(_CFG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cfg(d: dict) -> None:
    os.makedirs(os.path.dirname(_CFG), exist_ok=True)
    with open(_CFG, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def _mask(key: str) -> str:
    if not key:
        return ""
    return ("*" * (len(key) - 4) + key[-4:]) if len(key) > 4 else "***"


def _env_key() -> str:
    """兜底：源未配 key 时从环境变量读取（官方端点惯例，不落明文）。"""
    return os.environ.get("BAILING_API_KEY") or ""


def _sources(cfg: dict) -> list:
    srcs = cfg.get("sources") or []
    if srcs:
        return [x for x in srcs if x.get("enabled", True)]
    return [{"name": (cfg.get("base_url") or "default").rstrip("/"),
             "base_url": cfg.get("base_url"), "api_key": cfg.get("api_key")}]


def _models_of(src: dict, cfg: dict) -> list:
    ms = src.get("models") or []
    if ms:
        return ms
    base = (src.get("base_url") or "").rstrip("/")
    if (cfg.get("models_base_url") or "").rstrip("/") == base:
        return cfg.get("models") or []
    return []


def _fetch_models(base: str, key: str) -> list:
    """拉取 OpenAI 兼容端点模型列表（自动兼容 /v1）。失败返回 []。"""
    import httpx
    base = base.rstrip("/")
    candidates = [base + "/v1/models"] if base.endswith("/v1") else [base + "/models", base + "/v1/models"]
    for url in candidates:
        try:
            r = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
            r.raise_for_status()
            ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
            if ids:
                return ids
        except Exception:  # noqa: BLE001
            continue
    return []


def _scan() -> dict:
    from core import model_health as mh
    cfg = _read_cfg()
    global_key = cfg.get("api_key") or ""
    out = {}
    changed = False
    for src in _sources(cfg):
        base = (src.get("base_url") or "").rstrip("/")
        skey = ((src.get("api_key") or "").strip() or global_key or _env_key()).strip()
        models = _models_of(src, cfg)
        if not base or not skey:
            out[base or src.get("name", "?")] = {"error": "缺少 base_url 或 api_key"}
            continue
        if not models:
            models = _fetch_models(base, skey)
        if not models:
            out[base] = {"error": "无模型列表（端点不可达或无 /models）"}
            continue
        results = mh.scan_models(base, skey, models)
        mh.record(base, results)
        ok_models = [m for m in models if results.get(m, {}).get("ok")]
        for x in cfg.get("sources", []):
            if x.get("name") == src.get("name"):
                x["models"] = ok_models
                changed = True
        out[base] = mh.source_status(base)
    if changed:
        _save_cfg(cfg)
    return {"scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sources": out}


def _status() -> dict:
    from core import model_health as mh
    cfg = _read_cfg()
    return {
        "current": {
            "base_url": cfg.get("base_url"),
            "model": cfg.get("model"),
            "api_key_masked": _mask(cfg.get("api_key") or ""),
        },
        "sources": [
            {"name": s.get("name"), "base_url": s.get("base_url"),
             "api_key_masked": _mask(s.get("api_key") or ""),
             "enabled": s.get("enabled", True),
             "available_models": s.get("models") or []}
            for s in _sources(cfg)
        ],
        "health": mh.summary(),
    }


def _probe(model: str, source_name: str = "") -> dict:
    from core import model_health as mh
    cfg = _read_cfg()
    src = None
    for s in _sources(cfg):
        if not source_name or s.get("name") == source_name:
            src = s
            break
    if src is None:
        return {"ok": False, "error": f"来源不存在: {source_name}"}
    base = (src.get("base_url") or "").rstrip("/")
    skey = ((src.get("api_key") or "").strip() or cfg.get("api_key") or _env_key()).strip()
    if not base or not skey:
        return {"ok": False, "error": "缺少 base_url 或 api_key"}
    r = mh.probe(base, skey, model)
    r["model"] = model
    r["base_url"] = base
    return r


def _switch(model: str = "", source_name: str = "") -> dict:
    """切换当前模型/来源。先探测验证目标可用（防切到坏模型把自己带断），
    再写 config/llm.json——主循环下一轮开头自动重载，当前对话回合不中断。"""
    from core import model_health as mh
    cfg = _read_cfg()
    target_base = cfg.get("base_url") or ""
    target_key = cfg.get("api_key") or ""
    target_model = model
    if source_name:
        srcs = cfg.get("sources") or []
        hit = next((s for s in srcs if s.get("name") == source_name), None)
        if hit is None:
            return {"ok": False, "error": f"来源不存在: {source_name}"}
        target_base = hit.get("base_url")
        target_key = hit.get("api_key") or target_key
        if not target_model:
            if hit.get("models"):
                target_model = hit["models"][0]
            else:
                return {"ok": False, "error": f"来源 {source_name} 无可用模型列表（先 scan）"}
    if not target_model:
        return {"ok": False, "error": "未指定 model 或 source_name"}
    # 切前验证：目标模型必须真实可用（避免切到已失效模型导致下一轮报错）
    if target_base and target_key:
        r = mh.probe(target_base, target_key, target_model)
        if not r.get("ok"):
            return {"ok": False,
                    "error": f"目标 {target_model} 探测失败（{r.get('error')}），未切换——先 scan 找可用模型"}
        if r.get("latency", 0) > 8:
            return {"ok": False,
                    "error": f"目标 {target_model} 延迟 {r.get('latency')}s 过慢，未切换"}
    # 写入配置（下一轮主循环自动重载，当前对话不中断）
    cfg["base_url"] = target_base
    cfg["model"] = target_model
    if target_key:
        cfg["api_key"] = target_key
    _save_cfg(cfg)
    return {"ok": True,
            "message": f"已切换到 {target_base} / {target_model}（下一轮自动生效，当前对话不中断）",
            "base_url": target_base, "model": target_model}


@tool(
    "llm_health",
    "AI 模型健康与容灾：扫描各来源模型联通率更新健康表（scan）、查看健康状态与当前接入（status）、"
    "单模型快速探测（probe）、手动切换可用模型或来源（switch）。当当前模型报错或疑似失效时用本工具",
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["scan", "status", "probe", "switch"],
                "description": "scan=全量探测更新健康表（较慢）；status=查看健康表与当前配置；"
                               "probe=测单个模型；switch=切换当前模型/来源",
            },
            "model": {"type": "string", "description": "probe/switch 时指定模型 id"},
            "source_name": {"type": "string", "description": "来源名称（多源时指定，switch 可只切来源）"},
        },
        "required": ["action"],
    },
)
def run(action: str = "scan", model: str = "", source_name: str = "") -> dict:
    action = (action or "scan").strip().lower()
    try:
        if action == "scan":
            return _scan()
        if action == "status":
            return _status()
        if action == "probe":
            if not model:
                return {"ok": False, "error": "probe 需指定 model"}
            return _probe(model, source_name)
        if action == "switch":
            return _switch(model, source_name)
        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"执行异常: {type(e).__name__}: {e}"}
