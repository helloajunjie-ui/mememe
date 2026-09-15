"""内置工具：sys_check —— 一键桌面日常巡检（打包多条独立查询，输出紧凑状态卡）。

设计意图（省 token / 省往返 / 一次到位）：
- 用户问"电脑状态/卡不卡/体检/装了啥"时，不再逐条 cmd_run（N 条命令 = N 次 LLM 往返）。
- 一次调用并发跑完全部独立查询（磁盘/内存/CPU/进程/网络/启动项/工具链/软件），
  内部线程并行，总耗时 ≈ 最慢一条；返回一张紧凑状态卡，成功项只给摘要。
- 所有查询均为只读，无破坏性；软件列表量大时自动截断（前 20 + 总数）。
"""
from __future__ import annotations

import json
import threading

from tools.base import tool
from core import platform as plat

_QUERIES = {
    "system": [
        ("disk_c", "Get-PSDrive C | Select-Object @{n='used_gb';e={[math]::Round($_.Used/1GB,1)}},"
                   "@{n='free_gb';e={[math]::Round($_.Free/1GB,1)}} | ConvertTo-Json -Compress"),
        ("mem", "Get-CimInstance Win32_OperatingSystem | Select-Object "
                "@{n='free_gb';e={[math]::Round($_.FreePhysicalMemory/1048576,1)}},"
                "@{n='total_gb';e={[math]::Round($_.TotalVisibleMemorySize/1048576,1)}} | ConvertTo-Json -Compress"),
        ("cpu_top", "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 "
                    "Name,@{n='cpu_s';e={[math]::Round($_.CPU,1)}} | ConvertTo-Json -Compress"),
        ("uptime", "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss')"),
        ("machine", "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model | ConvertTo-Json -Compress"),
    ],
    "process": [
        ("windows", "Get-Process | Where-Object {$_.MainWindowTitle} | "
                    "Select-Object -ExpandProperty Name -Unique | ConvertTo-Json -Compress"),
    ],
    "network": [
        ("ping", "if (Test-Connection -ComputerName www.baidu.com -Count 2 -Quiet) {'online'} else {'offline'}"),
        ("ip", "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and "
               "$_.IPAddress -notlike '169.254.*'} | Select-Object -ExpandProperty IPAddress | ConvertTo-Json -Compress"),
        ("gateway", "Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Select-Object -First 1 -ExpandProperty NextHop"),
    ],
    "startup": [
        ("startup", "Get-CimInstance Win32_StartupCommand | Select-Object -ExpandProperty Name | "
                    "Select-Object -First 15 | ConvertTo-Json -Compress"),
    ],
    "tools": [
        ("git", "git --version 2>$null"),
        ("node", "node -v 2>$null"),
        ("python", "python --version 2>$null"),
        ("go", "go version 2>$null"),
        ("ffmpeg", "ffmpeg -version 2>$null | Select-Object -First 1"),
    ],
    "apps": [
        ("apps", "Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* | "
                 "Select-Object -ExpandProperty DisplayName -Unique | Where-Object {$_} | Sort-Object | ConvertTo-Json -Compress"),
    ],
}


def _parse(text: str):
    """stdout 优先解析 JSON，失败按纯文本返回。"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        return t


def _fmt_gb(val, digits: int = 1) -> str:
    try:
        return f"{float(val):.{digits}f}"
    except Exception:  # noqa: BLE001
        return str(val)


@tool(
    "sys_check",
    "一键桌面日常巡检：一次调用打包执行多条独立只读查询（磁盘/内存/CPU/运行窗口/网络/启动项/工具链版本/已装软件），"
    "返回一张紧凑状态卡。用户问电脑状态、卡不卡、体检、装了哪些软件、开机启动项、网络通不通时优先用它，"
    "不要逐条执行 cmd_run。软件列表量大时自动截断。",
    {
        "type": "object",
        "properties": {
            "sections": {
                "type": "array",
                "description": "要检查的部分：system/process/network/startup/tools/apps；不传默认全部（常用建议 system+process+network+tools）",
                "items": {"type": "string"},
                
            },
        },
        "required": [],
    },
)
def run(sections: list = None) -> dict:
    if not sections or not isinstance(sections, list):
        sections = list(_QUERIES.keys())
    sections = [s for s in sections if s in _QUERIES]
    if not sections:
        return {"ok": False, "error": f"未知检查项，可选: {', '.join(_QUERIES.keys())}"}

    results: dict = {}
    errors: dict = {}
    lock = threading.Lock()

    def worker(section: str, key: str, cmd: str) -> None:
        try:
            r = plat.run_shell(cmd, timeout=10)
            with lock:
                results.setdefault(section, {})[key] = {"ok": r["ok"], "val": _parse(r["stdout"] or r["stderr"])}
        except Exception as e:  # noqa: BLE001
            with lock:
                errors[f"{section}.{key}"] = str(e)

    threads = [threading.Thread(target=worker, args=(sec, key, cmd), daemon=True)
               for sec in sections for key, cmd in _QUERIES[sec]]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    # ---- 组装紧凑状态卡 ----
    card: dict = {}
    summary: list[str] = []

    if "system" in results:
        s = results["system"]
        if s.get("disk_c", {}).get("ok"):
            d = s["disk_c"]["val"]
            if isinstance(d, list):
                d = d[0]
            if isinstance(d, dict):
                card["磁盘"] = f"C: 可用 {d.get('free_gb')}GB / 总计 {float(d.get('used_gb', 0)) + float(d.get('free_gb', 0)):.1f}GB"
                summary.append(f"磁盘 C 可用 {d.get('free_gb')}GB")
        if s.get("mem", {}).get("ok"):
            m = s["mem"]["val"]
            if isinstance(m, list):
                m = m[0]
            if isinstance(m, dict):
                free, total = float(m.get("free_gb", 0)), float(m.get("total_gb", 0))
                used = total - free
                card["内存"] = f"已用 {used:.1f}GB / {total:.1f}GB（{used / total * 100:.0f}%）"
                summary.append(f"内存 {used:.1f}/{total:.1f}GB")
        if s.get("cpu_top", {}).get("ok"):
            c = s["cpu_top"]["val"]
            if not isinstance(c, list):
                c = [c]
            card["CPU占用Top5"] = [f"{x.get('Name')} {x.get('cpu_s')}s" for x in c if isinstance(x, dict)][:5]
        if s.get("uptime", {}).get("ok"):
            card["上次启动"] = s["uptime"]["val"]
        if s.get("machine", {}).get("ok"):
            m = s["machine"]["val"]
            if isinstance(m, list):
                m = m[0]
            if isinstance(m, dict):
                card["机型"] = f"{m.get('Manufacturer', '')} {m.get('Model', '')}".strip()

    if "process" in results and results["process"].get("windows", {}).get("ok"):
        w = results["process"]["windows"]["val"]
        names = w if isinstance(w, list) else ([w] if w else [])
        card["运行窗口"] = names[:12]
        summary.append(f"运行窗口 {len(names)} 个")

    if "network" in results:
        n = results["network"]
        if n.get("ping", {}).get("ok"):
            card["外网"] = n["ping"]["val"]
            summary.append(f"外网 {'通' if n['ping']['val'] == 'online' else '不通'}")
        if n.get("ip", {}).get("ok"):
            ips = n["ip"]["val"]
            card["本机IP"] = ips if isinstance(ips, list) else [ips]
        if n.get("gateway", {}).get("ok"):
            card["网关"] = n["gateway"]["val"]

    if "startup" in results and results["startup"].get("startup", {}).get("ok"):
        st = results["startup"]["startup"]["val"]
        items = st if isinstance(st, list) else ([st] if st else [])
        card["启动项"] = items[:10]
        summary.append(f"启动项 {len(items)} 个")

    if "tools" in results:
        card["工具链"] = {}
        for key, human in (("git", "git"), ("node", "node"), ("python", "python"), ("go", "go"), ("ffmpeg", "ffmpeg")):
            t = results["tools"].get(key, {})
            if t.get("ok"):
                v = t.get("val")
                if v:
                    card["工具链"][human] = str(v).splitlines()[0][:60] if isinstance(v, str) else v
            else:
                card["工具链"][human] = "未安装/不可用"

    if "apps" in results and results["apps"].get("apps", {}).get("ok"):
        a = results["apps"]["apps"]["val"]
        items = a if isinstance(a, list) else ([a] if a else [])
        card["已装软件"] = {"count": len(items), "sample": items[:15]}
        summary.append(f"已装软件 {len(items)} 个")

    if errors:
        card["失败项"] = list(errors.keys())

    return {
        "ok": True,
        "summary": " | ".join(summary) or "（无关键指标）",
        "card": card,
        "tip": "如需深挖某项（如结束进程、清理磁盘），基于此卡再定向执行。",
    }
