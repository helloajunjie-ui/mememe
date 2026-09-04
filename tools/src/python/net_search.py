"""内置工具：net_search —— 关键词搜索。"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

import httpx

from tools.base import tool

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


@tool(
    "net_search",
    "关键词搜索，返回结果标题+链接+摘要（走搜索引擎，最多 max_results 条）",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "number", "description": "返回结果数，默认 5"},
        },
        "required": ["query"],
    },
)
def run(query: str, max_results: int = 5) -> dict:
    try:
        url = f"https://www.bing.com/search?q={quote(query)}&count={max_results}"
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
        r.raise_for_status()

        results = []
        # 粗提取 b_algo 结果块（骨架实现，够用即可）
        blocks = re.findall(r'<li class="b_algo".*?</li>', r.text, re.S)
        for block in blocks[:max_results]:
            title_m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
            if not title_m:
                continue
            link = html.unescape(title_m.group(1))
            title = html.unescape(re.sub(r"<[^>]+>", "", title_m.group(2))).strip()
            snippet = ""
            if snippet_m:
                snippet = html.unescape(re.sub(r"<[^>]+>", "", snippet_m.group(1))).strip()
            results.append({"title": title, "url": link, "snippet": snippet})

        return {"ok": True, "query": query, "count": len(results), "results": results}
    except httpx.TimeoutException:
        return {"ok": False, "error": "搜索请求超时"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"搜索失败: {type(e).__name__}: {e}"}
