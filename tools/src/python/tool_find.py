"""内置工具：tool_find —— 工具索引查询器（工具库的检索入口）。

背景：工具世界书（core/registry.py::to_index）每轮把工具多级目录注入 system prompt，
但每条只带名字 + 截断的自动关键词，没有功能描述，于是"有没有能干 X 的工具"只能靠猜名字。
（归类口径已统一到 core/registry.py::resolve_group 单一真源；MCP 组在世界书里折叠，明细靠本工具查。）

本工具以 data/registry.json（工具索引底表：每条含 name / name_zh / description /
keywords / parameters / impl / runtime）为数据源，补上"检索"这一环：
  - query 为空 → 目录概览：按功能域列出全部工具
  - query 非空 → 能力检索：多词先 AND（全命中）匹配 name / 中文名 / 关键词 / 功能描述，
    全落空时自动放宽为 OR（任一词命中），打分排序
  - detail=True → 附完整描述、关键词、参数 schema、源文件路径
  - group=功能域 / type_filter=python_function|mcp / include_mcp=False → 收窄口径
  - export_md=True → 顺带导出可读工具目录到 workspace/tasks/tool_index/工具目录.md（生成物）

只读 data/registry.json，不加载、不执行任何工具源码。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from tools.base import tool

_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY = _ROOT / "data" / "registry.json"
_SPLIT = re.compile(r"[\s,，、;；/|+]+")



def _load():
    if not _REGISTRY.exists():
        return None, {"ok": False, "error": "索引底表不存在: " + str(_REGISTRY)}
    try:
        data = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    except Exception as e:
        return None, {"ok": False, "error": "索引底表解析失败: " + str(e)}
    tools = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(tools, dict):
        return None, {"ok": False, "error": "索引底表结构异常（期望 {tools: {name: {...}}}）"}
    return tools, None


def _grouping():
    """取功能域归类器与核心集：唯一真源 = core/registry.py::ToolRegistry.resolve_group。"""
    try:
        from core.registry import ToolRegistry
        return ToolRegistry.resolve_group, set(ToolRegistry.CORE_TOOLS), "core.registry"
    except Exception:
        return (lambda name, declared="": (declared or "").strip() or "其他"), set(), "fallback"


def _group_of(name, t, resolve):
    """判定功能域：显式声明 > 手工表 > 前缀规则 > 其他（口径全在 core，本工具不自带表）。"""
    return resolve(name, str(((t or {}).get("group")) or ""))


def _export_md() -> str:
    """导出可读工具目录 md 快照（生成物·勿手改；真源 core/registry.py，改工具后重导）。"""
    try:
        from core.registry import ToolRegistry
        reg = ToolRegistry(path=str(_REGISTRY), tools_dir=str(_ROOT / "tools"))
        reg.load()
        out = _ROOT / "workspace" / "tasks" / "tool_index" / "工具目录.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        head = (f"# 工具目录（生成物 · 勿手改）\n\n"
                f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 工具 {len(reg.tools)} 个 ｜ "
                f"真源 core/registry.py::group_of，改工具后重新导出即可\n"
                f"> 按能力检索用 tool_find；世界书每轮注入的是同源折叠版（MCP 组折叠）\n\n")
        out.write_text(head + reg.to_index() + "\n", encoding="utf-8")
        return str(out)
    except Exception as e:
        return f"导出失败: {type(e).__name__}: {e}"


def _score(t, terms):
    name = str(t.get("name") or "").lower()
    zh = str(t.get("name_zh") or "").lower()
    desc = str(t.get("description") or "").lower()
    kws = " ".join(str(k) for k in (t.get("keywords") or [])).lower()
    total = 0
    for term in terms:
        s = 0
        if name == term:
            s += 200
        elif name.startswith(term):
            s += 120
        elif term in name:
            s += 90
        if term in zh:
            s += 60
        if term in desc:
            s += 30
        if term in kws:
            s += 15
        if s == 0:
            return 0          # AND 语义：任一检索词不命中即淘汰
        total += s
    return total


def _entry(name, t, resolve, core, detail):
    impl = t.get("impl") or {}
    d = {
        "name": name,
        "name_zh": t.get("name_zh", ""),
        "group": _group_of(name, t, resolve),
        "type": impl.get("type", ""),
        "is_core": name in core,
        "status": (t.get("runtime") or {}).get("status", ""),
        "description": str(t.get("description") or ""),
    }
    if not detail:
        d["description"] = d["description"][:160]
    else:
        d["keywords"] = t.get("keywords")
        d["parameters"] = t.get("parameters")
        d["source"] = impl.get("source")
    return d


@tool(
    "tool_find",
    "查询本机工具索引（工具库检索入口）。query 为空=列全部工具目录（按功能域分组）；"
    "query 非空=按能力/用途多词检索 name/中文名/关键词/功能描述并打分排序（先 AND 全命中，全落空自动放宽为 OR），"
    "适合「有没有能做 X 的工具」「工具目录有哪些」这类需求，替代肉眼扫每轮注入的工具世界书。"
    "可选 group=功能域、type_filter=python_function|mcp|go_binary、include_mcp=False 排除 MCP 工具、"
    "detail=True 附完整描述与参数 schema；export_md=True 顺带导出可读目录快照。只读 data/registry.json。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "检索词，多词用空格分隔（先 AND 全命中，全落空自动放宽为 OR）；留空则输出全部工具目录"},
            "group": {"type": "string", "description": "只看某功能域，如 网络/文件系统/工具工程"},
            "type_filter": {"type": "string", "enum": ["python_function", "mcp", "go_binary"],
                            "description": "只看某种实现类型的工具"},
            "include_mcp": {"type": "boolean", "description": "检索结果是否包含 MCP 工具，默认 true"},
            "limit": {"type": "integer", "description": "检索模式最多返回条数，默认 20"},
            "detail": {"type": "boolean",
                       "description": "true=返回完整描述/关键词/参数 schema/源文件（默认 false，只给截断描述）"},
            "export_md": {"type": "boolean",
                          "description": "true=顺带导出可读工具目录 md 快照到 workspace/tasks/tool_index/（生成物，不动真源）"},
        },
        "required": [],
    },
)
def run(query="", group="", type_filter="", include_mcp=True, limit=20, detail=False,
        export_md=False):
    try:
        tools, err = _load()
        if err:
            return err
        resolve, core, src = _grouping()
        limit = max(1, int(limit or 20))
        group = (group or "").strip()
        type_filter = (type_filter or "").strip()

        def keep(name, t):
            if type_filter and (t.get("impl") or {}).get("type") != type_filter:
                return False
            if not include_mcp and (t.get("impl") or {}).get("type") == "mcp":
                return False
            if group and _group_of(name, t, resolve) != group:
                return False
            return True

        pool = [(n, t) for n, t in sorted(tools.items()) if keep(n, t)]

        # ③ 可选：导出可读目录快照（生成物，真源仍在 core/registry.py）
        md_note = {}
        if export_md:
            md_note["md_export"] = _export_md()

        # ① 目录概览
        if not query or not query.strip():
            buckets = {}
            for n, t in pool:
                buckets.setdefault(_group_of(n, t, resolve), []).append((n, t))
            groups_out = []
            for g in sorted(buckets, key=lambda x: (-len(buckets[x]), x)):
                items = buckets[g]
                if detail:
                    groups_out.append({"group": g, "count": len(items),
                                       "tools": [_entry(n, t, resolve, core, True)
                                                 for n, t in items[:limit]]})
                else:
                    groups_out.append({"group": g, "count": len(items),
                                       "tools": [f"{n}（{t.get('name_zh') or '-'}）" for n, t in items]})
            return {
                **md_note,
                "ok": True, "mode": "overview", "index_source": str(_REGISTRY),
                "group_source": src, "total_tools": len(tools),
                "listed": len(pool), "groups": groups_out,
                "hint": "检索用法：query='表格' / query='webdav 上传' / include_mcp=False 只看自有工具",
            }

        # ② 能力检索：先 AND，全落空退化为 OR
        terms = [x for x in _SPLIT.split(query.strip().lower()) if x]
        scored = []
        for n, t in pool:
            s = _score(t, terms)
            if s > 0:
                scored.append((s, n, t))
        relaxed = False
        if not scored and len(terms) > 1:
            relaxed = True
            for n, t in pool:
                best = max((_score(t, [x]) for x in terms), default=0)
                if best > 0:
                    scored.append((best, n, t))
        scored.sort(key=lambda x: (-x[0], x[1]))
        hits = [_entry(n, t, resolve, core, detail) for _, n, t in scored[:limit]]
        return {
            **md_note,
            "ok": True, "mode": "search", "index_source": str(_REGISTRY),
            "group_source": src, "query": query, "terms": terms, "relaxed_or": relaxed,
            "total_tools": len(tools), "matched": len(scored), "returned": len(hits),
            "tools": hits,
            "hint": ("命中为 0：换更短/更通用的词（如 '图像识别'→'图片'），或 include_mcp=True 扩大范围"
                     if not hits else "要参数细节：detail=True；要调用：长尾工具说名字即自动加载 schema"),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
