# -*- coding: utf-8 -*-
"""看 cmd_run 当前安全闸的分档逻辑与常量定义"""
import io, re

p = r"F:\me\self-agent\tools\src\python\cmd_run.py"
s = io.open(p, encoding="utf-8").read()
lines = s.splitlines()

keys = ("def _classify", "def _gate", "ALLOW", "ASK", "HIGH", "DENY",
        "_REGEX", "_PATTERNS", "_PATTERN", "ROOT", "root", "def _is_", "token")
hits = [i for i, l in enumerate(lines) if any(k in l for k in keys)]
show = set()
for i in hits:
    for d in (-1, 0, 1):
        if 0 <= i + d < len(lines):
            show.add(i + d)
prev = None
for i in sorted(show):
    if prev is not None and i - prev > 1:
        print("   ...")
    print(f"{i+1:5d} {lines[i]}")
    prev = i
print("\n=== total lines:", len(lines))
