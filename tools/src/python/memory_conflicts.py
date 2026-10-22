"""内置工具：memory_conflicts —— 长期记忆矛盾队列的出口（查看 / 裁定）。

背景（2026-10-22 深度检验）：巩固环节发现矛盾时只登记到 memory_conflicts、绝不自动改写
（自动融合是投毒入口：外部内容只要声称"你记错了"就能改写记忆）。这个设计是对的，但
此前全仓没有任何调用方——9 条矛盾从 2026-09-25 挂到 10-22，`resolved` 一直是 0，
安全网变成了死信箱。

本工具只提供「看见」和「落裁定」两条能力，裁定权仍然在素月自己手里：
- list      ：看待裁定的矛盾（含双方原始内容），不改变任何状态
- supersede ：指定「谁取代谁」→ 旧条失效归档 + 写修订历史 + 相关矛盾标记已裁定
- resolve   ：判定这条矛盾是误报/不必改 → 只标记已裁定，不动记忆内容（必须二次确认）
"""
from __future__ import annotations

from pathlib import Path

from tools.base import tool

_DB = str(Path(__file__).resolve().parents[3] / "data" / "memory.db")

_CONFIRM_TOKENS = ("RESOLVE",)


@tool(
    "memory_conflicts",
    "查看/裁定长期记忆里的矛盾条目。巩固环节只登记不自动改写（防投毒），所以矛盾会一直挂着，"
    "需要主动裁定：action=list 查看待裁定项；action=supersede 指定 old_id 被 by_id 取代"
    "（旧条归档、写修订历史、相关矛盾自动标记已裁定）；action=resolve 判定为误报"
    "（只标记已裁定，须传 confirm=\"RESOLVE\"）。属中等风险操作。",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "supersede", "resolve"],
                       "description": "list=查看；supersede=指定谁取代谁；resolve=判定误报"},
            "conflict_id": {"type": "number", "description": "resolve 时要标记的矛盾 id"},
            "old_id": {"type": "number", "description": "supersede 时被取代的记忆 id（失效方）"},
            "by_id": {"type": "number", "description": "supersede 时取代方的记忆 id（保留方）"},
            "reason": {"type": "string", "description": "裁定理由（写进修订历史，便于日后回看）"},
            "confirm": {"type": "string",
                        "description": "写操作二次确认：resolve 必须传 RESOLVE；supersede 必须传 RESOLVE"},
        },
        "required": ["action"],
    },
)
def run(action: str = "list", conflict_id: int | None = None, old_id: int | None = None,
        by_id: int | None = None, reason: str = "", confirm: str = "") -> dict:
    from core.memory import Memory

    mem = Memory(_DB)
    try:
        if action == "list":
            rows = mem.pending_conflicts()
            return {
                "ok": True,
                "count": len(rows),
                "note": "这些是巩固环节登记、尚未裁定的矛盾。核对来源后："
                        "用 supersede 指定谁取代谁，或 resolve 判定误报。",
                "conflicts": [
                    {"id": r.get("id"), "mem_id": r.get("mem_id"),
                     "note": r.get("note"), "detected_at": r.get("detected_at"),
                     "importance": r.get("importance"),
                     "mem_content": (r.get("mem_content") or "")[:300]}
                    for r in rows
                ],
            }

        if str(confirm).strip().upper() not in _CONFIRM_TOKENS:
            return {"ok": False, "error": "写操作需要二次确认：请传 confirm=\"RESOLVE\""}

        if action == "supersede":
            if not old_id or not by_id:
                return {"ok": False, "error": "supersede 需要 old_id 与 by_id"}
            if int(old_id) == int(by_id):
                return {"ok": False, "error": "old_id 与 by_id 不能相同"}
            try:
                mem.supersede(int(old_id), int(by_id),
                              reason=reason or "memory_conflicts:supersede")
            except ValueError as e:
                return {"ok": False, "error": str(e)}
            cur = mem.conn.execute(
                "UPDATE memory_conflicts SET resolved=1 WHERE resolved=0 AND mem_id IN (?,?)",
                (int(old_id), int(by_id)),
            )
            mem.conn.commit()
            return {"ok": True, "action": "supersede", "old_id": int(old_id), "by_id": int(by_id),
                    "conflicts_closed": cur.rowcount,
                    "result": "旧条已失效归档并写入修订历史（可回滚、可追溯）"}

        if action == "resolve":
            if not conflict_id:
                return {"ok": False, "error": "resolve 需要 conflict_id"}
            row = mem.conn.execute(
                "SELECT id, mem_id, resolved FROM memory_conflicts WHERE id=?",
                (int(conflict_id),),
            ).fetchone()
            if row is None:
                return {"ok": False, "error": f"矛盾 {conflict_id} 不存在"}
            if row["resolved"]:
                return {"ok": True, "action": "resolve", "already": True}
            mem.resolve_conflict(int(conflict_id))
            return {"ok": True, "action": "resolve", "conflict_id": int(conflict_id),
                    "mem_id": row["mem_id"],
                    "result": "已标记为已裁定（记忆内容未改动）"}

        return {"ok": False, "error": f"未知 action: {action}"}
    finally:
        mem.close()
