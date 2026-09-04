"""内置工具：tool_create —— 创建新工具（工具自举核心，支持 Python / Go）。

设计意图（见设计文档 4.3 / 5.4）：
- 当现有工具无法完成当前任务时，白绫可自行编写工具并注册。
- Python：写入 tools/src/python/ → 独立加载 → 注册（动态加载）。
- Go：写入 tools/src/go/ → go build 编译 → 冒烟 → 注册（go_binary，子进程 + stdin/stdout JSON）。
- 注册后立即进入工具注册表，后续任务可重复复用。
- 属高风险操作（写文件 + 编译 + 注册新工具），调用前须陈述五问。
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tools.base import get_meta, tool
from core.registry import normalize_schema

_PY_DIR = Path(__file__).resolve().parent           # tools/src/python
_GO_DIR = _PY_DIR.parent / "go"                     # tools/src/go
_BIN_DIR = _PY_DIR.parent.parent / "bin"            # tools/bin（Go 二进制统一目录）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # 项目根

# 禁止覆盖的工具（自举断点）
_PROTECTED = {"tool_create"}

_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# @schema 声明为单行 JSON 注释：贪婪匹配该行从 { 到最后一个 }（支持嵌套对象）
_SCHEMA_RE = re.compile(r"@schema\s+(\{.*\})")
_DESC_RE = re.compile(r"@desc\s+(\S.*?)$", re.MULTILINE)


# ================= Python 工具路径 =================

def _load_py_module(name: str):
    """强制独立加载 Python 模块（绕过 import 缓存）。"""
    sys.path.insert(0, str(_PROJECT_ROOT))
    mod = importlib.import_module(f"tools.src.python.{name}")
    return importlib.reload(mod)


# ============ 工具准入三关（先测可用，再进名单） ============

def _sample_value(v: dict):
    """从 schema 属性定义生成一个示例值（用于无 test_args 时的实弹冒烟）。"""
    t = v.get("type", "string")
    if t == "string":
        return "示例文本"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    return ""


def _admit_python(fn, meta, test_args) -> tuple:
    """工具准入三关（Python）。任一关不过 → 拒绝注册。

    返回 (ok, error, normalized_schema, smoke, smoke_result)
    关1 契约关：schema 规范化后必须是标准 JSON Schema（type:object + properties）。
    关2 一致性关：schema 声明的参数能被 run() 接收；run() 的必填参数在 schema 中可见（防接口/实现脱节）。
    关3 实弹关：用 test_args 或 schema 生成的示例参数真实调用，验证能执行、返回合法。
    """
    # 关1 契约关
    schema = normalize_schema(meta["parameters"])
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return (False, "参数 schema 必须是标准 JSON Schema（顶层 type:object + properties）", None, "", "")
    props = schema["properties"]
    # 关2 一致性关
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return (False, "无法解析函数签名（run 不是函数）", None, "", "")
    params = sig.parameters
    has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    for k in props:
        if k not in params and not has_kwargs:
            return (False, f"schema 声明参数 '{k}' 但函数 run() 不接受（签名: {list(params)}）", None, "", "")
    required = set(schema.get("required", []))
    for pname, p in params.items():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty and pname not in required:
            return (False, f"函数必填参数 '{pname}' 未在 schema required 中声明（LLM 不会传，调用必炸）", None, "", "")
    # 关3 实弹关
    call_args = test_args or {k: _sample_value(v) for k, v in props.items()}
    try:
        r = fn(**call_args)
        smoke = "ok" if test_args else "sample"
        return (True, "", schema, smoke, str(r)[:300])
    except Exception as e:  # noqa: BLE001
        return (False, f"实弹冒烟失败: {type(e).__name__}: {e}", None, "", "")


def _create_python(name: str, code: str, test_args: dict = None) -> dict:
    if "@tool" not in code or "def run(" not in code:
        return {"ok": False, "error": "Python 源码必须包含 @tool 装饰器与 def run 入口函数"}
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "error": f"语法错误: {e}"}
    target = _PY_DIR / f"{name}.py"
    try:
        target.write_text(code, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"写入失败: {e}"}
    try:
        mod = _load_py_module(name)
    except Exception as e:  # noqa: BLE001
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"加载失败（已删除半成品）: {type(e).__name__}: {e}"}
    meta = fn = None
    for _, f in inspect.getmembers(mod, inspect.isfunction):
        m = get_meta(f)
        if m and m["name"] == name:
            meta, fn = m, f
            break
    if fn is None:
        target.unlink(missing_ok=True)
        return {"ok": False, "error": "未找到与工具名一致的 @tool 注册函数（已删除半成品）"}
    # 准入三关（契约/一致性/实弹）——先测可用，再进名单
    ok, err, schema, smoke, smoke_result = _admit_python(fn, meta, test_args)
    if not ok:
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"工具准入未通过（已删除半成品，未进工具名单）: {err}"}
    return {
        "ok": True, "language": "python", "registered": name,
        "description": meta["description"], "schema": schema,
        "smoke": smoke, "smoke_result": smoke_result, "path": str(target),
        "note": "工具已通过准入三关（契约/一致性/实弹），可进工具名单复用。",
    }


# ================= Go 工具路径 =================

def _go_available() -> tuple:
    """探测 Go 编译工具链。返回 (ok, version 或错误信息)。"""
    go = shutil.which("go")
    if not go:
        return False, "未检测到 go 命令（PATH 中无 go）。可先确认 Go 工具链已安装。"
    try:
        proc = subprocess.run([go, "version"], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return False, f"go version 失败: {proc.stderr[:200]}"
        return True, proc.stdout.strip()
    except Exception as e:  # noqa: BLE001
        return False, f"go 探测异常: {e}"


def _parse_go_meta(code: str, name: str) -> tuple:
    """解析 Go 源码元信息。返回 (desc, schema_json_str) 或 (None, None) 表示缺关键注释。"""
    m = _SCHEMA_RE.search(code)
    if not m:
        return None, None
    schema_raw = m.group(1)
    # 校验 schema 是合法 JSON
    try:
        schema = json.loads(schema_raw)
    except json.JSONDecodeError as e:
        return None, None
    if not isinstance(schema, dict):
        return None, None
    desc = "Go 工具 " + name
    md = _DESC_RE.search(code)
    if md:
        desc = md.group(1).strip()
    return desc, schema


def _register_go_disk(name: str, desc: str, schema: dict,
                      binary: Path, source: Path) -> None:
    """把 Go 工具条目写入 data/registry.json（磁盘，供 execute 层 load 后生效）。"""
    reg_path = _PROJECT_ROOT / "data" / "registry.json"
    data = {"tools": {}}
    if reg_path.exists():
        try:
            data = json.loads(reg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"tools": {}}
    data.setdefault("tools", {})
    data["tools"][name] = {
        "name": name,
        "description": desc,
        "parameters": normalize_schema(schema),
        "impl": {
            "language": "go",
            "type": "go_binary",
            "binary": str(binary),
            "source": str(source),
            "hash": "go_binary",
        },
        "version": 1,
        "runtime": {
            "status": "active",
            "last_tested": None,
            "test_results": {"passed": 0, "failed": 0},
        },
    }
    reg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _create_go(name: str, code: str, test_args: dict = None) -> dict:
    # 1) 源码校验
    if "package main" not in code or "func main(" not in code:
        return {"ok": False, "error": "Go 源码必须包含 package main 与 func main 入口"}
    desc, schema = _parse_go_meta(code, name)
    if desc is None or schema is None:
        return {"ok": False, "error": "Go 源码必须包含 @schema 注释（一行 JSON），并建议写 @desc 描述"}
    # 准入·契约关：schema 规范化 + 必须是标准 JSON Schema（type:object + properties）
    schema = normalize_schema(schema)
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return {"ok": False, "error": "Go 工具 @schema 必须是标准 JSON Schema（顶层 type:object + properties）"}

    # 2) 环境可行性判定（go 工具链）
    go_ok, go_info = _go_available()
    if not go_ok:
        return {"ok": False, "error": f"Go 工具链不可用，暂无法编译 Go 工具：{go_info}"}

    # 3) 落盘源码
    _GO_DIR.mkdir(parents=True, exist_ok=True)
    target = _GO_DIR / f"{name}.go"
    try:
        target.write_text(code, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"写入失败: {e}"}

    # 4) 编译
    _BIN_DIR.mkdir(parents=True, exist_ok=True)
    exe_name = name + (".exe" if os.name == "nt" else "")
    binary = _BIN_DIR / exe_name
    try:
        proc = subprocess.run(
            ["go", "build", "-o", str(binary), str(target)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            target.unlink(missing_ok=True)
            binary.unlink(missing_ok=True)
            return {"ok": False, "error": f"编译失败（已删除半成品）: {proc.stderr[:500]}"}
    except Exception as e:  # noqa: BLE001
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"编译异常: {e}"}

    # 5) 冒烟测试（stdin/stdout JSON 协议）
    smoke, smoke_result = "skipped", ""
    if test_args:
        try:
            payload = json.dumps({"args": test_args}, ensure_ascii=False)
            p = subprocess.run([str(binary)], input=payload, capture_output=True,
                               text=True, timeout=30)
            if p.returncode != 0:
                binary.unlink(missing_ok=True)
                return {"ok": False, "error": f"冒烟：二进制退出码 {p.returncode}: {p.stderr[:300]}"}
            out = json.loads(p.stdout.strip())
            if not out.get("ok"):
                binary.unlink(missing_ok=True)
                return {"ok": False, "error": f"冒烟：工具返回错误: {out.get('error','')[:200]}"}
            smoke, smoke_result = "ok", str(out)[:300]
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            binary.unlink(missing_ok=True)
            return {"ok": False, "error": f"冒烟失败（已删除半成品）: {e}"}

    # 6) 注册（写入磁盘，execute 层 load 后生效）
    try:
        _register_go_disk(name, desc, schema, binary, target)
    except OSError as e:
        return {"ok": False, "error": f"注册失败: {e}"}

    return {
        "ok": True, "language": "go", "registered": name,
        "description": desc, "smoke": smoke, "smoke_result": smoke_result,
        "binary": str(binary), "source": str(target), "go_toolchain": go_info,
        "note": "Go 工具已编译并注册（go_binary 类型），可立即复用。",
    }


@tool(
    "tool_create",
    "创建新工具（工具自举核心）。支持两种语言：language=python（默认）写入 tools/src/python/，"
    "经语法校验、独立加载后注册；language=go 写入 tools/src/go/，经 go build 编译、冒烟测试后注册为 go_binary。"
    "注册成功后该工具立即被后续调用复用。属高风险操作，调用前须陈述五问结论。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "工具名，英文小写下划线，如 weather_query"},
            "code": {"type": "string",
                     "description": "完整工具源码。Python：须 import tools.base 的 tool 装饰器，"
                                    "@tool('工具名','描述',{参数schema}) 标记 def run(...) 入口。"
                                    "Go：须含 package main + func main，顶部注释 // @schema {JSON} 声明参数 schema"
                                    "（参考 tools/templates/go_tool_template.go）。"},
            "language": {"type": "string", "enum": ["python", "go"],
                         "description": "工具语言：python（默认）/ go（需本机有 go 工具链，自动判定可行性）"},
            "test_args": {"type": "object",
                          "description": "可选。创建后冒烟测试的参数字典，如 {\"name\": \"world\"}"},
        },
        "required": ["name", "code"],
    },
)
def run(name: str, code: str, language: str = "python", test_args: dict = None) -> dict:
    # 1) 名称校验（两语言共用）
    if not _NAME_RE.fullmatch(name):
        return {"ok": False, "error": "工具名只能含英文小写字母、数字、下划线，且不能以数字开头"}
    if name in _PROTECTED:
        return {"ok": False, "error": f"工具 {name} 受保护，不可覆盖"}

    if language == "go":
        return _create_go(name, code, test_args)
    return _create_python(name, code, test_args)
