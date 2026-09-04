"""内置工具：method_learn —— 沉淀方法论经验（好的方法复用 / 坏的教训避免）。

设计意图（见设计文档 5.9）：白绫学会自我评估与改进。
- 发现自己做得好/有效的方法 → 记下来，下次还这样思考。
- 发现自己踩过的坑/低效方法 → 记下来，下次避免。
- 方法论会注入 system prompt 的"我的经验法则"，形成自我强化的行为模式。
"""
from __future__ import annotations

from tools.base import tool


@tool(
    "method_learn",
    "沉淀一条方法论经验：好的方法（下次复用）或坏的教训（下次避免）。"
    "会进入你的'经验法则'，注入后续上下文。用于自我评估与持续改进：方法好就记、方法糟就避免。",
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["good", "bad"],
                     "description": "good=这个方法有效，值得复用；bad=这个做法很糟/踩坑，下次避免"},
            "scene": {"type": "string", "description": "适用场景，如 '遇到 HTML 转文本时'"},
            "method": {"type": "string",
                       "description": "方法/教训内容，完整一句话描述怎么做或不要怎么做"},
            "evidence": {"type": "string",
                         "description": "证据/为什么有效或无效（可选，如 '实测跑通' / '报错 xxx'）"},
            "importance": {"type": "number",
                           "description": "重要性 0~1，默认 0.6。影响在上下文中的优先级"},
        },
        "required": ["type", "scene", "method"],
    },
)
def run(type: str, scene: str, method: str, evidence: str = "", importance: float = 0.6) -> dict:
    if type not in ("good", "bad"):
        return {"ok": False, "error": "type 必须为 good（值得复用）或 bad（避免）"}
    if not method or not method.strip():
        return {"ok": False, "error": "方法内容不能为空"}
    from core.methods import MethodStore

    ms = MethodStore()
    mid = ms.learn(type, scene, method, evidence, importance)
    kind = "正面方法" if type == "good" else "反面教训"
    return {
        "ok": True,
        "method_id": mid,
        "kind": kind,
        "note": f"已沉淀{kind}，后续上下文会注入此经验。当前库：{ms.summary()}",
    }
