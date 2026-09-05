# 白绫核心包

# ===== Windows 子进程静默补丁 =====
# 背景：Windows 下 subprocess 创建控制台子进程（powershell/git/go/wmic 等）时，
# 若不设 CREATE_NO_WINDOW，每次执行都会闪出一个黑色命令框，干扰用户。
# 方案：在包加载最早处（本文件）全局包装 subprocess.Popen.__init__，
# Windows 下自动注入 creationflags=CREATE_NO_WINDOW。一处修改覆盖全进程，
# 现有与未来所有 subprocess 调用（run/call/check_output 均底层走 Popen）自动免疫。
# 若调用方显式传入 creationflags，尊重其值不覆盖。
import os
import subprocess as _sp


def _silence_console_windows() -> None:
    """Windows 下为所有新建子进程注入 CREATE_NO_WINDOW（不弹控制台窗口）。"""
    if os.name != "nt":
        return  # 仅 Windows 有此问题
    if getattr(_sp.Popen, "_bailian_no_window", False):
        return  # 已补丁，幂等
    _orig_init = _sp.Popen.__init__

    def _patched_init(self, *args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = _sp.CREATE_NO_WINDOW
        _orig_init(self, *args, **kwargs)

    _sp.Popen.__init__ = _patched_init
    _sp.Popen._bailian_no_window = True  # 幂等标记


_silence_console_windows()
