"""内置工具：lan_tools —— 局域网发现能力（扫描活跃主机 / 探测常见端口，跨平台，只读）。

设计意图（形态二·局域网能力·第一步"发现"）：
- 让素月能主动发现本机所在局域网内的活跃主机与开放端口，为后续"访问/控制开放接口
  的设备与服务"（SMB/HTTP/MCP 等）打基础。
- 本文件只做【发现】（只读），不做任何写/控制/远程执行——写操作与远程控制后续单独评估。

安全边界（如实，防被利用当扫描器）：
- 目标地址【强制限私有网段】：10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、127.0.0.1。
  拒绝公网/外部地址，防止素月（或被污染后）被当作公网扫描器。
- 端口扫描限【常见端口白名单】，不做全端口爆破。
- 严格超时 + 并发受限（线程池），避免拖垮本机网络或目标主机。
- 全部只读：ping / TCP connect 探测，不发送任何攻击性载荷。
"""
from __future__ import annotations

import ipaddress
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from tools.base import tool

# 常见端口白名单（服务发现用，非全端口爆破）
_COMMON_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 443, 445, 993, 995,
    3000, 3306, 5000, 5432, 5900, 6379, 8000, 8080, 8443, 8765, 8888, 27017,
]
_PORT_NAMES = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 80: "http",
    110: "pop3", 135: "msrpc", 139: "netbios-ssn", 143: "imap", 443: "https",
    445: "microsoft-ds", 993: "imaps", 995: "pop3s", 3000: "http-alt",
    3306: "mysql", 5000: "http-alt", 5432: "postgresql", 5900: "vnc",
    6379: "redis", 8000: "http-alt", 8080: "http-proxy", 8443: "https-alt",
    8765: "bailing-webui", 8888: "http-alt", 27017: "mongodb",
}

_MAX_WORKERS = 32          # 并发上限（防网络风暴）
_PING_TIMEOUT_S = 1.0      # 单主机 ping 超时
_CONNECT_TIMEOUT_S = 0.5   # 单端口 TCP connect 超时


def _is_private(ip: str) -> bool:
    """目标地址必须为私有网段（10/8、172.16/12、192.168/16、127/8）。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def _is_proxy_reserved(ip: str) -> bool:
    """是否为代理/基准测试保留网段（198.18.0.0/15，RFC 2544，clash TUN 等虚拟网卡用）。"""
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("198.18.0.0/15")
    except Exception:  # noqa: BLE001
        return False


def _local_ip() -> str:
    """获取真实局域网 IP。

    多网卡 + 代理(TUN)环境下，UDP connect 法会误选代理虚拟网卡（如 198.18.0.1），
    故改为：枚举本机所有 IPv4，排除回环与代理保留段(198.18/15)，优先返回真实私有
    局域网段(192.168/16、10/8、172.16/12)地址。失败回退 UDP connect 法，再失败返回 127.0.0.1。
    """
    # ① 枚举本机所有 IPv4，过滤出真实局域网地址
    candidates = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        seen = set()
        for info in infos:
            ip = info[4][0]
            if ip in seen:
                continue
            seen.add(ip)
            if ip.startswith("127.") or _is_proxy_reserved(ip):
                continue
            if _is_private(ip):
                candidates.append(ip)
    except Exception:  # noqa: BLE001
        pass
    # ② 优先真实局域网段：192.168/16 > 10/8 > 172.16/12
    for ip in candidates:
        try:
            a = ipaddress.ip_address(ip)
            if a in ipaddress.ip_network("192.168.0.0/16"):
                return ip
        except Exception:  # noqa: BLE001
            continue
    for ip in candidates:
        try:
            a = ipaddress.ip_address(ip)
            if a in ipaddress.ip_network("10.0.0.0/8"):
                return ip
        except Exception:  # noqa: BLE001
            continue
    for ip in candidates:
        try:
            a = ipaddress.ip_address(ip)
            if a in ipaddress.ip_network("172.16.0.0/12"):
                return ip
        except Exception:  # noqa: BLE001
            continue
    # ③ 回退：UDP connect 法
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if _is_private(ip) and not _is_proxy_reserved(ip):
            return ip
    except Exception:  # noqa: BLE001
        pass
    return "127.0.0.1"


def _family() -> str:
    import platform as _platform
    system = _platform.system().lower()
    if system in ("windows", "linux", "darwin"):
        return system
    return "other"


def _ping(ip: str) -> bool:
    """ping 单主机，返回是否可达。Windows 用 -n 1 -w，POSIX 用 -c 1 -W。"""
    fam = _family()
    try:
        if fam == "windows":
            cmd = ["ping", "-n", "1", "-w", str(int(_PING_TIMEOUT_S * 1000)), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(int(_PING_TIMEOUT_S)), ip]
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_PING_TIMEOUT_S + 2,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _arp_table() -> dict:
    """读本机 ARP 表，返回 {ip: mac}。Windows→arp -a；POSIX→ip neigh。"""
    fam = _family()
    table = {}
    try:
        if fam == "windows":
            proc = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=10)
            for line in (proc.stdout or "").splitlines():
                parts = line.split()
                # Windows arp -a 行：接口行或 "ip mac type" 行
                if len(parts) >= 3 and parts[0].count(".") == 3:
                    ip, mac = parts[0], parts[1]
                    if _is_private(ip) and "-" in mac:
                        table[ip] = mac
        else:
            proc = subprocess.run(["ip", "neigh"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=10)
            for line in (proc.stdout or "").splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].count(".") == 3:
                    ip, mac = parts[0], parts[4]
                    if _is_private(ip) and ":" in mac:
                        table[ip] = mac
    except Exception:  # noqa: BLE001
        pass
    return table


def _hostname(ip: str) -> str:
    """反向解析主机名（失败返回空串）。"""
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:  # noqa: BLE001
        return ""


def _resolve_hostnames(ips: list, deadline_s: float = 3.0) -> dict:
    """并发反向解析主机名，整体受 deadline 约束（防 gethostbyaddr 阻塞卡死）。

    返回 {ip: hostname}。gethostbyaddr 无法设单次超时，故用线程池并发 + 主线程
    deadline 收集：超时未返回的地址放弃（hostname 留空），不阻塞整体扫描。
    """
    if not ips:
        return {}
    out: dict = {}
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(ips))) as ex:
        futs = {ex.submit(_hostname, ip): ip for ip in ips}
        from concurrent.futures import wait, FIRST_COMPLETED
        import time as _time
        deadline = _time.monotonic() + deadline_s
        pending = set(futs)
        while pending and _time.monotonic() < deadline:
            done, pending = wait(pending, timeout=max(0.05, deadline - _time.monotonic()),
                                 return_when=FIRST_COMPLETED)
            for f in done:
                ip = futs[f]
                try:
                    h = f.result()
                except Exception:  # noqa: BLE001
                    h = ""
                if h:
                    out[ip] = h
    return out


@tool(
    "lan_scan",
    "扫描本机所在局域网网段，发现活跃主机（ARP 表优先 + 补 ping 粗筛 + 并发解析主机名，只读）。"
    "仅限私有网段，返回在线主机列表。",
    {
        "type": "object",
        "properties": {
            "max_hosts": {"type": "number", "description": "最多返回主机数，默认 50"},
            "timeout": {"type": "number", "description": "整体扫描超时秒数，默认 30"},
        },
        "required": [],
    },
)
def lan_scan(max_hosts: int = 50, timeout: float = 30) -> dict:
    """扫描本机网段活跃主机。效率优先：先读 ARP 表拿已知设备，只对缺失地址补 ping。

    逻辑（先确定有没有活的点，再细察）：
    1. 读本机 ARP 表 → 免费拿到近期通信过的设备（IP+MAC），视为存活候选，无需逐台 ping。
    2. 对网段内【不在 ARP 表】的地址并发 ping 补扫（粗筛存活）。
    3. 合并存活集合，仅对存活主机并发做反向 DNS（短 deadline，防卡死）。
    """
    try:
        local_ip = _local_ip()
        if local_ip == "127.0.0.1":
            return {"ok": False, "error": "无法确定本机局域网 IP（可能未连局域网）"}
        net = ipaddress.ip_network(f"{local_ip}/24", strict=False)
        hosts = [str(h) for h in net.hosts()]

        # ① ARP 表优先：已知设备直接视为存活（含 MAC）
        arp = _arp_table()
        arp_alive = [ip for ip in hosts if ip in arp]

        # ② 只对 ARP 表缺失的地址补 ping（粗筛）
        to_ping = [ip for ip in hosts if ip not in arp]
        ping_alive = []
        if to_ping:
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(to_ping))) as ex:
                results = list(ex.map(_ping, to_ping))
            ping_alive = [ip for ip, up in zip(to_ping, results) if up]

        # ③ 合并存活集合（ARP 已知 + ping 补到），去重保序
        alive = []
        seen = set()
        for ip in arp_alive + ping_alive:
            if ip not in seen:
                seen.add(ip)
                alive.append(ip)

        # ④ 仅对存活主机并发解析主机名（短 deadline，防 DNS 卡死）
        names = _resolve_hostnames(alive, deadline_s=min(3.0, max(1.0, timeout / 4)))

        out = []
        for ip in alive[:max_hosts]:
            out.append({
                "ip": ip,
                "mac": arp.get(ip, ""),
                "hostname": names.get(ip, ""),
            })
        return {
            "ok": True,
            "local_ip": local_ip,
            "network": str(net),
            "scanned": len(hosts),
            "arp_known": len(arp_alive),
            "ping_found": len(ping_alive),
            "alive_total": len(alive),
            "count": len(out),
            "hosts": out,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"局域网扫描失败: {type(e).__name__}: {e}"}


def _probe_port(ip: str, port: int) -> bool:
    """TCP connect 探测单端口是否开放。"""
    try:
        with socket.create_connection((ip, port), timeout=_CONNECT_TIMEOUT_S):
            return True
    except Exception:  # noqa: BLE001
        return False


@tool(
    "lan_portscan",
    "探测指定局域网主机的常见端口开放情况（TCP connect，只读）。"
    "目标必须为私有网段 IP，端口限常见白名单，不做全端口爆破。",
    {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "目标主机 IP（必须为私有网段，如 192.168.1.100）"},
            "ports": {"type": "array", "items": {"type": "number"},
                      "description": "自定义端口列表（可选，默认常见端口白名单）"},
        },
        "required": ["host"],
    },
)
def lan_portscan(host: str, ports: list = None) -> dict:
    """探测指定主机常见端口开放情况。"""
    ip = (host or "").strip()
    if not ip:
        return {"ok": False, "error": "host 不能为空"}
    if not _is_private(ip):
        return {"ok": False, "error": f"目标 {ip} 非私有网段，拒绝扫描（仅允许局域网内地址）"}
    # 端口列表：自定义需在白名单内，否则拒绝
    if ports:
        custom = [int(p) for p in ports if isinstance(p, (int, float)) and 1 <= int(p) <= 65535]
        allowed = [p for p in custom if p in _COMMON_PORTS]
        if len(allowed) != len(custom):
            return {"ok": False, "error": "含白名单外端口，拒绝（仅允许常见端口）"}
        targets = allowed
    else:
        targets = _COMMON_PORTS
    try:
        open_ports = []
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(targets))) as ex:
            results = list(ex.map(lambda p: (p, _probe_port(ip, p)), targets))
        for port, open_ in results:
            if open_:
                open_ports.append({"port": port, "service": _PORT_NAMES.get(port, "")})
        open_ports.sort(key=lambda x: x["port"])
        return {
            "ok": True,
            "host": ip,
            "hostname": _hostname(ip),
            "scanned_ports": len(targets),
            "open_count": len(open_ports),
            "open_ports": open_ports,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"端口扫描失败: {type(e).__name__}: {e}"}
