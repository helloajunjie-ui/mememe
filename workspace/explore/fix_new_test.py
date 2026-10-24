# -*- coding: utf-8 -*-
"""修正新测试的调用方式 + 同步文件头文档（deny 已取消）"""
import io

p = r"F:\me\self-agent\tools\tests\test_cmd_gate_tristate.py"
s = io.open(p, encoding="utf-8").read()
orig = s

# ① 测试体：模块名 → 直接函数名（本文件是 from ... import _classify, _issue_token）
s = s.replace("cmd_run._classify(", "_classify(")
s = s.replace("cmd_run._issue_token(", "_issue_token(")

# ② 文件头文档与实现同步：deny 已取消，改为 allow/ask/high
s = s.replace(
    '"""cmd 安全闸的三态测试（allow / ask / deny）。',
    '"""cmd 安全闸的三态测试（allow / ask / high）。',
)
s = s.replace(
    "      · 灾难级必须 deny —— 不可逆且影响范围无法界定，不给确认机会",
    "      · 灾难级必须 high —— 要一句理由才放行，但【永远留通道】（2026-10-25 二次改版取消 deny：\n"
    "        共建者场景：极端情况下他下令全盘格式化，若我被自己的框架卡死，那是规则在伤害他）",
)
s = s.replace(
    "    改为三态后，本文件钉死三条不变式：",
    "    改为三态后，本文件钉死三条不变式（2026-10-25 起 deny 层取消，第三条随之修订）：",
)

changed = s != orig
io.open(p, "w", encoding="utf-8").write(s)
print("=== 文件已更新:", changed)
print("=== 残留 cmd_run. 引用:", s.count("cmd_run."))
for i, l in enumerate(s.splitlines()[:14], 1):
    print("%4d %s" % (i, l))
