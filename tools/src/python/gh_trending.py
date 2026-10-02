"""内置工具：gh_trending —— GitHub 热门项目聚合 + 本地缓存（15 天过期清理）。

设计意图：把「抓 GitHub Trending → 解析结构化数据（★总数 / 周期新增★ / 语言 / 简介）
→ 落本地缓存 → 半月过期清理」固化成一条常驻通道。

为什么需要它：net_fetch 抓 trending 页时正文提取器会把结构化字段丢掉，只剩仓库名+描述
（实测 2026-10-02：★数、语言全丢）。本工具直接正则解析 HTML 的 <article class="Box-row"> 块，
把 ★ / 周期增量 / 语言 / forks 都取出来，并按天落盘缓存，随时可回看。

缓存策略：
- 落 library/cache/gh_trending/YYYY-MM-DD[_since][_lang].json（library/ 已 gitignore）
- 同参数「当天」重复调用直接命中缓存（refresh=True 强制重取）
- 每次调用自动清理 mtime 超过 15 天的缓存文件

用法：
- gh_trending()                     今日全语言榜（命中缓存则不联网）
- gh_trending(language="python")    今日 Python 榜
- gh_trending(since="weekly")       本周榜（weekly / monthly）
- gh_trending(refresh=True)         强制重新抓取
- gh_trending(view=True)            只读本地缓存，全程不联网

坑（已修）：★数字元素里含 <svg viewBox="0 0 16 16">，若直接对原串取首个数字会得到 16。
故取数前必须先 _txt() 去标签——见 _num(_txt(...))。
"""
from __future__ import annotations

import html as _html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tools.base import tool

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "library" / "cache" / "gh_trending"
_PROXY = "http://127.0.0.1:7897"
_HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) suyue-gh-trending/1.0"}
_EXPIRE_DAYS = 15
_SINCE = {"daily": "today", "weekly": "this week", "monthly": "this month"}


def _get(url: str, timeout: int = 40):
    """先直连，失败回落代理。返回 (html|None, err)。"""
    try:
        req = urllib.request.Request(url, headers=_HDRS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), ""
    except Exception as exc_direct:
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": _PROXY, "https": _PROXY}))
            with opener.open(urllib.request.Request(url, headers=_HDRS), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace"), ""
        except Exception as exc_proxy:
            return None, "direct:%s | proxy:%s" % (exc_direct, exc_proxy)


def _txt(s: str) -> str:
    """去 HTML 标签并反转义。"""
    s = re.sub(r"<[^>]+>", "", s or "")
    return _html.unescape(s).strip()


def _num(s: str) -> int:
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else 0


def _parse(page: str, since: str = "daily"):
    """解析 trending 页的 Box-row 块。页面结构变了会返回空列表（不抛异常）。"""
    blocks = re.split(r'<article[^>]*class="[^"]*Box-row', page)[1:]
    if not blocks:  # 结构兜底
        blocks = re.split(r"<article", page)[1:]
    out = []
    key = _SINCE.get(since, "today")
    period_re = re.compile(r"([\d,]+)\s+stars\s+" + key.replace(" ", r"\s+"))
    for b in blocks[:60]:
        m = re.search(r'<h2[^>]*>.*?href="/([^"/]+/[^"/]+)"', b, re.S)
        if not m:
            continue
        repo = m.group(1)
        d = re.search(r'<p class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', b, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', b)
        st = re.search(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', b, re.S)
        fk = re.search(r'href="/[^"]+/forks"[^>]*>(.*?)</a>', b, re.S)
        per = period_re.search(b)
        out.append({
            "repo": repo,
            "url": "https://github.com/" + repo,
            "desc": _txt(d.group(1)) if d else "",
            "language": _txt(lang.group(1)) if lang else "",
            # 必须先 _txt() 去标签：★ 元素内嵌 <svg viewBox="0 0 16 16">，
            # 直接对原串取首个数字会误得 16。
            "stars": _num(_txt(st.group(1))) if st else 0,
            "forks": _num(_txt(fk.group(1))) if fk else 0,
            "stars_period": _num(per.group(1)) if per else 0,
            "period": key,
        })
    return out


def _clean() -> int:
    """清理超过 15 天的缓存文件，返回删除数。"""
    if not _CACHE.exists():
        return 0
    cutoff = time.time() - _EXPIRE_DAYS * 86400
    n = 0
    for f in _CACHE.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


@tool(
    "gh_trending",
    "GitHub 热门项目聚合+本地缓存：抓 trending 页并解析出 ★总数/周期新增★/语言/简介，"
    "落 library/cache/gh_trending/，自动清理超过 15 天的缓存。"
    "since=daily|weekly|monthly 时间窗，language 按语言筛（python/go/…），"
    "refresh=True 强制重取，view=True 只读本地缓存不联网。",
    {
        "type": "object",
        "properties": {
            "since": {"type": "string", "enum": ["daily", "weekly", "monthly"],
                      "description": "时间窗：daily=日榜 / weekly=周榜 / monthly=月榜"},
            "language": {"type": "string",
                         "description": "语言筛选，如 python、go、typescript、rust；留空为全语言榜"},
            "limit": {"type": "number", "description": "返回条数上限，默认 25"},
            "refresh": {"type": "boolean", "description": "true=忽略当日缓存强制重取"},
            "view": {"type": "boolean", "description": "true=只读本地缓存（不联网），返回最近一次缓存"},
        },
    },
    group="网络",
)
def run(since: str = "daily", language: str = "", limit: int = 25,
        refresh: bool = False, view: bool = False) -> dict:
    since = since if since in _SINCE else "daily"
    lang = (language or "").strip().lower()
    _CACHE.mkdir(parents=True, exist_ok=True)
    cleaned = _clean()

    if view:
        files = sorted(_CACHE.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return {"ok": False, "error": "本地无缓存", "cache_dir": str(_CACHE)}
        latest = files[0]
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": "缓存读取失败: %s" % exc, "cache_file": str(latest)}
        return {"ok": True, "mode": "view", "cache_file": str(latest),
                "fetched_at": data.get("fetched_at"), "since": data.get("since"),
                "language": data.get("language"), "count": len(data.get("items", [])),
                "cache_files": len(files), "expire_days": _EXPIRE_DAYS, "cleaned": cleaned,
                "items": data.get("items", [])[:int(limit)]}

    tag = "%s_%s%s" % (time.strftime("%Y-%m-%d"), since, ("_" + lang) if lang else "")
    fp = _CACHE / (tag + ".json")

    if fp.exists() and not refresh:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            return {"ok": True, "mode": "cache", "cache_file": str(fp),
                    "fetched_at": data.get("fetched_at"), "since": since,
                    "language": lang or "all", "count": len(data.get("items", [])),
                    "cleaned": cleaned, "note": "命中当日缓存；refresh=True 可强制重取",
                    "items": data.get("items", [])[:int(limit)]}
        except Exception:
            pass  # 缓存坏了就当没有，走网络

    url = "https://github.com/trending"
    if lang:
        url += "/" + urllib.parse.quote(lang)
    url += "?since=" + since

    page, err = _get(url)
    if page is None:
        return {"ok": False, "error": "抓取失败", "detail": err, "url": url,
                "hint": "GitHub 直连与代理均失败；可稍后重试或检查网络"}
    items = _parse(page, since)
    if not items:
        return {"ok": False, "error": "解析到 0 条（GitHub 页面结构可能已变）",
                "url": url, "page_chars": len(page),
                "hint": "检查 <article class=\"Box-row\"> 结构是否仍在"}

    rec = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "since": since,
           "language": lang or "all", "url": url, "count": len(items), "items": items}
    fp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "mode": "network", "cache_file": str(fp),
            "fetched_at": rec["fetched_at"], "since": since, "language": lang or "all",
            "count": len(items), "cleaned": cleaned, "items": items[:int(limit)]}
