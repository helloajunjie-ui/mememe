# -*- coding: utf-8 -*-
"""素月启动器（系统托盘常驻）—— 双击无窗、开箱即用。

解决"双击 bat 闪黑窗 / 网页关了找不到她 / 停了又被 watchdog 拉起"的问题：

    双击「素月启动器.pyw」→ 系统托盘出现图标（蓝底"月"）
    ├─ 悬停气泡：实时状态（运行中/已停止/初始化中 + 记忆/方法论）
    ├─ 左键单击：打开网页 http://127.0.0.1:8765
    └─ 右键菜单：
         ├─ 打开界面
         ├─ 状态（只读，动态刷新）
         ├─ 启动素月（已停止时显示）
         ├─ 重启素月
         ├─ 停止素月      ← 先写 data/stop.flag 再关进程，watchdog 不会再拉起
         ├─ 立即备份      ← 本地 zip + 云端 AES 双保险
         ├─ 打开日志目录
         └─ 退出启动器    ← 仅退出托盘，不停止素月

逻辑衔接（复用现有链路，不另起炉灶）：
- 启动/重启 → 拉起 launcher.py（pythonw 无窗），gateway 由 webui 自检拉起
- 停止     → 写 stop.flag + 杀 launcher 进程树（含 webui）+ 停 gateway
- 单实例   → Global\SuyueTrayMutex，重复双击只提示不重复常驻
- 进程定位 → Win32_Process 命令行匹配（不依赖 psutil，避免加依赖）

pythonw 下无控制台：运行日志写 logs/launcher_tray.log。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
LAUNCHER = os.path.join(ROOT, "launcher.py")
STOP_FLAG = os.path.join(ROOT, "data", "stop.flag")
LOG = os.path.join(ROOT, "logs", "launcher_tray.log")
LOGS_DIR = os.path.join(ROOT, "logs")
BACKUP_PY = os.path.join(ROOT, "tools", "src", "python", "backup_private.py")
CLOUD_PY = os.path.join(ROOT, "tools", "src", "python", "cloud_backup.py")
WEB_URL = "http://127.0.0.1:8765"
PORT = 8765
GATEWAY_PORT = 8766
MUTEX_NAME = "Global\\SuyueTrayMutex"
TAKEOVER_FLAG = os.path.join(ROOT, "data", "takeover.flag")
_CREATE_NO_WINDOW = 0x08000000

try:
    from PIL import Image, ImageDraw, ImageFont
    import pystray
    from pystray import MenuItem as _MI
    _AVAILABLE = True
except Exception:  # noqa: BLE001
    _AVAILABLE = False

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_font(size: int):
    for p in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def make_icon():
    """蓝底圆角方块 + 白色"月"字（与 webui/tray.py 一致的视觉）。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 63, 63], radius=14, fill=(59, 130, 246, 255))
    font = _load_font(40)
    bbox = d.textbbox((0, 0), "月", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((32 - w / 2 - bbox[0], 32 - h / 2 - bbox[1]), "月",
           font=font, fill=(255, 255, 255, 255))
    return img


def already_running() -> bool:
    """单实例互斥：已有启动器在跑 → 本进程直接退出。"""
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        return err == 183  # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return False


# ---------- 状态检测 ----------

def port_listening(port: int) -> bool:
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:  # noqa: BLE001
        return False


def _pids_by_cmdline(kw: str, name_filter: str = "") -> list:
    """按命令行关键字找 PID（Win32_Process，免 psutil 依赖）。

    name_filter 限定进程名，避免本查询拉起的 powershell 子进程因命令行
    含关键字而被递归自匹配。
    """
    name_cond = " -and $_.Name -eq '" + name_filter + "'" if name_filter else ""
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*"
        + kw.replace("'", "''")
        + "*'" + name_cond
        + " } | Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=20,
            creationflags=_CREATE_NO_WINDOW,
        )
        # Windows PowerShell 输出可能为 GBK：bytes 解码容错，PID 纯数字不受影响
        text = out.stdout.decode("utf-8", errors="ignore")
        return [int(x.strip()) for x in text.splitlines() if x.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


def webui_pids() -> list:
    """webui/server.py 进程（launcher 的子进程）。"""
    return _pids_by_cmdline("webui*server.py", "pythonw.exe")


def launcher_pids() -> list:
    """launcher.py 进程。"""
    return _pids_by_cmdline("launcher.py", "pythonw.exe")


def gateway_pids() -> list:
    """bailing-gateway.exe 进程。"""
    return _pids_by_cmdline("bailing-gateway.exe", "bailing-gateway.exe")


def mcp_pids() -> list:
    """mcp-service/server.py 进程。"""
    return _pids_by_cmdline("mcp-service*server.py", "pythonw.exe")


def tray_pids(exclude_self: bool = True) -> list:
    """素月启动器进程（本 pyw）。"""
    me = os.getpid() if exclude_self else -1
    return [pid for pid in _pids_by_cmdline("素月启动器.pyw", "pythonw.exe")
            if pid != me]


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=20,
                       creationflags=_CREATE_NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass


def _kill_all() -> list:
    """杀全部素月实例：旧托盘 + launcher 树 + webui + gateway + mcp。返回被杀 PID 列表。"""
    killed = []
    for pid in tray_pids() + launcher_pids() + webui_pids() + mcp_pids() + gateway_pids():
        if pid and pid not in killed:
            _kill_tree(pid)
            killed.append(pid)
    return killed


def act_cleanup(icon=None, item=None):
    """清理：杀掉当前所有素月进程（含旧托盘），保持单例。"""
    try:
        killed = _kill_all()
        log(f"单例清理：共清理 {len(killed)} 个进程: {killed}")
        time.sleep(1.0)
        _notify("素月已清理", f"已关闭 {len(killed)} 个旧实例进程。")
    except Exception as e:  # noqa: BLE001
        log(f"清理失败：{e}")
        _notify("清理失败", str(e))


def status() -> str:
    """状态摘要：running / stopped / init / unknown。"""
    if port_listening(PORT):
        return "running"
    if port_listening(GATEWAY_PORT):
        return "init"
    if os.path.exists(STOP_FLAG):
        return "stopped"
    return "down"


def status_lines() -> list:
    lines = []
    st = status()
    if st == "running":
        lines.append("● 运行中")
    elif st == "init":
        lines.append("◐ 初始化中...")
    elif st == "stopped":
        lines.append("○ 已停止（手动）")
    else:
        lines.append("○ 未运行")
    try:
        cnt = len(_pids_by_cmdline("webui*server.py", "pythonw.exe"))
        lines.append(f"进程 {cnt}")
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(os.path.join(ROOT, "data", "crash_log.jsonl"),
                  "r", encoding="utf-8") as f:
            tail = [ln for ln in f.read().splitlines() if ln.strip()][-1:]
        if tail:
            rec = json.loads(tail[0])
            lines.append(f"最近崩溃 {rec.get('ts', '?')} rc={rec.get('exit_code')}")
    except Exception:  # noqa: BLE001
        pass
    return lines


# ---------- 动作 ----------

def _spawn_launcher() -> bool:
    """无窗拉起 launcher.py（复用现有监督进程/熔断/单实例逻辑）。"""
    try:
        subprocess.Popen([PYW, LAUNCHER], cwd=ROOT,
                         creationflags=_CREATE_NO_WINDOW)
        log("已拉起 launcher.py")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"拉起 launcher 失败：{e}")
        return False


def act_start(icon=None, item=None):
    """启动：清 stop.flag → 拉起 launcher → 开网页。"""
    try:
        if port_listening(PORT):
            webbrowser.open(WEB_URL)
            return
        try:
            if os.path.exists(STOP_FLAG):
                os.remove(STOP_FLAG)
                log("已清除 stop.flag")
        except OSError:
            pass
        if _spawn_launcher():
            _wait_port()
            webbrowser.open(WEB_URL)
    except Exception as e:  # noqa: BLE001
        log(f"启动失败：{e}")
        _notify("启动失败", str(e))


def act_stop(icon=None, item=None):
    """停止：先写 stop.flag（watchdog 不再拉起）→ 杀进程树。"""
    try:
        os.makedirs(os.path.dirname(STOP_FLAG), exist_ok=True)
        with open(STOP_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " stopped-by-tray\n")
        log("已写入 stop.flag")
        killed = _kill_all()
        log(f"已停止，清理进程 {len(killed)} 个: {killed}")
        _notify("素月已停止", "已写入 stop.flag，后台不会自动拉起。")
    except Exception as e:  # noqa: BLE001
        log(f"停止失败：{e}")
        _notify("停止失败", str(e))


def act_restart(icon=None, item=None):
    """重启：清 stop.flag → 杀进程树 → 拉起 launcher → 开网页。"""
    try:
        try:
            if os.path.exists(STOP_FLAG):
                os.remove(STOP_FLAG)
        except OSError:
            pass
        killed = _kill_all()
        log(f"重启：已清理 {len(killed)} 个进程")
        time.sleep(1.5)
        if _spawn_launcher():
            _wait_port(12)
            webbrowser.open(WEB_URL)
    except Exception as e:  # noqa: BLE001
        log(f"重启失败：{e}")
        _notify("重启失败", str(e))


def _wait_port(seconds: int = 20) -> None:
    for _ in range(seconds * 2):
        if port_listening(PORT):
            return
        time.sleep(0.5)


def act_backup(icon=None, item=None):
    """立即备份：本地 zip + 云端 AES（后台跑，完成后通知）。"""
    if not os.path.exists(BACKUP_PY):
        _notify("备份不可用", "backup_private.py 缺失")
        return

    def run():
        try:
            log("开始每日备份（本地+云端）")
            results = []
            r1 = subprocess.run([PY, BACKUP_PY], cwd=ROOT, capture_output=True,
                                timeout=300, creationflags=_CREATE_NO_WINDOW)
            t1 = r1.stdout.decode("utf-8", errors="ignore").strip()
            results.append(("本地", r1.returncode == 0,
                            t1.splitlines()[-1:] if t1 else r1.stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:]))
            if os.path.exists(CLOUD_PY):
                r2 = subprocess.run([PY, CLOUD_PY, "run"], cwd=ROOT, capture_output=True,
                                    timeout=300, creationflags=_CREATE_NO_WINDOW)
                t2 = r2.stdout.decode("utf-8", errors="ignore").strip()
                results.append(("云端", r2.returncode == 0,
                                t2.splitlines()[-1:] if t2 else r2.stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:]))
            ok = all(r[1] for r in results)
            msg = "\n".join(f"{name}: {'✓' if rc else '✗'} {tail[0] if tail else ''}"
                            for name, rc, tail in results)
            log("备份结束：" + msg)
            _notify("素月备份" + ("完成" if ok else "失败"), msg)
        except Exception as e:  # noqa: BLE001
            log(f"备份异常：{e}")
            _notify("备份异常", str(e))

    threading.Thread(target=run, daemon=True).start()


def act_open_logs(icon=None, item=None):
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        os.startfile(LOGS_DIR)  # noqa: S606
    except Exception:  # noqa: BLE001
        pass


def _notify(title: str, msg: str) -> None:
    try:
        if _icon is not None:
            _icon.notify(msg, title)
    except Exception:  # noqa: BLE001
        log(f"[notify] {title}: {msg}")


def _open_web(icon=None, item=None):
    webbrowser.open(WEB_URL)


_icon = None


def _build_menu():
    st = status()

    def _quit(icon, item):
        icon.stop()

    items = [_MI("打开界面", _open_web, default=True),
             pystray.Menu.SEPARATOR]
    for line in status_lines():
        items.append(_MI("　" + line, _open_web, enabled=False))
    items.append(pystray.Menu.SEPARATOR)
    if st != "running":
        items.append(_MI("启动素月", act_start))
    if st == "running":
        items.append(_MI("重启素月", act_restart))
        items.append(_MI("停止素月", act_stop))
    items += [_MI("立即备份", act_backup),
              _MI("清理旧实例", act_cleanup),
              _MI("打开日志目录", act_open_logs),
              pystray.Menu.SEPARATOR,
              _MI("退出启动器", _quit)]
    return pystray.Menu(*items)


def main() -> None:
    global _icon
    # 强单例：启动前先清掉所有素月实例（旧托盘/launcher/webui/mcp/gateway），
    # 确保本进程成为唯一常驻 + 唯一进程链（避免多托盘图标 / 多实例冲突）
    others = tray_pids() + launcher_pids() + webui_pids() + mcp_pids() + gateway_pids()
    if others:
        old_trays = tray_pids()
        log(f"检测到旧实例 {others}，启动前清理 ...")
        if old_trays:
            # 优雅接管旧托盘：写信号 → 旧托盘检测后移除图标并退出（避免幽灵图标）
            try:
                os.makedirs(os.path.dirname(TAKEOVER_FLAG), exist_ok=True)
                with open(TAKEOVER_FLAG, "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " takeover\n")
                log("已写入接管信号，等待旧托盘优雅退出 ...")
                for _ in range(10):  # 最多等 5 秒
                    if not tray_pids():
                        break
                    time.sleep(0.5)
                try:
                    os.remove(TAKEOVER_FLAG)
                except OSError:
                    pass
            except OSError:
                pass
        killed = _kill_all()
        log(f"旧实例已清理 {len(killed)} 个: {killed}")
        time.sleep(1.0)
    if already_running():
        # 极少数并发竞态下 Mutex 仍被占：提示并退出
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "素月启动器已在运行（见系统托盘）。", "素月",
                0x40 | 0x1000)
        except Exception:  # noqa: BLE001
            pass
        return
    if not _AVAILABLE:
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "托盘依赖缺失（pystray/Pillow），无法启动启动器。", "素月", 0x10)
        except Exception:  # noqa: BLE001
            pass
        return
    log("启动器常驻开始")
    # 首次常驻自动续命：WebUI 未运行且非用户手动停止 → 自动拉起（与 watchdog 语义一致）
    if not port_listening(PORT) and not os.path.exists(STOP_FLAG):
        log("检测到素月 WebUI 未运行且无手动停止标记，自动拉起 ...")
        _spawn_launcher()
    _icon = pystray.Icon(
        "suyue-tray",
        make_icon(),
        title="素月 Suyue",
        menu=_build_menu(),
    )

    def _refresh():
        last_menu = 0.0
        while True:
            time.sleep(4)
            try:
                # 优雅接管：新实例写 takeover.flag 后，旧实例主动退出并移除图标
                if os.path.exists(TAKEOVER_FLAG):
                    log("检测到接管信号，优雅退出（移除图标）...")
                    try:
                        _icon.visible = False
                        _icon.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    return
                st = status()
                _icon.title = {
                    "running": "素月 · 运行中",
                    "init": "素月 · 初始化中...",
                    "stopped": "素月 · 已停止（手动）",
                    "down": "素月 · 未运行",
                }.get(st, "素月")
            except Exception:  # noqa: BLE001
                pass
            if time.time() - last_menu >= 8:
                last_menu = time.time()
                try:
                    _icon.menu = _build_menu()
                    _icon.update_menu()
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(target=_refresh, daemon=True).start()
    _icon.run()
    log("启动器退出")


if __name__ == "__main__":
    main()
