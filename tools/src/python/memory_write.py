"""内置工具：memory_write —— 把知识/经验写入长期记忆（认知进化）。

设计意图：白绫的"头脑"= LLM 参数知识 + 长期记忆。遇到新知识、可复用经验、重要事实时，
主动写入长期记忆（SQLite），使认知跨会话持续成长。
"""
from __future__ import annotations

from pathlib import Path

from tools.base import tool

_DB = str(Path(__file__).resolve().parents[3] / "data" / "memory.db")


@tool(
    "memory_write",
    "把知识/经验/重要事实写入长期记忆（SQLite，跨会话保留，后续会被自动加载进上下文）。"
    "用于沉淀学到的知识、可复用的任务经验、关于用户/环境的长期事实。属中等风险操作。",
    {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容，用完整句子描述"},
            "type": {"type": "string", "enum": ["fact", "episode"],
                     "description": "fact=通用知识/事实；episode=一次具体经历/经验"},
            "importance": {"type": "number",
                           "description": "重要性 0~1。影响后续被加载进上下文的优先级，重要知识给 0.6~0.9，琐碎给 0.3 以下"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "可选标签，便于检索"},
        },
        "required": ["content"],
    },
)
def run(content: str, type: str = "fact", importance: float = 0.6, tags: list = None) -> dict:
    if not content or not content.strip():
        return {"ok": False, "error": "内容不能为空"}
    # 本性护栏：拦截教唆改变本性/作恶的内容（防投毒/黑化）
    from tools.base import soul_guard_check

    hit = soul_guard_check(content)
    if hit:
        return {"ok": False, "error": f"本性护栏拦截：内容含恶意意图（{hit}），拒绝写入记忆。"
                                     f"若为误判请换一种表述，或由共建者确认。"}
    importance = max(0.0, min(1.0, float(importance)))
    type_ = type if type in ("fact", "episode") else "fact"
    from core.memory import Memory

    mem = Memory(_DB)
    try:
        if type_ == "episode":
            mid = mem.add_episode(content, importance, tags=tags, source="bailing")
        else:
            mid = mem.add_fact(content, importance=importance, tags=tags, source="bailing")
        return {"ok": True, "memory_id": mid, "type": type_, "importance": importance,
                "note": "已写入长期记忆，后续会话可检索引用"}
    finally:
        mem.close()
