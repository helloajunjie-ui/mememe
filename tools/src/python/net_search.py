# -*- coding: utf-8 -*-
"""内置工具：net_search —— 多源网络搜索（API 源优先 + 多引擎聚合兜底）。

2026-10-22 重做（"总是被墙/搜不到"）：原来只有 HTML 引擎聚合，国外引擎经常被墙、
返回反爬挑战，中文专有名词还容易被拆词。现在按可靠性排序：

1. **bocha**（博查 API，国内直连，0.5s 级，中文质量好）—— 配了 BOCHA_API_KEY 就用
2. **langsearch**（API，国内外都可）—— 配了 LANGSEARCH_API_KEY 就用
3. **engines**（Bing / DuckDuckGo / Brave / Wikipedia / Google News RSS 聚合）—— 永远兜底

API 源不经过网页反爬、不受隧道影响，是"被墙"场景下最稳的一层；引擎层保留作兜底与图片搜索。
每一层的结果都会标出来源，失败原因也逐层记录（providers_tried），不再只说"搜索失败"。

source 参数可强制：auto（默认）/ api（只用 API 源）/ engines（只用引擎）。
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
# 外部搜索/正文组件（lazyhuman-ai/websearch, MIT）——2026-09-13 从 workspace 迁出：
# workspace 是临时区、不进备份也不进公开仓库，活依赖放那里 = 迁移/复活后工具瘫痪。
# 新址随私有备份（library/）存活；旧址保留作回退兼容，两边都没有才报缺失。
_WS_CANDIDATES = [
    os.path.join(_ROOT, "library", "depot", "vendor", "websearch"),
    os.path.join(_ROOT, "workspace", "tools_external", "websearch"),
]
_WS_DIR = next((p for p in _WS_CANDIDATES if os.path.isdir(p)), _WS_CANDIDATES[0])
if _WS_DIR not in sys.path:
    sys.path.insert(0, _WS_DIR)

from tools.base import tool  # noqa: E402

try:
    from websearch_service import web_search_payload as _ws_payload  # noqa: E402
    _WS_READY = True
    _WS_ERR = ""
except Exception as _e:  # noqa: BLE001
    _WS_READY = False
    _WS_ERR = str(_e)


def _norm_item(r: dict, engine: str) -> dict:
    return {
        "title": r.get("title", ""),
        "url": r.get("url", ""),
        "snippet": (r.get("snippet") or r.get("summary") or "")[:600],
        "engine": r.get("engine") or engine,
        "published_at": r.get("published_at") or r.get("date"),
        "image_url": r.get("image_url", ""),
        "image_meta": r.get("image_meta") or {},
    }


def _via_bocha(query: str, n: int):
    from tools.src.python import bocha_search
    r = bocha_search.run(query, n)
    if not r.get("ok"):
        return [], r.get("error") or "bocha 失败", {}
    return [_norm_item(x, "bocha") for x in (r.get("results") or [])], "", {}


def _via_langsearch(query: str, n: int):
    from tools.src.python import langsearch_search
    r = langsearch_search.run(query, n)
    if not r.get("ok"):
        return [], r.get("error") or "langsearch 失败", {}
    return [_norm_item(x, "langsearch") for x in (r.get("results") or [])], "", {}


def _via_engines(query: str, n: int, image: bool):
    if not _WS_READY:
        return [], f"多引擎搜索组件不可用: {_WS_ERR}", {}
    providers = ["bing_images"] if image else None
    payload = _ws_payload(
        query, count=n, language="zh-CN",
        providers=providers, category="image" if image else "auto",
    )
    rows = [_norm_item(x, x.get("engine") or "engines") for x in (payload.get("results") or [])]
    return rows, "", {
        "engines": payload.get("used_engines") or [],
        "engine_failures": payload.get("engine_failures") or {},
    }


@tool(
    "net_search",
    "多源网络搜索：API 源（博查/langsearch，国内直连、不怕墙）优先，多引擎聚合兜底。"
    "返回标题+链接+摘要+来源引擎。时效性内容（新闻/赛程/价格/动态）用它最合适；"
    "image=True 时走 Bing Images 图片搜索。source 可强制 auto/api/engines。"
    "若搜索结果仍不理想、且问题是中文专有名词被拆词，可改用 browserskill 真浏览器开 Google。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "number", "description": "返回结果数，默认 5"},
            "image": {"type": "boolean", "description": "图片搜索模式，默认 false"},
            "source": {"type": "string", "enum": ["auto", "api", "engines"],
                       "description": "auto=API 优先引擎兜底（默认）；api=只用 API 源；engines=只用网页引擎"},
        },
        "required": ["query"],
    },
)
def run(query: str, max_results: int = 5, image: bool = False, source: str = "auto") -> dict:
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "error": "query 不能为空"}
    n = max(1, min(20, int(max_results or 5)))
    source = (source or "auto").strip().lower()
    if source not in ("auto", "api", "engines"):
        source = "auto"

    chain = []
    if not image and source in ("auto", "api"):
        chain += [("bocha", lambda: _via_bocha(q, n)), ("langsearch", lambda: _via_langsearch(q, n))]
    if source in ("auto", "engines") or image:
        chain.append(("engines", lambda: _via_engines(q, n, image)))
    if not chain:                     # source=api 但 image=True 之类的边角
        chain = [("engines", lambda: _via_engines(q, n, image))]

    attempts, extra = [], {}
    for name, fn in chain:
        t0 = time.time()
        try:
            rows, err, meta = fn()
        except Exception as e:  # noqa: BLE001
            rows, err, meta = [], f"{type(e).__name__}: {e}", {}
        attempts.append({"provider": name, "ok": bool(rows), "count": len(rows),
                         "ms": int((time.time() - t0) * 1000),
                         **({"error": err} if err else {})})
        if meta:
            extra = meta
        if rows:
            return {
                "ok": True, "query": q, "count": len(rows),
                "provider": name,
                "providers_tried": attempts,
                "image_mode": bool(image),
                "results": rows[:n],
                **extra,
            }

    return {
        "ok": False,
        "error": "所有搜索源都没有结果（不是目标站点问题，各层的失败原因见 providers_tried）",
        "query": q,
        "providers_tried": attempts,
        "hint": "国内被墙环境下先确认 API 源是否配了 key（BOCHA_API_KEY / LANGSEARCH_API_KEY）；"
                "若网络整体不通，用 net_diag 判断是代理出口断了还是站点问题。",
    }
