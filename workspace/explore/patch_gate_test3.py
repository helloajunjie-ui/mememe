# -*- coding: utf-8 -*-
"""反转最后一条测试：deny 无 token 通道 → high 有通道但必须带理由。"""
import io
import os

TARGET = os.path.join(r"F:\me\self-agent", "tools", "tests", "test_cmd_gate_tristate.py")

with io.open(TARGET, "r", encoding="utf-8") as f:
    lines = f.read().split("\n")

# 定位旧测试块：从 def test_deny_has_no_token_channel 到下一条空行之前
start = None
for i, ln in enumerate(lines):
    if "def test_deny_has_no_token_channel" in ln:
        start = i
        break
assert start is not None, "未找到旧测试"

end = start
while end < len(lines) and lines[end].strip() != "":
    end += 1
# 包含 def 行到最后一个非空断言行
old_block = "\n".join(lines[start:end])
assert "denied" in old_block and "confirm_token" in old_block, "块内容不符：" + old_block

NEW = '''    def test_high_has_reason_channel(self):
        """原 test_deny_has_no_token_channel 的反转（本次改版核心）。

        灾难级不再封死——必须提供执行通道，因为封死只会被绕过；
        但严禁只带 token 就过，理由必须是硬条件。
        """
        CMD = "Clear-Disk -Number 1 -RemoveData"
        r = run(CMD)
        self.assertEqual(r.get("status"), "pending_confirmation")
        self.assertEqual(r.get("level"), "high")
        self.assertIn("confirm_token", r, "灾难级必须提供执行通道（取消 deny 的核心）")
        self.assertTrue(r.get("requires_reason"), "但必须要求理由")

        r2 = run(CMD, confirm_token=r["confirm_token"])
        self.assertEqual(r2.get("status"), "pending_confirmation",
                         "只带 token 不带理由，必须依旧拦住")
        self.assertIn("reason", r2.get("missing", []))'''

lines[start:end] = NEW.split("\n")

with io.open(TARGET, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print("OK: 替换第", start, "行起，旧块行数", len(old_block.split("\n")))
