"""内置工具：net_fetch —— 抓取 URL 内容。"""
from __future__ import annotations

import httpx

from tools.base import tool

USER_AGENT = "BailingAgent/0.1 (self-improving agent)"


@tool(
    "net_fetch",
    "抓取指定 URL 的内容（仅静态页面，自动处理重定向与超时）。返回原始 HTML，文本提取需另行处理。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "timeout": {"type": "number", "description": "超时秒数，默认 15"},
            "max_chars": {"type": "number", "description": "返回内容上限字符数，默认 10000"},
        },
        "required": ["url"],
    },
)
def run(url: str, timeout: float = 15, max_chars: int = 10000) -> dict:
    try:
        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        text = r.text
        return {
            "status": r.status_code,
            "final_url": str(r.url),
            "content_type": r.headers.get("content-type", ""),
            "content": text[:max_chars],
            "truncated": len(text) > max_chars,
            "total_chars": len(text),
        }
    except httpx.TimeoutException:
        return {"ok": False, "error": f"请求超时（>{timeout}s）"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"抓取失败: {type(e).__name__}: {e}"}
