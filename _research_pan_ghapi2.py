"""调研脚本（补充轮）：Repo API 精确拉取 search 新发现的真实高星仓库完整元数据。

首轮 ghapi 中部分候选 404/301（owner 名记错），search 轮发现了真实仓库名。
本脚本对这些真实仓库拉取完整元数据（含 forks/created_at 等 search 未提供字段）。
落盘到 _research_pan_ghapi2.json。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_research_pan_ghapi2.json"
UA = {"User-Agent": "BailingAgent/0.1 (self-improving agent)"}
API = "https://api.github.com/repos/"

CANDIDATES = {
    "baidu": [
        "PeterDing/BaiduPCS-Py",        # 百度网盘客户端/API (Python)
        "mozillazg/baidu-pcs-python-sdk",  # 百度 PCS Python SDK
    ],
    "aliyun": [
        "foyoux/aliyunpan",             # 阿里云盘 CLI (Go, 高星)
    ],
    "quark": [
        "lich0821/QuarkPan",            # 夸克网盘 Python 客户端
        "Cp0204/quark-auto-save",       # 夸克自动转存 (Python)
        "ev-flow/quark-engine",         # 夸克引擎 (Python)
        "ihmily/QuarkPanTool",          # 夸克批量转存 (Python)
    ],
    "tianyi": [
        "Aruelius/cloud189",            # 天翼云盘 CLI Python
    ],
    "lanzou": [
        "rachpt/lanzou-gui",            # 蓝奏云 GUI (Python)
        "zaxtyson/LanZouCloud-API",     # 蓝奏云第三方 API (Python)
        "zaxtyson/LanZouCloud-CMD",     # 蓝奏云 CMD (Python)
    ],
    "123pan": [
        "123panNextGen/123pan",         # 123云盘第三方客户端 (Python)
        "Bao-qing/123pan",              # 123云盘 CLI 工具和模块 (Python)
        "SodaCodeSave/Pan123",          # 123云盘开放平台非官方 Python SDK
    ],
    "onedrive": [
        "farfarfun/fundrive",           # 统一网盘框架 (Python, 含 OneDrive/百度/阿里等)
    ],
}


def fetch(repo: str) -> dict:
    try:
        r = httpx.get(API + repo, headers=UA, timeout=25)
        if r.status_code == 404:
            return {"repo": repo, "error": "404 not found"}
        if r.status_code == 403:
            return {"repo": repo, "error": "rate_limited"}
        r.raise_for_status()
        it = r.json()
        return {
            "repo": repo,
            "full_name": it.get("full_name"),
            "description": (it.get("description") or "")[:200],
            "stars": it.get("stargazers_count"),
            "forks": it.get("forks_count"),
            "language": it.get("language"),
            "license": (it.get("license") or {}).get("spdx_id"),
            "archived": it.get("archived"),
            "created_at": it.get("created_at"),
            "updated_at": it.get("updated_at"),
            "pushed_at": it.get("pushed_at"),
            "open_issues": it.get("open_issues_count"),
            "homepage": it.get("homepage"),
            "default_branch": it.get("default_branch"),
            "topics": it.get("topics") or [],
        }
    except Exception as e:  # noqa: BLE001
        return {"repo": repo, "error": f"{type(e).__name__}: {e}"}


def main() -> None:
    out: dict[str, list] = {}
    total = sum(len(v) for v in CANDIDATES.values())
    done = 0
    for service, repos in CANDIDATES.items():
        out[service] = []
        for repo in repos:
            done += 1
            print(f"[{done}/{total}] {service}: {repo}")
            out[service].append(fetch(repo))
            time.sleep(1.2)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘完成: {OUT}")


if __name__ == "__main__":
    main()
