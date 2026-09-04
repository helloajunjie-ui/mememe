"""ctx_search —— 全量上下文检索（从任务消息存档定位早期信息）。

设计（用户导师原则）：
- 全量上下文落盘到 workspace/tasks/<task_id>/messages.jsonl（不丢任何信息）；
- LLM 输入窗口每回合只提供最近 20 回合记录；
- 用户引用早期内容（"之前提到过 X""把 2 月前的报告拿来"）而窗口无对应记录时，
  由 AI 决定关键词并调用本工具检索（关键词由 AI 理解提取，不靠规则硬猜）；
- 返回【命中回合 ± context_rounds 回合】的上下文窗口，带回合号与时间戳，
  让 AI 看到完整语境而非孤立一条，据此"回想起来"。
"""
from __future__ import annotations

import glob
import json
import os

from tools.base import tool


@tool(
    "ctx_search",
    "从任务全量上下文存档检索早期信息。当用户引用更早的内容（如'之前提到的X'、'把之前的报告拿来'）"
    "而当前窗口没有对应记录时使用：按你理解的关键词检索，返回命中回合及其前后各 context_rounds 回合"
    "的上下文（带回合号与时间戳），据此回想，不要凭空编造缺失信息。",
    {
        "keyword": {"type": "string", "description": "检索关键词（由你按用户意图提取，如人名/主题/文件/时间相关词）", "required": True},
        "task_id": {"type": "string", "description": "限定任务ID（留空自动搜最近任务）", "required": False},
        "context_rounds": {"type": "integer", "description": "命中回合前后各取多少回合（默认5）", "required": False},
        "limit": {"type": "integer", "description": "最多返回命中组数（默认3）", "required": False},
    },
)
def run(keyword: str, task_id: str = "", context_rounds: int = 5, limit: int = 3):
    base = os.path.join("workspace", "tasks")
    if not os.path.isdir(base):
        return {"found": False, "tasks": [], "hint": "暂无任务存档目录"}
    try:
        context_rounds = max(0, min(int(context_rounds), 10))
    except (TypeError, ValueError):
        context_rounds = 5
    try:
        limit = max(1, min(int(limit), 5))
    except (TypeError, ValueError):
        limit = 3
    kw = (keyword or "").strip()
    if not kw:
        return {"found": False, "tasks": [], "hint": "keyword 不能为空"}
    tasks = sorted(glob.glob(os.path.join(base, "*", "messages.jsonl")), reverse=True)
    results = []
    for p in tasks:
        tid = os.path.basename(os.path.dirname(p))
        if task_id and tid != task_id:
            continue
        # 读入全部记录
        recs = []
        try:
            f = open(p, "r", encoding="utf-8")
        except OSError:
            continue
        with f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
        if not recs:
            continue
        # 标记每行所属回合号（assistant 起算；无 assistant 的记录归当前回合）
        round_of = []
        cur = 0
        for rec in recs:
            if rec.get("role") == "assistant":
                cur += 1
            round_of.append(cur)
        # 命中行 → 命中回合 → 扩展 ±context_rounds
        hit_rows = [i for i, rec in enumerate(recs)
                    if kw.lower() in str(rec.get("content") or "").lower()]
        if not hit_rows:
            continue
        hit_rounds = set()
        for i in hit_rows:
            r = round_of[i]
            for rr in range(max(1, r - context_rounds), r + context_rounds + 1):
                hit_rounds.add(rr)
        # 输出命中窗口（带回合号/时间戳/命中标记）
        out = []
        hit_row_set = set(hit_rows)
        for i, rec in enumerate(recs):
            if round_of[i] not in hit_rounds:
                continue
            out.append({
                "round": round_of[i],
                "ts": rec.get("ts", ""),
                "role": rec.get("role"),
                "hit": i in hit_row_set,
                "content": str(rec.get("content") or "")[:200],
            })
        if out:
            results.append({"task_id": tid, "rounds": out, "hit_rounds": sorted(hit_rounds)})
        if len(results) >= limit:
            break
    if not results:
        return {"found": False, "tasks": [], "hint": f"存档中未找到含「{kw}」的记录，可换关键词或说明需要的具体内容"}
    return {"found": True, "tasks": results,
            "hint": "以上为命中回合及其前后上下文（round=回合号, hit=含关键词）。据此回想，缺失信息不要虚构"}
