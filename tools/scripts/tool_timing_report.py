# -*- coding: utf-8 -*-
"""工具耗时 / 失败率排行（读 data/operation.log，不写库、不改状态）。

背景（2026-10-22）：任务时间线给每步标了耗时，但只在界面里看得到当前这一趟；
要回答"哪个工具最慢、哪个最容易失败"得跨任务统计。agent 的 operation.log 现在
每条记录都带 ms 与 task，正好够算。

用法：
    python tools/scripts/tool_timing_report.py                # 近 7 天，Top 15
    python tools/scripts/tool_timing_report.py --days 30
    python tools/scripts/tool_timing_report.py --json         # 给别的脚本消费
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OP_LOG = os.path.join(ROOT, "data", "operation.log")

# Windows 控制台默认 GBK，中文表头会乱码 —— 切 UTF-8 代码页并重设 stdout（不影响重定向）
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:  # noqa: BLE001
        pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def load_records(days: int) -> list:
    if not os.path.exists(OP_LOG) and not os.path.exists(OP_LOG + ".1"):
        return []
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    out = []
    # 2026-10-22：日志已加轮转，只读当前文件会丢掉被轮转出去的历史（7 天统计曾从 1109 条掉到 10 条）
    try:
        sys.path.insert(0, ROOT)
        from core.logutil import iter_lines
        lines = iter_lines(OP_LOG)
    except Exception:  # noqa: BLE001  独立运行/路径异常时退回单文件
        lines = open(OP_LOG, "r", encoding="utf-8", errors="replace") if os.path.exists(OP_LOG) else []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get("ts") or ""
        try:
            t = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if t < cutoff:
            continue
        rec["_t"] = t
        out.append(rec)
    out.sort(key=lambda r: r["_t"])
    return out


def pct(values: list, q: float):
    if not values:
        return 0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def aggregate(records: list) -> dict:
    per_tool = defaultdict(lambda: {"n": 0, "fail": 0, "ms": [], "last": None})
    per_task = defaultdict(lambda: {"n": 0, "ms": 0, "fail": 0, "start": None})
    for r in records:
        name = r.get("tool") or "?"
        d = per_tool[name]
        d["n"] += 1
        if not r.get("ok"):
            d["fail"] += 1
        if isinstance(r.get("ms"), (int, float)):
            d["ms"].append(r["ms"])
        d["last"] = max(d["last"] or "", r.get("ts") or "")
        task = r.get("task") or ""
        if task:
            t = per_task[task]
            t["n"] += 1
            if isinstance(r.get("ms"), (int, float)):
                t["ms"] += r["ms"]
            if not r.get("ok"):
                t["fail"] += 1
            t["start"] = min(t["start"] or "9", r.get("ts") or "9")
    return {"tools": per_tool, "tasks": per_task}


def fmt_ms(ms) -> str:
    if ms is None:
        return "-"
    ms = float(ms)
    if ms < 1000:
        return "%.0fms" % ms
    if ms < 60000:
        return "%.1fs" % (ms / 1000)
    return "%.1fm" % (ms / 60000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="统计最近多少天（默认 7）")
    ap.add_argument("--top", type=int, default=15, help="每张表显示多少行（默认 15）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    args = ap.parse_args()

    recs = load_records(args.days)
    if not recs:
        print("近 %d 天没有 operation.log 记录（%s）" % (args.days, OP_LOG))
        return 0
    agg = aggregate(recs)
    timed = [r for r in recs if isinstance(r.get("ms"), (int, float))]

    payload = {
        "days": args.days,
        "records": len(recs),
        "timed_records": len(timed),
        "window": [recs[0].get("ts"), recs[-1].get("ts")],
        "tools": [
            {
                "tool": k,
                "calls": v["n"],
                "fails": v["fail"],
                "fail_rate": round(v["fail"] / v["n"], 3) if v["n"] else 0,
                "avg_ms": round(sum(v["ms"]) / len(v["ms"])) if v["ms"] else None,
                "p95_ms": pct(v["ms"], 0.95) if v["ms"] else None,
                "max_ms": max(v["ms"]) if v["ms"] else None,
                "last": v["last"],
            }
            for k, v in agg["tools"].items()
        ],
        "tasks": [
            {"task": k, "steps": v["n"], "fails": v["fail"], "total_ms": v["ms"] if v["ms"] else None,
             "start": v["start"]}
            for k, v in agg["tasks"].items()
        ],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("窗口：近 %d 天 · %d 条工具调用（其中 %d 条带耗时） · %s ~ %s\n"
          % (args.days, len(recs), len(timed),
             (payload["window"][0] or "")[:10], (payload["window"][1] or "")[:10]))

    tools = payload["tools"]
    print("== 最慢（按平均耗时，至少 2 次调用）==")
    slow = sorted([t for t in tools if t["calls"] >= 2 and t["avg_ms"] is not None],
                  key=lambda t: -t["avg_ms"])[:args.top]
    print("  %-22s %6s %8s %8s %8s" % ("工具", "次数", "平均", "p95", "最慢"))
    for t in slow:
        print("  %-22s %6d %8s %8s %8s"
              % (t["tool"], t["calls"], fmt_ms(t["avg_ms"]), fmt_ms(t["p95_ms"]), fmt_ms(t["max_ms"])))

    print("\n== 最易失败（失败率，至少 3 次调用）==")
    bad = sorted([t for t in tools if t["calls"] >= 3], key=lambda t: -t["fail_rate"])[:args.top]
    bad = [t for t in bad if t["fail_rate"] > 0]
    if not bad:
        print("  无失败记录")
    else:
        print("  %-22s %6s %6s %8s" % ("工具", "次数", "失败", "失败率"))
        for t in bad:
            print("  %-22s %6d %6d %7.1f%%" % (t["tool"], t["calls"], t["fails"], t["fail_rate"] * 100))

    print("\n== 调用最多 ==")
    hot = sorted(tools, key=lambda t: -t["calls"])[:args.top]
    print("  %-22s %6s %8s %8s" % ("工具", "次数", "平均", "失败"))
    for t in hot:
        print("  %-22s %6d %8s %8d" % (t["tool"], t["calls"], fmt_ms(t["avg_ms"]), t["fails"]))

    tasks = [t for t in payload["tasks"] if t["total_ms"]]
    if tasks:
        print("\n== 最耗时的任务（按工具总耗时）==")
        print("  %-26s %6s %8s %6s" % ("任务", "步数", "工具耗时", "失败"))
        for t in sorted(tasks, key=lambda x: -x["total_ms"])[:min(10, args.top)]:
            print("  %-26s %6d %8s %6d"
                  % (t["task"], t["steps"], fmt_ms(t["total_ms"]), t["fails"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
