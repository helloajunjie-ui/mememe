"""内置工具：net_diag —— 网络出口三段对照诊断（国内 / 当前 API / 海外目标）。

为什么单独做一个工具：遇到"连不上"时，最耗时的是在"目标站 / 工具 / DNS / 代理"之间瞎试。
本工具把方法论里那条三行对照判分固化成机器动作，一次给出结论：

- 国内直连（baidu.com:443）TLS 通、海外目标全挂 → **代理出口断了**（节点失效/订阅到期/切到坏节点）
- 国内直连通、只有某一个海外目标挂 → **该站点问题**（反爬 / Cloudflare / 需要登录）
- 国内直连也不通 → **本机 DNS / 网络栈问题**（先看系统网络，不要去折腾代理）
- 本地代理端口不在监听 → **代理没开**

只做 TCP+TLS 握手（不发业务请求），秒级返回，不消耗任何 API 额度。
"""
from __future__ import annotations

import os
import socket
import ssl
import time

from tools.base import tool

_PROXY_PORTS = (7897, 7890, 7891, 10809, 10808, 1080, 8118)

_DEFAULT_TARGETS = [
    ("baidu", "www.baidu.com", "国内直连基线"),
    ("deepseek", "api.deepseek.com", "当前依赖的 LLM API"),
    ("github", "github.com", "海外常访"),
    ("google", "www.google.com", "海外常访"),
]


def _tls_probe(host: str, port: int = 443, timeout: float = 5.0) -> dict:
    t0 = time.time()
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            sock = None
            return {"host": host, "ok": True, "ms": int((time.time() - t0) * 1000),
                    "tls": ss.version(), "peer": ss.getpeername()[0]}
    except ssl.SSLError as e:
        return {"host": host, "ok": False, "ms": int((time.time() - t0) * 1000),
                "error": f"SSLError: {str(e)[:80]}"}
    except OSError as e:
        return {"host": host, "ok": False, "ms": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {str(e)[:80]}"}
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


def _local_proxy() -> dict:
    for port in _PROXY_PORTS:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
            s.close()
            return {"detected": True, "port": port}
        except OSError:
            continue
    return {"detected": False, "port": None}


@tool(
    "net_diag",
    "网络连不上时先跑这个：对比「国内直连 / 当前 LLM API / 海外目标」三段 TLS 握手，"
    "直接判定是代理出口断了、还是某个站点反爬、还是本机 DNS/网络问题，并给出下一步。"
    "只做 TCP+TLS 握手，秒级返回、不耗额度。可选 host 参数追加一个你正关心的域名。",
    {
        "type": "object",
        "properties": {
            "host": {"type": "string",
                     "description": "额外要诊断的域名（不含协议），如 en.wikipedia.org"},
        },
        "required": [],
    },
)
def run(host: str = "") -> dict:
    targets = list(_DEFAULT_TARGETS)
    h = (host or "").strip().lower()
    h = h.replace("https://", "").replace("http://", "").split("/")[0]
    if h:
        targets.append(("target", h, "你关心的目标"))

    results = []
    for key, name, role in targets:
        r = _tls_probe(name)
        r.update({"key": key, "role": role})
        results.append(r)

    by = {r["key"]: r for r in results if r["key"] != "target"}
    domestic_ok = by["baidu"]["ok"]
    api_ok = by["deepseek"]["ok"]
    abroad = [r for r in results if r["key"] in ("github", "google")]
    abroad_ok = any(r["ok"] for r in abroad)
    target = next((r for r in results if r["key"] == "target"), None)
    proxy = _local_proxy()

    # 代理当前节点（只读）：判定"proxy_down"时能直接说清是哪个节点挂了
    node_info = None
    try:
        from tools.src.python import proxy_ctl as pc
        cur = pc.current()
        if cur:
            node_info = {"group": cur.get("group"), "node": cur.get("node")}
    except Exception:  # noqa: BLE001  控制口不可用不影响诊断主体
        node_info = None

    if not domestic_ok:
        verdict = ("本机 DNS/网络有问题：连 www.baidu.com 的 TLS 都握不上，先查系统网络/防火墙，"
                   "不要先怀疑代理或目标站。")
        code = "local_network"
    elif not abroad_ok and not api_ok:
        verdict = ("代理出口断了：国内直连正常，但所有海外目标（含当前依赖的 LLM API）都握手失败。"
                   "大概率是节点失效 / 订阅到期 / 切到了坏节点——去代理软件里换节点或更新订阅，"
                   "不是目标站的问题，重试原站点没用。")
        code = "proxy_down"
    elif target is not None and not target["ok"] and abroad_ok:
        verdict = (f"只有 {target['host']} 连不上，其它海外目标正常：该站点问题（反爬/Cloudflare/需要登录），"
                   "换通道（browserskill 真浏览器、r.jina.ai 阅读器、或官方 API）比反复重试有效。")
        code = "site_blocked"
    elif not abroad_ok:
        verdict = ("海外目标部分不可达（API 通、常用站不通）：可能是分流规则把目标域名走了直连，"
                   "或该节点对某些域名做了屏蔽。先看代理规则，再考虑换节点。")
        code = "partial"
    else:
        verdict = "三段都通：网络没问题，问题在别处（认证/额度/目标站业务逻辑）。"
        code = "ok"

    return {
        "ok": True,
        "verdict_code": code,
        "verdict": verdict,
        "local_proxy": proxy,
        "proxy_node": node_info,
        "probes": [{k: r.get(k) for k in ("key", "host", "role", "ok", "ms", "error", "peers") if k in r}
                   for r in results],
        "hint": ("国内基线=www.baidu.com；API=api.deepseek.com；海外=github/google。"
                 "握手只证明出口通，不代表目标站业务可用（反爬仍可能 403）。"
                 "判定 proxy_down 时可用 proxy_node 工具换节点（需 confirm=\"SWITCH\"）。"),
    }
