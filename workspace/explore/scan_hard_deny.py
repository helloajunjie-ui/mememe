# -*- coding: utf-8 -*-
"""扫全库：还有哪些地方存在"无条件拒绝"式的自我封死"""
import io, os, re

roots = [r"F:\me\self-agent\core", r"F:\me\self-agent\tools\src\python"]
# 硬拒绝语义（排除注释里的说明性文字由人工看）
pats = [
    ("拒绝执行", re.compile(r"拒绝执行")),
    ("不允许", re.compile(r"不允许")),
    ("禁止执行", re.compile(r"禁止执行")),
    ("blocked/forbidden", re.compile(r"blocked|forbidden|not_allowed|NotAllowed", re.I)),
    ("PermissionError raise", re.compile(r"raise\s+(PermissionError|RuntimeError)\(.*(拒|禁|不允许|block)", re.I)),
]

hits = []
for root in roots:
    for dirpath, dirnames, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                lines = io.open(fp, encoding="utf-8", errors="ignore").read().splitlines()
            except Exception:
                continue
            for i, l in enumerate(lines, 1):
                st = l.strip()
                if st.startswith("#"):
                    continue
                for name, rx in pats:
                    if rx.search(l):
                        hits.append((fp.replace(r"F:\me\self-agent\\", ""), i, name, st[:100]))
                        break

print("=== 硬拒绝语义命中（已排除纯注释行）: %d 处" % len(hits))
for fp, i, name, l in hits:
    print("%-52s %5d [%s] %s" % (fp, i, name, l))

print()
print("=== cmd_batch 是否对每条命令过安全闸 ===")
bp = r"F:\me\self-agent\tools\src\python\cmd_batch.py"
if os.path.exists(bp):
    src = io.open(bp, encoding="utf-8").read()
    for kw in ["_classify", "_is_destructive", "cmd_run", "run_shell", "_gate", "confirm"]:
        print("   %-16s -> %s" % (kw, "有" if kw in src else "无"))
else:
    print("   cmd_batch.py 不存在")
