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


@tool(
    "net_fetch",
    "抓取 URL 并提取可读正文（自动提取标题/正文/摘要/正文图片，含重定向与超时处理），返回文本+元数据，适合读文章内容。"
    "遇到 401/403/429 反爬或正文为空时，会自动改用 r.jina.ai 阅读器再试一次（被墙/反爬场景的兜底通道）。"
    "需要原始 HTML 时设 include_raw=True；不需要提取图片时设 include_images=False。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "max_chars": {"type": "number", "description": "返回文本上限字符数，默认 10000"},
            "include_raw": {"type": "boolean", "description": "是否附带原始 HTML，默认 false"},
            "include_images": {"type": "boolean", "description": "是否提取正文图片列表（URL+alt，已滤噪音），默认 true"},
            "fallback": {"type": "boolean",
                         "description": "反爬/空正文时是否走 r.jina.ai 阅读器兜底，默认 true"},
        },
        "required": ["url"],
    },
)
def run(url: str, max_chars: int = 10000, include_raw: bool = False,
        include_images: bool = True, fallback: bool = True) -> dict:
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
    try:
        fb = _reader_fetch(url, max_chars)
        body = (fb.get("text") or "").strip()
        # 阅读器本身也可能被同源封禁：它会把 "Warning: Target URL returned error 403"
        # 当正文返回（实测 Reddit），此时"成功"是假象，宁可明确报失败。
        if "Warning: Target URL returned error" in body[:400] or len(body) < 80:
            return {"ok": False,
                    "error": f"抓取失败（直连: {err}；阅读器也只拿到错误页）。"
                             "该目标站对当前出口封禁，换通道更有效：browserskill 真浏览器 / 官方 API。"}
        fb["fallback_reason"] = str(err)[:160]
        return fb
    except Exception as e:  # noqa: BLE001
        return {"ok": False,
                "error": f"抓取失败（直连: {err}；阅读器兜底也失败: {type(e).__name__}: {e}）"}
