# -*- coding: utf-8 -*-
"""Clash / mihomo 控制口访问层（只做管道与协议，不含业务判断）。

背景：Clash Verge 默认 `enable_external_controller: false`，HTTP 控制器（9097）是关的，
只剩一个 Windows 命名管道 `\\\\.\\pipe\\verge-mihomo-<...>`。所以常规的 127.0.0.1:9090
API 打不通，但通过管道仍能读到代理组与当前节点、也能切换节点。

只提供三个动作：proxies() 读全量、current() 读"现在选的节点"、switch() 切节点。
报错一律返回字符串，不抛异常——调用方（工具）负责决定是否降级。
"""
from __future__ import annotations

import json
import os
import re
import time

_CFG_DIRS = [
    os.path.expandvars(r"%APPDATA%\io.github.clash-verge-rev.clash-verge-rev"),
    os.path.expandvars(r"%APPDATA%\clash_win"),
    os.path.expandvars(r"%APPDATA%\mihomo-party"),
]


def _config_path() -> str:
    for d in _CFG_DIRS:
        p = os.path.join(d, "config.yaml")
        if os.path.isfile(p):
            return p
    return ""


def secret() -> str:
    p = _config_path()
    if not p:
        return ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            m = re.search(r"^secret:\s*(.+)$", f.read(), re.M)
        return (m.group(1).strip().strip('"').strip("'") if m else "")
    except OSError:
        return ""


def _pipe_candidates():
    p = _config_path()
    if not p:
        return []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return []
    m = re.search(r"^external-controller-pipe:\s*(.+)$", txt, re.M)
    if not m:
        return []
    base = m.group(1).strip().strip('"').strip("'")
    out = [base]
    for alt in ("production", "sidecar-release"):
        cand = re.sub(r"verge-mihomo-[a-z-]+-", "verge-mihomo-%s-" % alt, base)
        if cand not in out:
            out.append(cand)
    return out


def _http_controller():
    p = _config_path()
    if not p:
        return ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            m = re.search(r"^external-controller:\s*'?([^'\n]+)'?$", f.read(), re.M)
        return (m.group(1).strip() if m else "")
    except OSError:
        return ""


def _read_all(f, limit=2_000_000) -> bytes:
    data = b""
    while len(data) < limit:
        chunk = f.read(65536)
        if not chunk:
            break
        data += chunk
    return data


def _parse(resp: bytes):
    head, _, body = resp.partition(b"\r\n\r\n")
    lines = head.decode("latin-1", "replace").split("\r\n")
    status = lines[0] if lines else ""
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        out, i = b"", 0
        while i < len(body):
            j = body.find(b"\r\n", i)
            if j < 0:
                break
            try:
                n = int(body[i:j], 16)
            except ValueError:
                break
            if n == 0:
                break
            out += body[j + 2: j + 2 + n]
            i = j + 2 + n + 2
        body = out
    return status, body


def request(method: str, path: str, body: dict | None = None, timeout: float = 5.0):
    """向 Clash 控制口发一条请求。返回 (ok, status_or_error, parsed_json_or_None)。"""
    payload = b""
    headers = ["Host: localhost", "Connection: close"]
    sec = secret()
    if sec:
        headers.append("Authorization: Bearer " + sec)
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        headers.append("Content-Type: application/json")
    req = (("%s %s HTTP/1.1\r\n" % (method, path)) + "\r\n".join(headers) +
           "\r\nContent-Length: %d\r\n\r\n" % len(payload)).encode() + payload

    for pipe in _pipe_candidates():
        try:
            f = open(pipe, "r+b", buffering=0)
        except OSError:
            continue
        try:
            f.write(req)
            raw = _read_all(f)
        except OSError as e:
            return False, "pipe_io:%s" % e, None
        finally:
            try:
                f.close()
            except OSError:
                pass
        if not raw:
            continue
        status, data = _parse(raw)
        if "200" not in status:
            return False, status or "no_status", None
        try:
            return True, "200", json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return False, "bad_json", None
    return False, "no_controller（外部控制器未启用，且管道不可达）", None


def proxies():
    ok, st, js = request("GET", "/proxies")
    return js if ok else None


def current():
    """返回 {group, node, groups:[{name, now, type, count}]}。"""
    js = proxies()
    if not isinstance(js, dict):
        return None
    px = js.get("proxies") or {}
    groups, current_group, current_node = [], None, None
    for name, p in px.items():
        tp = (p or {}).get("type") or ""
        if tp in ("Selector", "URLTest", "Fallback", "LoadBalance", "Relay"):
            now = (p or {}).get("now") or ""
            groups.append({"name": name, "type": tp, "now": now,
                           "count": len((p or {}).get("all") or [])})
            if current_group is None:
                current_group, current_node = name, now
    return {"group": current_group, "node": current_node, "groups": groups}


def switch(group: str, node: str):
    ok, st, _ = request("PUT", "/proxies/" + group.replace(" ", "%20"), {"name": node})
    return ok, st
