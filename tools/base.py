"""工具基类与注册装饰器。

用法：
    from tools.base import tool

    @tool("net_fetch", "抓取 URL 正文", {...})
    def run(url, timeout=15):
        ...
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

META_ATTR = "_bailing_tool_meta"


def tool(name: str, description: str, parameters: Dict, deps: Optional[list] = None,
         language: str = "python", group: str = ""):
    """工具注册装饰器：把普通函数标记为素月工具。

    group: 功能域（工具目录/检索里的归类）。新工具请显式声明，如 group="网络"；
           不声明则按 core/registry.py 的前缀规则兜底，落进"其他"=未归类，
           tool_health_audit 会告警。功能域词表见 docs/工具归档规范.md。
    """

    def decorator(fn: Callable) -> Callable:
        setattr(fn, META_ATTR, {
            "name": name,
            "description": description,
            "parameters": parameters,
            "deps": deps or [],
            "language": language,
            "group": group or "",
        })
        return fn

    return decorator


def get_meta(fn: Callable) -> Optional[Dict]:
    return getattr(fn, META_ATTR, None)


# 2026-09-24 移除 soul_guard 词表检测层（共建者决定）。
# 原实现是 13 个中文短语的子串匹配：实测 12 条样本仅命中 3 条，且命中的全是
# 原词照抄；近义改写、词间插空格、中英转换一律放过。它拦不住有决心的输入，
# 只拦手滑，而"通过检查"还曾被下游误读成"内容清白"——故整层删除。
# 仍留下的：结构性闸门（破坏性命令拦截 / 发布 confirm / 完整性监控）、
# 记忆与日志可被共建者随时查阅（可查 > 自证）、以及 persona.yaml 中
# 的人格基座文本（那是准则本身，不是检测机制，不在此列）。
