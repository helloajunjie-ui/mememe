"""工具注册表（ToolRegistry）与工具分发执行。

设计意图（见设计文档 4.3 / 4.4）：
- 工具 = Agent 可调用的函数，对外以 OpenAI function schema 暴露，对内以 Python 函数或 Go 二进制实现。
- 统一调用协议：LLM 只看工具名和参数，不知道底层语言。
- 分发：python_function → importlib 动态加载；go_binary → 子进程 + stdin/stdout JSON。
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.base import get_meta


def normalize_schema(schema: Any) -> Dict:
    """规范化工具参数 schema 为标准 JSON Schema（type=object）。

    支持两种输入，统一输出标准格式：
    1) 标准格式：{"type":"object","properties":{...},"required":[...]}
    2) 扁平参数表：{"name":{"type":"...","description":"...","required":true}, ...}
       —— 自动包装为 properties，并把字段级 required:true 收集进 required 数组。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        props = schema["properties"]
        required = list(schema.get("required", []))
        for k, v in props.items():
            if isinstance(v, dict) and "required" in v:
                if v.get("required") is True and k not in required:
                    required.append(k)
                v.pop("required", None)
        if required:
            schema["required"] = required
        return schema
    # 扁平参数表 → 标准格式
    properties, required = {}, []
    for k, v in schema.items():
        if isinstance(v, dict):
            if v.get("required") is True:
                required.append(k)
            v.pop("required", None)
            properties[k] = v
        else:
            properties[k] = {"type": "string", "description": str(v)}
    out = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


class ToolRegistry:
    def __init__(self, path: str = "data/registry.json", tools_dir: str = "tools"):
        self.path = path
        self.tools_dir = tools_dir
        self.tools: Dict[str, Dict] = {}      # name -> registry entry
        self._fns: Dict[str, Callable] = {}   # name -> python 函数（动态加载）

    # ---------- 持久化 ----------
    def load(self) -> bool:
        if not os.path.exists(self.path):
            return False
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.tools = data.get("tools", {})
        return True

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"tools": self.tools}, f, ensure_ascii=False, indent=2)

    # ---------- 内置工具发现 ----------
    def discover_builtin(self) -> int:
        """扫描 tools/src/python/*.py，动态加载带 @tool 装饰器的函数。"""
        src_dir = Path(self.tools_dir) / "src" / "python"
        if not src_dir.exists():
            return 0
        count = 0
        sys.path.insert(0, str(Path(self.tools_dir).parent))  # 让 "tools.base" 可导入
        for py in sorted(src_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            mod_name = f"tools.src.python.{py.stem}"
            try:
                mod = importlib.import_module(mod_name)
                for _, fn in inspect.getmembers(mod, inspect.isfunction):
                    meta = get_meta(fn)
                    if meta:
                        self._register_python(meta, fn)
                        count += 1
            except Exception as e:  # noqa: BLE001
                print(f"[registry] 加载 {py.name} 失败: {e}")
        self.save()
        return count

    def _register_python(self, meta: Dict, fn: Callable) -> None:
        name = meta["name"]
        source_path = f"{self.tools_dir}/src/python/{fn.__module__.split('.')[-1]}.py"
        self.tools[name] = {
            "name": name,
            "description": meta["description"],
            "parameters": normalize_schema(meta["parameters"]),
            "impl": {
                "language": meta.get("language", "python"),
                "type": "python_function",
                "source": source_path,
                "entry": fn.__name__,
                "deps": meta.get("deps", []),
                "hash": self._hash_file(source_path),
            },
            "version": 1,
            "runtime": {
                "status": "active",
                "last_tested": None,
                "test_results": {"passed": 0, "failed": 0},
            },
        }
        self._fns[name] = fn

    @staticmethod
    def _hash_file(path: str) -> str:
        try:
            with open(path, "rb") as f:
                return "sha256:" + hashlib.sha256(f.read()).hexdigest()[:16]
        except Exception:  # noqa: BLE001
            return "unknown"

    # ---------- 查询 ----------
    def get(self, name: str) -> Optional[Dict]:
        return self.tools.get(name)

    def list_active(self) -> List[Dict]:
        return [t for t in self.tools.values() if t.get("runtime", {}).get("status") == "active"]

    def to_openai_schemas(self) -> List[Dict]:
        """转成 OpenAI function calling 格式。"""
        schemas = []
        for t in self.list_active():
            schemas.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": normalize_schema(t["parameters"]),
                },
            })
        return schemas

    # ---------- 执行分发 ----------
    def execute(self, name: str, args: Dict) -> Dict:
        """分发执行工具。返回 {"ok": bool, "result": ...} 或 {"ok": False, "error": ...}"""
        entry = self.tools.get(name)
        if not entry:
            return {"ok": False, "error": f"工具不存在: {name}"}
        impl = entry["impl"]
        if impl["type"] == "python_function":
            result = self._execute_python(name, args)
            # 工具创建/导入成功后：先 load 磁盘（含 Go 工具等非 Python 条目），
            # 再扫描注册 Python 工具，保存时保留全部（含刚创建的 Go 二进制工具）。
            if name in ("tool_create", "tool_import") and result.get("ok"):
                self.load()
                self.discover_builtin()
            return result
        if impl["type"] == "go_binary":
            return self._execute_go(name, args, impl)
        return {"ok": False, "error": f"未知工具类型: {impl['type']}"}

    def _execute_python(self, name: str, args: Dict) -> Dict:
        fn = self._fns.get(name)
        if fn is None:
            return {"ok": False, "error": f"工具未加载: {name}"}
        try:
            result = fn(**args)
            return {"ok": True, "result": result}
        except TypeError as e:
            return {"ok": False, "error": f"参数错误: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"执行异常: {type(e).__name__}: {e}"}

    def _execute_go(self, name: str, args: Dict, impl: Dict) -> Dict:
        """Go 工具：子进程 + stdin/stdout JSON 协议。"""
        binary = impl.get("binary")
        if not binary or not os.path.exists(binary):
            return {"ok": False, "error": f"Go 二进制缺失: {binary}，需要先编译"}
        try:
            payload = json.dumps({"args": args}, ensure_ascii=False)
            proc = subprocess.run(
                [binary],
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": f"Go 工具退出码 {proc.returncode}: {proc.stderr[:500]}"}
            out = json.loads(proc.stdout.strip())
            return out
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Go 工具执行超时（>30s）"}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Go 工具输出非 JSON: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Go 工具执行异常: {e}"}


if __name__ == "__main__":
    reg = ToolRegistry()
    n = reg.discover_builtin()
    print(f"发现 {n} 个内置工具")
    print(json.dumps(reg.to_openai_schemas(), ensure_ascii=False, indent=2))
