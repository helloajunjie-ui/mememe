"""内置工具：app_probe —— 软件安装目录探查（素月核心能力，独立于 MCP 服务）。

定位：素月核心工具 = 沟通 + 日常命令。探查本机装了什么软件是日常能力，
用于「判定能否执行某项任务 / 决定用 MCP 哪个接口」的前置步骤，不依赖 MCP 服务。

与 sys_probe 的区别：
- sys_probe：注册表/包管理器全量清单（快照落盘，含版本），适合"全面盘点本机"。
- app_probe：目标软件可执行文件路径级定位（实时、毫秒级），适合"查某个软件装没装、在哪"。

与 MCP 的关系（完全解耦）：
- app_probe 只回答"本机有什么"；用哪个 MCP 接口由素月结合 mcp_list（接口目录）判定；
  判定后 mcp_connect（激活）才与 MCP 服务通信。
- 调用链：app_probe（核心，探查）→ mcp_list（MCP 目录）→ mcp_connect（MCP 激活）。

跨平台：exe 名 + PATH（shutil.which）跨平台通用；固定路径候选按 Windows 常见安装目录；
glob 用于版本号目录（Blender/WPS/Chrome）。macOS/Linux 靠 which 兜底，缺失即如实为空。

DIR 校验（定位链末环，不只"确认存在"）：
- 定位到可执行体后记录【体积 + 文件类型】（PE 架构/子系统），并判定 launcher（启动器）vs body（本体）。
- 判据优先级：体积 > PE 类型 > 文件名——同目录里"哪个是本体"靠体积一眼可分
  （实测 Blender 5.2：blender-launcher.exe 335KB vs blender.exe 112MB）；文件名只是最弱辅助证据。
- 副产品：PE 子系统能解释"某 exe 跑 --version 挂死"——GUI 子系统程序不带 --headless 会常驻。
"""
from __future__ import annotations

import glob
import io
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


def _from_registry(exe: str, name: str = "") -> str:
    """注册表权威定位（抗升级/换盘/换目录）：①App Paths 的默认值即官方注册的全路径；
    ②卸载项 InstallLocation 兜底：DisplayName 含软件名时，在安装目录内递归找 exe。
    仅 Windows 生效；读不到返回空串（如实）。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    exe_file = exe if exe.lower().endswith(".exe") else exe + ".exe"
    app_paths = ("SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\",
                 "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\App Paths\\")
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in app_paths:
            try:
                with winreg.OpenKey(hive, base + exe_file) as k:
                    p = winreg.QueryValue(k, "")
                if p and os.path.exists(p):
                    return p
            except OSError:
                continue
    if not name:
        return ""
    tokens = [t for t in re.split(r"[^0-9A-Za-z]+", name.lower()) if len(t) >= 4]
    if not tokens:
        return ""
    uninst = ("SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
              "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall")
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, uninst[0]), (winreg.HKEY_LOCAL_MACHINE, uninst[1]),
                       (winreg.HKEY_CURRENT_USER, uninst[0])):
        try:
            root = winreg.OpenKey(hive, view)
        except OSError:
            continue
        for i in range(300):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(root, sub) as sk:
                    dn, _ = winreg.QueryValueEx(sk, "DisplayName")
                    if not any(t in dn.lower() for t in tokens):
                        continue
                    loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
            except OSError:
                continue
            if not loc or not os.path.isdir(loc):
                continue
            try:
                hits = glob.glob(os.path.join(loc, "**", exe_file), recursive=True)
            except (OSError, ValueError):
                continue
            if hits:
                return hits[0]
    return ""


# ---- DIR 校验：真身特征（体积 + 文件类型 + 同族判据）----
# 为什么需要：同一目录常有 launcher（启动器，几百 KB）与本体（几十~几百 MB）两个可执行体，
# 文件名都像，靠名字分不清。实测 Blender 5.2：blender-launcher.exe 335KB vs blender.exe 112MB。
# 另：PE 子系统能解释"某 exe 带 --version 会挂死"——GUI 子系统程序不带 --headless 会常驻不返。
#
# 判据（按可靠性排序，体积只在【同族/同名】内比较，绝不跨族比大小）：
#   ① 同 stem 换扩展名：soffice.exe → soffice.bin（实测 523KB → 2.73MB）
#   ② 去 launcher 后缀：blender-launcher.exe → blender.exe（实测 335KB → 112MB）
#   ③ 同名跨目录：Git\cmd\git.exe → Git\mingw64\bin\git.exe（实测 43KB → 4.38MB）
# 反面教训（为什么不用"同目录体积最大"）：
#   LibreOffice 同目录最大的是 xpdfimport.exe（PDF 导入过滤器），本体 soffice.bin 反而更小；
#   Git\cmd 同目录最大的是 gitk.exe（图形界面），本体根本不在同目录。跨族比大小必然误判。
# 找不到同族/同名更大者 → 按本体记录，不硬猜（宁缺勿假）。
_MACHINE = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0x01C4: "arm"}
_SUBSYSTEM = {2: "GUI", 3: "Console"}
_LAUNCHER_HINT = ("launcher", "stub", "boot", "update", "helper", "setup", "install")
_OUT_KEYS = ("size", "size_bytes", "arch", "bits", "subsystem", "role",
             "body_hint", "name_hint", "ver_note")
_BIG = 5 * 1024 * 1024  # 5MB：远超启动器的体积，作为"不可能是启动器"的下限参考
_WALK_MAX_DIRS = 4000   # 向上搜索熔断：最多扫描这么多目录，避免大目录拖慢


def _fmt_size(n: int) -> str:
    if n >= 1024 ** 3:
        return "%.2f GB" % (n / 1024.0 ** 3)
    if n >= 1024 ** 2:
        return "%.2f MB" % (n / 1024.0 ** 2)
    if n >= 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%d B" % n


def _read_pe(path: str) -> dict:
    """只读文件头解析 PE 架构与子系统（零依赖）。非 PE / 读不到返回空 dict（如实）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return {}
    if len(head) < 0x40 or head[:2] != b"MZ":
        return {}
    try:
        off = int.from_bytes(head[0x3C:0x40], "little")
        if off < 0x40 or off + 0x5A > len(head) or head[off:off + 4] != b"PE\x00\x00":
            return {}
        machine = int.from_bytes(head[off + 4:off + 6], "little")
        magic = int.from_bytes(head[off + 24:off + 26], "little")
        sub = int.from_bytes(head[off + 92:off + 94], "little")  # off + 24(opt) + 68
    except (IndexError, ValueError):
        return {}
    return {"arch": _MACHINE.get(machine, "0x%04X" % machine),
            "bits": {0x10B: 32, 0x20B: 64}.get(magic, 0),
            "subsystem": _SUBSYSTEM.get(sub, "sub%d" % sub)}


def _stem(name: str) -> str:
    return os.path.splitext(name)[0].lower()


def _strip_launcher(name: str) -> str:
    """去掉启动器词根后缀，返回可能的本体名；无则空串。blender-launcher.exe → blender"""
    s = _stem(name)
    for h in _LAUNCHER_HINT:
        for sep in ("-", "_", "."):
            suf = sep + h
            if s.endswith(suf) and len(s) > len(suf):
                return s[: -len(suf)]
    return ""


def _walk_same_name(root: str, base: str, size: int, max_depth: int = 3):
    """在 root 下（限深、限目录数）找同名且更大的可执行体。返回 (体积, 路径) 或 None。"""
    if not root or not os.path.isdir(root):
        return None
    root_depth = root.rstrip("\\/").count(os.sep)
    best = None
    scanned = 0
    for dp, dns, fns in os.walk(root):
        scanned += 1
        if scanned > _WALK_MAX_DIRS:
            break
        if dp.count(os.sep) - root_depth >= max_depth:
            dns[:] = []
            continue
        for n in fns:
            if n.lower() != base:
                continue
            fp = os.path.join(dp, n)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if sz > size and (best is None or sz > best[0]):
                best = (sz, fp)
    return best


def _find_body(path: str, size: int):
    """寻找"本体"证据。返回 (体积, 本体路径, 判据说明) 或 None（宁缺勿假）。"""
    d = os.path.dirname(path)
    base = os.path.basename(path)
    lower = base.lower()
    stem = _stem(base)

    # ① 同目录同 stem 换扩展（soffice.exe → soffice.bin）
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    for n in names:
        if n.lower() == lower or _stem(n) != stem:
            continue
        fp = os.path.join(d, n)
        try:
            if os.path.isfile(fp) and os.path.getsize(fp) > size:
                return (os.path.getsize(fp), fp, "同 stem 换扩展")
        except OSError:
            continue

    # ② 去 launcher 后缀（blender-launcher.exe → blender.exe）
    bare = _strip_launcher(lower)
    if bare:
        for ext in (".exe", ".bin", ".com"):
            fp = os.path.join(d, bare + ext)
            try:
                if os.path.isfile(fp) and os.path.getsize(fp) > size:
                    return (os.path.getsize(fp), fp, "去 launcher 后缀")
            except OSError:
                continue

    # ③ 同名跨目录：向上最多 2 层，每层限深 3（Git\cmd\git.exe → Git\mingw64\bin\git.exe）
    up = os.path.dirname(d)
    for _ in range(2):
        if not up or up == os.path.dirname(up):
            break
        hit = _walk_same_name(up, lower, size)
        if hit:
            return (hit[0], hit[1], "同名跨目录")
        up = os.path.dirname(up)
    return None


def _inspect_exe(path: str) -> dict:
    """DIR 校验：记录体积 + PE 类型（架构/子系统），判定 launcher（启动器）vs body（本体）。
    判据：同族命名 > 同名跨目录；体积只在同族/同名内比较。定位不到则如实返回空 dict。"""
    if not path:
        return {}
    try:
        size = os.path.getsize(path)
    except OSError:
        return {}
    base = os.path.basename(path)
    info = {"size_bytes": size, "size": _fmt_size(size)}
    info.update(_read_pe(path))

    if _strip_launcher(base.lower()):
        info["name_hint"] = "名字含启动器词根"

    body = _find_body(path, size)
    if body:
        bsize, bpath, why = body
        info["role"] = "launcher"
        info["body_hint"] = "%s (%s, %s)" % (os.path.basename(bpath), _fmt_size(bsize), why)
    else:
        info["role"] = "body"
        if size < _BIG:
            info["ver_note"] = "未找到更大的同族/同名可执行体，按入口本体记录"
    return info


# 版本从安装文件读（避开 GUI 进程）：这类 GUI 启动器不带 --headless 会常驻不退，
# 跑 --version 只会超时（实测 soffice.exe --version 挂死 >25s），版本必须从安装文件读。
_VER_FROM_FILE: dict = {"LibreOffice": ("bootstrap.ini", "ProductKey")}


def _file_hint(name: str):
    low = (name or "").lower()
    for k, v in _VER_FROM_FILE.items():
        if k.lower() in low:
            return v
    return None


def _read_version_file(path: str, name: str) -> str:
    """从 exe 同目录的 ini 读版本号（避开发起 GUI 进程）。读不到返回空串。"""
    hint = _file_hint(name)
    if not hint or not path:
        return ""
    ini, key = hint
    f = os.path.join(os.path.dirname(path), ini)
    try:
        for line in io.open(f, encoding="utf-8", errors="replace"):
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip().lower() == key.lower():
                    m = _VER_RE.search(v)
                    return m.group(1) if m else v.strip()
    except OSError:
        return ""
    return ""


def _find_one(exe: str, candidates: list, globs: list, name: str = "") -> str:
    """定位可执行文件：注册表（权威/抗升级）→ 固定路径 → glob → PATH。找不到返回空串（如实）。"""
    reg = _from_registry(exe, name)
    if reg:
        return reg
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
        path = _find_one(exe, cands, globs, name)
        ver = ""
        info: dict = {}
        if path:
            info = _inspect_exe(path)
            ver = _read_version_file(path, name) if _file_hint(name) else _read_version(path, ver_args)
        if not ver and info.get("subsystem") == "GUI":
            info["ver_note"] = "GUI 子系统：不带 --headless 跑 --version 易挂死"
        out.append({"name": name, "category": cat, "installed": bool(path),
                    "path": path, "version": ver, **info})
    return out


@tool(
    "app_probe",
    "软件安装目录探查（素月核心能力，独立于 MCP）：实时定位本机关键软件的可执行文件路径和版本号"
    "（办公 LibreOffice/Office/WPS、3D Blender/Godot、开发 Git/Node/Python/Go/VS Code、"
    "浏览器 Chrome/Edge、媒体 ffmpeg）。用于「判定本机装了什么、版本是否达标 → 决定用 MCP 哪个接口」。"
    "与 sys_probe 区别：sys_probe 是注册表全量清单（快照），本工具是目标软件路径+版本实时定位。"
    "与 MCP 解耦：只回答本机有什么；用哪个 MCP 接口由你结合 mcp_list 判定后再 mcp_connect 激活。"
    "版本号由 --version 类命令实时读取，读不到为空串（如实）。默认查全部；可传 apps 只查指定软件。"
    "定位后做 DIR 校验：记录文件体积与 PE 类型（架构/子系统），并识别 launcher（启动器）vs body（本体）"
    "——体积与类型是比文件名更强的真身特征，同目录里靠它一眼分出本体。",
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
                    "version": s["version"],
                    **{k: s[k] for k in _OUT_KEYS if k in s}} for s in installed],
        "missing": [s["name"] for s in software if not s["installed"]],
        "summary": {"installed": len(installed), "checked": len(software)},
        "note": "结果实时来自本机；用哪个 MCP 接口请结合 mcp_list 判定，再 mcp_connect 激活。",
    }
