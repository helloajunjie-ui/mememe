"""调研脚本：GitHub Search API 检索各网盘/云存储服务的 Python SDK 候选。

复用 _research_ghsearch 模式：对每个服务构造一个 language:python 的 search 查询，
按 stars 排序取 top N，落盘到 _research_pan_ghsearch.json。

注意：无 token 时 GitHub Search API 限流 10 req/min，故每个服务仅 1 个查询，
用 in:name,description 提升相关性，避免浪费配额。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_research_pan_ghsearch.json"
UA = {"User-Agent": "BailingAgent/0.1 (self-improving agent)"}
API = "https://api.github.com/search/repositories"

# 每个服务的检索查询（language:python + 关键词，按 stars 排序）
QUERIES = {
    # 百度网盘
    "baidu pan python": "baidu pan OR baidupcs OR bypy language:python",
    "baidu netdisk sdk": "baidu netdisk OR baiduyun OR baidu pcs language:python",
    # 阿里云盘
    "aliyun drive python": "aliyun drive OR aliyundrive OR aliyunpan language:python",
    "aliyun openapi drive": "aliyunpan OR aliyun-drive openapi language:python",
    # 夸克网盘
    "quark netdisk python": "quark netdisk OR quarkdrive OR quarkpan language:python",
    # 115 网盘
    "115 driver python": "115driver OR 115pan OR 115 cloud language:python",
    # 天翼云盘
    "tianyi ecloud python": "tianyi OR ecloud OR 189cloud OR 天翼云盘 language:python",
    # OneDrive
    "onedrive python sdk": "onedrive sdk language:python",
    # Google Drive
    "google drive python": "google drive OR pydrive language:python",
    # Dropbox
    "dropbox python sdk": "dropbox sdk language:python",
    # 蓝奏云
    "lanzou python": "lanzou OR lanzouyun OR lanzous language:python",
    # 123 云盘
    "123pan python": "123pan OR 123云盘 OR 123yunpan language:python",
}


def search(query: str, per_page: int = 8) -> list[dict]:
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": per_page}
    try:
        r = httpx.get(API, params=params, headers=UA, timeout=25)
        if r.status_code == 403:
            # 读取限流重置时间，等待后重试一次
            reset = int(r.headers.get("X-RateLimit-Reset", "0"))
            wait = max(reset - int(time.time()), 0) + 2
            print(f"  !! rate_limited, 等待 {wait}s 后重试")
            time.sleep(wait)
            r = httpx.get(API, params=params, headers=UA, timeout=25)
        r.raise_for_status()
        items = r.json().get("items", [])
        return [{
            "full_name": it.get("full_name"),
            "description": (it.get("description") or "")[:200],
            "stars": it.get("stargazers_count"),
            "language": it.get("language"),
            "license": (it.get("license") or {}).get("spdx_id"),
            "pushed_at": it.get("pushed_at"),
            "archived": it.get("archived"),
            "html_url": it.get("html_url"),
        } for it in items]
    except Exception as e:  # noqa: BLE001
        return [{"error": f"{type(e).__name__}: {e}", "query": query}]


def main() -> None:
    out: dict[str, list] = {}
    for i, (label, q) in enumerate(QUERIES.items()):
        print(f"[{i+1}/{len(QUERIES)}] {label}: {q}")
        out[label] = search(q)
        # Search API 无 token 限流 10/min，每请求间隔 8s 保守等待
        if i < len(QUERIES) - 1:
            time.sleep(8)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘完成: {OUT}")


if __name__ == "__main__":
    main()
