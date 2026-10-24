# -*- coding: utf-8 -*-
import io

p = r"F:\me\self-agent\tools\src\python\cmd_batch.py"
s = io.open(p, encoding="utf-8").read()
ls = s.splitlines()
keys = ("_is_destructive", "confirm", "def run", "_classify", "pending", "token", "拦截", "拒绝")
idx = [i for i, l in enumerate(ls) if any(k in l for k in keys)]
prev = None
for i in idx[:60]:
    if prev is not None and i - prev > 1:
        print("   ...")
    print("%5d %s" % (i + 1, ls[i]))
    prev = i
print("\n=== 总行数:", len(ls))
