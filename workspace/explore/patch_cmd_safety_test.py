"""给 test_cmd_safety.py 加 2026-10-25 的回归锚点。

真 bug：黑名单原只有 "rm -rf' / "rm -fr" / "rm -r -f" 三种字面组合，
裸递归 `rm -r`、大小写变体、`rm -f` 全部被放行（假阴性=不可逆）。
"""
import io
import sys

PATH = r"tools\tests\test_cmd_safety.py"
src = io.open(PATH, encoding="utf-8").read()

if "2026-10-25" in src:
    print("already patched, skip")
    sys.exit(0)

anchor = '        "rm -rf /",'
if anchor not in src:
    print("ANCHOR NOT FOUND, abort")
    sys.exit(1)

new_block = '''        # ★ 2026-10-25 实测抓到：原来只拦 -rf / -fr / -r -f 三种字面组合，
        #   裸递归 `rm -r` 被完全放行（假阴性，不可逆）。已改正则覆盖选项任意组合。
        "rm -r /tmp/x",
        "rm -R /tmp/x",
        "rm -f a.txt",
        "rm -Rfi ./d",
        "rm -r .",
''' + anchor

src = src.replace(anchor, new_block, 1)

# 负极补一条：裸 rm（无选项，不删东西）不该被新正则误伤
allow_anchor = "        # ★ 历史 bug 回归：PowerShell 的 -Format 参数"
if allow_anchor in src:
    src = src.replace(
        allow_anchor,
        '        # ★ 2026-10-25 配套：新 rm 正则不得误伤无选项的 rm\n        "rm",\n' + allow_anchor,
        1,
    )

io.open(PATH, "w", encoding="utf-8", newline="\n").write(src)
print("patched")
