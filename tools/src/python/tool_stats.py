"""内置工具：tool_stats —— 工具调用的耗时 / 失败率排行（读 data/operation.log）。

背景（2026-10-22）：界面时间线只让人看见"这一趟"每步花了多久；
要回答"哪个工具最慢 / 哪个最容易失败"，得跨任务统计。agent 的 operation.log
现在每条记录都带 ms 与 task，本工具把它聚合成三张榜，直接在对话里答。

与 tools/scripts/tool_timing_report.py 共用同一套聚合逻辑，避免两处口径漂移。
"""
from __future__ import annotations

from tools.base import tool


@tool(
    "tool_stats",
    "查看工具调用的耗时与失败率排行（近 N 天，读 data/operation.log）。"
    "用于回答「哪个工具最慢」「哪个工具最容易失败」「最近哪类任务最耗时」，"
    "也可用于自查是否需要优化某个工具。只读，不改任何状态。",
    {
        "type": "object",
        "properties": {
            "days": {"type": "number", "description": "统计最近多少天，默认 7"},
            "top": {"type": "number", "description": "每张榜返回前几名，默认 8"},
        },
        "required": [],
    },
)
def run(days: int = 7, top: int = 8) -> dict:
    from tools.scripts.tool_timing_report import aggregate, load_records, pct

    try:
        days = max(1, min(90, int(days or 7)))
        top = max(1, min(30, int(top or 8)))
    except (TypeError, ValueError):
        days, top = 7, 8

    recs = load_records(days)
    if not recs:
        return {"ok": True, "days": days, "calls": 0,
                "note": "近 %d 天没有工具调用记录" % days}

    agg = aggregate(recs)
    tools = []
    for name, v in agg["tools"].items():
        tools.append({
            "tool": name,
            "calls": v["n"],
            "fails": v["fail"],
            "fail_rate": round(v["fail"] / v["n"], 3) if v["n"] else 0,
            "avg_ms": round(sum(v["ms"]) / len(v["ms"])) if v["ms"] else None,
            "p95_ms": pct(v["ms"], 0.95) if v["ms"] else None,
            "max_ms": max(v["ms"]) if v["ms"] else None,
        })

    slow = sorted([t for t in tools if t["calls"] >= 2 and t["avg_ms"] is not None],
                  key=lambda t: -t["avg_ms"])[:top]
    bad = [t for t in tools if t["calls"] >= 3 and t["fail_rate"] > 0]
    bad = sorted(bad, key=lambda t: -t["fail_rate"])[:top]
    hot = sorted(tools, key=lambda t: -t["calls"])[:top]

    timed = sum(1 for r in recs if isinstance(r.get("ms"), (int, float)))
    return {
        "ok": True,
        "days": days,
        "calls": len(recs),
        "timed_calls": timed,
        "note": ("耗时列只统计带 ms 的记录（2026-10-22 之后才有）；失败率是全量统计。"
                 if timed < len(recs) else ""),
        "slowest": slow,
        "most_failed": bad,
        "busiest": hot,
    }
