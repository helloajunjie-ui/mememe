"""调研脚本：GitHub Repo API 精确拉取各网盘/云存储服务的高星官方+社区候选仓库元数据。

复用 _research_ghapi 模式。core API 无 token 限流 60 req/min，配额充足，
对每个已知候选仓库精确拉取 stars/forks/语言/许可/更新时间等。

候选清单基于领域知识预判（官方 SDK + 高星社区封装），用 Repo API 验证真实存在与元数据。
落盘到 _research_pan_ghapi.json。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_research_pan_ghapi.json"
UA = {"User-Agent": "BailingAgent/0.1 (self-improving agent)"}
API = "https://api.github.com/repos/"

# 已知候选：owner/repo（官方 SDK + 高星社区封装）
CANDIDATES = {
    # 百度网盘
    "baidu": [
        "houtianze/bypy",            # 高星社区：百度网盘命令行/API
        "foyoux/py-baidu-netdisk",   # 社区：百度网盘 Python SDK
        "PeterDing/iScript",         # 社区：百度网盘下载脚本
        "baidupcs/baidupcs",         # 社区：百度网盘客户端
        "ly0/baidu-netdisk-downloaderx",  # 社区：百度网盘下载器
    ],
    # 阿里云盘
    "aliyun": [
        "foyoux/aliyunpan",          # 社区：阿里云盘 CLI/SDK
        "wxy1343/aliyunpan",         # 社区：阿里云盘 Python
        "tickstep/aliyunpan",        # 社区：阿里云盘 CLI (Go)
        "Xhofe/alist",               # 社区：聚合网盘 (Go)
        "messense/aliyundrive-webdav",  # 社区：阿里云盘 WebDAV
    ],
    # 夸克网盘
    "quark": [
        "CikeyQi/quark-netdisk",     # 社区：夸克网盘
        "LiLittleCat/awesome-free-chatgpt",  # 占位（夸克社区方案较少）
    ],
    # 115 网盘
    "115": [
        "Aruelius/115driver",        # 社区：115 网盘驱动
        "tickstep/115driver",        # 社区：115 网盘 CLI (Go)
        "chenkkk/115",               # 社区：115 网盘
    ],
    # 天翼云盘
    "tianyi": [
        "tickstep/cloudpan189-go",   # 社区：天翼云盘 CLI (Go)
    ],
    # OneDrive
    "onedrive": [
        "OneDrive/onedrive-sdk-python",  # 官方（已弃用，转 Graph）
        "OneDrive/onedrive-api-docs",    # 官方文档
    ],
    # Google Drive
    "googledrive": [
        "googleapis/google-api-python-client",  # 官方
        "gsuitedev/PyDrive",                    # 社区高星
        "googleworkspace/drive-api-python-samples",  # 官方示例
    ],
    # Dropbox
    "dropbox": [
        "dropbox/dropbox-sdk-python",  # 官方
    ],
    # 蓝奏云
    "lanzou": [
        "zaxtyson/LanZouCloud",      # 社区：蓝奏云
        "rocketcc/lanzou-downloader",  # 社区：蓝奏云下载器
    ],
    # 123 云盘
    "123pan": [
        "tickstep/123pan",           # 社区：123 云盘 CLI (Go)
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
            time.sleep(1.2)  # core 60/min，保守间隔
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘完成: {OUT}")


if __name__ == "__main__":
    main()
