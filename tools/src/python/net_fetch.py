"""内置工具：net_fetch —— 抓取 URL 并提取可读正文（trafilatura 成熟方案）。

从"返回原始 HTML、文本提取需另行处理"升级为"返回可读正文 + 元数据"：
- 复用 lazyhuman-ai/websearch 的 web_fetch 原语（trafilatura 提取正文，自动处理
  标题/规范 URL/正文/摘要），不重复造轮子。
- 原始 HTML 默认不返回（省 token），需要时 include_raw=True。
- 超时/重定向/错误统一处理。

来源：https://github.com/lazyhuman-ai/websearch（MIT），clone 于 workspace/tools_external/websearch。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_WS_DIR = os.path.join(_ROOT, "workspace", "tools_external", "websearch")
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
    "抓取 URL 并提取可读正文（自动提取标题/正文/摘要，含重定向与超时处理），返回文本+元数据，适合读文章内容。"
    "需要原始 HTML 时设 include_raw=True。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "max_chars": {"type": "number", "description": "返回文本上限字符数，默认 10000"},
            "include_raw": {"type": "boolean", "description": "是否附带原始 HTML，默认 false"},
        },
        "required": ["url"],
    },
)
def run(url: str, max_chars: int = 10000, include_raw: bool = False) -> dict:
    if not url or not str(url).strip():
        return {"ok": False, "error": "url 不能为空"}
    if not _WS_READY:
        return {"ok": False,
                "error": f"正文提取组件不可用: {_WS_ERR}（需安装 workspace/tools_external/websearch 依赖）"}
    try:
        r = _ws_fetch(str(url).strip())
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
        if include_raw:
            out["raw"] = text
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"抓取失败: {type(e).__name__}: {e}"}
