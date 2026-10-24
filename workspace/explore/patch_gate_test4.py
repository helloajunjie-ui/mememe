# -*- coding: utf-8 -*-
"""把 if __name__ 入口块移到文件末尾，让后追加的测试类能被收集。"""
import io
import os

TARGET = os.path.join(r"F:\me\self-agent", "tools", "tests", "test_cmd_gate_tristate.py")

with io.open(TARGET, "r", encoding="utf-8") as f:
    lines = f.read().split("\n")

# 摘出入口块（连续的非空行，从 if __name__ 起到下一个空行为止）
start = None
for i, ln in enumerate(lines):
    if ln.startswith("if __name__"):
        start = i
        break
assert start is not None, "未找到入口块"

end = start
while end < len(lines) and lines[end].strip() != "":
    end += 1
entry = lines[start:end]
print("入口块行数:", len(entry))

rest = lines[:start] + lines[end:]
# 去掉尾部空行
while rest and rest[-1].strip() == "":
    rest.pop()

new_src = "\n".join(rest) + "\n\n\n" + "\n".join(entry) + "\n"

with io.open(TARGET, "w", encoding="utf-8", newline="\n") as f:
    f.write(new_src)

print("OK, 总行数", len(new_src.split("\n")))
