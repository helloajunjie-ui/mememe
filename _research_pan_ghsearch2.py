"""调研脚本（补充轮）：Search API 补查 115 网盘真实 Python 仓库 + PyDrive 真实仓库名。

首轮 115 查询被噪音污染（ansible/localstack 等），未发现真实仓库。
用更精确的查询词补查。落盘到 _research_pan_ghsearch2.json。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_research_pan_ghsearch2.json"
UA = {"User-Agent": "BailingAgent/0.1 (self-improving agent)"}
API = "https://api.github.com/search/repositories"

QUERIES = {
    "115pan python": "115pan OR 115网盘 OR 115driver language:python",
    "115 cloud driver": "115driver OR 115 cloud driver language:python",
    "pydrive": "pydrive in:name language:python",
}


def search(query: str, per_page: int = 8) -> list[dict]:
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": per_page}
    try:
        r = httpx.get(API, params=params, headers=UA, timeout=25)
        if r.status_code == 403:
            reset = int(r.headers.get("X-RateLimit-Reset", "0"))
            wait = max(reset - int(time.time()), 0) + 2
            print(f"  !! rate_limited, 等待 {wait}s")
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
        if i < len(QUERIES) - 1:
            time.sleep(8)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘完成: {OUT}")


if __name__ == "__main__":
    main()
