"""内置工具：tool_health_audit —— 工具库全链路健康自检。

判据以「真实加载结果」为准，而非「文件能否被孤立 import」：
data/registry.json 记录全部已注册工具；启动时 ToolRegistry 按
tools.src.python.<stem> 真实加载源码 —— 加载失败的工具不会进入 registry._fns。
据此比对「注册表清单」与「真实加载结果」，即可定位僵尸工具（注册了却加载不进来）。

此前一版审计脚本的教训：直接孤立 import 工具文件，会因为 sys.path 未含项目根、
以及用错解释器（系统 Python 而非 .venv）产生大片假失败（No module named 'tools' /
'httpx'）。本工具复用注册表同款加载路径，避免同类假阳性。

检查项：
1. python_function：是否真实加载、源文件是否存在、源码 hash 是否与注册表一致、deps 是否可导入
2. go_binary：二进制是否存在
3. mcp：按 server 统计工具数，校验 config/mcp.json 中是否有对应 server 定义
4. 未归类：按 core/registry.py::resolve_group（唯一真源）判功能域，落进"其他"即告警
   —— 新工具要么显式 @tool(group=...)，要么被前缀规则/手工表覆盖

模式：scan（默认，全量扫描并写报告）/ status（只读上次报告）。
只读工具：不改注册表、不写项目状态，仅落一份报告到 workspace。
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _find_root(start: Path) -> Path:
    """向上定位项目根（含 core/registry.py 与 tools/base.py），保证脚本放到哪都能算对路径。"""
    p = start
    for _ in range(8):
        if (p / "core" / "registry.py").is_file() and (p / "tools" / "base.py").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start


_ROOT = _find_root(Path(__file__).resolve().parent)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.base import tool, get_meta  # noqa: E402

_DEFAULT_REPORT = "workspace/tasks/tool_health/audit_report.json"


# ---------- 真实加载（复用注册表同款路径） ----------

def _load_python_tools() -> tuple:
    """按 tools.src.python.<stem> 逐个真实加载，返回 (loaded, failures)。

    loaded: {tool_name: {"module": stem, "entry": fn_name}}
    failures: {stem: "错误信息"}   —— 模块级加载失败
    """
    src = _ROOT / "tools" / "src" / "python"
    loaded: dict = {}
    failures: dict = {}
    if not src.is_dir():
        return loaded, {"<dir>": f"目录不存在: {src}"}
    for py in sorted(src.glob("*.py")):
        if py.name.startswith("_"):
            continue
        mod_name = f"tools.src.python.{py.stem}"
        try:
            if mod_name in sys.modules:
                mod = importlib.reload(sys.modules[mod_name])  # 反映当前磁盘状态
            else:
                mod = importlib.import_module(mod_name)
        except Exception as e:  # noqa: BLE001
            failures[py.stem] = f"{type(e).__name__}: {e}"
            continue
        try:
            for _, fn in inspect.getmembers(mod, inspect.isfunction):
                meta = get_meta(fn)
                if meta and meta.get("name"):
                    loaded[meta["name"]] = {"module": py.stem, "entry": fn.__name__}
        except Exception as e:  # noqa: BLE001
            failures[py.stem] = f"inspect 失败: {type(e).__name__}: {e}"
    return loaded, failures


_GROUP_RESOLVER = None


def _group_of(name: str, entry: dict) -> str:
    """功能域判定：唯一真源 core/registry.py::resolve_group。

    取不到真源（如脱离项目根运行）时返回 ""，即不参与未归类判定，避免误报。
    """
    global _GROUP_RESOLVER
    if _GROUP_RESOLVER is None:
        try:
            from core.registry import ToolRegistry
            _GROUP_RESOLVER = ToolRegistry.resolve_group
        except Exception:  # noqa: BLE001
            _GROUP_RESOLVER = False
    if not _GROUP_RESOLVER:
        return ""
    return _GROUP_RESOLVER(name, str((entry or {}).get("group") or ""))


# ---------- 审计主体 ----------

def _audit(probe_deps: bool = True) -> dict:
    reg_path = _ROOT / "data" / "registry.json"
    if not reg_path.is_file():
        return {"ok": False, "error": f"注册表不存在: {reg_path}"}
    try:
        registry = json.loads(reg_path.read_text(encoding="utf-8")).get("tools", {})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"registry.json 解析失败: {type(e).__name__}: {e}"}

    loaded, failures = _load_python_tools()

    problems = {"zombie": [], "missing_source": [], "hash_mismatch": [],
                "deps_missing": [], "go_binary_missing": [], "unclassified": []}
    counts = {"python_function": 0, "go_binary": 0, "mcp": 0, "other": 0}
    mcp_servers: dict = {}
    deps_cache: dict = {}
    group_counts: dict = {}

    for name, entry in sorted(registry.items()):
        impl = entry.get("impl", {}) or {}
        typ = impl.get("type", "other")
        counts[typ if typ in counts else "other"] += 1

        grp = _group_of(name, entry)
        if grp:
            group_counts[grp] = group_counts.get(grp, 0) + 1
            if grp == "其他":
                problems["unclassified"].append(name)

        if typ == "python_function":
            if name not in loaded:
                problems["zombie"].append(name)
            src = impl.get("source") or ""
            sp = _ROOT / src if src else None
            if not src or sp is None or not sp.is_file():
                problems["missing_source"].append({"tool": name, "source": src})
            else:
                disk_hash = "sha256:" + hashlib.sha256(sp.read_bytes()).hexdigest()[:16]
                if impl.get("hash") and impl["hash"] != disk_hash:
                    problems["hash_mismatch"].append(
                        {"tool": name, "registry": impl["hash"], "disk": disk_hash})
            if probe_deps:
                for d in (impl.get("deps") or []):
                    if d not in deps_cache:
                        try:
                            importlib.import_module(d)
                            deps_cache[d] = True
                        except Exception:  # noqa: BLE001
                            deps_cache[d] = False
                    if not deps_cache[d]:
                        problems["deps_missing"].append({"tool": name, "dep": d})

        elif typ == "go_binary":
            b = impl.get("binary") or ""
            if not b or not os.path.exists(b):
                problems["go_binary_missing"].append({"tool": name, "binary": b})

        elif typ == "mcp":
            s = impl.get("server", "?")
            mcp_servers[s] = mcp_servers.get(s, 0) + 1

    # MCP 配置对照
    cfg_servers: list = []
    mcp_cfg = _ROOT / "config" / "mcp.json"
    if mcp_cfg.is_file():
        try:
            cfg_servers = list((json.loads(mcp_cfg.read_text(encoding="utf-8")).get("servers") or {}).keys())
        except Exception:  # noqa: BLE001
            pass
    orphan_servers = sorted(set(mcp_servers) - set(cfg_servers))
    idle_servers = sorted(set(cfg_servers) - set(mcp_servers))

    healthy = (not failures) and all(not v for v in problems.values())
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(_ROOT),
        "total_tools": len(registry),
        "counts_by_impl": counts,
        "python_tools_loaded": len(loaded),
        "module_load_failures": failures,
        "problems": problems,
        "groups": dict(sorted(group_counts.items(), key=lambda kv: -kv[1])),
        "mcp": {
            "servers_in_registry": mcp_servers,
            "servers_in_config": cfg_servers,
            "orphan_servers": orphan_servers,
            "idle_servers": idle_servers,
        },
        "deps_ok": deps_cache,
        "healthy": healthy,
    }


def _summarize(rep: dict) -> dict:
    p = rep.get("problems", {})
    return {
        "total_tools": rep.get("total_tools"),
        "counts_by_impl": rep.get("counts_by_impl"),
        "python_tools_loaded": rep.get("python_tools_loaded"),
        "healthy": rep.get("healthy"),
        "groups": rep.get("groups"),
        "unclassified": p.get("unclassified", []),
        "zombie": p.get("zombie", []),
        "missing_source": p.get("missing_source", []),
        "hash_mismatch": [x.get("tool") for x in p.get("hash_mismatch", [])],
        "deps_missing": sorted({x.get("dep") for x in p.get("deps_missing", [])}),
        "go_binary_missing": [x.get("tool") for x in p.get("go_binary_missing", [])],
        "module_load_failures": rep.get("module_load_failures", {}),
        "mcp": rep.get("mcp", {}),
    }


@tool(
    "tool_health_audit",
    "工具库全链路健康自检：以真实加载结果为准，检测僵尸工具/源文件缺失/hash漂移/依赖缺失/go二进制缺失/MCP server 对不上，输出报告",
    {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "description": "scan=全量扫描并写报告（默认）；status=只读上次报告"},
            "report_path": {"type": "string", "description": "报告路径（相对项目根），默认 workspace/tasks/tool_health/audit_report.json"},
            "probe_deps": {"type": "boolean", "description": "是否实测 deps 可导入，默认 true"},
        },
        "required": [],
    },
)
def run(mode="scan", report_path=None, probe_deps=True):
    rp = _ROOT / (report_path or _DEFAULT_REPORT)
    if mode == "status":
        if not rp.is_file():
            return {"ok": False, "error": f"尚无报告，请先扫一次（mode=scan）: {rp}"}
        try:
            rep = json.loads(rp.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"报告解析失败: {e}"}
        return {"ok": True, "mode": "status", "report_path": str(rp),
                "generated_at": rep.get("generated_at"), "summary": _summarize(rep)}

    rep = _audit(probe_deps=bool(probe_deps))
    if not rep.get("ok"):
        return rep
    try:
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"报告写入失败: {e}", "summary": _summarize(rep)}
    return {"ok": True, "mode": "scan", "report_path": str(rp),
            "summary": _summarize(rep), "problems": rep["problems"]}
