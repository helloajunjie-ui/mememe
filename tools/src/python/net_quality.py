"""内置工具：net_quality —— 网络质量速查（一次打包多条独立网络探测，输出紧凑结论）。

场景：用户说"网卡不卡/断网了/网络慢/连不上"时，一次调用给出延迟、丢包、DNS、
网卡、连接数、代理、公网出口 IP 全景，不再逐条 ping / ipconfig。
全部只读探测；公网出口 IP 查询失败自动跳过，不影响其他项。
"""
from __future__ import annotations

import json
import threading

from tools.base import tool
from core import platform as plat

_QUERIES = [
    # (key, 命令)
    ("ping_ali", "Test-Connection -ComputerName 223.5.5.5 -Count 3 -ErrorAction SilentlyContinue | "
                 "Measure-Object -Property ResponseTime -Average | ForEach-Object { if ($_.Average) {[math]::Round($_.Average,0)} else {'unreachable'} }"),
    ("ping_baidu", "Test-Connection -ComputerName www.baidu.com -Count 3 -ErrorAction SilentlyContinue | "
                   "Measure-Object -Property ResponseTime -Average | ForEach-Object { if ($_.Average) {[math]::Round($_.Average,0)} else {'unreachable'} }"),
    ("ping_tencent", "Test-Connection -ComputerName 119.29.29.29 -Count 3 -ErrorAction SilentlyContinue | "
                     "Measure-Object -Property ResponseTime -Average | ForEach-Object { if ($_.Average) {[math]::Round($_.Average,0)} else {'unreachable'} }"),
    ("dns", "Resolve-DnsName www.baidu.com -Type A -ErrorAction SilentlyContinue | "
            "Where-Object {$_.IPAddress} | Select-Object -First 2 -ExpandProperty IPAddress | ConvertTo-Json -Compress"),
    ("adapters", "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                 "Select-Object Name,LinkSpeed | ConvertTo-Json -Compress"),
    ("tcp_states", "Get-NetTCPConnection -ErrorAction SilentlyContinue | Group-Object State | "
                   "Select-Object Name,Count | ConvertTo-Json -Compress"),
    ("proxy", "Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' | "
              "Select-Object ProxyEnable,ProxyServer | ConvertTo-Json -Compress"),
    ("pub_ip", "(Invoke-RestMethod -Uri 'https://api.ipify.org?format=text' -TimeoutSec 6) 2>$null"),
]


@tool(
    "net_quality",
    "网络质量速查：一次打包多条只读探测（多目标延迟、DNS 解析、网卡状态、TCP 连接分布、代理设置、公网出口 IP），"
    "返回紧凑结论。用户说网卡不卡/断网/网络慢/连不上时优先用它，不要逐条 ping / ipconfig。",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def run() -> dict:
    results: dict = {}
    lock = threading.Lock()

    def worker(key: str, cmd: str) -> None:
        try:
            r = plat.run_shell(cmd, timeout=12)
            raw = (r["stdout"] or r["stderr"] or "").strip()
            val: object = raw
            try:
                val = json.loads(raw)
            except Exception:  # noqa: BLE001
                pass
            with lock:
                results[key] = {"ok": r["ok"], "val": val}
        except Exception as e:  # noqa: BLE001
            with lock:
                results[key] = {"ok": False, "val": f"err:{type(e).__name__}"}

    threads = [threading.Thread(target=worker, args=(k, c), daemon=True) for k, c in _QUERIES]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    card: dict = {}

    pings = []
    for k, name in (("ping_ali", "阿里DNS"), ("ping_baidu", "百度"), ("ping_tencent", "腾讯DNS")):
        if results.get(k, {}).get("ok"):
            v = results[k]["val"]
            pings.append(f"{name} {v}ms" if v and v != "unreachable" else f"{name} 不通")
    if pings:
        card["延迟"] = pings

    if results.get("dns", {}).get("ok") and results["dns"]["val"]:
        card["DNS解析"] = results["dns"]["val"]

    if results.get("adapters", {}).get("ok"):
        a = results["adapters"]["val"]
        if isinstance(a, dict):
            a = [a]
        card["网卡"] = [f"{x.get('Name')} {x.get('LinkSpeed')}" for x in a if isinstance(x, dict)][:4]

    if results.get("tcp_states", {}).get("ok"):
        s = results["tcp_states"]["val"]
        if isinstance(s, dict):
            s = [s]
        card["TCP连接"] = {x.get("Name", "?"): x.get("Count", 0) for x in s if isinstance(x, dict)}

    if results.get("proxy", {}).get("ok"):
        p = results["proxy"]["val"]
        if isinstance(p, dict):
            card["代理"] = f"开启({p.get('ProxyServer')})" if p.get("ProxyEnable") else "未开启"

    if results.get("pub_ip", {}).get("ok") and results["pub_ip"]["val"]:
        card["公网IP"] = str(results["pub_ip"]["val"]).strip()

    summary = " | ".join(pings) if pings else "（延迟数据缺失）"
    ok = any(results.get(k, {}).get("ok") for k, _ in _QUERIES)
    return {"ok": ok, "summary": summary, "card": card,
            "tip": "延迟高/不通 → 先看 DNS 与网卡；代理异常时检查系统代理设置。"}
