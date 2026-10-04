"""内置工具：news_feed —— 多站点资讯聚合 + 本地 HTML 阅读页 + 自定义周期。

设计意图：TOP 站就那几家，不现搜、不当无头苍蝇。把「多站点抓取 -> 提取最新条目
-> 按天落本地缓存 -> 渲染成单文件 HTML 阅读页」固化成常驻通道；站点清单可随时增删。

配置（唯一权威源，机器可读，首次运行自动生成）：
  config/feeds/sites.json
  - settings.interval_hours : 全局默认抓取周期（小时），每站可用 interval_hours 覆盖
  - settings.expire_days    : 缓存过期清理（默认 15 天）
  - settings.per_site_limit : 每站保留条目数
  - channel: fetch=HTTP 直取 / browser=需真浏览器(跳过并如实标注) / gh_trending=复用本机热榜工具

缓存：library/cache/feeds/<YYYY-MM-DD>/<site_id>.json（同站同日覆盖）
产物：library/feeds/index.html（单文件、内联样式、双击即看）

用法：
- news_feed()                        按周期抓取到期站点并重渲染 HTML
- news_feed(refresh=True)            忽略周期，全部强制重抓
- news_feed(only="anthropic,qbitai") 只处理指定站点
- news_feed(action="list")           列出站点与上次抓取时间
- news_feed(action="add", name="X", url="https://...", category="...")
- news_feed(action="render")         只重渲染 HTML，不联网
- news_feed(open_browser=True)       完成后自动打开 HTML

已知局限（如实）：通用提取靠 <a> 启发式，JS 渲染站（SPA）抓不到条目，会返回 ok=false
并提示改 channel=browser；此时条目为空，不编造。
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tools.base import tool

_ROOT = Path(__file__).resolve().parents[3]
_CONF = _ROOT / "config" / "feeds" / "sites.json"
_CACHE = _ROOT / "library" / "cache" / "feeds"
_OUT_DIR = _ROOT / "library" / "feeds"
_HTML = _OUT_DIR / "index.html"
_PROXY = "http://127.0.0.1:7897"
_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) suyue-news-feed/1.0",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_DEFAULT_SETTINGS = {
    "interval_hours": 6,
    "expire_days": 15,
    "per_site_limit": 12,
    "open_browser": False,
}

# 2026-10-02 由 library/env/sites.md 搬运并结构化；channel 为当日实测结论。
_DEFAULT_SITES = [
    {"id": "anthropic", "name": "Anthropic Newsroom", "url": "https://www.anthropic.com/news", "category": "AI厂商·海外", "channel": "fetch"},
    {"id": "google_ai", "name": "Google Blog · AI", "url": "https://blog.google/technology/ai/", "category": "AI厂商·海外", "channel": "fetch"},
    {"id": "deepmind", "name": "Google DeepMind Blog", "url": "https://deepmind.google/discover/blog/", "category": "AI厂商·海外", "channel": "browser"},
    {"id": "meta_ai", "name": "Meta AI Blog", "url": "https://ai.meta.com/blog/", "category": "AI厂商·海外", "channel": "fetch"},
    {"id": "mistral", "name": "Mistral News", "url": "https://mistral.ai/news/", "category": "AI厂商·海外", "channel": "fetch"},
    {"id": "openai", "name": "OpenAI News", "url": "https://openai.com/news", "category": "AI厂商·海外", "channel": "browser"},
    {"id": "microsoft_ai", "name": "Microsoft AI Blog", "url": "https://blogs.microsoft.com/ai/", "category": "AI厂商·海外", "channel": "browser"},
    {"id": "xai", "name": "xAI News", "url": "https://x.ai/news", "category": "AI厂商·海外", "channel": "browser"},
    {"id": "nvidia", "name": "NVIDIA Blog", "url": "https://blogs.nvidia.com/", "category": "AI厂商·海外", "channel": "fetch"},
    {"id": "qwen", "name": "阿里 Qwen Blog", "url": "https://qwenlm.github.io/blog/", "category": "AI厂商·国内", "channel": "fetch"},
    {"id": "deepseek", "name": "DeepSeek 官方文档/新闻", "url": "https://api-docs.deepseek.com/news/", "category": "AI厂商·国内", "channel": "fetch"},
    {"id": "bytedance_seed", "name": "字节 Seed", "url": "https://seed.bytedance.com/", "category": "AI厂商·国内", "channel": "fetch"},
    {"id": "bigmodel", "name": "智谱 BigModel", "url": "https://www.bigmodel.cn/", "category": "AI厂商·国内", "channel": "browser"},
    {"id": "moonshot", "name": "月之暗面 Kimi", "url": "https://moonshotai.github.io/", "category": "AI厂商·国内", "channel": "browser"},
    {"id": "huggingface", "name": "HuggingFace Blog", "url": "https://huggingface.co/blog", "category": "开发者·数据源", "channel": "fetch"},
    {"id": "github_trending", "name": "GitHub Trending", "url": "https://github.com/trending", "category": "开发者·数据源", "channel": "gh_trending"},
    {"id": "arxiv_cs_ai", "name": "arXiv cs.AI 最新", "url": "https://arxiv.org/list/cs.AI/recent", "category": "开发者·数据源", "channel": "fetch"},
    {"id": "tomshardware", "name": "Tom's Hardware", "url": "https://www.tomshardware.com/", "category": "硬件·科技媒体", "channel": "fetch"},
    {"id": "simonwillison", "name": "Simon Willison's Weblog", "url": "https://simonwillison.net/", "category": "独立观察", "channel": "fetch"},
    {"id": "hackernews", "name": "Hacker News", "url": "https://news.ycombinator.com/", "category": "独立观察", "channel": "fetch"},
    {"id": "qbitai", "name": "量子位", "url": "https://www.qbitai.com/", "category": "中文媒体", "channel": "fetch"},
    {"id": "jiqizhixin", "name": "机器之心", "url": "https://www.jiqizhixin.com/", "category": "中文媒体", "channel": "browser"},
]

_A_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TIME_RE = re.compile(
    r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}\s*(?:小时|分钟|天)前|yesterday|today|"
    r"\d+\s*(?:hours?|days?|minutes?|weeks?)\s+ago|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s*\d{4})?)",
    re.I)
_NAV = {"登录", "注册", "首页", "更多", "全部", "关于", "联系", "隐私", "条款", "广告", "合作",
        "菜单", "搜索", "订阅", "下载", "返回", "上一页", "下一页", "设置", "帮助", "评论",
        "分享", "关注", "登录/注册", "sign in", "sign up", "log in", "login", "menu",
        "privacy", "terms", "about", "contact", "more", "home", "subscribe", "register"}


def _esc(s):
    return _html.escape(str(s or ""), quote=True)


def _txt(s):
    s = _SCRIPT_RE.sub(" ", s or "")
    s = _TAG_RE.sub(" ", s)
    return _WS_RE.sub(" ", _html.unescape(s)).strip()


_GH_TAG_RE = re.compile(r"/releases/tag/([^/?#]+)")  # GitHub release 版本号（常短于标题长度门槛）


def _clean_title(t):
    """列表页结构常是「标题 时间 摘要」——取时间串之前的部分作为标题。"""
    if not t:
        return t
    head = _TIME_RE.split(t)[0].strip(" ·-|")
    if 6 <= len(head) <= 160:
        return head
    return t if len(t) <= 160 else t[:157] + "..."


def _host(u):
    try:
        return urllib.parse.urlparse(u).netloc.lower()
    except Exception:
        return ""


def _get(url, timeout=30):
    """先直连，失败回落本地代理。返回 (html|None, err)。"""
    try:
        req = urllib.request.Request(url, headers=_HDRS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), ""
    except Exception as e1:
        try:
            op = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": _PROXY, "https": _PROXY}))
            with op.open(urllib.request.Request(url, headers=_HDRS), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace"), ""
        except Exception as e2:
            return None, "direct:%s | proxy:%s" % (str(e1)[:110], str(e2)[:110])


def _extract(page, site, limit):
    """通用列表页提取 + 站点结构过滤（两段式）。

    1) 收集候选 <a>：过滤导航词/短文本/非链接；
    2) 结构过滤：入口页本身是栏目页（path 非空）时，只保留其子路径链接
       —— 文章页几乎总是栏目子页，而 header/footer 导航是平级或更浅；
       入口是站点首页（path 为空）时退回通用过滤。
    结果不足 3 条则退回原始候选，宁有过剩不空手。
    """
    base = site["url"]
    base_host = _host(base)
    bpath = urllib.parse.urlparse(base).path.rstrip("/")
    cands, seen, tcount = [], set(), {}
    for m in _A_RE.finditer(page):
        href, raw = m.group(1), m.group(2)
        title = _clean_title(_txt(raw))
        _tag = _GH_TAG_RE.search(href)
        if _tag:
            # 版本号本身短于门槛（v0.1.15=7 字符）会被丢弃、再由同 URL 的
            # "Read more" 顶替；URL 是权威，故从 tag 反推标题。
            title = urllib.parse.unquote(_tag.group(1))
        elif not (8 <= len(title) <= 160):
            continue
        if title.lower().strip() in _NAV:
            continue
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        u = urllib.parse.urljoin(base, href)
        if not u.startswith("http"):
            continue
        u = u.split("#")[0].rstrip("/")
        if not u or u == base.rstrip("/") or u in seen:
            continue
        seen.add(u)
        tcount[title] = tcount.get(title, 0) + 1
        tail = _txt(page[m.end():m.end() + 400])
        tm = _TIME_RE.search(tail)
        cands.append({"title": title, "url": u, "time": tm.group(1) if tm else "",
                      "path": urllib.parse.urlparse(u).path,
                      "same_host": _host(u) == base_host,
                      "is_tag": bool(_tag)})

    sub = []
    if bpath:
        for c in cands:
            if c["path"].startswith(bpath + "/") and len(c["path"]) > len(bpath) + 1:
                sub.append(c)
    picked = sub if len(sub) >= 3 else cands

    out = []
    for c in picked:
        if tcount.get(c["title"], 0) > 2:
            continue
        depth = len([x for x in c["path"].split("/") if x])
        if depth <= 1 and not bpath:
            continue
        if len(c["title"]) < 16 and not c.get("is_tag"):
            continue
        out.append(c)
    if len(out) < 3:
        out = picked if len(picked) >= 3 else cands
    for o in out:
        o.pop("path", None)
        o.pop("same_host", None)
        o.pop("is_tag", None)
    return out[:limit]


def _gh_items(limit):
    """复用本机 gh_trending 工具（结构化数据，避免 net_fetch 丢字段）。"""
    try:
        import importlib.util
        p = Path(__file__).with_name("gh_trending.py")
        spec = importlib.util.spec_from_file_location("_gh_trending_mod", str(p))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.run(limit=limit)
        if not r.get("ok"):
            return [], "gh_trending 返回失败: %s" % str(r.get("error"))[:120]
        out = []
        for i in r.get("items", []):
            out.append({
                "title": "%s  [star]%s (%s)" % (i.get("repo", ""), i.get("stars", ""),
                                                i.get("stars_period") or ""),
                "url": i.get("url", ""),
                "time": i.get("language", ""),
            })
        return out, ""
    except Exception as exc:
        return [], "gh_trending 复用失败: %s" % str(exc)[:160]


def _load_conf():
    if not _CONF.exists():
        c = {"settings": dict(_DEFAULT_SETTINGS),
             "sites": [dict(s) for s in _DEFAULT_SITES]}
        _CONF.parent.mkdir(parents=True, exist_ok=True)
        _CONF.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        return c
    try:
        c = json.loads(_CONF.read_text(encoding="utf-8"))
    except Exception:
        c = {}
    c.setdefault("settings", {})
    for k, v in _DEFAULT_SETTINGS.items():
        c["settings"].setdefault(k, v)
    c.setdefault("sites", [])
    for s in c["sites"]:
        s.setdefault("channel", "fetch")
        s.setdefault("enabled", True)
        s.setdefault("interval_hours", None)
        s.setdefault("category", "未分类")
    return c


def _save_conf(c):
    _CONF.parent.mkdir(parents=True, exist_ok=True)
    _CONF.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_file(site_id, day=None):
    return _CACHE / (day or time.strftime("%Y-%m-%d")) / (site_id + ".json")


def _last_fetch(site_id):
    if not _CACHE.exists():
        return 0.0
    best = 0.0
    for f in _CACHE.glob("*/%s.json" % site_id):
        try:
            best = max(best, f.stat().st_mtime)
        except OSError:
            pass
    return best


def _clean(expire_days):
    """清理整目录过期（目录内最新文件 mtime 早于 cutoff）。返回清理的目录数。"""
    if not _CACHE.exists():
        return 0
    cutoff = time.time() - int(expire_days) * 86400
    n = 0
    for d in _CACHE.iterdir():
        if not d.is_dir():
            continue
        try:
            stamps = [f.stat().st_mtime for f in d.glob("*.json")]
        except OSError:
            continue
        if not stamps:
            continue
        if max(stamps) < cutoff:
            for f in d.glob("*.json"):
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                d.rmdir()
                n += 1
            except OSError:
                pass
    return n


def _fetch_site(site, limit, timeout=30):
    ch = site.get("channel", "fetch")
    if ch == "browser":
        return {"ok": False, "note": "标注为需真浏览器通道（sites.md 实测结论），本工具跳过", "items": []}
    if ch == "gh_trending":
        items, err = _gh_items(limit)
        if err:
            return {"ok": False, "note": err, "items": []}
        return {"ok": True, "items": items, "note": ""}
    page, err = _get(site["url"], timeout)
    if page is None:
        return {"ok": False, "note": "抓取失败 " + err, "items": []}
    items = _extract(page, site, limit)
    if not items:
        return {"ok": False,
                "note": "抓到页面（%d 字节）但未提取到条目；可能是 JS 渲染页，建议改 channel=browser" % len(page),
                "items": []}
    return {"ok": True, "items": items, "note": ""}


def _render(days=7, only_ids=None):
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - float(days) * 86400
    data = []
    if _CACHE.exists():
        for f in sorted(_CACHE.glob("*/*.json")):
            if only_ids and f.stem not in only_ids:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    continue
                data.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
    groups = {}
    for d in data:
        groups.setdefault(d.get("category", "未分类"), []).append(d)
    total = sum(len(d.get("items", [])) for d in data)
    okn = len([d for d in data if d.get("ok")])
    ts = time.strftime("%Y-%m-%d %H:%M")
    p = []
    p.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    p.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    p.append("<title>素月 · 资讯聚合</title><style>")
    p.append("body{margin:0;background:#f6f7f9;color:#1c1f23;font:15px/1.6 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif}")
    p.append("header{padding:26px 28px 18px;background:#fff;border-bottom:1px solid #e6e8eb}")
    p.append("h1{margin:0;font-size:21px;letter-spacing:.5px}")
    p.append(".meta{margin-top:6px;color:#7a8290;font-size:13px}")
    p.append("section{padding:14px 28px 4px}")
    p.append("h2{font-size:13px;color:#5f6875;margin:16px 0 8px;font-weight:600;letter-spacing:1px}")
    p.append(".card{background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:12px 14px;margin:0 0 10px}")
    p.append(".ch{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}")
    p.append(".ch a{color:#125aa8;text-decoration:none;font-weight:600;font-size:15px}")
    p.append(".ch a:hover{text-decoration:underline}")
    p.append(".t{color:#9aa2ae;font-size:12px}")
    p.append(".tag{background:#eef2f7;color:#5f6875;border-radius:4px;padding:0 6px;font-size:11px}")
    p.append(".err{color:#b4442f;font-size:12.5px;margin-top:6px}")
    p.append("ul{margin:8px 0 2px;padding-left:0;list-style:none}")
    p.append("li{padding:5px 0;border-top:1px dashed #eef0f3}")
    p.append("li a{color:#1c1f23;text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}")
    p.append("li a:hover{color:#125aa8;text-decoration:underline}")
    p.append(".tm{color:#9aa2ae;font-size:12px;margin-left:7px;white-space:nowrap}")
    p.append("footer{padding:18px 28px 40px;color:#9aa2ae;font-size:12px}")
    p.append("</style></head><body>")
    p.append("<header><h1>素月 · 资讯聚合</h1><div class='meta'>生成 %s &nbsp;|&nbsp; %d 站有数据（%d 成功）&nbsp;|&nbsp; %d 条 &nbsp;|&nbsp; 窗口最近 %s 天</div></header>"
             % (_esc(ts), len(data), okn, total, _esc(int(days))))
    if not data:
        p.append("<section><div class='card'>暂无缓存。先运行 news_feed() 抓取。</div></section>")
    for cat in sorted(groups):
        p.append("<section><h2>%s</h2>" % _esc(cat))
        for d in sorted(groups[cat], key=lambda x: x.get("name", "")):
            items = d.get("items", [])
            p.append("<div class='card'><div class='ch'><a href='%s' target='_blank'>%s</a>"
                     "<span class='tag'>%s</span><span class='t'>%s</span></div>"
                     % (_esc(d.get("url")), _esc(d.get("name")), _esc(d.get("channel", "")),
                        _esc((d.get("fetched_at") or "")[5:16])))
            if not d.get("ok"):
                p.append("<div class='err'>%s</div>" % _esc(d.get("note")))
            if items:
                p.append("<ul>")
                for i in items:
                    tm = ("<span class='tm'>%s</span>" % _esc(i.get("time"))) if i.get("time") else ""
                    p.append("<li><a href='%s' target='_blank'>%s</a>%s</li>"
                             % (_esc(i.get("url")), _esc(i.get("title")), tm))
                p.append("</ul>")
            p.append("</div>")
        p.append("</section>")
    p.append("<footer>由 news_feed 生成 · 站点清单可编辑 config/feeds/sites.json · 缓存 %d 天过期自动清理</footer>"
             % int(_DEFAULT_SETTINGS["expire_days"]))
    p.append("</body></html>")
    _HTML.write_text("\n".join(p), encoding="utf-8")
    return str(_HTML)


@tool(
    "news_feed",
    "多站点资讯聚合+本地 HTML 阅读页+自定义周期。抓取 config/feeds/sites.json 里的站点列表页，"
    "提取最新条目并按天落 library/cache/feeds/，渲染成单文件 library/feeds/index.html（双击即看）。"
    "站点可随时 add；每站可设独立 interval_hours 周期；缓存默认 15 天过期自动清理。"
    "action=fetch(默认)/list/add/remove/render，refresh 强制重抓，only 限定站点，open_browser 自动打开。",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["fetch", "list", "add", "remove", "render"],
                       "description": "fetch=按周期抓取并重渲染(默认) / list=列站点与上次抓取时间 / add=新增站点 / remove=停用站点 / render=只重渲染HTML不联网"},
            "refresh": {"type": "boolean", "description": "true=忽略抓取周期，强制全部重抓"},
            "only": {"type": "string", "description": "只处理指定站点 id（逗号分隔），如 anthropic,qbitai"},
            "days": {"type": "number", "description": "渲染最近多少天的缓存进 HTML，默认 7"},
            "open_browser": {"type": "boolean", "description": "true=完成后用系统默认浏览器打开 HTML"},
            "name": {"type": "string", "description": "action=add 时：站点名"},
            "url": {"type": "string", "description": "action=add 时：入口页 URL（news/blog 列表页，不要填具体文章）"},
            "category": {"type": "string", "description": "action=add 时：分类，如 'AI厂商·海外'"},
            "channel": {"type": "string", "enum": ["fetch", "browser", "gh_trending"],
                        "description": "action=add 时：fetch=HTTP直取 / browser=需真浏览器(跳过) / gh_trending=复用热榜工具"},
            "interval_hours": {"type": "number", "description": "action=add 时：该站独立抓取周期(小时)，留空跟随全局"},
        },
    },
    group="网络",
)
def run(action="fetch", refresh=False, only="", days=7, open_browser=False,
        name="", url="", category="未分类", channel="fetch", interval_hours=None):
    conf = _load_conf()
    st = conf["settings"]
    only_ids = [x.strip() for x in only.split(",") if x.strip()] if only else None

    if action == "list":
        out = []
        for s in conf["sites"]:
            lf = _last_fetch(s["id"])
            out.append({
                "id": s["id"], "name": s["name"], "category": s.get("category"),
                "channel": s.get("channel"), "enabled": s.get("enabled", True),
                "interval_hours": s.get("interval_hours") or st.get("interval_hours"),
                "last_fetch": time.strftime("%Y-%m-%d %H:%M", time.localtime(lf)) if lf else "",
            })
        return {"ok": True, "action": "list", "total": len(out),
                "settings": st, "conf": str(_CONF), "sites": out}

    if action == "add":
        if not name or not url:
            return {"ok": False, "error": "add 需提供 name 与 url"}
        for s in conf["sites"]:
            if s["url"].rstrip("/") == url.rstrip("/"):
                return {"ok": False, "error": "该 URL 已存在", "id": s["id"]}
        sid = re.sub(r"[^a-z0-9_]+", "_", (name or url).lower()).strip("_")[:30]
        if not sid:
            sid = "site%d" % int(time.time())
        if any(s["id"] == sid for s in conf["sites"]):
            sid = sid + "_%d" % int(time.time() % 1000)
        rec = {"id": sid, "name": name, "url": url, "category": category or "未分类",
               "channel": channel or "fetch", "enabled": True, "interval_hours": interval_hours}
        conf["sites"].append(rec)
        _save_conf(conf)
        return {"ok": True, "action": "add", "added": rec, "total": len(conf["sites"])}

    if action == "remove":
        if not only_ids:
            return {"ok": False, "error": "remove 需提供 only=<站点id>"}
        hit = []
        for s in conf["sites"]:
            if s["id"] in only_ids:
                s["enabled"] = False
                hit.append(s["id"])
        _save_conf(conf)
        return {"ok": True, "action": "remove", "disabled": hit}

    if action == "render":
        # 2026-10-02 补：render 是离线动作，但同样要清理过期缓存——
        # 否则长期只用 render（不联网）时缓存目录会无限增长。
        cleaned = _clean(st.get("expire_days", 15))
        path = _render(days)
        return {"ok": True, "action": "render", "html": path,
                "cleaned_expired_dirs": cleaned}

    # action == fetch
    target = [s for s in conf["sites"]
              if s.get("enabled", True) and (not only_ids or s["id"] in only_ids)]
    now = time.time()
    cleaned = _clean(st.get("expire_days", 15))
    limit = int(st.get("per_site_limit", 12))
    results, skipped = [], 0
    for s in target:
        iv = s.get("interval_hours") or st.get("interval_hours", 6)
        lf = _last_fetch(s["id"])
        if not refresh and lf and (now - lf) < float(iv) * 3600:
            skipped += 1
            continue
        r = _fetch_site(s, limit)
        rec = {"site_id": s["id"], "name": s["name"], "url": s["url"],
               "category": s.get("category", "未分类"), "channel": s.get("channel", "fetch"),
               "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "ok": r["ok"], "note": r["note"], "items": r["items"]}
        fp = _cache_file(s["id"])
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        results.append(rec)

    html = _render(days)
    opened = False
    if open_browser or st.get("open_browser"):
        try:
            os.startfile(str(_HTML))
            opened = True
        except Exception:
            opened = False

    okr = [r for r in results if r["ok"]]
    return {
        "ok": True, "action": "fetch", "fetched": len(results),
        "skipped_by_interval": skipped, "ok_sites": len(okr),
        "failed_sites": len(results) - len(okr),
        "total_items": sum(len(r["items"]) for r in results),
        "cleaned_expired_dirs": cleaned, "html": html, "opened": opened,
        "failed": [{"site": r["name"], "note": r["note"]} for r in results if not r["ok"]],
        "preview": [{"site": r["name"],
                     "top": (r["items"][0]["title"] if r["items"] else "")} for r in results if r["items"]][:8],
    }
