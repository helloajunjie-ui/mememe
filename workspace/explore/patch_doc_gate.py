# -*- coding: utf-8 -*-
"""同步 docs/设计文档.md 里关于 cmd_run 安全策略的过时描述（拦截 → 分级确认）。"""
import io
import os

TARGET = os.path.join(r"F:\me\self-agent", "docs", "设计文档.md")

with io.open(TARGET, "r", encoding="utf-8") as f:
    lines = f.read().split("\n")

hits = {}


def find_one(pred, label):
    found = [i for i, ln in enumerate(lines) if pred(ln)]
    assert len(found) == 1, label + " 命中 " + str(len(found)) + " 处"
    hits[label] = found[0]
    return found[0]


# ---- 1) tool description 行 ----
i = find_one(lambda ln: ln.startswith('description: "执行系统命令（平台自适应）。只读/查询命令放行；破坏性命令被安全策略拦截。"'),
             "desc")
lines[i] = ('description: "执行系统命令（平台自适应）。只读/查询命令放行；危险命令分级确认：'
            '可逆改动需 confirm_token，严重不可逆操作需 confirm_token + reason（理由入审计），无硬禁档。"')

# ---- 2) 安全策略段（单行 → 多行块） ----
i = find_one(lambda ln: ln.startswith("- **只读/查询命令放行**；破坏性命令（"), "policy")
BLOCK = [
    '- **只读/查询命令放行（`allow`）**；危险命令按后果分两档放行，**没有「硬禁」档**：',
    '  - `ask` 档：范围可控但可能造成数据损失的（递归/强制删除、移动覆盖、`git reset --hard`、`pip uninstall` 等）→ 返回 `pending_confirmation` + 一次性 `confirm_token`（5 分钟内有效、绑定命令原文、用后作废）。',
    '  - `high` 档：不可逆且影响范围难以界定的（`format` / `diskpart clean` / `dd of=/dev/sdX` / `mkfs` / `shutdown` / `reg delete` 等）→ 除 `confirm_token` 外**还必须给出非空 `reason`**，理由写入审计日志。',
    '  - 闸门分类函数 `_classify` 只返回 `allow` / `ask` / `high` 三种，测试 `test_never_deny` 钉死这一不变式。',
    '- **设计理由（2026-10-25 共建者判断）**：一条危险命令出现在上下文里，说明它是当前场景的备选方案，本身有信息价值；执不执行该由后果判断决定，不该被一张正则表提前钉死。硬禁的实际后果是被绕过——同一轮里命令中残留的 `rm -rf` 字样让 `git commit` 被拦，改走 `git commit -F <文件>` 绕开，绕开之后连审计记录都没有了。**有出口的闸才有人守**。',
]
lines[i:i + 1] = BLOCK

# ---- 3) 威胁模型里的黑名单描述 ----
i = find_one(lambda ln: ln.startswith("- cmd_run 破坏性命令黑名单拦截（删除/格式化/关机等，宁拦勿放）"), "threat")
lines[i] = ('- cmd_run 危险命令分级确认（`allow` / `ask` / `high` 三档，无硬禁档；`high` 必须附理由）'
            '+ 全命令审计日志 logs/cmd.log。')

# ---- 4) 工具表里的简短描述 ----
i = find_one(lambda ln: "破坏性命令拦截+审计" in ln, "table")
lines[i] = lines[i].replace("破坏性命令拦截+审计", "危险命令分级确认+审计（ask/high，无硬禁档）")

with io.open(TARGET, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print("文档更新 OK，命中行:", hits)
