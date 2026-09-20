# -*- coding: utf-8 -*-
"""精确验证：记忆库里是否存在【真实的密钥值】（不是"密钥住在哪"的元描述）。
正则只匹配典型密钥形态，避免把 base_url / 路径误判成泄漏。只读。
"""
import io
import re
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
c = sqlite3.connect(r"F:\me\self-agent\data\memory.db")

print("--- id 12 全文 ---")
row = c.execute("SELECT content FROM memories WHERE id=12").fetchone()
print((row[0] if row else "")[:800])

rows = c.execute("SELECT id, content FROM memories").fetchall()
rx = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_\-]{20,}"
    r"|eyJ[A-Za-z0-9_\-]{20,}"          # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
hits = [(i, rx.findall(t or "")) for i, t in rows if rx.search(t or "")]
print("REAL KEY VALUES in memory db:", len(hits))
for i, found in hits[:10]:
    print(" ", i, "->", [x[:12] + "..." for x in found])
