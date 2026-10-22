# -*- coding: utf-8 -*-
"""内置工具：method_find —— 按关键词检索方法论库（世界书全文）。

为什么需要：世界书目录为了省上下文，每类只列前 30 条（2026-10-22 实测原目录
占 20k tokens/轮）。被截掉的条目必须能按需取回，否则等于删知识。
本工具就是那个"按需取回"入口：名称/场景/方法/关键词/分类全文检索。
"""
from __future__ import annotations

from tools.base import tool


@tool(
    "method_find",
    "按关键词检索方法论库（世界书全文）：名称/场景/方法/关键词/分类都会搜。"
    "目录里每类只列前 30 条、其余标注「另 N 条」，用本工具把这部分找回来。"
    "返回 id + 名称 + 场景 + 方法摘要；full=True 时返回完整方法正文。",
    {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "检索关键词，如「记忆」「快照」「并发」"},
            "limit": {"type": "number", "description": "返回条数，默认 8"},
            "full": {"type": "boolean", "description": "是否返回完整方法正文（默认 false，只给摘要）"},
        },
        "required": ["keyword"],
    },
)
def run(keyword: str = "", limit: int = 8, full: bool = False) -> dict:
    kw = (keyword or "").strip()
    if not kw:
        return {"ok": False, "error": "keyword 不能为空"}
    from core.methods import MethodStore

    n = max(1, min(30, int(limit or 8)))
    ms = MethodStore()
    hits = ms.find(kw, limit=n)
    out = []
    for m in hits:
        item = {
            "id": m.get("id"),
            "name": m.get("name"),
            "category": m.get("category"),
            "type": m.get("type"),
            "scene": (m.get("scene") or "")[:160],
            "importance": m.get("importance"),
        }
        if full:
            item["method"] = m.get("method")
            item["evidence"] = m.get("evidence")
        else:
            item["method"] = (m.get("method") or "")[:200]
        out.append(item)
    return {"ok": True, "keyword": kw, "count": len(out), "results": out,
            "note": ("没找到就换更短的词（如「记忆」而不是「记忆模块升级」）；"
                     "也可以直接读 data/methodology.json。") if not out else ""}
