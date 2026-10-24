"""最小补丁：cmd_run 黑名单补 rm 递归/强制删除变体。

背景（2026-10-25 实测）：
  'rm -r /tmp/x'  -> False   # 裸递归删除被放行（真漏网）
  'rm -rf /tmp/x' -> True
  'rm -r ./dir'   -> False   # 同上
原黑名单只有 "rm -rf" / "rm -fr" / "rm -r -f" 三种字面组合，
`rm -r`、`rm -R`、`rm -f` 全部漏掉。改为正则覆盖选项任意组合。
"""
import io
import sys

PATH = r"tools\src\python\cmd_run.py"

src = io.open(PATH, encoding="utf-8").read()
lines = src.split("\n")

if re_marker := [l for l in lines if "brm" in l and "re.compile" in l]:
    print("already patched, skip")
    sys.exit(0)

# 定位 _DESTRUCTIVE_REGEX 元组内第一行（format C: 那条）
idx = None
for i, l in enumerate(lines):
    if "re.compile" in l and "a-z]:" in l and "format" in l:
        idx = i
        break

if idx is None:
    print("ANCHOR NOT FOUND, abort")
    sys.exit(1)

new_line = (
    '    re.compile(r"\\brm\\s+-[a-z]*(?:r|f)[a-z]*"),   '
    '# rm -r / rm -f / rm -Rf —— 递归或强制删除（2026-10-25 补：原仅拦 -rf 字面组合，裸 -r 被放行）'
)
lines.insert(idx, new_line)
io.open(PATH, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print(f"patched at line {idx + 1}")
