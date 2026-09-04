"""内置工具：sys_tools —— 发现本机已安装的系统工具与常用软件。

设计意图：避免重复造轮子。白绫能发现系统里已装好的命令工具（PATH 下的 CLI）与常用软件
（Office/浏览器等），发现后可直接用 cmd_run 调用使用，或按其路径启动，而不必自己重新实现。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from tools.base import tool

# 常见 CLI 工具名单（白绫可能用到的）
CLI_TOOLS = [
    "git", "python", "python3", "pip", "pip3", "node", "npm", "go", "java", "javac",
    "docker", "kubectl", "ffmpeg", "ffprobe", "curl", "wget", "tar", "7z", "gcc",
    "make", "cmake", "gh", "jq", "sqlite3", "redis-cli", "psql", "mysql", "code", "rg",
    "everything", "es",  # Everything 本地文件秒搜（GUI=everything，命令行=es）
]

# 常用软件检测路径（Windows）
WIN_APP_CANDIDATES = [
    ("Microsoft Word", r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"),
    ("Microsoft Excel", r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE"),
    ("Microsoft PowerPoint", r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"),
    ("Microsoft Outlook", r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE"),
    ("Google Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("Microsoft Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("VS Code", str(Path.home() / "AppData/Local/Programs/Microsoft VS Code/Code.exe")),
    ("WeChat", r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"),
    ("Notepad++", r"C:\Program Files\Notepad++\notepad++.exe"),
    ("Everything", r"C:\Program Files\Everything\Everything.exe"),
]

# macOS / Linux 常见
MAC_APP_CANDIDATES = [
    ("Microsoft Word", "/Applications/Microsoft Word.app"),
    ("Safari", "/Applications/Safari.app"),
    ("Chrome", "/Applications/Google Chrome.app"),
]
LINUX_APP_CANDIDATES = [
    ("LibreOffice", "/usr/bin/libreoffice"),
    ("Firefox", "/usr/bin/firefox"),
]


def _detect_gui() -> list:
    apps = []
    if os.name == "nt":
        for name, p in WIN_APP_CANDIDATES:
            if os.path.exists(p):
                apps.append({"name": name, "path": p})
    elif sys_platform_darwin():
        for name, p in MAC_APP_CANDIDATES:
            if os.path.isdir(p) or os.path.exists(p):
                apps.append({"name": name, "path": p})
    else:
        for name, p in LINUX_APP_CANDIDATES:
            if os.path.exists(p):
                apps.append({"name": name, "path": p})
    return apps


def sys_platform_darwin() -> bool:
    return os.uname().sysname == "Darwin" if os.name == "posix" else False


@tool(
    "sys_tools",
    "发现本机已安装的系统工具/命令与常用软件。两种模式：\n"
    "- scan_all=false（默认）：查重点工具名单（git/python/ffmpeg/curl/everything 等）+ 检测 Office/浏览器等软件，走快照。\n"
    "- scan_all=true：全量扫描 PATH 下的第三方工具（跳过系统核心目录），让你看到本机真实安装的全部命令行工具——主动发现用这个。\n"
    "发现后可直接用 cmd_run 调用（如 es.exe 搜索 / git status），避免重复造轮子。",
    {
        "type": "object",
        "properties": {
            "check_version": {"type": "boolean",
                              "description": "是否顺带探测重点工具版本（较慢，默认 false）"},
            "scan_all": {"type": "boolean",
                         "description": "是否全量扫描 PATH 下的第三方工具（主动发现用，默认 false）"},
            "refresh": {"type": "boolean",
                        "description": "是否强制重新扫描刷新快照（默认 false，走缓存）"},
        },
        "required": [],
    },
)
def run(check_version: bool = False, scan_all: bool = False, refresh: bool = False) -> dict:
    from core.cache import SnapshotCache

    cache = SnapshotCache()

    if scan_all:
        value, fetched_at, refreshed = cache.get(
            "sys_tools_all", _scan_path_all, ttl_seconds=21600, force_refresh=refresh
        )
        value["from_cache"] = not refreshed
        value["fetched_at"] = fetched_at
        return value

    key = "sys_tools_v" if check_version else "sys_tools"

    def _scan():
        return _do_scan(check_version)

    value, fetched_at, refreshed = cache.get(key, _scan, ttl_seconds=21600, force_refresh=refresh)
    value["from_cache"] = not refreshed
    value["fetched_at"] = fetched_at
    return value


def _scan_path_all(limit: int = 300) -> dict:
    """全量扫描 PATH 下的第三方工具（跳过系统核心目录，避免海量噪声）。"""
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    known_names = set(CLI_TOOLS)
    seen = set()
    tools = []
    for d in path_dirs:
        d = d.strip().strip('"')
        if not d or not os.path.isdir(d):
            continue
        dl = d.lower()
        # 跳过 Windows 系统核心目录（System32 等海量系统工具）
        if dl.startswith(("c:\\windows\\system32", "c:\\windows\\syswow64",
                          "c:\\windows\\system", "/usr/bin", "/bin", "/usr/sbin", "/sbin")):
            continue
        try:
            for f in os.listdir(d):
                fpath = os.path.join(d, f)
                if not os.path.isfile(fpath):
                    continue
                if os.name == "nt":
                    if not f.lower().endswith((".exe", ".cmd", ".bat")):
                        continue
                    name = os.path.splitext(f)[0].lower()
                else:
                    if not os.access(fpath, os.X_OK):
                        continue
                    name = f
                if name in seen:
                    continue
                seen.add(name)
                tools.append({
                    "name": name,
                    "path": fpath,
                    "known": name in known_names,  # 是否为重点名单工具
                })
        except (PermissionError, OSError):
            continue
    tools.sort(key=lambda x: (not x["known"], x["name"]))  # 重点工具优先
    return {
        "ok": True,
        "scan_mode": "all",
        "cli_tools": tools[:limit],
        "cli_count": len(tools[:limit]),
        "note": "全量 PATH 扫描（已跳过系统核心目录）。known=true 为重点名单工具，优先关注。",
    }


def _do_scan(check_version: bool) -> dict:
    cli = []
    for name in CLI_TOOLS:
        p = shutil.which(name)
        if not p:
            continue
        entry = {"name": name, "path": p}
        if check_version:
            ver = _try_version(p)
            if ver:
                entry["version"] = ver
        cli.append(entry)

    return {
        "ok": True,
        "cli_tools": cli,
        "cli_count": len(cli),
        "gui_apps": _detect_gui(),
        "shell": os.environ.get("COMSPEC" if os.name == "nt" else "SHELL", ""),
        "note": "发现后可用 cmd_run 直接调用系统命令；GUI 应用可用其路径启动。",
    }


def _try_version(exe: str, timeout: float = 4) -> str:
    for flag in ("--version", "-version", "-V", "version"):
        try:
            r = subprocess.run([exe, flag], capture_output=True, text=True,
                               timeout=timeout, errors="replace")
            out = (r.stdout or r.stderr or "").strip().splitlines()
            if out:
                line = out[0].strip()
                if line:
                    return line[:80]
        except Exception:  # noqa: BLE001
            continue
    return ""
