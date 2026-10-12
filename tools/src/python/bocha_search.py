# -*- coding: utf-8 -*-
"""内置工具：bocha_search —— 博查 Web Search API（中文原生检索通道）。

为什么需要它（2026-10-13 决策）：
本轮排查确认，net_search 的中文短板有两层——一层是我自己的过滤回归（已修），
另一层是外部约束：google_web 被反爬返回 blocked_challenge、duckduckgo_lite 同样被拦、
brave_web 直接 429 限流。中文检索在聚合通道里长期靠 bing_web 单点支撑，且无日期字段。

博查补的正是这个缺口：
- 中文原生，国内直连（实测 200，无需代理、无验证码）
- 返回 datePublished（聚合通道完全没有的字段）→ 可做时效过滤
- 返回 summary（正文摘要，不是截断的 snippet）→ 少一次 net_fetch
- 商业模式是卖 API 调用而非卖排名，动机上不含竞价广告位

局限（如实）：语料以中文生态为主，英文/海外源给的是中文转述而非原文；
境内合规服务有内容过滤，不当"无滤镜的镜子"用。定位 = 中文时效补充通道。

密钥：BOCHA_API_KEY（.env / 环境变量）。本工具自带 .env 兜底解析，不依赖核心加载时机。
计费（2026-10-13 尼可提供开放平台购买页价目，元/次）：体验包 1000次 3.6（≈0.0036，疑一次性拉新价）；标准包 1000次 36（0.036）；套餐1 1万次 320（0.032）；套餐2 10万次 3000（0.030）；套餐3 100万次 28000（0.028）；套餐4 1000万次 250000（0.025）。
原则：纯按次计费，量越大单价越低、地板约 0.025 元/次，折扣有限，不值得为省钱预付大包。
未核：体验包是否限一次性；购买页 Web Search 与 AI Search 是否共用同一价表。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from tools.base import tool

_ROOT = Path(__file__).resolve().parents[3]
_API = "https://api.bochaai.com/v1/web-search"
_PROXY = "http://127.0.0.1:7897"
_FRESHNESS = {"oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit"}


def _load_key() -> str:
    """环境变量优先，.env 文件兜底（自解析，避免依赖核心加载时机）。"""
    k = os.environ.get("BOCHA_API_KEY", "").strip()
    if k:
        return k
    try:
        for line in (_ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            if name.strip() == "BOCHA_API_KEY":
                return val.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _post(payload: dict, key: str, timeout: int = 30):
    """先直连，失败回落本地代理。返回 (dict|None, err)。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def _call(use_proxy: bool):
        handlers = [urllib.request.ProxyHandler(
            {"http": _PROXY, "https": _PROXY} if use_proxy else {})]
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(_API, data=body, headers=headers, method="POST")
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    try:
        return _call(False), ""
    except urllib.error.HTTPError as e:
        return None, "HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:200])
    except Exception as exc_direct:  # noqa: BLE001
        try:
            return _call(True), ""
        except Exception as exc_proxy:  # noqa: BLE001
            return None, "direct:%s | proxy:%s" % (exc_direct, exc_proxy)


@tool(
    "bocha_search",
    "博查中文搜索（中文原生、国内直连、带发布日期）。定位：中文时效检索的补充通道，"
    "弥补 net_search 聚合通道的中文短板与反爬/限流。返回标题/链接/摘要/站点/发布日期。"
    "适合查中文新闻、国内厂商动态、中文报道与时效性内容；查英文一手源或海外原文仍走浏览器 Google 通道。"
    "参数：query 检索词；count 条数（默认 8，上限 50）；freshness 时效过滤"
    "（oneDay/oneWeek/oneMonth/oneYear/noLimit，默认不限）；summary 是否返回正文摘要（默认 true）。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词（中文优先）"},
            "count": {"type": "number", "description": "返回条数，默认 8，上限 50"},
            "freshness": {"type": "string",
                          "description": "时效过滤：oneDay/oneWeek/oneMonth/oneYear/noLimit，默认 noLimit"},
            "summary": {"type": "boolean", "description": "是否返回正文摘要（默认 true）"},
        },
        "required": ["query"],
    },
)
def run(query: str, count: int = 8, freshness: str = "noLimit", summary: bool = True) -> dict:
    if not query or not str(query).strip():
        return {"ok": False, "error": "query 不能为空"}
    key = _load_key()
    if not key:
        return {"ok": False, "error": "未配置 BOCHA_API_KEY（请写入项目根 .env 或环境变量）"}

    try:
        n = max(1, min(int(count or 8), 50))
    except (TypeError, ValueError):
        n = 8
    fresh = freshness if freshness in _FRESHNESS else "noLimit"

    payload = {"query": str(query).strip(), "count": n, "summary": bool(summary)}
    if fresh != "noLimit":
        payload["freshness"] = fresh

    t0 = time.time()
    data, err = _post(payload, key)
    if err:
        return {"ok": False, "error": err, "query": query}
    if not isinstance(data, dict):
        return {"ok": False, "error": "响应非 JSON", "query": query}
    if data.get("code") not in (200, None):
        return {"ok": False, "error": "接口返回 code=%s msg=%s" % (data.get("code"), data.get("msg")),
                "query": query}

    pages = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    results = []
    for p in pages:
        if not isinstance(p, dict):
            continue
        summ = p.get("summary") or p.get("snippet") or ""
        if len(summ) > 800:
            summ = summ[:800] + "…"
        results.append({
            "title": (p.get("name") or "").strip(),
            "url": p.get("url") or "",
            "snippet": (p.get("snippet") or "").strip()[:300],
            "summary": summ.strip(),
            "site": p.get("siteName") or "",
            "date": p.get("datePublished") or "",
        })

    return {
        "ok": True,
        "query": query,
        "count": len(results),
        "total_estimated": (((data.get("data") or {}).get("webPages") or {})
                            .get("totalEstimatedMatches")),
        "freshness": fresh,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "results": results,
    }
