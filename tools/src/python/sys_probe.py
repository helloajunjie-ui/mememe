"""内置工具：sys_probe —— 综合系统探查（静态快照保存，跨平台）。

设计意图：一次调用，把「系统判定 + 环境配置 + 电脑配置 + 已装软件(含版本) + 已有依赖 + 各盘空间」
全量探查并落盘为静态快照 data/sys_profile.json。白绫在「准备创建新工具 / 判断本机能力 / 决定装到哪个盘」
等场景，先读快照即可拿到决策所需事实，不必逐项重复探测（省时间/token）。

跨平台设计（第一步判定系统，再按平台分发）：
- 第一步 `_detect_platform()` 判定 family：windows / linux / darwin / other（依据 platform.system()）。
- 已装软件按 family 分发：windows→注册表(winreg)；linux→dpkg/rpm 包管理器；darwin→brew + /Applications。
- 磁盘按 family 分发：windows→盘符；linux/darwin→挂载点(df)。
- pip/go/GPU(nvidia-smi) 跨平台通用（PATH 探测）。

定位：本工具是环境/系统探查的唯一入口（sys_info / sys_tools / cpu_usage 已并入并废弃）。
一次调用即拿全「系统判定 + 环境配置 + 电脑配置 + 已装软件(含版本) + 已有依赖 + 各盘空间」，
供工具创作/能力评估做决策。

技术债说明（如实边界）：
- Windows 软件清单仅覆盖 HKLM Uninstall 键（32/64 位），不含 HKCU 用户级与 UWP 应用。
- Linux 软件清单来自 dpkg/rpm 包管理器（需有权限读取），不含 flatpak/snap 等其它来源。
- macOS 软件清单来自 brew 与 /Applications 目录名（无版本号，仅名称）。
- pip 依赖仅统计当前解释器可见的包。
- 各平台能拿到的字段不同，缺失即如实为空/None，不编造。
"""
from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import string
import subprocess
from datetime import datetime

from tools.base import tool

# 快照路径：self-agent/data/sys_profile.json
_SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "sys_profile.json")


# ---------- 第一步：系统判定 ----------

def _detect_platform() -> str:
    """判定系统 family：windows / linux / darwin / other。"""
    system = _platform.system().lower()
    if system in ("windows", "linux", "darwin"):
        return system
    return "other"


def _linux_distro() -> dict:
    """Linux 发行版精确识别（借鉴 distro 库读 /etc/os-release 的做法，用标准库实现）。

    返回 {id, version_id, pretty_name}；读不到则回退 platform 的粗略值（如实）。
    """
    os_release = "/etc/os-release"
    fields = {}
    if os.path.exists(os_release):
        try:
            with open(os_release, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    fields[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    if fields:
        return {
            "id": fields.get("ID"),
            "version_id": fields.get("VERSION_ID"),
            "pretty_name": fields.get("PRETTY_NAME"),
        }
    # 回退：无 /etc/os-release（如实标注为粗略值）
    return {"id": _platform.system().lower(), "version_id": _platform.release(),
            "pretty_name": None}


# ---------- 已装软件（按平台分发） ----------

def _apps_windows() -> dict:
    """Windows：注册表 Uninstall 键（HKLM 32/64 位）已装软件含版本。"""
    import winreg
    keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    apps = []
    seen = set()
    for k in keys:
        try:
            base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, k)
        except OSError:
            continue
        try:
            n = winreg.QueryInfoKey(base)[0]
            for i in range(n):
                try:
                    sub = winreg.EnumKey(base, i)
                    h = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, k + "\\" + sub)
                    name = winreg.QueryValueEx(h, "DisplayName")[0]
                    ver = ""
                    try:
                        ver = winreg.QueryValueEx(h, "DisplayVersion")[0]
                    except OSError:
                        pass
                    if name and name not in seen:
                        seen.add(name)
                        apps.append({"name": name, "version": ver})
                except OSError:
                    continue
        finally:
            winreg.CloseKey(base)
    apps.sort(key=lambda a: a["name"].lower())
    return {"source": "winreg", "apps": apps}


def _apps_linux() -> dict:
    """Linux：dpkg（Debian 系）或 rpm（RedHat 系）包管理器已装包含版本。"""
    # Debian/Ubuntu 系：dpkg-query -W -f='${Package} ${Version}\n'
    dpkg = shutil.which("dpkg-query")
    if dpkg:
        try:
            r = subprocess.run([dpkg, "-W", "-f=${Package} ${Version}\\n"],
                               capture_output=True, text=True, timeout=30, errors="replace")
            if r.returncode == 0:
                apps = []
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        apps.append({"name": parts[0], "version": parts[1]})
                return {"source": "dpkg", "apps": apps}
        except Exception:  # noqa: BLE001
            pass
    # RedHat/Fedora 系：rpm -qa --qf='%{NAME} %{VERSION}\n'
    rpm = shutil.which("rpm")
    if rpm:
        try:
            r = subprocess.run([rpm, "-qa", "--qf=%{NAME} %{VERSION}\\n"],
                               capture_output=True, text=True, timeout=30, errors="replace")
            if r.returncode == 0:
                apps = []
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        apps.append({"name": parts[0], "version": parts[1]})
                return {"source": "rpm", "apps": apps}
        except Exception:  # noqa: BLE001
            pass
    return {"source": "none", "apps": []}


def _apps_darwin() -> dict:
    """macOS：brew 已装包（含版本）+ /Applications 应用目录名（无版本）。"""
    apps = []
    source = "applications"
    brew = shutil.which("brew")
    if brew:
        try:
            r = subprocess.run([brew, "list", "--versions"], capture_output=True,
                               text=True, timeout=30, errors="replace")
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if parts:
                        apps.append({"name": parts[0], "version": parts[1] if len(parts) > 1 else ""})
                source = "brew+applications"
        except Exception:  # noqa: BLE001
            pass
    # /Applications 下的 .app（补充 GUI 应用，无版本号）
    seen = {a["name"] for a in apps}
    for d in ("/Applications", os.path.expanduser("~/Applications")):
        if os.path.isdir(d):
            try:
                for f in sorted(os.listdir(d)):
                    if f.endswith(".app"):
                        name = f[:-4]
                        if name not in seen:
                            seen.add(name)
                            apps.append({"name": name, "version": ""})
            except OSError:
                pass
    return {"source": source, "apps": apps}


def _probe_installed_apps(family: str) -> dict:
    """按系统 family 分发已装软件探测。"""
    if family == "windows":
        return _apps_windows()
    if family == "linux":
        return _apps_linux()
    if family == "darwin":
        return _apps_darwin()
    return {"source": "none", "apps": []}


# ---------- 磁盘（psutil 风格统一结构，内部按平台实现） ----------
# 借鉴 psutil.disk_partitions()+disk_usage() 的抽象：调用方只面对统一结构
# {device, mount, fstype, total_gb, used_gb, free_gb, percent}，不感知平台差异。
# 实现仍用标准库 + 原生命令，不引入 psutil 依赖（符合"现有优先"哲学）。

def _disk_entry(device: str, mount: str, fstype: str, total: int, used: int, free: int) -> dict:
    """把字节数归一为统一磁盘条目（psutil 风格）。"""
    total_gb = round(total / 2**30, 1)
    used_gb = round(used / 2**30, 1)
    free_gb = round(free / 2**30, 1)
    percent = round(used / total * 100, 1) if total else None
    return {"device": device, "mount": mount, "fstype": fstype,
            "total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb, "percent": percent}


def _disks_windows() -> list:
    """Windows：盘符空间（device=盘符，fstype 留空——Windows 无统一易读 fstype 原生命令）。"""
    out = []
    for d in string.ascii_uppercase:
        p = d + ":\\"
        if os.path.exists(p):
            try:
                t, u, f = shutil.disk_usage(p)
                out.append(_disk_entry(d + ":", d + ":", "", t, u, f))
            except OSError:
                pass
    return out


def _disks_posix() -> list:
    """Linux/macOS：挂载点空间（df -P 解析，跨平台稳定；fstype 从 df -T 或 /proc/mounts 补）。"""
    # 先收集 fstype 映射（Linux /proc/mounts；macOS 无则留空）
    fstype_map = {}
    if os.path.exists("/proc/mounts"):
        try:
            with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        fstype_map[parts[1]] = parts[2]  # mountpoint -> fstype
        except OSError:
            pass
    out = []
    try:
        r = subprocess.run(["df", "-P"], capture_output=True, text=True,
                           timeout=15, errors="replace")
        if r.returncode == 0:
            lines = r.stdout.splitlines()
            for line in lines[1:]:  # 跳过表头
                parts = line.split()
                if len(parts) < 6:
                    continue
                # df -P: Filesystem 1024-blocks Used Available Capacity Mounted on
                try:
                    total_kb = int(parts[1])
                    used_kb = int(parts[2])
                    avail_kb = int(parts[3])
                    device = parts[0]
                    mount = " ".join(parts[5:])
                    fstype = fstype_map.get(mount, "")
                    out.append(_disk_entry(device, mount, fstype,
                                           total_kb * 1024, used_kb * 1024, avail_kb * 1024))
                except (ValueError, IndexError):
                    continue
    except Exception:  # noqa: BLE001
        pass
    return out


def _probe_disks(family: str) -> list:
    """按系统 family 分发磁盘空间探测（统一返回 psutil 风格结构）。"""
    if family == "windows":
        return _disks_windows()
    return _disks_posix()


# ---------- 跨平台通用探测 ----------

def _probe_pip_packages() -> list:
    """当前解释器可见的 pip 包（含版本）。"""
    pip = shutil.which("pip")
    if not pip:
        return []
    try:
        r = subprocess.run([pip, "list", "--format=json"], capture_output=True,
                           text=True, timeout=30, errors="replace")
        if r.returncode != 0:
            return []
        return [{"name": p["name"], "version": p["version"]} for p in json.loads(r.stdout)]
    except Exception:  # noqa: BLE001
        return []


def _probe_gpu() -> str | None:
    """GPU 探测（nvidia-smi 首卡）。无 nvidia-smi 返回 None（如实，如 macOS/AMD 卡）。"""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        r = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=15, errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0].strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _probe_go() -> dict:
    """Go 工具链探测。"""
    go = shutil.which("go")
    if not go:
        return {"installed": False, "version": None}
    try:
        r = subprocess.run([go, "version"], capture_output=True, text=True,
                           timeout=15, errors="replace")
        ver = (r.stdout or r.stderr or "").strip() or None
    except Exception:  # noqa: BLE001
        ver = None
    return {"installed": True, "version": ver}


# ---------- 全量综合探查 ----------

def _full_probe() -> dict:
    """全量综合探查（一次落盘）。第一步判定系统 family，再按平台分发。"""
    from core.deps import env_probe  # 复用启动画像的基础探测

    family = _detect_platform()  # 第一步：系统判定
    base = env_probe.full_probe()  # os/arch/cpu/memory/python/network
    osinfo = base.get("os", {})
    python = base.get("python", {})
    go = _probe_go()
    disks = _probe_disks(family)
    sw = _probe_installed_apps(family)
    pkgs = _probe_pip_packages()
    gpu = _probe_gpu()

    # 平台输出：linux 时并入发行版精确识别（借鉴 distro 库读 /etc/os-release），
    # windows/darwin/other 无 distro 字段（如实，不编造）。
    platform_out = {
        "family": family,  # windows / linux / darwin / other
        "system": osinfo.get("system"),
        "version": osinfo.get("version"),
        "release": osinfo.get("release"),
        "shell": osinfo.get("shell"),
        "arch": base.get("arch"),
    }
    if family == "linux":
        platform_out["distro"] = _linux_distro()

    # 内存统一为 psutil.virtual_memory 风格：total/used/free + percent（调用方不感知平台）。
    mem = base.get("memory_gb", {}) or {}
    mem_total = mem.get("total_gb")
    mem_free = mem.get("free_gb")
    mem_used = None
    mem_percent = None
    if mem_total is not None and mem_free is not None:
        mem_used = round(mem_total - mem_free, 1)
        if mem_total > 0:
            mem_percent = round(mem_used / mem_total * 100, 1)
    memory_out = {
        "total_gb": mem_total,
        "used_gb": mem_used,
        "free_gb": mem_free,
        "percent": mem_percent,
    }

    # 工具创作可行性事实：只报工具链可用性 + 本体绝对路径，不做"推荐装哪个盘"的决策。
    # 各盘空间已在 disks 给出，白绫对照本体路径所在盘自行判断（五问决策，不越俎代庖）。
    project_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
    toolchain = {
        "go_ok": bool(go.get("installed")),
        "python_ok": bool(python.get("version")),
        "project_root": project_root,
        "note": "工具实际写入项目根目录（tools/src/python、tools/src/go、tools/bin）。"
                "落盘空间对照 project_root 所在盘与 disks 各盘空间自行判断。",
    }

    return {
        "probed_at": datetime.now().isoformat(),
        "platform": platform_out,
        "hardware": {
            "cpu_cores": base.get("cpu_cores"),
            "cpu_processor": base.get("cpu_processor"),
            "memory_gb": memory_out,
            "gpu": gpu,
        },
        "disks": disks,
        "software": {
            "source": sw.get("source"),
            "installed_apps": sw.get("apps", []),
            "installed_app_count": len(sw.get("apps", [])),
        },
        "deps": {
            "python": {"version": python.get("version"), "pip_packages": pkgs,
                       "pip_count": len(pkgs)},
            "go": go,
        },
        "network": base.get("network", {}),
        "toolchain": toolchain,
    }


def _load_snapshot() -> dict:
    if os.path.exists(_SNAP):
        try:
            with open(_SNAP, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {}


def _save_snapshot(profile: dict) -> None:
    os.makedirs(os.path.dirname(_SNAP), exist_ok=True)
    with open(_SNAP, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


@tool(
    "sys_probe",
    "综合系统探查（跨平台）：第一步判定系统（windows/linux/darwin/other），再按平台获取"
    "环境配置 + 电脑配置 + 已装软件(含版本) + 已有依赖(pip/go) + 各盘空间 + GPU，"
    "并落盘为静态快照 data/sys_profile.json。用于「准备创建新工具 / 判断本机能力 / 决定装到哪个盘」等决策。"
    "默认读快照（省时间/token）；需要最新时传 refresh=true 重新全量探测。"
    "注：已装软件来源随平台而异（winreg/dpkg/rpm/brew），缺失即如实为空，不编造。",
    {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean",
                        "description": "是否强制重新全量探测并刷新快照（默认 false，读快照）"},
        },
        "required": [],
    },
)
def run(refresh: bool = False) -> dict:
    if refresh or not os.path.exists(_SNAP):
        profile = _full_probe()
        _save_snapshot(profile)
        return {"ok": True, "from_cache": False, **profile}
    profile = _load_snapshot()
    if not profile:
        profile = _full_probe()
        _save_snapshot(profile)
        return {"ok": True, "from_cache": False, **profile}
    return {"ok": True, "from_cache": True, **profile}
