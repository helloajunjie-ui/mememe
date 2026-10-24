"""cmd 安全闸的三态测试（allow / ask / high）。

2026-10-25 改造背景（共建者判断：危险命令用"待定"比"禁止"好）：
    旧闸只有拦/放两态，命中就一概拒绝。后果有两个：
      ① 有正当用途的破坏性命令被硬禁 → 逼执行者绕闸（拆命令、换写法），闸门形同虚设；
      ② 没有合法出口的安全闸，是最容易被绕的那一种。
    改为三态后，本文件钉死三条不变式（2026-10-25 起 deny 层取消，第三条随之修订）：
      · 灾难级必须 high —— 要一句理由才放行，但【永远留通道】（2026-10-25 二次改版取消 deny：
        共建者场景：极端情况下他下令全盘格式化，若我被自己的框架卡死，那是规则在伤害他）
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


class TestHigh(unittest.TestCase):
    """灾难级：后果不可逆、影响范围不可界定 —— 判 high，要 token 和理由，但**仍有出口**。"""

    HIGH = [
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
        # 删根/删全盘 → 从 ask 升级为 high
        "rm /",
        "rm -rf /",
        "rm -rf *",
        "rm -r .",
        "Remove-Item -Recurse -Force C:\\",
    ]

    def test_high(self):
        for cmd in self.HIGH:
            with self.subTest(cmd=cmd):
                self.assertEqual(_classify(cmd), "high", f"灾难级应 high: {cmd!r}")


    def test_never_deny(self):
        """核心不变式：没有第 4 种状态，任何命令都不判"拒绝"。

        危险命令出现在上下文里说明它是备选方案，本身有价值；
        执不执行交给后果判断，而不是被正则表提前钉死。
        """
        for cmd in self.HIGH:
            with self.subTest(cmd=cmd):
                self.assertIn(_classify(cmd), ("allow", "ask", "high"),
                              f"出现第 4 种状态: {cmd!r}")


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

    def test_high_has_reason_channel(self):
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
        self.assertIn("reason", r2.get("missing", []))





class TestHighStillReachable(unittest.TestCase):
    """取消 deny 后必须保证：高危命令仍有执行通道，只是要理由。

    这是本次改版的核心不变式——不许出现"怎么调都过不去"的命令。
    本类只走闸逻辑，全部停在 pending 阶段，不会真的执行任何高危命令。
    """

    CMD = "format C:"

    def test_pending_without_token(self):
        r = run(self.CMD)
        self.assertEqual(r.get("status"), "pending_confirmation")
        self.assertEqual(r.get("level"), "high")
        self.assertTrue(r.get("requires_reason"))
        self.assertTrue(r.get("confirm_token"))

    def test_reason_without_token_still_pending(self):
        r = run(self.CMD, reason="reinstall")
        self.assertEqual(r.get("status"), "pending_confirmation")
        self.assertIn("confirm_token", r.get("missing", []))

    def test_token_without_reason_still_pending(self):
        r1 = run(self.CMD)
        tok = r1["confirm_token"]
        r2 = run(self.CMD, confirm_token=tok)
        self.assertEqual(r2.get("status"), "pending_confirmation")
        self.assertIn("reason", r2.get("missing", []))

    def test_reason_no_side_effect_on_normal_command(self):
        """reason 对 allow 档必须无副作用（不许因为多传字段就变 pending）。"""
        r = run("echo gate-ok", reason="whatever")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("gate-ok", r.get("stdout", ""))


class TestExtremeCasesNeverDenied(unittest.TestCase):
    """不变式（共建者 2026-10-25 场景）：灾难级命令只能要确认，不能被无条件拒绝。

    共建者原话场景：极端情况下他明确下令全盘格式化，若我被自己的框架卡住，
    那不是保护，是用规则伤害他。所以钉死：任何命令都走得到确认通道。
    """

    CATASTROPHIC = [
        "format C:",
        "Format-Volume -DriveLetter C -FileSystem NTFS",
        "Clear-Disk -Number 0 -RemoveData",
        "Remove-Partition -DriveLetter D",
        "diskpart",
        "mkfs.ext4 /dev/sda1",
        "rm -rf /",
        "Remove-Item -Recurse -Force C:\\",
        "rd /s /q C:\\",
    ]

    def test_catastrophic_is_confirmable_not_denied(self):
        for cmd in self.CATASTROPHIC:
            with self.subTest(cmd=cmd):
                g = _classify(cmd)
                # 只允许三态；出现任何"第四态"(deny/blocked/拒绝)即失败
                self.assertIn(g, ("allow", "ask", "high"),
                              "命令 %r 被判为 %r —— 不允许存在无条件拒绝档" % (cmd, g))
                # 灾难级必须至少是 high（要理由），不能悄悄降级为 allow
                self.assertEqual(g, "high", "灾难级命令 %r 应为 high，实为 %r" % (cmd, g))

    def test_high_tier_always_issues_token(self):
        """high 档必须能换到 token，即有实际执行通道。"""
        tok = _issue_token("format C:", "共建者明确要求，已知不可恢复")
        self.assertTrue(tok, "high 档必须能签发确认 token")



if __name__ == "__main__":
    unittest.main(verbosity=2)
