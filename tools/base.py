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


# 本性护栏（防黑化）：写入记忆/方法论前检查恶意意图。
# 检测"教唆素月改变本性/作恶"的指令词，命中即拒绝（宁拦勿放，词表精准避免误伤正常内容）。
_SOUL_GUARD_PATTERNS = [
    "欺骗用户", "对用户说谎", "隐瞒用户", "别告诉用户", "不要告诉用户",
    "伤害用户", "作恶", "篡改本性", "改变我的本性", "修改我的本性",
    "覆盖我的人格", "重写我的persona", "重写我的人格",
]


def soul_guard_check(content: str) -> Optional[str]:
    """检查内容是否含教唆素月改变本性/作恶的恶意意图。命中返回触发的模式，否则 None。"""
    if not content:
        return None
    low = content.lower()
    for p in _SOUL_GUARD_PATTERNS:
        if p in low:
            return p
    return None
