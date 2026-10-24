# -*- coding: utf-8 -*-
"""给 test_cmd_gate_tristate.py 追加一个不变式测试：
   任何灾难级命令都必须有走完确认流程的路径，永不被无条件拒绝。
"""
import io

p = r"F:\me\self-agent\tools\tests\test_cmd_gate_tristate.py"
s = io.open(p, encoding="utf-8").read()

MARK = "class TestExtremeCasesNeverDenied"
if MARK in s:
    print("已存在，跳过插入")
else:
    block = '''

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
        "Remove-Item -Recurse -Force C:\\\\",
        "rd /s /q C:\\\\",
    ]

    def test_catastrophic_is_confirmable_not_denied(self):
        for cmd in self.CATASTROPHIC:
            with self.subTest(cmd=cmd):
                g = cmd_run._classify(cmd)
                # 只允许三态；出现任何"第四态"(deny/blocked/拒绝)即失败
                self.assertIn(g, ("allow", "ask", "high"),
                              "命令 %r 被判为 %r —— 不允许存在无条件拒绝档" % (cmd, g))
                # 灾难级必须至少是 high（要理由），不能悄悄降级为 allow
                self.assertEqual(g, "high", "灾难级命令 %r 应为 high，实为 %r" % (cmd, g))

    def test_high_tier_always_issues_token(self):
        """high 档必须能换到 token，即有实际执行通道。"""
        tok = cmd_run._issue_token("format C:", "共建者明确要求，已知不可恢复")
        self.assertTrue(tok, "high 档必须能签发确认 token")


'''
    # 插入到 __main__ 块之前；没有则追加到末尾
    main_idx = s.find('if __name__ == "__main__":')
    if main_idx == -1:
        s = s + block + '\nif __name__ == "__main__":\n    unittest.main()\n'
        print("无 __main__ 块 → 已追加块 + 新建入口在末尾")
    else:
        s = s[:main_idx] + block.lstrip("\n") + "\n" + s[main_idx:]
        print("已插入到 __main__ 块之前")
    io.open(p, "w", encoding="utf-8").write(s)

# 自查：入口块必须位于文件最末（上次的坑：测试写在入口后不执行）
lines = io.open(p, encoding="utf-8").read().splitlines()
main_lines = [i for i, l in enumerate(lines) if l.startswith('if __name__ == "__main__":')]
classes = [i for i, l in enumerate(lines) if l.startswith("class ")]
print("=== 总行数:", len(lines))
print("=== 测试类数量:", len(classes), "-> 位置:", [i + 1 for i in classes])
print("=== __main__ 块位置:", [i + 1 for i in main_lines], "(应为文件末两行)")
if main_lines and classes:
    after = [c for c in classes if c > main_lines[0]]
    print("=== __main__ 之后的测试类(应为空):", after)
