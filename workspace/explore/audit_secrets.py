# -*- coding: utf-8 -*-
"""对照 WeKnora evalset.json 明列的记忆失败模式，审计我自己的记忆库。
失败模式：存一次性问题 / 存助手自己的职责 / 存密钥 / 已完成任务不删。
本脚本先查最要命的一条：密钥是否落库。只读，不改任何数据。
"""
import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DB = r"F:\me\self-agent\data\memory.db"
c = sqlite3.connect(DB)

cols = [r[1] for r in c.execute("PRAGMA table_info(memories)")]
rows = c.execute("SELECT id, content FROM memories").fetchall()
print("schema:", cols)
print("total rows:", len(rows))

pats = ["sk-", "password", "passwd", "api_key", "api key", "api-key",
        "密码", "私钥", "secret", "token=", "key=", "bearer ", "ghp_", "AKIA"]
hits = []
for i, t in rows:
    low = (t or "").lower()
    if any(p.lower() in low for p in pats):
        hits.append((i, t or ""))

print("secret-pattern hits:", len(hits))
for i, t in hits[:25]:
    print(" ", i, "|", t[:100].replace("\n", " "))

# 助手职责类：把"我是谁/我的岗位"当用户事实存
role_pats = ["作为ai", "作为 ai", "你是一个", "我是一个ai", "我是一个 ai", "assistant"]
rhits = [(i, t or "") for i, t in rows if any(p in (t or "").lower() for p in role_pats)]
print("assistant-role-pattern hits:", len(rhits))
for i, t in rhits[:10]:
    print(" ", i, "|", t[:100].replace("\n", " "))
