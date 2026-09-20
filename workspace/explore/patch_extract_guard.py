# -*- coding: utf-8 -*-
"""修正版：先回滚备份，再用【全角引号】重新补护栏（半角引号会闭合 Python 字符串字面量）。
最后 py_compile 硬校验——上一次就是这一步抓到了 bug。
"""
import io
import pathlib
import py_compile
import shutil
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

P = pathlib.Path(r"F:\me\self-agent\core\memory_extract.py")
BAK = P.with_name(P.name + ".bak_20260920_weknora_guard")
assert BAK.exists(), "backup missing!"

shutil.copy2(BAK, P)
with open(P, "r", encoding="utf-8", newline="") as f:
    src = f.read()
nl = "\r\n" if "\r\n" in src else "\n"
print("restored from backup; line ending:", repr(nl))
print("contains old rule:", "绝不提炼密钥" in src)

old2 = '"2. 最多输出 8 条；没有值得记的就输出空数组。\\n"'
new2 = ('"2. 绝不提炼密钥/口令/token/私钥/连接串等敏感凭据的值'
        '（可记「存在某凭据」这一事实，不记凭据本身）。\\n"' + nl +
        '        "3. 不要把助手自身的职责、能力、人设当作使用者的事实来记。\\n"' + nl +
        '        "4. 最多输出 8 条；没有值得记的就输出空数组。\\n"')
old3 = '"3. 严格输出 JSON'
new3 = '"5. 严格输出 JSON'
assert old2 in src, "anchor2 missing"
assert old3 in src, "anchor3 missing"

src = src.replace(old2, new2).replace(old3, new3)
with open(P, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("patched")

try:
    py_compile.compile(str(P), doraise=True)
    print("py_compile: OK")
except Exception as e:
    print("py_compile FAILED:", e)
    raise SystemExit(1)

chk = P.read_text(encoding="utf-8")
i = chk.find("规则：")
print("---- 改后区间 ----")
print(chk[i:i + 520])
