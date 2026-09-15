"""模型健康表：探测 OpenAI 兼容端点上各模型的联通率，供素月自动容灾选型。

设计意图（容灾策略）：
- 中转站/官方的模型列表 ≠ 全部可用（已实测：yuegle 13 个模型仅 1-2 个可用，
  pro/flash3 等返回 503 或首 token 12s+）。健康表把"能不能用、多快"量化落盘。
- 探测口径：轻量 chat（max_tokens 4），记录 ok / 延迟 / 是否含回复 / 错误摘要。
- 容灾选择：ok 优先 → 延迟升序 → 名称稳定排序（避免每次换模型抖动）。
- 数据文件：config/llm_health.json，按 base_url 分键，含探测时间。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

_HEALTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "llm_health.json")
_PROBE_MSG = [{"role": "user", "content": "请只回复两个字：OK"}]
_TIMEOUT = 6          # 单模型探测超时（秒）—— 8s 以上即视为"慢到不可用"
_GOOD_LATENCY = 8.0   # 高于此延迟标记 slow（可用但体验差）


def health_path() -> str:
    return _HEALTH_FILE


def probe(base_url: str, api_key: str, model: str, timeout: int = _TIMEOUT) -> Dict:
    """单模型探测。返回 {ok, latency, error, tested_at}。"""
    t0 = time.time()
    import httpx
    body = {
        "model": model,
        "messages": _PROBE_MSG,
        "max_tokens": 4,
        "stream": False,
    }
    try:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            url = base + "/chat/completions"
        else:
            url = base + "/v1/chat/completions"
        r = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
        )
        latency = round(time.time() - t0, 2)
        if r.status_code != 200:
            err = ""
            try:
                err = r.json().get("error", {}).get("message", "")[:160] or r.text[:120]
            except Exception:  # noqa: BLE001
                err = r.text[:120]
            return {"ok": False, "latency": latency, "error": f"HTTP {r.status_code} {err}",
                    "tested_at": time.time()}
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return {"ok": True, "latency": latency, "error": "",
                "reply_ok": "OK" in content[:80],
                "tested_at": time.time()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latency": round(time.time() - t0, 2),
                "error": f"{type(e).__name__}: {str(e)[:120]}", "tested_at": time.time()}


def load_health() -> Dict:
    try:
        with open(_HEALTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_health(health: Dict) -> None:
    os.makedirs(os.path.dirname(_HEALTH_FILE), exist_ok=True)
    with open(_HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=2)


def _entry(health: Dict, base_url: str) -> Dict:
    return health.setdefault(base_url, {"models": {}, "tested_at": None})


def record(base_url: str, results: Dict[str, Dict]) -> None:
    """把一次扫描结果写入健康表（保留未测模型的旧记录，已测覆盖）。"""
    health = load_health()
    ent = _entry(health, base_url)
    now = time.time()
    for model, r in results.items():
        ent["models"][model] = r
    ent["tested_at"] = now
    health["_updated_at"] = now
    save_health(health)


def scan_models(base_url: str, api_key: str, models: List[str],
                timeout: int = _TIMEOUT, workers: int = 4) -> Dict[str, Dict]:
    """并发探测一组模型。返回 {model: probe_result}（探测失败也有记录）。"""
    import concurrent.futures
    results: Dict[str, Dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe, base_url, api_key, m, timeout): m for m in models}
        for fut in concurrent.futures.as_completed(futs):
            m = futs[fut]
            try:
                results[m] = fut.result()
            except Exception as e:  # noqa: BLE001
                results[m] = {"ok": False, "latency": -1, "error": str(e)[:120], "tested_at": time.time()}
    return results


def rank(base_url: str, exclude: Optional[str] = None) -> List[str]:
    """当前源可用模型排序：ok 优先 → 延迟升序 → 名称稳定。返回模型 id 列表（不含 exclude）。"""
    health = load_health()
    ent = _entry(health, base_url)
    items = []
    for m, r in ent["models"].items():
        if m == exclude:
            continue
        if not r.get("ok"):
            continue
        items.append((r.get("latency", 99), m))
    items.sort(key=lambda x: (x[0], x[1]))
    return [m for _, m in items]


def pick_healthy(base_url: str, exclude: Optional[str] = None) -> Optional[str]:
    """返回当前源最优可用模型；没有则 None。"""
    ranked = rank(base_url, exclude)
    return ranked[0] if ranked else None


def source_status(base_url: str) -> Dict:
    """某源健康摘要：可用数/最慢延迟/最近探测时间。"""
    health = load_health()
    ent = _entry(health, base_url)
    models = ent.get("models", {})
    ok = [m for m, r in models.items() if r.get("ok")]
    return {
        "base_url": base_url,
        "tested_at": ent.get("tested_at"),
        "total": len(models),
        "ok": len(ok),
        "slow": len([m for m in ok if models[m].get("latency", 0) > _GOOD_LATENCY]),
        "ok_models": ok,
    }


def summary() -> Dict:
    """全量健康摘要（工具/前端展示用）。"""
    health = load_health()
    out = {"_updated_at": health.get("_updated_at"), "sources": {}}
    for base_url, ent in health.items():
        if base_url.startswith("_"):
            continue
        out["sources"][base_url] = source_status(base_url)
    return out
