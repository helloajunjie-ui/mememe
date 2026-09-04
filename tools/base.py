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
         language: str = "python"):
    """工具注册装饰器：把普通函数标记为白绫工具。"""

    def decorator(fn: Callable) -> Callable:
        setattr(fn, META_ATTR, {
            "name": name,
            "description": description,
            "parameters": parameters,
            "deps": deps or [],
            "language": language,
        })
        return fn

    return decorator


def get_meta(fn: Callable) -> Optional[Dict]:
    return getattr(fn, META_ATTR, None)
