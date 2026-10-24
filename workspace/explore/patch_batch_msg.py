# -*- coding: utf-8 -*-
"""cmd_batch：把"安全策略拦截：破坏性命令"改为"请改走 cmd_run 单独确认"。

理由：批量模式确实没法逐条弹确认，所以跳过是对的；
但不该说成"被拦截/禁止"——那会被读成此路不通。正确信息是"这个通道不支持确认，换个通道"。
"""
import io
import os

ROOT = r"F:\me\self-agent"
TARGET = os.path.join(ROOT, "tools", "src", "python", "cmd_batch.py")

with io.open(TARGET, "r", encoding="utf-8") as f:
    src = f.read()

pairs = []

pairs.append((
    '"破坏性命令仍会被安全策略拦截。返回每条命令的紧凑结果摘要，失败项单独列出。"',
    '"需确认的破坏性命令会被跳过，并提示改用 cmd_run 单独确认（批量模式无法逐条确认）。"\n'
    '    "返回每条命令的紧凑结果摘要，失败项单独列出。"',
))

pairs.append((
    '''            _audit(cmd, {"ok": False, "stderr": "被安全策略拦截"})
            results.append({"idx": i, "desc": desc, "ok": False, "error": "安全策略拦截：破坏性命令"})''',
    '''            # 批量通道没有确认交互，所以跳过；但明确告诉调用方"换哪个通道能过"。
            _audit(cmd, {"ok": False, "stderr": "批量通道跳过：需单独确认"})
            results.append({
                "idx": i, "desc": desc, "ok": False,
                "skipped": True,
                "error": ("批量模式不执行需确认的命令（本通道无逐条确认交互）。"
                          "改用 cmd_run 单独调用这条命令：先拿 pending_confirmation，"
                          "再把命令与后果告知用户，带 confirm_token（high 档还需 reason）重新调用。"),
            })''',
))

applied = 0
for old, new in pairs:
    if old in src:
        src = src.replace(old, new, 1)
        applied += 1
        print("[ OK ]", old.split("\n")[0][:50])
    else:
        print("[MISS]", old.split("\n")[0][:50])

assert applied == len(pairs), "有替换未命中，未写回"
with io.open(TARGET, "w", encoding="utf-8", newline="\n") as f:
    f.write(src)
print("WRITE OK, applied =", applied)
