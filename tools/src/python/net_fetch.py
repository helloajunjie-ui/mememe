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


@tool(
    "net_fetch",
    "抓取 URL 并提取可读正文（自动提取标题/正文/摘要/正文图片，含重定向与超时处理），返回文本+元数据，适合读文章内容。"
    "需要原始 HTML 时设 include_raw=True；不需要提取图片时设 include_images=False。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "max_chars": {"type": "number", "description": "返回文本上限字符数，默认 10000"},
            "include_raw": {"type": "boolean", "description": "是否附带原始 HTML，默认 false"},
            "include_images": {"type": "boolean", "description": "是否提取正文图片列表（URL+alt，已滤噪音），默认 true"},
        },
        "required": ["url"],
    },
)
def run(url: str, max_chars: int = 10000, include_raw: bool = False, include_images: bool = True) -> dict:
    if not url or not str(url).strip():
        return {"ok": False, "error": "url 不能为空"}
    if not _WS_READY:
        return {"ok": False,
                "error": f"正文提取组件不可用: {_WS_ERR}（需安装 library/depot/vendor/websearch 依赖）"}
    try:
        r = _ws_fetch(str(url).strip(), include_images=bool(include_images))
        if isinstance(r, dict) and r.get("error"):
            return {"ok": False, "error": str(r["error"])}
        text = (r.get("text") or "") if isinstance(r, dict) else str(r)
        truncated = len(text) > max_chars
        out = {
            "ok": True,
            "url": r.get("url") if isinstance(r, dict) else url,
            "title": r.get("title") if isinstance(r, dict) else "",
            "excerpt": (r.get("excerpt") or "")[:400] if isinstance(r, dict) else "",
            "text": text[:max_chars],
            "truncated": truncated,
            "total_chars": len(text),
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
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"抓取失败: {type(e).__name__}: {e}"}
