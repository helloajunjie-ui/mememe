"""内置工具：disk_report —— 磁盘空间去哪了（一次打包统计磁盘与缓存大户，只读不删）。

场景：用户说"C 盘满了/磁盘空间不够/清理一下"时，一次调用给出各盘用量 + 常见缓存
大户体积（用户临时/Windows 临时/回收站/浏览器缓存/npm/pip/Docker），可清理合计。
只做统计与建议，【不删除任何文件】；确认清理走受控流程。
"""
from __future__ import annotations

import json
import os
import threading

from tools.base import tool
from core import platform as plat

_DRIVES = "Get-PSDrive -PSProvider FileSystem | Select-Object Name,"
_DRIVES += "@{n='used_gb';e={[math]::Round($_.Used/1GB,1)}},"
_DRIVES += "@{n='free_gb';e={[math]::Round($_.Free/1GB,1)}} | ConvertTo-Json -Compress"

# (key, 人类名, 目录表达式) —— 注意：PowerShell 中 $env:X\路径 裸写会解析失败，
# 必须双引号包裹；含 $ 的段用 Join-Path + 单引号。
_CACHE_DIRS = [
    ("user_temp", "用户临时", '"$env:TEMP"'),
    ("win_temp", "Windows临时", '"$env:WINDIR\\Temp"'),
    ("recycle", "回收站", "Join-Path $env:SystemDrive '$Recycle.Bin'"),
    ("chrome_cache", "Chrome缓存", '"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\Default\\Cache"'),
    ("edge_cache", "Edge缓存", '"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\Default\\Cache"'),
    ("npm_cache", "npm缓存", '"$env:LOCALAPPDATA\\npm-cache"'),
    ("pip_cache", "pip缓存", '"$env:LOCALAPPDATA\\pip\\cache"'),
    ("docker", "Docker数据", '"$env:LOCALAPPDATA\\Docker"'),
]


def _size_cmd(path_expr: str) -> str:
    return (f"$p = {path_expr}; if (Test-Path $p) {{ "
            f"$s = (Get-ChildItem $p -Recurse -Force -ErrorAction SilentlyContinue | "
            f"Measure-Object -Property Length -Sum).Sum; "
            f"if ($s) {{ [math]::Round($s/1GB, 2) }} else {{ 0 }} }} else {{ 'none' }}")


@tool(
    "disk_report",
    "磁盘空间速查：一次打包统计各盘用量与常见缓存大户体积（用户临时/Windows临时/回收站/浏览器缓存/npm/pip/Docker），"
    "返回紧凑报告与可清理合计。【只统计不删除】。用户说 C 盘满/磁盘不够/清理时优先用它。",
    {
        "type": "object",
        "properties": {
            "include_docker": {
                "type": "boolean",
                "description": "是否统计 Docker 数据（可能很大且扫描慢），默认 true"
            },
        },
        "required": [],
    },
)
def run(include_docker: bool = True) -> dict:
    cache_dirs = _CACHE_DIRS if include_docker else [d for d in _CACHE_DIRS if d[0] != "docker"]
    results: dict = {}
    lock = threading.Lock()

    def worker(key: str, cmd: str) -> None:
        try:
            r = plat.run_shell(cmd, timeout=30)
            raw = (r["stdout"] or r["stderr"] or "").strip()
            val: object = raw
            try:
                val = json.loads(raw)
            except Exception:  # noqa: BLE001
                pass
            with lock:
                results[key] = {"ok": r["ok"], "val": val}
        except Exception as e:  # noqa: BLE001
            with lock:
                results[key] = {"ok": False, "val": f"err:{type(e).__name__}"}

    threads = [threading.Thread(target=worker, args=("drives", _DRIVES), daemon=True)]
    threads += [threading.Thread(target=worker, args=(k, _size_cmd(expr)), daemon=True) for k, _, expr in cache_dirs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)

    card: dict = {}

    if results.get("drives", {}).get("ok"):
        d = results["drives"]["val"]
        if isinstance(d, dict):
            d = [d]
        lines = []
        for x in d if isinstance(d, list) else []:
            if isinstance(x, dict) and x.get("Name"):
                try:
                    used, free = float(x.get("used_gb", 0)), float(x.get("free_gb", 0))
                except Exception:  # noqa: BLE001
                    continue
                lines.append(f"{x['Name']}: 可用{free:.1f}GB/共{used + free:.1f}GB")
        card["磁盘"] = lines

    caches = {}
    total_clean = 0.0
    for key, human, _ in cache_dirs:
        item = results.get(key, {})
        if not item.get("ok"):
            continue
        v = item["val"]
        if v == "none":
            caches[human] = "无"
        elif isinstance(v, (int, float)) and float(v) > 0:
            caches[human] = f"{v}GB"
            try:
                total_clean += float(v)
            except Exception:  # noqa: BLE001
                pass
        elif v == 0:
            caches[human] = "0"
        else:
            caches[human] = str(v)[:30]
    card["缓存大户"] = caches

    if total_clean > 0:
        card["可清理合计"] = f"约 {total_clean:.1f}GB"
        top = max([(float(results[k]["val"]), h) for k, h, _ in cache_dirs
                   if results.get(k, {}).get("ok") and isinstance(results[k]["val"], (int, float))],
                  default=None)
        if top and top[0] > 0:
            card["清理建议"] = f"最大头是{top[1]}（{top[0]:.1f}GB）；确认后可用受控方式清理，白绫不会自动删除。"

    summary = " | ".join([f"{k} {v}" for k, v in caches.items() if v not in ("无", "0")][:6])
    return {"ok": True, "summary": summary or "（缓存统计完成，无显著可清理项）", "card": card,
            "tip": "本工具只统计不删除；确认清理请单独指示。"}
