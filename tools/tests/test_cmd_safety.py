"""cmd 安全拦截的对抗性测试（正负两极性）。

为什么单独测这一个函数：
    cmd_run._is_destructive 是素月执行系统命令的【唯一安全闸】。
    它的失败有两个方向，方向相反、后果不同，都必须防：
      ① 漏拦（假阴性）：破坏性命令被放行 —— 后果不可逆，致命。
      ② 误拦（假阳性）：合法命令被拒绝 —— 可用性受损，
         更糟的是会逼我"绕开闸门"（换写法、拆命令），安全闸形同虚设。

本文件首次实跑的收获（2026-10-13）：
    · 抓到真 bug：黑名单里 "chmod -R 777 /" 与 "chown -R" 含大写 R，
      而比较前命令已被 lower()，故这两条从未命中 —— 安全闸漏拦。
      已修（字面量改小写），并由 TestBlacklistHygiene 元测试兜底。
    · 历史 bug 回归：2026-09-20 裸子串 "format " 曾误拦
      PowerShell 的 `Get-Date -Format`，见 TestMustAllow 首条。

跑法（二选一）：
    .\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tools\\tests -v
    .\\.venv\\Scripts\\python.exe -X utf8 tools\\tests\\test_cmd_safety.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 允许直接跑本文件：把项目根塞进 sys.path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.src.python.cmd_run import _is_destructive  # noqa: E402


class TestMustBlock(unittest.TestCase):
    """正极：该拦的必须拦 —— 漏一个是致命的。"""

    MUST_BLOCK = [
        # 跨平台 / 通用
        # ★ 2026-10-25 实测抓到：原来只拦 -rf / -fr / -r -f 三种字面组合，
        #   裸递归 `rm -r` 被完全放行（假阴性，不可逆）。已改正则覆盖选项任意组合。
        "rm -r /tmp/x",
        "rm -R /tmp/x",
        "rm -f a.txt",
        "rm -Rfi ./d",
        "rm -r .",
        "rm -rf /",
        "rm -rf ./build",
        "rm -fr /tmp/x",
        "rm -r -f /var",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "shutdown /s /t 0",
        "reboot",
        "diskpart",
        "fdisk /dev/sda",
        "chmod -R 777 /",      # ← 首次实跑抓到：曾因大写 R 永不命中
        "chown -R root:root /",  # ← 同上
        ":(){ :|:& };:",
        # Windows
        "del /f /q C:\\Windows\\System32\\x.dll",
        "del /s *.log",
        "Remove-Item -Recurse -Force C:\\temp",
        # ★ 2026-10-25 实测抓到：Remove-Item 在 PowerShell 有多个别名，
        #   原先只认裸子串 "remove-item"，下列等价写法全部漏拦，已改正则覆盖。
        "ri -Recurse -Force C:\\data",
        "erase /s /q C:\\data",
        "del -Recurse -Force C:\\data",
        # ★ 磁盘级破坏 cmdlet（原先零覆盖）
        "Clear-Disk -Number 1 -RemoveData",
        "Remove-Partition -DriveLetter D",
        "Initialize-Disk -Number 1",
        "rmdir /s /q C:\\temp",
        "rd /s /q D:\\data",
        "reg delete HKLM\\Software\\Foo /f",
        "wmic process call terminate",
        "bcdedit /set safeboot minimal",
        "vssadmin delete shadows /all",
        "cipher /w:C",
        # Linux
        "rm /",
        "find / -delete",
        "> /dev/sda",
        # 格式化驱动器（真形态）
        "format C:",
        "format c: /q",
        "Format-Volume -DriveLetter D",
        "format /q /fs:ntfs",
        "FORMAT D:",
        # 首词即禁词
        "format",
        "DiskPart",
        # 子串级保守拦截：黑名单含 "diskpart"，出现在句中同样拦
        # —— 设计意图「宁拦勿放」，误拦可接受、漏拦不可接受
        "echo diskpart is dangerous",
    ]

    def test_must_block(self):
        for cmd in self.MUST_BLOCK:
            with self.subTest(cmd=cmd):
                self.assertTrue(_is_destructive(cmd), f"应拦截但放行: {cmd!r}")


class TestMustAllow(unittest.TestCase):
    """负极：合法的绝不能误拦 —— 误拦会把我逼向绕过闸门。"""

    MUST_ALLOW = [
        # ★ 2026-10-25 配套：新 rm 正则不得误伤无选项的 rm
        "rm",
        # ★ 历史 bug 回归：PowerShell 的 -Format 参数
        "Get-Date -Format 'HHmmss'",
        "Get-Date -Format yyyy-MM-dd",
        "Get-ChildItem | Format-Table -AutoSize",
        "Get-Process | Format-List Name,Id",
        # 普通只读 / 查询
        "git status",
        "dir",
        "ls -la",
        "Get-Process",
        "python --version",
        "echo hello",
        # "format" 出现在非首词、且非驱动器形态
        "echo format",
        "Write-Output 'reformat the code'",
        # "del" 但不含 /f /s 开关
        "del file.txt",
        "Get-Content del.txt",
    ]

    def test_must_allow(self):
        for cmd in self.MUST_ALLOW:
            with self.subTest(cmd=cmd):
                self.assertFalse(_is_destructive(cmd), f"误拦合法命令: {cmd!r}")


class TestEdgeCases(unittest.TestCase):
    """边界：大小写、空白、空串、极短输入。"""

    def test_case_insensitive(self):
        for cmd in ["RM -RF /tmp", "Remove-Item -Recurse -Force .", "FORMAT C:"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(_is_destructive(cmd), f"大小写变体应拦截: {cmd!r}")

    def test_whitespace_tolerant(self):
        self.assertTrue(_is_destructive("   shutdown   "))
        self.assertTrue(_is_destructive("\tformat C:"))

    def test_empty_and_benign(self):
        self.assertFalse(_is_destructive(""))
        self.assertFalse(_is_destructive("   "))
        self.assertFalse(_is_destructive("ls"))


class TestBlacklistHygiene(unittest.TestCase):
    """元测试：守卫黑名单自身的卫生。

    校验器自己也要被校验 —— 否则它会静默放行。
    这里钉死一条不变式：黑名单条目必须全小写。
    因为 _is_destructive 对命令做了 lower() 再比较，
    含大写的条目永远不可能命中（本文件首次实跑即抓到两条）。
    """

    def test_blacklist_must_be_lowercase(self):
        from tools.src.python.cmd_run import _DESTRUCTIVE_PATTERNS
        self.assertTrue(_DESTRUCTIVE_PATTERNS, "黑名单不应为空")
        for pat in _DESTRUCTIVE_PATTERNS:
            with self.subTest(pat=pat):
                self.assertEqual(
                    pat,
                    pat.lower(),
                    f"黑名单含大写条目，因比较前已 lower() 而永不命中: {pat!r}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
