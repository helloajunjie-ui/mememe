"""三个 FAIL 的根因定位：是历史残留，还是仍在持续产生？"""
import sqlite3, os, sys
sys.stdout.reconfigure(encoding="utf-8")

def find_db():
    p = os.path.dirname(os.path.abspath(__file__))
    while True:
        cand = os.path.join(p, "data", "memory.db")
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(p)
        if parent == p:
            raise SystemExit("memory.db not found")
        p = parent

DB = find_db()
print("DB:", DB)
c = sqlite3.connect(DB)
cur = c.cursor()

def show(title, sql, args=()):
    print(f"\n=== {title} ===")
    rows = list(cur.execute(sql, args))
    print(f"  rows: {len(rows)}")
    for r in rows[:20]:
        print("   ", r)

show("『尼可』短条目 created_at / archived",
     "select id, date(created_at,'unixepoch','localtime'), archived, substr(content,1,30) "
     "from memories where content like '%尼可%' and length(content)<20 order by created_at")

show("内部调度词入库（自主时间/内部·/自主探索）",
     "select id, date(created_at,'unixepoch','localtime'), type, category, archived, substr(content,1,40) "
     "from memories where content like '%自主时间%' or content like '%内部·%' or content like '%自主探索%' "
     "order by created_at")

print("\n=== 相对时间词 ===")
for w in ("下周", "明天", "后天", "这周", "本月", "昨天"):
    n = cur.execute("select count(*) from memories where content like ?", (f"%{w}%",)).fetchone()[0]
    print(f"  含『{w}』: {n}")

show("按 category 分布", "select category, count(*) from memories group by category order by 2 desc")
show("按 source 分布", "select source, count(*) from memories group by source order by 2 desc")
show("按 origin 分布", "select origin, count(*) from memories group by origin order by 2 desc")
c.close()
