# -*- coding: utf-8 -*-
"""内置工具：知识资料库（kb_save / kb_search / kb_list）。

素月的长期知识资产库（区别于记忆 memory.db 与方法论 methodology.json）：
按主题分类沉淀"反复要用的知识块"——平台规则、行业资料、技术方案、经验总结等，
避免每次重复上网搜索浪费 token。先查库、命中直接用，未命中再上网、有价值就存回。

位置：library/knowledge/（并入素月既有 library 资料库体系，随私有备份走本地+云端）
  library/knowledge/
    index.json                # 索引（JSON：ID/标题/关键词/来源/时间戳/摘要/路径）
    <分类>/                   # 分类目录（zimeiti/ai-agent/programming 起步，素月可自主新增）

分类：默认三方向（自媒体/AI智能体/编程），素月可在 kb_save 时传新分类名 → 自动创建，
体现自我进化（先查后建防碎片化，但不设限制）。资料库属素月私有实例数据，不公开。
"""
import datetime
import json
import os
import re

from tools.base import tool

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_KB_ROOT = os.path.join(_BASE, "library", "knowledge")
_INDEX = os.path.join(_KB_ROOT, "index.json")
_HISTORY = os.path.join(_KB_ROOT, ".history")   # 修订历史目录（2026-09-19，WeKnora Wiki 借鉴）

# 默认分类：目录名 -> 显示名。素月可自主扩展（kb_save 传新分类自动创建）
DEFAULT_CATEGORIES = {
    "zimeiti": "自媒体",
    "ai-agent": "AI智能体",
    "programming": "编程",
}


def _load_index() -> dict:
    if os.path.exists(_INDEX):
        try:
            with open(_INDEX, encoding="utf-8") as f:
                idx = json.load(f)
            if isinstance(idx, dict):
                idx.setdefault("categories", dict(DEFAULT_CATEGORIES))
                idx.setdefault("entries", [])
                return idx
        except (OSError, json.JSONDecodeError):
            pass
    return {"categories": dict(DEFAULT_CATEGORIES), "entries": []}


def _save_index(idx: dict) -> None:
    os.makedirs(_KB_ROOT, exist_ok=True)
    with open(_INDEX, "w", encoding="utf-8", newline="") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def _safe_dirname(name: str) -> str:
    """分类名 → 安全目录名：去掉路径非法字符，防目录穿越。"""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name).strip())
    s = re.sub(r"\s+", "_", s)
    return s[:40].strip("._ ") or "misc"


def _summary(content: str, limit: int = 120) -> str:
    txt = re.sub(r"\s+", " ", content or "").strip()
    if len(txt) <= limit:
        return txt
    return txt[: limit - 1] + "…"


# ---------- 修订历史（2026-09-19，借鉴 WeKnora Wiki 修订模式） ----------

def _hist_ts() -> str:
    """历史文件名时间戳（Windows 安全：不用冒号）。"""
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _hist_dir(eid: str) -> str:
    """条目修订历史目录：library/knowledge/.history/<eid>/"""
    d = os.path.join(_HISTORY, _safe_dirname(eid))
    os.makedirs(d, exist_ok=True)
    return d


def _snapshot_old(eid: str, path: str) -> int:
    """保存前快照旧版本到 .history/<eid>/<ts>.md。返回历史文件数；无旧文件返回 0。"""
    if not (path and os.path.isfile(path)):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
    except (OSError, TypeError):
        return 0
    hist = os.path.join(_hist_dir(eid), _hist_ts() + ".md")
    try:
        with open(hist, "w", encoding="utf-8", newline="") as f:
            f.write(old)
    except OSError:
        return 0
    return len([n for n in os.listdir(_hist_dir(eid)) if n.endswith(".md")])


def _list_versions(eid: str) -> list:
    """列出条目全部历史版本（按时间倒序）：{ts, size}。"""
    d = _hist_dir(eid)
    vers = []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d), reverse=True):
            if n.endswith(".md"):
                p = os.path.join(d, n)
                try:
                    vers.append({"ts": n[:-3], "size": os.path.getsize(p)})
                except OSError:
                    continue
    return vers


def _read_version(eid: str, ts: str) -> str | None:
    """读取指定历史版本正文；不存在返回 None。"""
    safe = _safe_dirname(eid)
    if not re.fullmatch(r"\d{8}_\d{6}", str(ts or "")):
        return None
    p = os.path.join(_KB_ROOT, ".history", safe, str(ts) + ".md")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except (OSError, TypeError):
        return None


@tool(
    "kb_save",
    "保存知识到资料库（library/knowledge/）：按主题分类存档成 Markdown + 更新索引，"
    "之后 kb_search 直接检索，避免重复上网。分类用 zimeiti(自媒体)/ai-agent(AI智能体)/programming(编程)，"
    "也可传新分类名（素月自主扩展，自动创建目录）。",
    {
        "title": {"type": "string", "description": "词条标题（如：小红书内容规范速查）", "required": True},
        "category": {"type": "string", "description": "分类：zimeiti / ai-agent / programming，或自定义新分类名", "required": True},
        "content": {"type": "string", "description": "资料正文（Markdown 格式，尽量精炼成要点，便于快查）", "required": True},
        "keywords": {"type": "string", "description": "关键词，逗号分隔（用于检索，如：小红书,限流,违禁词）", "required": False},
        "source": {"type": "string", "description": "来源（如：官方文档/文章 URL/经验总结），可空", "required": False},
    },
)
def save(title: str, category: str, content: str, keywords: str = "", source: str = "") -> dict:
    if not title or not str(title).strip():
        return {"ok": False, "error": "title 不能为空"}
    category = (category or "").strip()
    if not category:
        return {"ok": False, "error": "category 不能为空"}
    if not content or not str(content).strip():
        return {"ok": False, "error": "content 不能为空"}
    title = str(title).strip()
    content = str(content)
    kw_list = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    source = (source or "").strip()

    idx = _load_index()
    cats = idx["categories"]
    if category not in cats:
        cats[category] = category  # 素月自主扩展新分类（目录名=分类名）
        created_new = True
    else:
        created_new = False
    cat_dir = os.path.join(_KB_ROOT, _safe_dirname(category))
    os.makedirs(cat_dir, exist_ok=True)

    seq = sum(1 for e in idx["entries"] if e["category"] == category) + 1
    eid = f"{_safe_dirname(category)}_{seq:03d}"
    for e in idx["entries"]:
        if e["category"] == category and e["title"] == title:
            eid = e["id"]
            break

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(cat_dir, f"{eid}.md")
    display = cats.get(category, category)

    # 修订历史：已有旧版本 → 先快照进 .history/<eid>/，再覆盖写
    hist_count = _snapshot_old(eid, path)

    md = (f"# {title}\n\n"
          f"> ID: {eid} | 分类: {display} | 存档: {ts}"
          + (f" | 来源: {source}" if source else "") + "\n\n"
          + content.strip() + "\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(md)

    entry = {
        "id": eid, "title": title, "category": category,
        "keywords": kw_list, "source": source, "ts": ts,
        "path": path, "summary": _summary(content),
        "versions": hist_count, "updated": ts,
    }
    idx["entries"] = [e for e in idx["entries"] if e.get("id") != eid]
    idx["entries"].append(entry)
    idx["entries"].sort(key=lambda e: (e["category"], e["id"]))
    _save_index(idx)
    return {"ok": True, "id": eid, "title": title, "path": path,
            "category": display, "new_category": created_new,
            "total": len(idx["entries"]), "versions": hist_count,
            "note": "已存档并更新索引，kb_search 可检索"
                    + (f"；旧版本已入历史（共 {hist_count} 个），kb_history 可查/回滚" if hist_count else "")}


@tool(
    "kb_search",
    "检索知识资料库（library/knowledge/）：按关键词/标题/正文全文匹配已有知识"
    "（先查库，命中直接用，不命中再上网）。返回条目列表（ID/标题/分类/摘要/路径），全文用 fs_read 读 path。",
    {
        "query": {"type": "string", "description": "检索词（支持中文子串/英文，多词用空格分隔，全部命中才返回）", "required": True},
        "category": {"type": "string", "description": "限定分类（如 zimeiti/ai-agent/programming 或自定义分类），可空=全部", "required": False},
        "limit": {"type": "integer", "description": "最多返回条数，默认 8", "required": False},
    },
)
def search(query: str, category: str = "", limit: int = 8) -> dict:
    if not query or not str(query).strip():
        return {"ok": False, "error": "query 不能为空"}
    query = str(query).strip()
    terms = [t for t in re.split(r"[\s,，]+", query) if t]
    limit = max(1, min(int(limit or 8), 50))

    idx = _load_index()
    cats = idx["categories"]
    hits = []
    for e in idx["entries"]:
        if category and e["category"] != category:
            continue
        haystack = " ".join([
            e.get("title", ""), e.get("summary", ""),
            " ".join(e.get("keywords", [])), e.get("source", ""),
        ]).lower()
        try:
            with open(e.get("path", ""), encoding="utf-8") as _f:
                haystack += " " + _f.read().lower()
        except (OSError, TypeError):
            pass
        if all(t.lower() in haystack for t in terms):
            hits.append(e)
    hits.sort(key=lambda e: e["ts"], reverse=True)
    hits = hits[:limit]
    return {"ok": True, "query": query, "count": len(hits),
            "hits": [{"id": e["id"], "title": e["title"],
                      "category": cats.get(e["category"], e["category"]),
                      "keywords": e.get("keywords", []), "summary": e.get("summary", ""),
                      "path": e.get("path", ""), "ts": e.get("ts", "")} for e in hits],
            "note": "命中后全文用 fs_read 读 path；无命中时再上网搜索"}


@tool(
    "kb_list",
    "列出知识资料库条目：按分类浏览全部已存知识（含素月自主新增的分类），返回各分类条目数。",
    {
        "category": {"type": "string", "description": "限定分类（可空=全部）", "required": False},
    },
)
def list_(category: str = "") -> dict:
    idx = _load_index()
    cats = idx["categories"]
    entries = idx["entries"]
    if category:
        entries = [e for e in entries if e["category"] == category]
    by_cat = {}
    for e in entries:
        c = cats.get(e["category"], e["category"])
        by_cat.setdefault(c, []).append({"id": e["id"], "title": e["title"], "ts": e.get("ts", "")})
    return {"ok": True, "total": len(entries),
            "categories": [{"dir": k, "display": v} for k, v in cats.items()],
            "by_category": by_cat,
            "note": "需要详情用 kb_search 或 fs_read"}


@tool(
    "kb_history",
    "查看知识资料库条目的修订历史（kb_save 每次覆盖前自动快照旧版本到 .history/）。"
    "返回版本列表（时间戳+大小）；传 version 读取指定版本正文（时间戳格式 YYYYMMDD_HHMMSS，"
    "用 kb_search 的 id 定位条目）。回滚用 kb_rollback。",
    {
        "id": {"type": "string", "description": "条目 ID（如 zimeiti_001，来自 kb_search/kb_list）", "required": True},
        "version": {"type": "string", "description": "指定版本时间戳（YYYYMMDD_HHMMSS）；不传=只列版本清单", "required": False},
    },
)
def history(id: str, version: str = "") -> dict:
    if not id or not str(id).strip():
        return {"ok": False, "error": "id 不能为空"}
    eid = str(id).strip()
    idx = _load_index()
    ent = next((e for e in idx["entries"] if e.get("id") == eid), None)
    if ent is None:
        return {"ok": False, "error": "条目不存在: " + eid}
    vers = _list_versions(eid)
    if version:
        body = _read_version(eid, version)
        if body is None:
            return {"ok": False, "error": "版本不存在: " + version
                    + "（可用 kb_history id= 查版本清单）"}
        return {"ok": True, "id": eid, "title": ent.get("title", ""),
                "version": version, "content": body,
                "note": "回滚用 kb_rollback id= version=" + version}
    return {"ok": True, "id": eid, "title": ent.get("title", ""),
            "total_versions": len(vers), "versions": vers,
            "note": "传 version 读取某版本正文；回滚用 kb_rollback"}


@tool(
    "kb_rollback",
    "回滚知识资料库条目到指定历史版本：当前内容先快照进历史（防误回滚丢东西），"
    "再用目标版本内容覆盖主文件并更新索引。版本时间戳用 kb_history 查询。",
    {
        "id": {"type": "string", "description": "条目 ID（如 zimeiti_001）", "required": True},
        "version": {"type": "string", "description": "目标版本时间戳（YYYYMMDD_HHMMSS，来自 kb_history）", "required": True},
    },
)
def rollback(id: str, version: str) -> dict:
    if not id or not str(id).strip():
        return {"ok": False, "error": "id 不能为空"}
    if not version or not str(version).strip():
        return {"ok": False, "error": "version 不能为空（用 kb_history 查版本清单）"}
    eid = str(id).strip()
    body = _read_version(eid, version)
    if body is None:
        return {"ok": False, "error": "版本不存在: " + str(version)}
    idx = _load_index()
    ent = next((e for e in idx["entries"] if e.get("id") == eid), None)
    if ent is None:
        return {"ok": False, "error": "条目不存在: " + eid}
    path = ent.get("path", "")
    if not (path and os.path.isfile(path)):
        return {"ok": False, "error": "条目文件缺失，无法回滚: " + str(path)}

    # 当前内容先快照进历史，再写目标版本
    _snapshot_old(eid, path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(body)

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ent["ts"] = ts
    ent["updated"] = ts
    ent["versions"] = len(_list_versions(eid))
    ent["summary"] = _summary(re.sub(r"^#.*\n", "", body))
    ent["rollback_to"] = version
    idx["entries"] = [e for e in idx["entries"] if e.get("id") != eid]
    idx["entries"].append(ent)
    idx["entries"].sort(key=lambda e: (e["category"], e["id"]))
    _save_index(idx)
    return {"ok": True, "id": eid, "title": ent.get("title", ""),
            "rolled_back_to": version, "versions": ent["versions"],
            "path": path, "note": "已回滚并更新索引；原当前版本已进历史，可再用 kb_rollback 撤销"}
