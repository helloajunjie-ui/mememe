"""内置工具：net_fetch —— 抓取 URL 并提取可读正文（trafilatura 成熟方案）。

从"返回原始 HTML、文本提取需另行处理"升级为"返回可读正文 + 元数据 + 正文图片"：
- 复用 lazyhuman-ai/websearch 的 web_fetch 原语（trafilatura 提取正文，自动处理
  标题/规范 URL/正文/摘要/图片列表），不重复造轮子。
- 原始 HTML 默认不返回（省 token），需要时 include_raw=True。
- include_images=True（默认）时返回正文图片列表（URL+alt，已过滤图标/占位/小图噪音），
  可配合 net_download 下载到 workspace 供对话展示。
- 超时/重定向/错误统一处理。

来源：https://github.com/lazyhuman-ai/websearch（MIT），clone 于 library/depot/vendor/websearch。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
# 外部搜索/正文组件（lazyhuman-ai/websearch, MIT）——2026-09-13 从 workspace 迁出：
# workspace 是临时区、不进备份也不进公开仓库，活依赖放那里 = 迁移/复活后工具瘫痪。
# 新址随私有备份（library/）存活；旧址保留作回退兼容，两边都没有才报缺失。
_WS_CANDIDATES = [
    os.path.join(_ROOT, "library", "depot", "vendor", "websearch"),
    os.path.join(_ROOT, "workspace", "tools_external", "websearch"),
]
_WS_DIR = next((p for p in _WS_CANDIDATES if os.path.isdir(p)), _WS_CANDIDATES[0])
if _WS_DIR not in sys.path:
    sys.path.insert(0, _WS_DIR)

from tools.base import tool  # noqa: E402

try:
    from websearch_service import web_fetch as _ws_fetch  # noqa: E402
    _WS_READY = True
    _WS_ERR = ""
except Exception as _e:  # noqa: BLE001
    _WS_READY = False
    _WS_ERR = str(_e)


def _reader_fetch(url: str, max_chars: int) -> dict:
    """r.jina.ai 阅读器兜底：把目标页转成干净正文。

    2026-10-22：本机实测 wikipedia/reuters/zhihu 这类站点直连会吃 401/403/429
    （不是墙，是反爬/IP 判黑），而 r.jina.ai 同一个 URL 能拿到 200 正文。
    这不是万能通道（有速率限制、也可能同样被挡），所以只作为"直连失败后的第二跳"。
    """
    import httpx
    with httpx.Client(timeout=25, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/plain,text/markdown,*/*",
    }) as c:
        r = c.get("https://r.jina.ai/" + url)
    r.raise_for_status()
    text = (r.text or "").strip()
    return {"ok": True, "url": url, "title": "", "text": text[:max_chars],
            "truncated": len(text) > max_chars, "total_chars": len(text),
            "via": "jina-reader"}


_CHALLENGE_MARKS = ("Just a moment", "cf-challenge", "Attention Required", "Checking your browser",
                    "Enable JavaScript and cookies to continue", "cf_chl_opt")


def _stealth_fetch(url: str, max_chars: int) -> dict:
    """curl_cffi 通道：用浏览器的 TLS/JA3 指纹重试一次。

    2026-10-22：Cloudflare 这类防护很大一部分按 **TLS 指纹** 判人，httpx 的指纹一眼就被认出。
    curl_cffi 走 BoringSSL + Chrome 指纹，实测 en.wikipedia.org 由 403 → 200（85KB）。
    对真正的 JS 挑战（Turnstile / cf_chl）它过不去——那是下一层（阅读器/真浏览器）的事。
    """
    from curl_cffi import requests as creq
    import trafilatura

    proxies = None
    env = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")}
    p = env.get("HTTPS_PROXY") or env.get("https_proxy") or env.get("HTTP_PROXY") or env.get("http_proxy")
    if p:
        proxies = {"http": p, "https": p}

    r = creq.get(url, impersonate="chrome", timeout=20, allow_redirects=True,
                 proxies=proxies,
                 headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    html = r.text or ""
    if r.status_code in (401, 403, 429, 451):
        return {"ok": False, "error": f"HTTP {r.status_code}",
                "challenge": any(m in html for m in _CHALLENGE_MARKS)}
    if any(m in html[:4000] for m in _CHALLENGE_MARKS):
        return {"ok": False, "error": "CF 人机挑战页（需要浏览器执行 JS）", "challenge": True}

    text = ""
    try:
        text = trafilatura.extract(html, include_comments=False, include_tables=True,
                                   favor_precision=True) or ""
    except Exception:  # noqa: BLE001
        text = ""
    if not text.strip():
        # trafilatura 提取不到（纯 JS 站）→ 退化为去标签文本
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            text = " ".join(soup.get_text(" ").split())
        except Exception:  # noqa: BLE001
            text = ""
    title = ""
    try:
        from bs4 import BeautifulSoup
        t = BeautifulSoup(html, "lxml").title
        title = t.get_text(strip=True) if t else ""
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "url": str(r.url), "title": title, "text": text[:max_chars],
            "truncated": len(text) > max_chars, "total_chars": len(text),
            "http_status": r.status_code, "via": "curl_cffi"}


def _flaresolverr_fetch(url: str, max_chars: int) -> dict:
    """FlareSolverr 通道：真浏览器解 Cloudflare JS 挑战，返回解出来的 HTML。

    默认不启用——它需要一个常驻服务（Docker 一条命令即可）：
        docker run -d --name flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr
    然后在 .env 里写 FLARESOLVERR_URL=http://127.0.0.1:8191。
    只有在直连/curl_cffi/阅读器三层都失败、且页面确实是 JS 挑战时才值得走这条（单次 10-30 秒）。
    """
    import httpx
    base = (os.environ.get("FLARESOLVERR_URL") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 FLARESOLVERR_URL"}
    with httpx.Client(timeout=90) as c:
        r = c.post(base + "/v1", json={"cmd": "request.get", "url": url, "maxTimeout": 60000})
    r.raise_for_status()
    js = r.json()
    sol = (js or {}).get("solution") or {}
    html = sol.get("response") or ""
    status = sol.get("status")
    if not html:
        return {"ok": False, "error": f"FlareSolverr 无返回（status={status}）"}
    if status and int(status) >= 400:
        return {"ok": False, "error": f"HTTP {status}"}
    import trafilatura
    try:
        text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    except Exception:  # noqa: BLE001
        text = ""
    if not text.strip():
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            text = " ".join(soup.get_text(" ").split())
        except Exception:  # noqa: BLE001
            text = ""
    return {"ok": True, "url": sol.get("url") or url, "title": "", "text": text[:max_chars],
            "truncated": len(text) > max_chars, "total_chars": len(text),
            "http_status": status, "via": "flaresolverr"}


@tool(
    "net_fetch",
    "抓取 URL 并提取可读正文（自动提取标题/正文/摘要/正文图片，含重定向与超时处理），返回文本+元数据，适合读文章内容。"
    "三层兜底：直连 → curl_cffi（浏览器 TLS 指纹，过 Cloudflare 指纹类拦截）→ r.jina.ai 阅读器。"
    "需要原始 HTML 时设 include_raw=True；不需要提取图片时设 include_images=False。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "max_chars": {"type": "number", "description": "返回文本上限字符数，默认 10000"},
            "include_raw": {"type": "boolean", "description": "是否附带原始 HTML，默认 false"},
            "include_images": {"type": "boolean", "description": "是否提取正文图片列表（URL+alt，已滤噪音），默认 true"},
            "fallback": {"type": "boolean",
                         "description": "反爬/空正文时是否继续走 curl_cffi 与阅读器兜底，默认 true"},
            "stealth": {"type": "boolean",
                        "description": "是否先用 curl_cffi 浏览器指纹重试（过 Cloudflare 指纹拦截），默认 true"},
        },
        "required": ["url"],
    },
)
def run(url: str, max_chars: int = 10000, include_raw: bool = False,
        include_images: bool = True, fallback: bool = True, stealth: bool = True) -> dict:
    if not url or not str(url).strip():
        return {"ok": False, "error": "url 不能为空"}
    if not _WS_READY:
        return {"ok": False,
                "error": f"正文提取组件不可用: {_WS_ERR}（需安装 library/depot/vendor/websearch 依赖）"}
    url = str(url).strip()
    err = ""
    try:
        r = _ws_fetch(url, include_images=bool(include_images))
        text = (r.get("text") or "") if isinstance(r, dict) else str(r)
        status = (r.get("metadata") or {}).get("http_status") if isinstance(r, dict) else None
        blocked = status in (401, 403, 429, 451)
        if not (r.get("error") if isinstance(r, dict) else False) and len(text.strip()) >= 40 and not blocked:
            truncated = len(text) > max_chars
            out = {
                "ok": True,
                "url": r.get("url") if isinstance(r, dict) else url,
                "title": r.get("title") if isinstance(r, dict) else "",
                "excerpt": (r.get("excerpt") or "")[:400] if isinstance(r, dict) else "",
                "text": text[:max_chars],
                "truncated": truncated,
                "total_chars": len(text),
                "via": "direct",
            }
            if isinstance(r, dict) and r.get("metadata"):
                m = r["metadata"]
                out["domain"] = m.get("domain")
                out["http_status"] = m.get("http_status")
                out["extractor"] = m.get("extractor")
            if isinstance(r, dict) and include_images:
                imgs = r.get("images") or []
                out["images"] = imgs[:20]
                out["image_count"] = len(imgs)
            if include_raw:
                out["raw"] = text
            return out
        err = (r.get("error") if isinstance(r, dict) else "") or (
            "HTTP %s" % status if blocked else "正文为空")
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    if not fallback:
        return {"ok": False, "error": f"抓取失败: {err}"}

    # 第二层：curl_cffi（浏览器 TLS 指纹）——专治"按 TLS 指纹判人"的 Cloudflare 拦截
    stealth_err = ""
    challenge = False
    if stealth:
        try:
            st = _stealth_fetch(url, max_chars)
            if st.get("ok"):
                st["fallback_reason"] = str(err)[:160]
                if include_raw:
                    st["raw"] = st.get("text", "")
                return st
            stealth_err = str(st.get("error") or "未知")
            challenge = bool(st.get("challenge"))
        except Exception as e:  # noqa: BLE001
            stealth_err = f"{type(e).__name__}: {e}"

    # 第三层：r.jina.ai 阅读器
    reader_err = ""
    try:
        fb = _reader_fetch(url, max_chars)
        body = (fb.get("text") or "").strip()
        # 阅读器本身也可能被同源封禁：它会把 "Warning: Target URL returned error 403"
        # 当正文返回（实测 Reddit），此时"成功"是假象，宁可继续往下走。
        if "Warning: Target URL returned error" not in body[:400] and len(body) >= 80:
            fb["fallback_reason"] = str(err)[:160]
            return fb
        reader_err = "阅读器只拿到错误页/正文过短"
    except Exception as e:  # noqa: BLE001
        reader_err = f"{type(e).__name__}: {e}"

    # 第四层：FlareSolverr（配了 FLARESOLVERR_URL 才走）——真浏览器解 CF JS 挑战
    fs_err = ""
    try:
        fs = _flaresolverr_fetch(url, max_chars)
        if fs.get("ok"):
            fs["fallback_reason"] = str(err)[:160]
            return fs
        fs_err = str(fs.get("error") or "")
    except Exception as e:  # noqa: BLE001
        fs_err = f"{type(e).__name__}: {e}"

    _hint = ("目标站是 JS 人机挑战（Cloudflare Turnstile 类）：只能上真浏览器——"
             "mcp_connect 激活 browserskill/playwright，或按 README 起一个 FlareSolverr 配 FLARESOLVERR_URL。"
             if challenge else
             "换通道更有效：mcp_connect 激活 browserskill/playwright 用真浏览器，"
             "或改用该站官方 API / FlareSolverr。")
    return {"ok": False,
            "error": (f"抓取失败（直连: {err}；curl_cffi: {stealth_err}；"
                      f"阅读器: {reader_err}；FlareSolverr: {fs_err or '未配置'}）。{_hint}")}
