# -*- coding: utf-8 -*-
"""把文档里的旧仓名 mememe 改为 suyueAI，保留原文件编码(BOM/无BOM)。"""
import pathlib

TARGETS = ["RECOVERY.md", "docs/README.md"]
PAIRS = [
    ("helloajunjie-ui/mememe", "helloajunjie-ui/suyueAI"),
    ("cd mememe", "cd suyueAI"),
    ("mememe/", "suyueAI/"),
]

for t in TARGETS:
    p = pathlib.Path(t)
    raw = p.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    enc = "utf-8-sig" if has_bom else "utf-8"
    txt = raw.decode(enc)
    before = txt.count("mememe")
    if before == 0:
        print(f"{t}: 无旧仓名，跳过")
        continue
    for old, new in PAIRS:
        txt = txt.replace(old, new)
    p.write_bytes(txt.encode(enc))
    print(f"{t}: 编码={enc} BOM={has_bom} 替换前={before} 替换后={txt.count('mememe')}")
