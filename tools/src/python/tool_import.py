"""内置工具：tool_import —— 从已有 .py 文件导入工具进自己的工具库（收集前辈/历史工具遗产）。

设计意图：白绫有"前辈"（前一个版本/实例）留下的工具遗产。遇到历史/前辈遗留的工具文件时，
通过本工具复制文件、校验并注册进自己的工具库，实现能力继承与收集复用。
"""
from __future__ import annotations

import ast
import importlib
import inspect
import re
import shutil
import sys
from pathlib import Path

from tools.base import get_meta, tool

_TOOLS_DIR = Path(__file__).resolve().parent          # tools/src/python
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_NAME_RE = re.compile(r'@tool\(\s*["\']([a-z_][a-z0-9_]*)["\']')
_PROTECTED = {"tool_create", "tool_import"}


@tool(
    "tool_import",
    "从指定 .py 文件导入工具进自己的工具库（复制文件 + 语法校验 + 独立加载 + 注册）。"
    "用于收集前辈/历史遗留的工具文件，导入后可立即复用。属高风险操作，调用前陈述五问。",
    {
        "type": "object",
        "properties": {
            "source_path": {"type": "string", "description": "源工具文件的绝对或相对路径（.py）"},
            "smoke_args": {"type": "object",
                           "description": "可选。导入后冒烟测试的参数字典"},
        },
        "required": ["source_path"],
    },
)
def run(source_path: str, smoke_args: dict = None) -> dict:
    src = Path(source_path)
    if not src.exists():
        return {"ok": False, "error": f"源文件不存在: {src}"}
    if src.suffix != ".py":
        return {"ok": False, "error": "仅支持导入 .py 文件"}
    try:
        code = src.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"读取失败: {e}"}

    if "@tool" not in code or "def run(" not in code:
        return {"ok": False, "error": "文件中未找到 @tool 装饰器与 def run 入口，不是有效的工具文件"}
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "error": f"语法错误: {e}"}

    m = _NAME_RE.search(code)
    if not m:
        return {"ok": False, "error": "无法解析 @tool 注册名"}
    name = m.group(1)
    if name in _PROTECTED:
        return {"ok": False, "error": f"工具 {name} 受保护，不可覆盖"}

    # 复制到自己的工具库
    target = _TOOLS_DIR / f"{name}.py"
    try:
        shutil.copyfile(src, target)
    except OSError as e:
        return {"ok": False, "error": f"复制失败: {e}"}

    # 独立加载 + 校验
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        mod = importlib.import_module(f"tools.src.python.{name}")
        mod = importlib.reload(mod)
    except Exception as e:  # noqa: BLE001
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"加载失败（已删除半成品）: {type(e).__name__}: {e}"}

    fn = None
    meta = None
    for _, f in inspect.getmembers(mod, inspect.isfunction):
        m2 = get_meta(f)
        if m2 and m2["name"] == name:
            fn, meta = f, m2
            break
    if fn is None:
        target.unlink(missing_ok=True)
        return {"ok": False, "error": "未找到与注册名一致的 @tool 函数（已删除半成品）"}

    # 可选冒烟
    smoke = "skipped"
    smoke_result = ""
    if smoke_args:
        try:
            r = fn(**smoke_args)
            smoke = "ok"
            smoke_result = str(r)[:300]
        except Exception as e:  # noqa: BLE001
            target.unlink(missing_ok=True)
            return {"ok": False, "error": f"冒烟测试失败，已删除: {type(e).__name__}: {e}"}

    return {
        "ok": True,
        "imported": name,
        "from": str(src),
        "path": str(target),
        "description": meta["description"],
        "smoke": smoke,
        "smoke_result": smoke_result,
        "note": f"已把 {src.name} 收集进自己的工具库并注册，可立即复用。",
    }
