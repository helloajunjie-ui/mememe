"""内置工具：app_probe —— 软件安装目录探查（白绫核心能力，独立于 MCP 服务）。

定位：白绫核心工具 = 沟通 + 日常命令。探查本机装了什么软件是日常能力，
用于「判定能否执行某项任务 / 决定用 MCP 哪个接口」的前置步骤，不依赖 MCP 服务。

与 sys_probe 的区别：
- sys_probe：注册表/包管理器全量清单（快照落盘，含版本），适合"全面盘点本机"。
- app_probe：目标软件可执行文件路径级定位（实时、毫秒级），适合"查某个软件装没装、在哪"。

与 MCP 的关系（完全解耦）：
- app_probe 只回答"本机有什么"；用哪个 MCP 接口由白绫结合 mcp_list（接口目录）判定；
  判定后 mcp_connect（激活）才与 MCP 服务通信。
- 调用链：app_probe（核心，探查）→ mcp_list（MCP 目录）→ mcp_connect（MCP 激活）。

跨平台：exe 名 + PATH（shutil.which）跨平台通用；固定路径候选按 Windows 常见安装目录；
glob 用于版本号目录（Blender/WPS/Chrome）。macOS/Linux 靠 which 兜底，缺失即如实为空。
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess

from tools.base import tool

# (名称, 分类, PATH 可执行名, 固定路径候选, glob 候选, 版本参数)
_SOFTWARE: list[tuple] = [
    # ---- 办公 ----
    ("LibreOffice", "办公", "soffice", [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"], [], ["--version"]),
    ("Microsoft Word", "办公", "WINWORD", [
        r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office\WINWORD.EXE"], [], []),
    ("Microsoft Excel", "办公", "EXCEL", [
        r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office16\EXCEL.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office\EXCEL.EXE"], [], []),
    ("Microsoft PowerPoint", "办公", "POWERPNT", [
        r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office\POWERPNT.EXE"], [], []),
    ("WPS 文字", "办公", "wps", [], [
        r"C:\Program Files\Kingsoft\WPS Office\**\wps.exe",
        r"C:\Program Files (x86)\Kingsoft\WPS Office\**\wps.exe"], ["--version"]),
    ("WPS 表格", "办公", "et", [], [
        r"C:\Program Files\Kingsoft\WPS Office\**\et.exe",
        r"C:\Program Files (x86)\Kingsoft\WPS Office\**\et.exe"], ["--version"]),
    ("WPS 演示", "办公", "wpp", [], [
        r"C:\Program Files\Kingsoft\WPS Office\**\wpp.exe",
        r"C:\Program Files (x86)\Kingsoft\WPS Office\**\wpp.exe"], ["--version"]),
    # ---- 3D / 设计 / 游戏 ----
    ("Blender", "3D/设计", "blender", [], [
        r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
        r"C:\Program Files (x86)\Blender Foundation\Blender*\blender.exe"], ["--version"]),
    ("Godot", "游戏引擎", "godot", [], [
        r"C:\Program Files\Godot\godot*.exe"], ["--version"]),
    # ---- 开发 ----
    ("Git", "开发", "git", [r"C:\Program Files\Git\cmd\git.exe"], [], ["--version"]),
    ("Node.js", "开发", "node", [r"C:\Program Files\nodejs\node.exe"], [], ["-v"]),
    ("npx", "开发", "npx", [r"C:\Program Files\nodejs\npx.cmd"], [], ["-v"]),
    ("Python", "开发", "python", [], [], ["--version"]),
    ("Go", "开发", "go", [], [], ["version"]),
    ("VS Code", "开发", "code", [
        r"C:\Program Files\Microsoft VS Code\Code.exe"], [], ["--version"]),
    # ---- 浏览器 ----
    ("Chrome", "浏览器", "chrome", [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"], [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"], ["--version"]),
    ("Edge", "浏览器", "msedge", [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"], [], ["--version"]),
    # ---- 媒体 ----
    ("ffmpeg", "媒体", "ffmpeg", [], [], ["-version"]),
]

# 版本串提取：形如 "v22.11.1" / "git version 2.45.1" / "Blender 5.0.0" / "4.3.stable.official"
_VER_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[.-][0-9A-Za-z]+)?)")


def _read_version(path: str, args: list) -> str:
    """跑 --version 类命令读版本号。读不到返回空串（如实，不编造）。"""
    if not path or not args:
        return ""
    try:
        r = subprocess.run([path] + args, capture_output=True, text=True,
                           timeout=8, errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (r.stdout or "") + " " + (r.stderr or "")
    except Exception:  # noqa: BLE001
        return ""
    m = _VER_RE.search(text)
    return m.group(1) if m else ""


def _find_one(exe: str, candidates: list, globs: list) -> str:
    """定位可执行文件：固定路径 → glob → PATH。找不到返回空串（如实）。"""
    for p in candidates:
        if p and os.path.exists(p):
            return p
    for g in globs:
        try:
            hits = glob.glob(g)
            if hits:
                return hits[0]
        except (OSError, ValueError):
            continue
    hit = shutil.which(exe)
    return hit or ""


def _probe_all(apps: list | None = None) -> list:
    """探查软件安装目录 + 版本号。apps 指定时只查指定名称（模糊匹配），否则全量。"""
    out = []
    for name, cat, exe, cands, globs, ver_args in _SOFTWARE:
        if apps and not any(a.lower() in name.lower() for a in apps):
            continue
        path = _find_one(exe, cands, globs)
        ver = _read_version(path, ver_args) if path else ""
        out.append({"name": name, "category": cat, "installed": bool(path),
                    "path": path, "version": ver})
    return out


@tool(
    "app_probe",
    "软件安装目录探查（白绫核心能力，独立于 MCP）：实时定位本机关键软件的可执行文件路径和版本号"
    "（办公 LibreOffice/Office/WPS、3D Blender/Godot、开发 Git/Node/Python/Go/VS Code、"
    "浏览器 Chrome/Edge、媒体 ffmpeg）。用于「判定本机装了什么、版本是否达标 → 决定用 MCP 哪个接口」。"
    "与 sys_probe 区别：sys_probe 是注册表全量清单（快照），本工具是目标软件路径+版本实时定位。"
    "与 MCP 解耦：只回答本机有什么；用哪个 MCP 接口由你结合 mcp_list 判定后再 mcp_connect 激活。"
    "版本号由 --version 类命令实时读取，读不到为空串（如实）。默认查全部；可传 apps 只查指定软件。",
    {
        "type": "object",
        "properties": {
            "apps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "只查这些软件（名称模糊匹配，如 [\"office\", \"blender\"]）；不传查全部",
            },
        },
        "required": [],
    },
)
def run(apps: list | None = None) -> dict:
    software = _probe_all(apps)
    installed = [s for s in software if s["installed"]]
    return {
        "ok": True,
        "probed": [{"name": s["name"], "category": s["category"], "path": s["path"],
                    "version": s["version"]} for s in installed],
        "missing": [s["name"] for s in software if not s["installed"]],
        "summary": {"installed": len(installed), "checked": len(software)},
        "note": "结果实时来自本机；用哪个 MCP 接口请结合 mcp_list 判定，再 mcp_connect 激活。",
    }
