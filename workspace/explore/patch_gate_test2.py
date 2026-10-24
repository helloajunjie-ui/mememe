# -*- coding: utf-8 -*-
"""用短锚点逐行更新测试文件（避开多行匹配的隐藏字符问题）。"""
import io
import os

ROOT = r"F:\me\self-agent"
TARGET = os.path.join(ROOT, "tools", "tests", "test_cmd_gate_tristate.py")

with io.open(TARGET, "r", encoding="utf-8") as f:
    lines = f.read().split("\n")

out = []
stat = {}


def cnt(tag):
    stat[tag] = stat.get(tag, 0) + 1


for ln in lines:
    s = ln

    # 类名
    if 'class TestDeny(unittest.TestCase):' in s:
        s = s.replace('class TestDeny(unittest.TestCase):', 'class TestHigh(unittest.TestCase):')
        cnt("class")

    # docstring 正文（避开三引号本身，只换中间文字）
    if '必须硬禁，不给 token。' in s:
        s = s.replace('必须硬禁，不给 token。', '判 high，要 token 和理由，但**仍有出口**。')
        cnt("doc")

    # 列表变量名
    if s.strip() == 'DENY = [':
        s = s.replace('DENY = [', 'HIGH = [')
        cnt("var")

    if 'self.DENY' in s:
        s = s.replace('self.DENY', 'self.HIGH')
        cnt("self")

    # 断言与测试方法
    if 'def test_deny(self):' in s:
        s = s.replace('def test_deny(self):', 'def test_high(self):')
        cnt("method")

    if 'self.assertEqual(_classify(cmd), "deny"' in s:
        s = s.replace('self.assertEqual(_classify(cmd), "deny"',
                      'self.assertEqual(_classify(cmd), "high"')
        cnt("assert")

    if 'f"灾难级应 deny: {cmd!r}"' in s:
        s = s.replace('f"灾难级应 deny: {cmd!r}"', 'f"灾难级应 high: {cmd!r}"')
        cnt("msg")

    if '从 ask 升级为 deny' in s:
        s = s.replace('从 ask 升级为 deny', '从 ask 升级为 high')
        cnt("comment")

    out.append(s)

print("替换统计:", stat)

need = {"class": 1, "doc": 1, "var": 1, "method": 1, "assert": 1, "msg": 1, "comment": 1}
for k, v in need.items():
    if stat.get(k, 0) != v:
        raise SystemExit("[ABORT] " + k + " 命中 " + str(stat.get(k, 0)) + " 次，预期 " + str(v))

src = "\n".join(out)

# 追加核心不变式测试类
if "class TestHighStillReachable" not in src:
    src = src.rstrip("\n") + '\n\n' + '''

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
'''

with io.open(TARGET, "w", encoding="utf-8", newline="\n") as f:
    f.write(src)

print("WRITE OK")
