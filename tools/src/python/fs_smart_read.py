"""内置工具：fs_smart_read —— 按行号范围或正则精准读取文本文件片段。

补齐 fs_read 的短板：fs_read 全量读取会被 max_chars 截断且不带行号，
拿到大文件时无法定位/核对具体行（此前只能退回 PowerShell Select-Object -Skip）。

- 行范围模式：读第 [start_line, end_line] 行（负数从末尾倒数），带行号。
- 正则模式（pattern）：返回匹配行（带行号）及上下文行，近似 grep -n -C。

只读工具，路径校验复用 fs_explore._check_read_path（项目根 + 只读白名单）。
"""
from __future__ import annotations

import os
import re

from tools.base import tool
from tools.src.python.fs_explore import _check_read_path

_MAX_SCAN_BYTES = 8 * 1024 * 1024


@tool(
    "fs_smart_read",
    "按行号范围或正则精准读取文本文件片段（返回带行号内容，补 fs_read 无法定位大文件的短板）",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对项目根或绝对路径）"},
            "start_line": {"type": "integer", "description": "起始行号，从 1 开始；负数从末尾倒数（-10=最后10行）"},
            "end_line": {"type": "integer", "description": "结束行号（含）；默认读到文件末尾"},
            "pattern": {"type": "string", "description": "正则表达式；提供时进入 grep 模式，返回匹配行及上下文"},
            "context": {"type": "integer", "description": "grep 模式上下文行数，默认 0"},
            "max_lines": {"type": "integer", "description": "最多返回行数，默认 400"},
            "case_sensitive": {"type": "boolean", "description": "正则是否区分大小写，默认 false"},
        },
        "required": ["path"],
    },
)
def run(path, start_line=None, end_line=None, pattern=None, context=0,
        max_lines=400, case_sensitive=False):
    try:
        p = _check_read_path(path)
        if not os.path.isfile(p):
            return {"ok": False, "error": "不是文件: " + str(path)}
        if os.path.getsize(p) > _MAX_SCAN_BYTES:
            return {"ok": False, "error": "文件过大(>8MB)，请缩小范围或改用 fs_search: " + str(path)}
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception as e:
        return {"ok": False, "error": "读取失败: " + str(e)}

    total = len(lines)
    width = max(3, len(str(total)))
    try:
        cap = int(max_lines) if max_lines else 400
    except (TypeError, ValueError):
        cap = 400

    if pattern:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            return {"ok": False, "error": "正则非法: " + str(e)}
        try:
            ctx = max(0, int(context))
        except (TypeError, ValueError):
            ctx = 0
        hit = [i for i, t in enumerate(lines) if rx.search(t)]
        if not hit:
            return {"ok": True, "path": p, "mode": "grep", "total_lines": total,
                    "matches": 0, "returned_lines": 0, "content": "", "truncated": False}
        hit_set = set(hit)
        keep = set()
        for i in hit:
            keep.update(range(max(0, i - ctx), min(total, i + ctx + 1)))
        out, prev, shown, truncated = [], None, 0, False
        for i in sorted(keep):
            if prev is not None and i > prev + 1:
                out.append("%s |" % ("...".rjust(width)))
            if shown >= cap:
                truncated = True
                break
            mark = ":" if i in hit_set else "-"
            out.append("%s %s %s" % (str(i + 1).rjust(width), mark, lines[i]))
            prev, shown = i, shown + 1
        return {"ok": True, "path": p, "mode": "grep", "total_lines": total,
                "matches": len(hit), "returned_lines": shown,
                "content": "\n".join(out), "truncated": truncated}

    def resolve(v, default):
        if v is None:
            return default
        try:
            v = int(v)
        except (TypeError, ValueError):
            return default
        return total + v + 1 if v < 0 else v

    start = max(1, resolve(start_line, 1))
    end = min(total, resolve(end_line, total))
    if start > end:
        return {"ok": True, "path": p, "mode": "range", "total_lines": total,
                "returned_lines": 0, "content": "", "truncated": False}
    seg = lines[start - 1:end]
    truncated = len(seg) > cap
    if truncated:
        seg = seg[:cap]
    content = "\n".join("%s | %s" % (str(start + i).rjust(width), t) for i, t in enumerate(seg))
    return {"ok": True, "path": p, "mode": "range", "total_lines": total,
            "start_line": start, "end_line": start + len(seg) - 1,
            "returned_lines": len(seg), "content": content, "truncated": truncated}
