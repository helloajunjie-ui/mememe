# -*- coding: utf-8 -*-
"""内置工具：net_search —— 多引擎网络搜索（成熟方案：lazyhuman-ai/websearch）。

替代旧的单引擎方案（抓 Bing HTML 用正则抠 b_algo 块，脆弱、反爬一改就废、单点失败）：
- 多引擎聚合容错：Bing / DuckDuckGo / Brave / Wikipedia / Google News RSS 等，单一引擎
  被反爬或失败时自动换源，并透明报告哪些引擎成功/失败。
- 模型无关：不依赖任何 LLM 厂商的专属接口，任何接入的模型都能用。
- 零外部搜索 API key：默认 HTML/RSS 引擎即可工作。
- 返回规范化结果：标题 + 链接 + 摘要 + 来源引擎。
- 图片模式（image=True）：走 Bing Images，返回原图 URL + 缩略图 + 来源页，可配合 net_download 取图。

来源：https://github.com/lazyhuman-ai/websearch（MIT），已 clone 到 library/depot/vendor/websearch。
"""
from __future__ import annotations

import os
import sys

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


@tool(
    "net_search",
    "⚠️通道前置（调本工具前先想）：质量型/中文/专有名词搜索优先走 browserskill 真浏览器开 Google——"
    "实测零拆词、中文新闻带来源+日期；本工具是多引擎聚合，专有名词易被拆词，宜快速探路或图片搜索。"
    "多引擎网络搜索（Bing/DuckDuckGo/Brave/Wikipedia 等聚合容错，单个引擎失败自动换源，透明报告），"
    "返回规范化结果：标题+链接+摘要+来源引擎。时效性内容（新闻/赛程/价格/动态）用它最合适。"
    "image=True 时走图片搜索（Bing Images）：返回原图 URL/缩略图/来源页，适合找图/配图/视觉素材。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "number", "description": "返回结果数，默认 5"},
            "image": {"type": "boolean", "description": "图片搜索模式，默认 false"},
        },
        "required": ["query"],
    },
)
def run(query: str, max_results: int = 5, image: bool = False) -> dict:
    if not _WS_READY:
        return {"ok": False,
                "error": f"多引擎搜索组件不可用: {_WS_ERR}（需安装 library/depot/vendor/websearch 依赖）"}
    if not query or not str(query).strip():
        return {"ok": False, "error": "query 不能为空"}
    try:
        providers = ["bing_images"] if image else None
        payload = _ws_payload(
            str(query).strip(),
            count=int(max_results or 5),
            language="zh-CN",
            providers=providers,
            category="image" if image else "auto",
        )
        results = payload.get("results") or []
        used = payload.get("used_engines") or []
        failed = payload.get("engine_failures") or {}
        out = [{
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("snippet", ""),
            "engine": r.get("engine"),
            "published_at": r.get("published_at"),
            "image_url": r.get("image_url", ""),
            "image_meta": r.get("image_meta") or {},
        } for r in results]
        return {
            "ok": True,
            "query": str(query),
            "count": len(out),
            "engines": used,
            "engine_failures": failed,
            "image_mode": bool(image),
            "results": out,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"搜索失败: {e}"}
