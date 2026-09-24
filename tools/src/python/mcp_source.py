"""内置工具：MCP 源目录管理（mcp_source_update / mcp_source_search / mcp_source_install）。

MCP 源 = punkpeye/awesome-mcp-servers（mcpservers.org 主站数据源，README.md 定期更新）。
  update 拉取解析为 data/mcp_index.json，与 config/mcp.json 已装 MCP 融合并标注 installed；
  search 按关键词/分类搜索候选（标注是否已装）；
  install 环境判定 + git clone + 提取安装指引，测试通过后由素月登记（mcp.json + mcp_scan）。

流程：mcp_source_update（刷目录）→ mcp_source_search（找能力）→ mcp_source_install（拉取+指引）
      → 按指引安装并测试 → 成功 → 写入 mcp.json → mcp_scan 同步
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# 允许 CLI 独立运行（定时任务直接调本文件时，项目根不在 sys.path）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.base import tool

ROOT = Path(__file__).resolve().parents[3]          # F:\me\self-agent
INDEX = ROOT / "data" / "mcp_index.json"
INDEX_HISTORY = ROOT / "data" / "mcp_index_history"   # 滚动存档目录（最多留 3 份）
HISTORY_KEEP = 3
MCP_CONFIG = ROOT / "mcp-service" / "config" / "mcp.json"
SERVERS_DIR = ROOT / "mcp-service" / "servers"

README_URL = "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md"
MIRRORS = [
    "https://ghfast.top/https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
    "https://ghproxy.net/https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
]
MAX_README = 4 * 1024 * 1024

# GitHub repo stats local cache (anonymous core API is only 60 req/h).
# Same repo re-queried within TTL is served from disk, no network call.
GH_CACHE_FILE = ROOT / "data" / "gh_stats_cache.json"
GH_CACHE_TTL = 24 * 3600
GH_CACHE_MAX = 800
_GH_LOCK = threading.Lock()


_LANG = {"🐍": "python", "📇": "typescript", "🏎️": "go", "🦀": "rust",
         "#️⃣": "csharp", "☕": "java", "🌊": "cpp", "💎": "ruby"}
_SCOPE = {"☁️": "cloud", "🏠": "local", "📟": "embedded"}
_OS = {"🍎": "macos", "🪟": "windows", "🐧": "linux"}
_OFFICIAL = "🎖️"

_CAT_RE = re.compile(r'^###\s+.*<a name="([^"]+)">', re.I)
_CAT_RE2 = re.compile(r"^###\s+\S+\s+(.+)$")
_ENTRY_RE = re.compile(r"^-\s+\[([^\]]+)\]\((https?://[^)\s]+)\)(.*)$")
_BADGE_RE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)|!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")

_INSTALL_PATTERNS = [
    (r"uvx\s+[A-Za-z0-9._/-]+", "uvx"),
    (r"uv\s+tool\s+install\s+[A-Za-z0-9._/-]+", "uv"),
    (r"pip\s+install\s+[^\r\n]+", "pip"),
    (r"npx\s+[^\r\n]+", "npx"),
    (r"npm\s+(?:install|i)\s+[^\r\n]+", "npm"),
    (r"go\s+(?:install|build)\s+[^\r\n]+", "go"),
]


def _fetch_readme() -> str:
    """拉取 README，失败依次走国内镜像。"""
    for url in [README_URL] + MIRRORS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "suyue-mcp-source/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(MAX_README + 1)
                if len(data) > MAX_README:
                    data = data[:MAX_README]
                return data.decode("utf-8", errors="replace")
        except Exception:
            continue
    raise RuntimeError("README 拉取失败：官方源与镜像均不可用")


def _parse_readme(text: str) -> tuple[list, list]:
    """解析 Server Implementations 段 → (servers, categories)。"""
    servers, categories = [], []
    in_impl, cur_cat = False, "Other"
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            in_impl = line.lower().startswith("## server implementations")
            if in_impl:
                cur_cat = "Other"
            continue
        if not in_impl:
            continue
        if line.startswith("###"):
            m = _CAT_RE.match(line) or _CAT_RE2.match(line)
            cur_cat = (m.group(1) if m else line[4:].strip()).title()
            if cur_cat not in categories:
                categories.append(cur_cat)
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            continue
        name, url, rest = m.group(1), m.group(2), m.group(3)
        rest = _BADGE_RE.sub("", rest)
        lang = scope = os_ = None
        official = False
        for emoji, v in _LANG.items():
            if emoji in rest:
                lang = v
                break
        for emoji, v in _SCOPE.items():
            if emoji in rest:
                scope = v
                break
        for emoji, v in _OS.items():
            if emoji in rest:
                os_ = v
                break
        if _OFFICIAL in rest:
            official = True
        desc = rest.split("-", 1)[-1].strip() if "-" in rest else rest.strip()
        desc = _LINK_RE.sub(r"\1", desc).strip()
        servers.append({
            "name": name, "repo": url, "category": cur_cat,
            "desc": desc[:300], "lang": lang, "scope": scope,
            "os": os_, "official": official,
            "installed": False, "local_name": None,
        })
    return servers, categories


def _load_installed() -> dict:
    """读 mcp.json 已装 server 名 → {小写名: 原名}。"""
    try:
        cfg = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
        return {k.lower(): k for k in cfg.get("servers", {})}
    except Exception:
        return {}


# 已装 MCP → 允许匹配的分类关键词（利用源自带分类收紧，避免跨类误伤）
_CAT_HINT = {
    "blender": ("art", "design", "3d"),
    "godot": ("gaming",),
    "filesystem": ("file",),
    "playwright": ("browser",),
    "browserskill": ("browser",),
    "git": ("version control",),
    "github": ("version control",),
    "figma": ("design",),
    "kordoc": ("workplace", "productivity", "document"),
    "dbhub": ("database",),
    "edit2docs": ("workplace", "productivity"),
    "ffmpeg": ("multimedia",),
    "graphify": ("developer", "code"),
    "urara": (),
}


def _norm_cat(s: str) -> str:
    """分类名归一化：兼容 "Knowledge--Memory" / "Knowledge & Memory" / "Workplace-And-Productivity" 等写法。"""
    s = (s or "").lower().replace("&", " and ")
    for ch in ("--", "-", "_", "/", ":"):
        s = s.replace(ch, " ")
    return " ".join(w for w in s.split() if w != "and")

def _fuse(servers: list, installed: dict) -> set:
    """融合：把已装 MCP 标注到索引条目（name 精确 / repo 名去 .git 后精确或 endswith + 分类限定）。
    返回未在源里找到的已装 server 名集合（由调用方补进索引）。"""
    matched_local = set()
    for s in servers:
        key = s["name"].lower()
        repo_name = s["repo"].rstrip("/").split("/")[-1].lower()
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        k_local = None
        if key in installed:
            k_local = installed[key]
        else:
            for k, orig in installed.items():
                if k in matched_local:
                    continue
                hints = _CAT_HINT.get(k, ())
                if not hints or any(_norm_cat(h) in _norm_cat(s["category"])
                                    for h in hints):
                    # 词边界匹配：blender-mcp/mcp-server-blender → blender ✓；godotlens → godot ✗
                    if repo_name == k or re.search(
                            rf"(^|[-_/\.]){re.escape(k)}([-_\.]|$)", repo_name):
                        k_local = orig
                        break
        if k_local:
            s["installed"], s["local_name"] = True, k_local
            matched_local.add(k_local.lower())
    return set(installed.keys()) - matched_local


def _load_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("索引不存在，请先运行 mcp_source_update")


def _env_probe() -> dict:
    env = {}
    for k, c in (("python", "python"), ("node", "node"), ("npx", "npx"),
                 ("go", "go"), ("git", "git"), ("uvx", "uvx")):
        env[k] = shutil.which(c) or ""
    if not env["python"]:
        for p in ("C:\\Python310\\python.exe", "C:\\Python313\\python.exe"):
            if Path(p).exists():
                env["python"] = p
                break
    env["venv_python"] = str(ROOT / ".venv" / "Scripts" / "python.exe")
    return env


_GH_SUBDIR_RE = re.compile(
    r"^(https?://(?:www\.)?github\.com/[^/]+/[^/]+)"
    r"(?:/(?:tree|blob)/[^/]+)?(?:/(.*))?$", re.I)


def _repo_split(url: str) -> tuple[str, str]:
    """把 GitHub URL 拆成 (仓库根 URL, 仓库内子目录)。

    索引里不少条目指向 monorepo 的子目录，形如
    https://github.com/owner/repo/tree/main/packages/xxx
    这种 URL 直接 git clone 必然 404，必须先还原成仓库根再克隆。
    """
    u = (url or "").strip()
    u = re.sub(r"[?#].*$", "", u).rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    m = _GH_SUBDIR_RE.match(u)
    if not m:
        return u, ""
    return m.group(1), (m.group(2) or "").strip("/")


def _extract_install_hints(repo_dir: Path, subdir: str = "") -> list:
    """从 clone 的 README 提取常见安装命令；monorepo 优先读子目录内的 README。"""
    hints = []
    bases = []
    sub = repo_dir / subdir if subdir else None
    if sub is not None and sub.is_dir():
        bases.append(sub)
    bases.append(repo_dir)
    for base in bases:
        got = False
        for name in ("README.md", "README.MD", "readme.md", "README"):
            f = base / name
            if not f.exists():
                continue
            try:
                t = f.read_text(encoding="utf-8", errors="replace")[:200_000]
            except Exception:
                continue
            for pat, kind in _INSTALL_PATTERNS:
                for m in re.findall(pat, t, re.I):
                    cmd = m.strip()
                    if cmd not in hints:
                        hints.append(cmd)
            got = True
            break
        if got and hints:
            break
    return hints[:6]


@tool(
    "mcp_source_update",
    "刷新 MCP 源目录：从 punkpeye/awesome-mcp-servers（mcpservers.org 主站数据源）拉取最新清单，"
    "解析为 data/mcp_index.json，并与 config/mcp.json 已装 MCP 融合标注 installed。"
    "周期自动更新（每周日 03:00 计划任务）；也可随时手动刷新。"
    "【流程】本工具刷新目录 → mcp_source_search 找能力 → mcp_source_install 拉取安装。",
    {
        "type": "object",
        "properties": {},
    },
    group="工具工程",
)
def run_update() -> dict:
    try:
        text = _fetch_readme()
        servers, categories = _parse_readme(text)
        installed = _load_installed()
        unmatched = _fuse(servers, installed)
        # 源里没有的自建 MCP（urara/ffmpeg/graphify/edit2docs 等）补进索引，保证清单完整
        for k in sorted(unmatched):
            servers.append({
                "name": installed[k], "repo": "", "category": "本地自建",
                "desc": "素月自建/本地定制的 MCP（config/mcp.json 已登记）",
                "lang": None, "scope": "local", "os": "windows",
                "official": False, "installed": True, "local_name": installed[k],
            })
        if "本地自建" not in categories:
            categories.append("本地自建")
        payload = {
            "source": "punkpeye/awesome-mcp-servers",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(servers),
            "installed_count": sum(1 for s in servers if s["installed"]),
            "categories": categories,
            "servers": servers,
        }
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        # 滚动存档：带时间戳写历史，只保留最近 HISTORY_KEEP 份，多的替换旧的
        now = datetime.now()
        INDEX_HISTORY.mkdir(parents=True, exist_ok=True)
        hist_name = f"mcp_index_{now.strftime('%Y%m%d_%H%M%S')}.json"
        (INDEX_HISTORY / hist_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        olds = sorted(INDEX_HISTORY.glob("mcp_index_*.json"))
        removed = []
        for f in olds[:-HISTORY_KEEP]:
            f.unlink(missing_ok=True)
            removed.append(f.name)
        return {
            "ok": True,
            "count": len(servers),
            "installed_count": payload["installed_count"],
            "categories_count": len(categories),
            "index": str(INDEX),
            "history": str(INDEX_HISTORY / hist_name),
            "history_kept": len(olds) - len(removed),
            "removed": removed,
            "updated_at": payload["updated_at"],
            "提示": "已融合已装 MCP（installed=true）。用 mcp_source_search 按关键词/分类找能力。",
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _gh_cache_load() -> dict:
    try:
        d = json.loads(GH_CACHE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _gh_cache_flush(cache: dict) -> None:
    try:
        if len(cache) > GH_CACHE_MAX:
            keep = sorted(cache.items(), key=lambda kv: kv[1].get("ts", 0),
                          reverse=True)[:GH_CACHE_MAX]
            cache.clear()
            cache.update(dict(keep))
        GH_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception:
        pass


def _gh_fmt(ent: dict, cached: bool, stale: bool = False) -> dict:
    days = None
    pushed = ent.get("pushed_at")
    if pushed:
        try:
            days = (datetime.utcnow()
                    - datetime.strptime(pushed, "%Y-%m-%dT%H:%M:%SZ")).days
        except Exception:
            days = None
    out = {"stars": ent.get("stars"), "pushed_days_ago": days,
           "archived": bool(ent.get("archived"))}
    if cached:
        out["stats_cached"] = True
        if stale:
            out["stats_stale"] = True
    return out


def _gh_stats(repo: str, cache: dict | None = None) -> dict:
    """Repo stars / last-push-days / archived, with a 24h local cache.

    Hit within TTL -> served from cache, no network call (anonymous core API
    allows only 60 req/h). On failure, fall back to stale cache if present.
    Never raises; errors are reported as stats_error.
    """
    m = re.match(r"https://github\.com/([^/]+)/([^/#]+)", repo or "")
    if not m:
        return {"stats_error": "not a github repo"}
    key = f"{m.group(1)}/{m.group(2)}"
    now_ts = time.time()
    if cache is not None:
        ent = cache.get(key)
        if ent and (now_ts - ent.get("ts", 0)) < GH_CACHE_TTL:
            return _gh_fmt(ent, cached=True)
    api = f"https://api.github.com/repos/{key}"
    try:
        req = urllib.request.Request(api, headers={
            "User-Agent": "suyue-mcp-source/1.0",
            "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.load(r)
        ent = {"stars": d.get("stargazers_count"), "pushed_at": d.get("pushed_at"),
               "archived": bool(d.get("archived")), "ts": now_ts}
        if cache is not None:
            with _GH_LOCK:
                cache[key] = ent
                _gh_cache_flush(cache)
        return _gh_fmt(ent, cached=False)
    except Exception as e:
        if cache is not None:
            ent = cache.get(key)
            if ent:
                return _gh_fmt(ent, cached=True, stale=True)
        return {"stats_error": str(e)[:80]}


@tool(
    "mcp_source_search",
    "在 MCP 源目录（data/mcp_index.json）里按关键词/分类搜索可用 MCP 服务器，"
    "返回候选的名称/GitHub 仓库/分类/描述/语言/官方标记，并标注是否已安装（installed）。"
    "【流程】mcp_source_update 刷新目录后 → 本工具搜索 → 挑中后 mcp_source_install 拉取。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "搜索关键词（匹配名称/描述/仓库），如 video / browser / database / qr"},
            "category": {"type": "string",
                         "description": "可选。按分类过滤，如 Browser Automation / Multimedia Process / Knowledge & Memory"},
            "limit": {"type": "integer", "description": "返回条数上限，默认 15，最大 200"},
            "installed_only": {"type": "boolean", "description": "只看已安装的，默认 false"},
            "with_stats": {"type": "boolean",
                           "description": "可选。为返回的候选项查 GitHub，附 stars/最后推送距今天数/是否归档，"
                                          "用于在 4000+ 长尾里快速筛质量（默认 false；结果本地缓存 24h，重复查询不耗配额）"},
        },
    },
    group="工具工程",
)
def run_search(query: str = "", category: str = "", limit: int = 15,
               installed_only: bool = False, with_stats: bool = False) -> dict:
    try:
        idx = _load_index()
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    q = query.strip().lower()
    hits = []
    for s in idx.get("servers", []):
        if installed_only and not s["installed"]:
            continue
        if category and _norm_cat(s["category"]) != _norm_cat(category):
            continue
        if q:
            hay = f"{s['name']} {s['repo']} {s['desc']} {s['category']}".lower()
            if q not in hay:
                continue
        hits.append(s)
    hits.sort(key=lambda x: (not x["installed"], not x["official"]))
    matched = len(hits)                                  # 截断前的真实命中总数
    hits = [dict(s) for s in hits[: max(1, min(limit, 200))]]
    if with_stats and hits:
        from concurrent.futures import ThreadPoolExecutor
        cache = _gh_cache_load()                         # 24h cache, repeat hits cost nothing
        with ThreadPoolExecutor(max_workers=4) as ex:    # lower burst, avoid secondary limit
            stats = list(ex.map(lambda s: _gh_stats(s["repo"], cache), hits))
        for s, st in zip(hits, stats):
            s.update(st)
    return {
        "ok": True,
        "total_matched": matched,                        # 截断前真实命中数（旧版误为返回条数）
        "returned": len(hits),
        "query": query, "category": category,
        "results": hits,
        "提示": "挑中后 mcp_source_install 传 name 拉取；已装(installed=true)的直接 mcp_connect 激活。"
                " total_matched 为截断前真实命中数，返回条数受 limit 限制。",
    }


@tool(
    "mcp_source_install",
    "从 MCP 源拉取一个 MCP 服务器到本地：环境判定（python/node/go/git 是否可用）→ "
    "git clone 到 mcp-service/servers/<名称>/ → 读取其 README 提取安装命令建议。"
    "【注意】clone 与提取指引只是第一步：先按指引安装并实测可用，测试通过后才写入 mcp.json "
    "（或告诉我帮你登记），再 mcp_scan 同步。不结实的不进名单。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "索引里的服务器名（来自 mcp_source_search）"},
            "repo": {"type": "string", "description": "或直接传 GitHub 仓库 URL，如 https://github.com/xxx/yyy"},
        },
    },
    group="工具工程",
)
def run_install(name: str = "", repo: str = "") -> dict:
    if not name and not repo:
        return {"ok": False, "error": "需要 name 或 repo 参数"}
    try:
        idx = _load_index()
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    target = None
    if name:
        for s in idx.get("servers", []):
            if s["name"].lower() == name.lower():
                target = s
                break
        if not target:
            return {"ok": False, "error": f"索引里没有 {name}，先 mcp_source_update 或换 repo 直传"}
        if target.get("installed"):
            return {"ok": True, "already_installed": True, "local_name": target["local_name"],
                    "提示": "已装，直接 mcp_connect 激活即可"}
    elif repo:
        target = {"name": repo.rstrip("/").split("/")[-1], "repo": repo, "desc": "", "category": ""}
    root_url, subdir = _repo_split(target.get("repo", ""))
    if not root_url:
        return {"ok": False, "error": f"无法解析仓库地址: {target.get('repo')!r}"}
    env = _env_probe()
    env["repo_root"] = root_url
    env["subdir"] = subdir
    if not env["git"]:
        return {"ok": False, "error": "本机没有 git，无法 clone（git 是安装前置）"}
    safe = re.sub(r"[^a-z0-9_-]", "-", target["name"].lower()).strip("-") or "mcp-server"
    dest = SERVERS_DIR / safe
    if dest.exists():
        return {"ok": True, "path": str(dest), "already_cloned": True, "env": env,
                "repo_root": root_url, "subdir": subdir,
                "hints": _extract_install_hints(dest, subdir)}
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["git", "clone", "--depth", "1", root_url, str(dest)],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        return {"ok": False, "error": f"git clone 失败: {e}"}
    if r.returncode != 0:
        return {"ok": False, "error": f"git clone 失败: {r.stderr[-500:]}"}
    hints = _extract_install_hints(dest, subdir)
    return {
        "ok": True,
        "name": target["name"],
        "repo": target["repo"],
        "repo_root": root_url,
        "subdir": subdir,
        "path": str(dest),
        "env": env,
        "install_hints": hints,
        "next": "按 install_hints（或该仓库 README）安装依赖并实测一个工具调用；"
                "测试通过后写入 config/mcp.json 登记，再 mcp_scan 同步。",
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="MCP 源目录管理")
    ap.add_argument("action", choices=["update", "search", "install"])
    ap.add_argument("--query", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--repo", default="")
    ap.add_argument("--limit", type=int, default=15)
    a = ap.parse_args()
    if a.action == "update":
        print(json.dumps(run_update(), ensure_ascii=False, indent=1))
    elif a.action == "search":
        print(json.dumps(run_search(a.query, a.category, a.limit), ensure_ascii=False, indent=1))
    elif a.action == "install":
        print(json.dumps(run_install(a.name, a.repo), ensure_ascii=False, indent=1))
