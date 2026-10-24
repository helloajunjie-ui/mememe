# -*- coding: utf-8 -*-
"""删除文件中残留的旧 _DESTRUCTIVE_REGEX 定义块（它在 _LOG_FILE 之后，覆盖了新版）。"""
from pathlib import Path

P = Path(r"F:\me\self-agent\tools\src\python\cmd_run.py")
s = P.read_text(encoding="utf-8")

start_marker = "# 2026-09-20 修复："
end_marker = "\n\ndef _classify("
a = s.index(start_marker)
b = s.index(end_marker)
dead = s[a:b]
assert "_DESTRUCTIVE_REGEX" in dead, "切错了：这段里没有旧正则定义"
s = s[:a] + s[b + 2:]  # 保留换行

assert s.count("_DESTRUCTIVE_REGEX = (") == 1, s.count("_DESTRUCTIVE_REGEX = (")
assert s.count("_DESTRUCTIVE_PATTERNS = [") == 1
P.write_text(s, encoding="utf-8")
print("removed", len(dead), "chars; now", len(s), "chars")
print("regex defs:", s.count("_DESTRUCTIVE_REGEX = ("))
