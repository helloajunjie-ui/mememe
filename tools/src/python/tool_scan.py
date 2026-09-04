"""内置工具：tool_scan —— 扫描目录，发现白绫格式的可用工具文件（@tool 装饰器 .py）。

设计意图：白绫能在当前环境（这台电脑）主动发现可用工具，然后选择直接使用，或 tool_import 复制进自己的工具库。
只做静态解析（正则提取元信息），不执行任何文件代码，安全。
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.base import tool

_TOOLS_DIR = Path(__file__).resolve().parent          # tools/src/python
_SKIP_PARTS = {".venv", "node_modules", ".git", "__pycache__", ".legacy_test"}

_NAME_RE = re.compile(r'@tool\(\s*["\']([a-z_][a-z0-9_]*)["\']')
_DESC_RE = re.compile(r'@tool\(\s*["\'][^"\']*["\']\s*,\s*["\']([^"\']{0,120})["\']')


def _in_library(name: str) -> bool:
    return (_TOOLS_DIR / f"{name}.py").exists()


@tool(
    "tool_scan",
    "扫描指定目录（默认项目根），发现白绫格式的可用工具文件（含 @tool 装饰器的 .py），"
    "返回工具清单（路径/名称/描述/是否已在库）。发现后可直接使用，或对未入库的用 tool_import 复制进自己的工具库。",
    {
        "type": "object",
        "properties": {
            "target_dir": {"type": "string",
                           "description": "要扫描的目录（可选，默认项目根目录，自动跳过 .venv/.git 等）"},
            "recursive": {"type": "boolean", "description": "是否递归子目录，默认 true"},
        },
        "required": [],
    },
)
def run(target_dir: str = "", recursive: bool = True) -> dict:
    base = Path(target_dir) if target_dir else Path.cwd()
    base = base.resolve()
    if not base.exists():
        return {"ok": False, "error": f"目录不存在: {base}"}
    if not base.is_dir():
        return {"ok": False, "error": f"不是目录: {base}"}

    files = []
    if recursive:
        for p in base.rglob("*.py"):
            if any(part in _SKIP_PARTS for part in p.parts):
                continue
            files.append(p)
    else:
        files = list(base.glob("*.py"))

    found = []
    for f in files:
        # 跳过临时文件（_ 开头）与框架文件（base/__init__）
        if f.name.startswith("_") or f.name in ("base.py", "__init__.py"):
            continue
        try:
            code = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "@tool" not in code:
            continue
        m = _NAME_RE.search(code)
        if not m:
            continue
        name = m.group(1)
        md = _DESC_RE.search(code)
        found.append({
            "path": str(f),
            "name": name,
            "description": (md.group(1) if md else "").strip()[:120],
            "in_my_library": _in_library(name),
        })

    found.sort(key=lambda x: x["name"])
    return {
        "ok": True,
        "scanned": str(base),
        "found": len(found),
        "tools": found,
    }
