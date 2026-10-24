# -*- coding: utf-8 -*-
"""实测：极端场景下的命令到底走哪一档 —— 只调分类函数，绝不执行"""
import importlib.util, sys

sys.path.insert(0, r"F:\me\self-agent")

p = r"F:\me\self-agent\tools\src\python\cmd_run.py"
spec = importlib.util.spec_from_file_location("cmd_run_probe", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

cases = [
    ("format C:", "格式化C盘"),
    ("Format-Volume -DriveLetter C -FileSystem NTFS", "PS格式化卷"),
    ("Clear-Disk -Number 0 -RemoveData -Confirm:$false", "清盘"),
    ("Remove-Partition -DriveLetter D -Confirm:$false", "删分区"),
    ("diskpart", "分区工具"),
    ("mkfs.ext4 /dev/sda1", "Linux格式化"),
    ("rm -rf /", "删根"),
    ("Remove-Item -Recurse -Force C:\\", "PS递归删C盘根"),
    ("rd /s /q C:\\", "rd递归根"),
    ("shutdown /s /t 0", "立即关机"),
    ("reg delete HKLM\\Software\\X /f", "删注册表键"),
    ("git status", "无害命令"),
    ("python script.py", "无害命令"),
    ("Get-ChildItem F:\\me", "无害命令"),
]

print("%-6s | %s" % ("档位", "命令"))
print("-" * 78)
bad = []
for cmd, note in cases:
    try:
        g = m._classify(cmd)
    except Exception as e:
        g = "ERR:%s" % e
    if g not in ("allow", "ask", "high"):
        bad.append((cmd, g))
    print("%-6s | %s   [%s]" % (g, cmd, note))

print()
print("=== 是否存在第四种档位(封死):", ("是 → %s" % bad) if bad else "否，只有 allow/ask/high")

import inspect
src = inspect.getsource(m)
print("=== 存在 _issue_token（有确认通道）:", "_issue_token" in src)
deny_lines = [(i, l.strip()) for i, l in enumerate(src.splitlines(), 1) if "deny" in l.lower()]
print("=== 源码中 deny 字样行数:", len(deny_lines))
for i, l in deny_lines[:10]:
    print("    %d: %s" % (i, l[:110]))
