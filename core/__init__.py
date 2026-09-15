# 素月核心包

# ===== Windows 子进程静默补丁 =====
# 背景：Windows 下 subprocess 创建控制台子进程（powershell/git/python/soffice 等）时，
# 若不做处理，每次执行都会闪出黑色命令框，干扰用户。
#
# 方案演进（2026-09-15）：
#   旧方案：注入 creationflags=CREATE_NO_WINDOW —— 只对"直接子进程"有效。
#   PowerShell 内部再启动 console 程序（python.exe / soffice.com / git 等）时，
#   因父进程无控制台，console 子进程会【新建】一个黑窗 —— 素月跑脚本/转 PPT 时反复弹窗。
#   新方案：注入 STARTUPINFO(STARTF_USESHOWWINDOW + SW_HIDE) —— 给子进程一个【隐藏控制台】，
#   其自身的 console 孙进程会继承该隐藏控制台，不再新建窗口。一处修改覆盖全部层级。
#   若调用方显式传入 startupinfo/creationflags，尊重其值不覆盖。
import os
import subprocess as _sp


def _silence_console_windows() -> None:
    """Windows 下为所有新建子进程注入隐藏控制台（SW_HIDE），不弹任何命令框。"""
    if os.name != "nt":
        return  # 仅 Windows 有此问题
    if getattr(_sp.Popen, "_bailian_no_window", False):
        return  # 已补丁，幂等
    _orig_init = _sp.Popen.__init__

    def _patched_init(self, *args, **kwargs):
        if not kwargs.get("startupinfo"):
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            si.wShowWindow = _sp.SW_HIDE
            kwargs["startupinfo"] = si
        _orig_init(self, *args, **kwargs)

    _sp.Popen.__init__ = _patched_init
    _sp.Popen._bailian_no_window = True  # 幂等标记


_silence_console_windows()
