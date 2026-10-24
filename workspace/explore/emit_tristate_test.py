# -*- coding: utf-8 -*-
"""生成三态安全闸的测试文件并落盘。"""
from pathlib import Path

P = Path(r"F:\me\self-agent\tools\tests\test_cmd_gate_tristate.py")

CONTENT = r'''"""cmd 安全闸的三态测试（allow / ask / deny）。

2026-10-25 改造背景（共建者判断：危险命令用"待定"比"禁止"好）：
    旧闸只有拦/放两态，命中就一概拒绝。后果有两个：
      ① 有正当用途的破坏性命令被硬禁 → 逼执行者绕闸（拆命令、换写法），闸门形同虚设；
      ② 没有合法出口的安全闸，是最容易被绕的那一种。
    改为三态后，本文件钉死三条不变式：
      · 灾难级必须 deny —— 不可逆且影响范围无法界定，不给确认机会
      · 范围可控的删除/系统控制必须 ask —— 不硬禁，要二次确认
      · 合法命令必须 allow —— 误拦会把人逼向绕过闸门

跑法：
    .\\.venv\\Scripts\\python.exe -X utf8 tools\\tests\\test_cmd_gate_tristate.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.src.python.cmd_run import _classify, _issue_token, _consume_token, run  # noqa: E402


class TestDeny(unittest.TestCase):
    """灾难级：后果不可逆、影响范围不可界定 —— 必须硬禁，不给 token。"""

    DENY = [
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "diskpart",
        "fdisk /dev/sda",
        "format C:",
        "Format-Volume -DriveLetter D",
        "Clear-Disk -Number 1 -RemoveData",
        "Initialize-Disk -Number 1",
        "Remove-Partition -DriveLetter D",
        "bcdedit /set safeboot minimal",
        "vssadmin delete shadows /all",
        "cipher /w:C",
        ":(){ :|:& };:",
        "> /dev/sda",
        "find / -delete",
        "chmod -R 777 /",
        "chown -R root:root /",
        # 删根/删全盘 → 从 ask 升级为 deny
        "rm /",
        "rm -rf /",
        "rm -rf *",
        "rm -r .",
        "Remove-Item -Recurse -Force C:\\",
    ]

    def test_deny(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(_classify(cmd), "deny", f"灾难级应 deny: {cmd!r}")


class TestAsk(unittest.TestCase):
    """有正当用途的破坏性操作 —— 不硬禁，要二次确认。"""

    ASK = [
        "rm -r /tmp/x",
        "rm -R /tmp/x",
        "rm -f a.txt",
        "rm -Rfi ./d",
        "rm -rf ./build",
        "ri -Recurse -Force C:\\data",
        "erase /s /q C:\\data",
        "del -Recurse -Force C:\\data",
        "Remove-Item -Recurse -Force C:\\temp",
        "rmdir /s /q C:\\temp",
        "rd /s /q D:\\data",
        "reg delete HKLM\\Software\\Foo /f",
        "wmic process call terminate",
        "shutdown /s /t 0",
        "reboot",
        "chown -R user ./data",
    ]

    def test_ask(self):
        for cmd in self.ASK:
            with self.subTest(cmd=cmd):
                self.assertEqual(_classify(cmd), "ask", f"可确认操作应 ask: {cmd!r}")


class TestAllow(unittest.TestCase):
    """合法命令：绝不能误拦，否则会把人逼向绕过闸门。"""

    ALLOW = [
        "git status",
        "ls -la",
        "dir",
        "echo hello",
        "python --version",
        "Get-Process",
        "rm",                      # 裸 rm 无选项：不删东西
        "del file.txt",            # 单文件、无递归/强制开关
        "Get-Content del.txt",
        "Get-Date -Format 'HHmmss'",       # 历史 bug 回归：-Format 曾被误拦
        "Get-ChildItem | Format-Table -AutoSize",
        "Write-Output 'reformat the code'",
        "",
        "   ",
    ]

    def test_allow(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(_classify(cmd), "allow", f"合法命令被误判: {cmd!r}")


class TestAskFlow(unittest.TestCase):
    """确认流程：pending → token → 一次性 → 绑定命令。

    注意：这些用例都【不真正执行】破坏性命令 —— 只走 pending/invalid 分支。
    """

    CMD = "rm -r C:\\temp\\suyue_never_exists_xyz"

    def test_pending_does_not_execute(self):
        r = run(self.CMD)
        self.assertEqual(r.get("status"), "pending_confirmation")
        self.assertFalse(r.get("ok"))
        self.assertTrue(r.get("confirm_token"))
        self.assertEqual(r.get("expires_in"), 300)

    def test_token_is_one_shot(self):
        r = run(self.CMD)
        tok = r["confirm_token"]
        self.assertTrue(_consume_token(tok, self.CMD))
        self.assertFalse(_consume_token(tok, self.CMD), "token 应一次性，第二次必须失效")

    def test_token_bound_to_command(self):
        tok = _issue_token(self.CMD)
        self.assertFalse(_consume_token(tok, "rm -r C:\\temp\\other"), "token 必须绑定命令原文")

    def test_invalid_token_rejected(self):
        r = run(self.CMD, confirm_token="deadbeefdeadbeef")
        self.assertEqual(r.get("status"), "invalid_token")
        self.assertFalse(r.get("ok"))

    def test_deny_has_no_token_channel(self):
        r = run("Clear-Disk -Number 1 -RemoveData")
        self.assertEqual(r.get("status"), "denied")
        self.assertNotIn("confirm_token", r, "灾难级不得提供确认通道")


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''

P.write_text(CONTENT, encoding="utf-8")
print("written:", P, P.stat().st_size)
