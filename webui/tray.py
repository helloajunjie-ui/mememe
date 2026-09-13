# -*- coding: utf-8 -*-
"""白绫系统托盘 —— 常驻入口。

解决"网页关了找不到她 / 不知道怎么停止"的问题：
- 托盘图标常驻（服务启动即有，与浏览器无关）
- 悬停气泡（tooltip）实时显示她的状态：运行模式 / 记忆数 / 方法论数 / 情绪
- 左键单击 = 打开网页；右键菜单 = 打开网页 / 状态（实时刷新）/ 退出白绫
- 状态直接显示在菜单里（不依赖 Windows 通知气泡——旧式 balloon 常被系统抑制）
- 退出托盘 = 停止服务（干净的收尾：保存状态、关闭 Agent）

依赖：pystray + Pillow（见 requirements.txt）。未安装时由 server.py 容错降级，
不影响服务本身。

运行方式（server.py 主线程调用）：
    run_tray(url="http://127.0.0.1:8765", on_quit=server.shutdown)
    # 阻塞直到用户从托盘退出
"""
from __future__ import annotations

import sys
import threading
import time
import webbrowser

try:
    from PIL import Image, ImageDraw, ImageFont
    import pystray
    from pystray import MenuItem as _MI
    _AVAILABLE = True
except Exception:  # noqa: BLE001
    _AVAILABLE = False

_ICON_SIZE = 64
_TITLE_REFRESH_S = 5   # 悬停气泡刷新间隔
_MENU_REFRESH_S = 10   # 菜单状态刷新间隔

# 字体候选（Windows 微软雅黑 / 黑体；找不到退回默认字体）
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def _load_font(size: int):
    for p in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def make_icon() -> "Image.Image":
    """生成托盘图标：蓝底圆角方块 + 白色"绫"字。"""
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, _ICON_SIZE - 1, _ICON_SIZE - 1],
                        radius=14, fill=(59, 130, 246, 255))
    font = _load_font(40)
    bbox = d.textbbox((0, 0), "绫", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((_ICON_SIZE - w) / 2 - bbox[0], (_ICON_SIZE - h) / 2 - bbox[1]),
           "绫", font=font, fill=(255, 255, 255, 255))
    return img


def _srv():
    """拿到 server 模块实例。

    真实运行中 server.py 是 __main__（`python webui/server.py`），必须优先复用
    __main__，否则 `import server` 会重新加载出第二个实例（_ready 恒为 False，
    状态永远显示"初始化中"）。兼容 `python -m webui.server` 与测试进程。
    """
    if _srv.cache is not None:
        return _srv.cache
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and hasattr(main_mod, "_ready") and hasattr(main_mod, "get_agent"):
        _srv.cache = main_mod
        return main_mod
    try:
        import server as srv  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        try:
            from webui import server as srv  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            srv = None
    _srv.cache = srv
    return srv


_srv.cache = None


def _status_text() -> str:
    """组装悬停气泡文本（容错：任何异常都回退到基础文案）。"""
    try:
        srv = _srv()
        if srv is None or not srv._ready:
            return "白绫 Bailing · 初始化中..."
        a = srv.get_agent()
        emo = ""
        try:
            snap = a.emotion.snapshot()
            emo = snap.get("current") or snap.get("mood") or snap.get("mood_name") or ""
        except Exception:  # noqa: BLE001
            emo = ""
        mode = ""
        try:
            bm = getattr(a, "boot_mode", "") or ""
            if bm:
                mode = {"FIRST_BOOT": "首次觉醒", "RECOVERY_BOOT": "恢复启动",
                        "NORMAL_BOOT": "正常启动"}.get(bm, bm) + " · "
        except Exception:  # noqa: BLE001
            pass
        parts = [f"白绫 Bailing · {mode}运行中",
                 f"记忆 {srv.memory_count()}", f"方法论 {srv.method_count()}"]
        if emo:
            parts.append(f"情绪 {emo}")
        parts.insert(0, f"状态：{_agent_activity()}")
        return " · ".join(parts)
    except Exception:  # noqa: BLE001
        return "白绫 Bailing"


def _uptime() -> str:
    """运行时长（自 server 模块记录的服务启动时刻）。"""
    try:
        srv = _srv()
        if srv is None or not getattr(srv, "_started_at", None):
            return ""
        sec = int(time.time() - srv._started_at)
        h, m = divmod(sec // 60, 60)
        if h:
            return f"运行 {h}h{m:02d}m"
        return f"运行 {m}m{sec % 60:02d}s"
    except Exception:  # noqa: BLE001
        return ""


def _agent_activity() -> str:
    """当前活动状态（agent.activity → 中文标签）。

    映射：idle=空闲ing / thinking=思考ing / tool=任务执行ing（查询类工具显示"正在查询…"）
    """
    _QUERY_HINTS = ("search", "query", "查", "搜", "检", "lookup", "retrieve",
                    "weather", "stock", "quote", "行情", "汇率", "fetch", "get_", "read_")
    try:
        srv = _srv()
        if srv is None or not srv._ready:
            return "初始化ing"
        act = srv.get_agent().activity or {}
        state = act.get("state") or "idle"
        detail = act.get("detail") or ""
        if state == "thinking":
            return "思考ing"
        if state == "tool":
            if any(h in detail for h in _QUERY_HINTS):
                return "正在查询…"
            return f"任务执行ing · {detail}" if detail else "任务执行ing"
        return "空闲ing"
    except Exception:  # noqa: BLE001
        return "空闲ing"


def _status_lines() -> list:
    """状态行（菜单展示用）：["当前状态：空闲ing", "记忆 227 · 方法论 74 · 情绪 calm", "运行 1h23m", ...]"""
    try:
        srv = _srv()
        if srv is None or not srv._ready:
            return ["初始化中..."]
        lines = [f"当前状态：{_agent_activity()}"]
        mid = f"记忆 {srv.memory_count()} · 方法论 {srv.method_count()}"
        try:
            a = srv.get_agent()
            snap = a.emotion.snapshot()
            emo = snap.get("current") or snap.get("mood") or ""
            if emo:
                mid += f" · 情绪 {emo}"
        except Exception:  # noqa: BLE001
            pass
        lines.append(mid)
        up = _uptime()
        if up:
            lines.append(up)
        return lines
    except Exception:  # noqa: BLE001
        return ["状态读取失败"]


def _full_status() -> str:
    """状态速览（通知气泡用，尽力而为）。"""
    try:
        srv = _srv()
        line = []
        if srv is not None:
            up = _uptime()
            if up:
                line.append(up)
            if srv._ready:
                line.append(f"记忆 {srv.memory_count()}")
                line.append(f"方法论 {srv.method_count()}")
                try:
                    a = srv.get_agent()
                    snap = a.emotion.snapshot()
                    emo = snap.get("current") or snap.get("mood") or ""
                    if emo:
                        line.append(f"情绪 {emo}")
                except Exception:  # noqa: BLE001
                    pass
            else:
                line.append("初始化中")
        return "白绫 Bailing\n" + " · ".join(x for x in line if x)
    except Exception:  # noqa: BLE001
        return "白绫 Bailing"


def _build_menu(url: str, on_quit):
    """构建托盘菜单：打开网页 / 状态（动态刷新）/ 退出。"""

    def _open_web(icon=None, item=None):
        webbrowser.open(url)

    def _show_status(icon, item):
        # 尽力而为的通知气泡（部分系统抑制旧式 balloon，菜单本身已显示状态）
        try:
            icon.notify(_full_status(), "白绫 Bailing")
        except Exception:  # noqa: BLE001
            pass

    def _quit(icon, item):
        icon.stop()
        if on_quit:
            try:
                on_quit()
            except Exception:  # noqa: BLE001
                pass

    items = [_MI("打开网页", _open_web, default=True)]
    status_lines = _status_lines()
    if status_lines:
        items.append(pystray.Menu.SEPARATOR)
        for line in status_lines:
            items.append(_MI("● " + line, _show_status, enabled=True))
    items += [pystray.Menu.SEPARATOR, _MI("退出白绫", _quit)]
    return pystray.Menu(*items)


def run_tray(url: str, on_quit=None) -> None:
    """启动托盘并阻塞，直到用户选择"退出"。

    参数：
        url: 网页地址（左键/菜单"打开网页"用）
        on_quit: 退出回调（server.py 传服务关闭逻辑）；None 时只退出托盘进程。
    """
    if not _AVAILABLE:
        return

    icon = pystray.Icon(
        "bailing",
        make_icon(),
        title=_status_text(),
        menu=_build_menu(url, on_quit),
    )

    # 悬停气泡 + 菜单状态实时刷新（不打断托盘事件循环）
    def _refresh():
        last_menu = 0.0
        while True:
            time.sleep(_TITLE_REFRESH_S)
            try:
                icon.title = _status_text()
            except Exception:  # noqa: BLE001
                pass
            if time.time() - last_menu >= _MENU_REFRESH_S:
                last_menu = time.time()
                try:
                    icon.menu = _build_menu(url, on_quit)
                    icon.update_menu()
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(target=_refresh, daemon=True).start()
    icon.run()


def available() -> bool:
    return _AVAILABLE
