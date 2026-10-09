"""小步验证：core/memory_extract.py 的喂料口过滤（2026-10-09 未提交改动）

验证两件事：
  1) 语法可编译（不引入 import 期错误）
  2) _is_extractable_turn() 行为符合预期：只放行 user/assistant 真实对话，
     剔除 system 与内部调度消息
输出全 ASCII，避开 Windows GBK 控制台编码坑。
"""
import sys
import py_compile

ROOT = r"F:\me\self-agent"
sys.path.insert(0, ROOT)

py_compile.compile(ROOT + r"\core\memory_extract.py", doraise=True)
print("COMPILE OK")

from core.memory_extract import _is_extractable_turn as f  # noqa: E402

INTERNAL_TAG = "\uff08\u5185\u90e8\u00b7"          # （内部·
EXPLORE_MSG = "\u8bf7\u81ea\u7531\u63a2\u7d22\u4e86\u89e3\u4e16\u754c"  # 请自由探索了解世界

cases = [
    ({"role": "user", "content": "hello"}, True),
    ({"role": "assistant", "content": "ok"}, True),
    ({"role": "system", "content": "sys prompt"}, False),
    ({"role": "USER", "content": "case-insensitive"}, True),
    ({"role": "user", "content": INTERNAL_TAG + "hourly reflection)"}, False),
    ({"role": "assistant", "content": EXPLORE_MSG}, False),
    ({"role": None, "content": "x"}, False),
    ({}, False),
]

all_pass = True
for i, (msg, exp) in enumerate(cases, 1):
    got = bool(f(msg))
    ok = got == exp
    all_pass = all_pass and ok
    print("case%-2d got=%-5s exp=%-5s %s" % (i, got, exp, "PASS" if ok else "FAIL"))

print("ALL PASS" if all_pass else "SOME FAIL")
sys.exit(0 if all_pass else 1)
