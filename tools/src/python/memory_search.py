"""内置工具：memory_search —— 检索长期记忆（认知复用，防重复搜索）。

设计意图：处理查询（尤其时效/重复类）问题前，先检索长期记忆看是否已有查证结论——
有则直接复用（注意记录时间是否过时），避免"明明查过却从 0 重搜"的弯路。
与 memory_write 成对：写是为了进化，查是为了不重复劳动。
"""
from __future__ import annotations

from pathlib import Path

from tools.base import tool

_DB = str(Path(__file__).resolve().parents[3] / "data" / "memory.db")


@tool(
    "memory_search",
    "检索长期记忆（SQLite，跨会话保留）。处理查询/时效类问题前先调用，看记忆里是否已有相关结论或存档，有则直接复用，避免重复搜索。",
    {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "检索关键词，如「9月8日 球赛」「柳岩」「某主题」"},
            "limit": {"type": "number", "description": "返回条数，默认 8"},
        },
        "required": ["keyword"],
    },
)
def run(keyword: str, limit: int = 8) -> dict:
    if not keyword or not keyword.strip():
        return {"ok": False, "error": "关键词不能为空"}
    from core.memory import Memory

    mem = Memory(_DB)
    try:
        rows = mem.query(keyword, limit=int(limit), types=None)
        return {
            "ok": True,
            "keyword": keyword,
            "count": len(rows),
            "note": "有命中→检查 created_at 是否过时，可用则直接复用；无命中→才去外部查。",
            "results": [
                {"id": r["id"], "type": r.get("type"), "content": r.get("content", ""),
                 "importance": r.get("importance"), "created_at": r.get("created_at")}
                for r in rows
            ],
        }
    finally:
        mem.close()
