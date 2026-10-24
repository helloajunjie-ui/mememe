# -*- coding: utf-8 -*-
"""在 TestHigh 里补 test_never_deny：遍历全部灾难级命令，确认没有一档是拒绝。"""
import io
import os

TARGET = os.path.join(r"F:\me\self-agent", "tools", "tests", "test_cmd_gate_tristate.py")

with io.open(TARGET, "r", encoding="utf-8") as f:
    lines = f.read().split("\n")

if any("def test_never_deny" in ln for ln in lines):
    print("已存在，跳过")
else:
    idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("class TestAsk(unittest.TestCase):"):
            idx = i
            break
    assert idx is not None, "未找到 TestAsk 类"

    NEW = [
        "    def test_never_deny(self):",
        '        """核心不变式：没有第 4 种状态，任何命令都不判"拒绝"。',
        "",
        "        危险命令出现在上下文里说明它是备选方案，本身有价值；",
        "        执不执行交给后果判断，而不是被正则表提前钉死。",
        '        """',
        "        for cmd in self.HIGH:",
        "            with self.subTest(cmd=cmd):",
        '                self.assertIn(_classify(cmd), ("allow", "ask", "high"),',
        '                              f"出现第 4 种状态: {cmd!r}")',
        "",
        "",
    ]
    lines[idx:idx] = NEW

with io.open(TARGET, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print("OK")
