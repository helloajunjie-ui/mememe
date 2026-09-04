"""内置工具：net_download —— 下载文件到 workspace/。"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from tools.base import tool

WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"
MAX_BYTES = 50 * 1024 * 1024  # 默认 50MB 上限
USER_AGENT = "BailingAgent/0.1 (self-improving agent)"


@tool(
    "net_download",
    "下载文件到 workspace/ 工作区（可指定任务子目录，限制大小默认 50MB，全部记日志）",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "文件 URL"},
            "subdir": {"type": "string",
                       "description": "可选，workspace/ 下的相对子目录，如 tasks/20260904_股票分析"},
            "filename": {"type": "string", "description": "保存文件名（可选，默认取 URL 末尾）"},
            "max_mb": {"type": "number", "description": "大小上限 MB，默认 50"},
        },
        "required": ["url"],
    },
)
def run(url: str, subdir: str = "", filename: str = "", max_mb: float = 50) -> dict:
    max_bytes = int(max_mb * 1024 * 1024)
    try:
        if not filename:
            filename = unquote(urlparse(url).path.split("/")[-1]) or "download.bin"
        # 防路径穿越
        safe_name = Path(filename).name
        base = WORKSPACE
        if subdir:
            base = (WORKSPACE / subdir).resolve()
            if WORKSPACE.resolve() not in base.parents:
                return {"ok": False, "error": "非法子目录"}
        target = (base / safe_name).resolve()
        if base not in target.parents and target != base:
            return {"ok": False, "error": "非法文件名"}

        base.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", url, timeout=60, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            size = 0
            with open(target, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > max_bytes:
                        f.close()
                        target.unlink(missing_ok=True)
                        return {"ok": False, "error": f"下载超过大小上限 {max_mb}MB，已中止"}
                    f.write(chunk)
        return {"ok": True, "path": str(target), "size": size, "url": url}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"下载失败: {type(e).__name__}: {e}"}
