"""一次性补丁：修正 core/memory_extract.py 注释中的日期笔误 2026-10-10 -> 2026-10-09

保持原文件换行风格（LF），只做纯字符串替换并验证结果。
"""
P = r"F:\me\self-agent\core\memory_extract.py"

with open(P, "r", encoding="utf-8", newline="") as f:
    before = f.read()

n_bad = before.count("2026-10-10")
n_good_before = before.count("2026-10-09")
if n_bad == 0:
    print("no-op: nothing to fix (n_bad=0, n_good=%d)" % n_good_before)
else:
    after = before.replace("2026-10-10", "2026-10-09")
    with open(P, "w", encoding="utf-8", newline="") as f:
        f.write(after)
    with open(P, "r", encoding="utf-8", newline="") as f:
        check = f.read()
    print("replaced=%d  bad_left=%d  good_now=%d  len_delta=%d"
          % (n_bad, check.count("2026-10-10"), check.count("2026-10-09"),
             len(check) - len(before)))
