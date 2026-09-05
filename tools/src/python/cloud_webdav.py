# -*- coding: utf-8 -*-
"""内置工具：cloud_webdav —— 局域网/个人云 WebDAV 联通（列目录/读/写/删/建目录）。

设计意图（形态二·局域网能力·第二步"联通"）：
- lan_tools 完成【发现】（扫描主机/端口），本工具完成【联通】：对发现的 WebDAV 服务
  （飞牛云 fnOS、群晖 DSM、威联通、Nextcloud、坚果云等）做文件读写。
- WebDAV 是 HTTP 之上的文件协议，跨平台、无需额外依赖（复用 httpx），是 NAS/云 OS
  最通用的联通协议。

安全边界（如实）：
- 凭据：base_url + 用户名由调用方显式传入；密码从环境变量 WEBDAV_PASSWORD 读取
  （不硬编码、不进日志、不进 registry）。这是技术债：无持久化凭据存储，每次需显式传参
  或先设环境变量。
- 目标不限私有网段：个人云可能是公网（如坚果云 dav.jianguoyun.com），故不做网段强制
  （与 lan_scan 的私有网段限制不同，那是"发现"防扫描器；这里是用户显式授权的"联通"）。
- 写/删操作需二次确认（confirm 口令），防误操作覆盖/删除远端文件。
- 全部走 HTTPS 优先；HTTP 仅限局域网私有网段（防明文凭据走公网）。
"""
from __future__ import annotations

import os
import posixpath
from urllib.parse import quote, urlparse

import httpx
from xml.etree import ElementTree as ET

from tools.base import tool

# WebDAV XML 命名空间
_DAV_NS = "{DAV:}"
_TIMEOUT_S = 30.0
_MAX_READ_BYTES = 2 * 1024 * 1024  # 单文件读上限 2MB（防拉爆内存）
_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 单文件写上限 10MB
_USER_AGENT = "BailingAgent/0.1 (webdav client)"


def _password() -> str:
    """从环境变量取 WebDAV 密码（不硬编码）。"""
    return os.environ.get("WEBDAV_PASSWORD", "").strip()


def _is_private_host(host: str) -> bool:
    """判断主机是否为私有网段/回环（用于 HTTP 明文传输的许可判定）。"""
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False


def _normalize_base(base_url: str) -> str:
    """规范化 base_url：补协议、去尾斜杠。返回 (scheme, netloc, base_path)。"""
    base_url = (base_url or "").strip()
    if not base_url:
        raise ValueError("base_url 不能为空")
    if "://" not in base_url:
        base_url = "http://" + base_url
    parsed = urlparse(base_url)
    if not parsed.netloc:
        raise ValueError(f"base_url 非法（缺主机）: {base_url}")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"不支持的协议: {scheme}（仅 http/https）")
    # 安全：HTTP 明文仅允许私有网段主机（防明文凭据走公网）
    if scheme == "http" and not _is_private_host(parsed.hostname or ""):
        raise ValueError("HTTP 明文仅允许局域网私有网段主机；公网 WebDAV 请用 HTTPS")
    base_path = parsed.path.rstrip("/")
    return scheme, parsed.netloc, base_path


def _client(base_url: str, username: str, password: str) -> httpx.Client:
    """构造带认证的 httpx Client。"""
    scheme, netloc, _ = _normalize_base(base_url)
    auth = None
    if username:
        auth = (username, password)
    return httpx.Client(
        base_url=f"{scheme}://{netloc}",
        auth=auth,
        timeout=_TIMEOUT_S,
        headers={"User-Agent": _USER_AGENT},
        follow_redirects=True,
    )


def _join(base_path: str, path: str) -> str:
    """把 WebDAV 内相对路径拼到 base_path 后，返回 URL 编码后的完整路径。"""
    path = (path or "").strip().replace("\\", "/").strip("/")
    full = posixpath.join(base_path, path) if path else base_path
    if not full.startswith("/"):
        full = "/" + full
    # 逐段 quote（保留 /），防特殊字符
    return "/".join(quote(seg, safe="") for seg in full.split("/"))


def _err(e: Exception, prefix: str) -> dict:
    return {"ok": False, "error": f"{prefix}: {type(e).__name__}: {e}"}


def _http_err(r: httpx.Response) -> str:
    """把 HTTP 状态码转成可读错误。"""
    code = r.status_code
    if code == 401:
        return f"认证失败(HTTP 401)：用户名/密码错误，或未设 WEBDAV_PASSWORD 环境变量"
    if code == 403:
        return f"无权限(HTTP 403)：该账号无权访问此路径"
    if code == 404:
        return f"路径不存在(HTTP 404)"
    if code == 405:
        return f"方法不被允许(HTTP 405)：该服务可能不支持此操作"
    if code == 409:
        return f"冲突(HTTP 409)：目标已存在或父目录不存在"
    return f"HTTP {code}"


@tool(
    "cloud_webdav_list",
    "列出局域网/个人云 WebDAV 目录内容（PROPFIND）。支持飞牛云/群晖/威联通/Nextcloud/坚果云等。"
    "base_url 传 WebDAV 根地址（如 http://192.168.1.100:5005 或 https://dav.jianguoyun.com/dav/），"
    "username 传账号，密码从环境变量 WEBDAV_PASSWORD 读取。",
    {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "WebDAV 根地址，如 http://192.168.1.100:5005 或 https://dav.jianguoyun.com/dav/"},
            "path": {"type": "string", "description": "WebDAV 内相对目录路径，默认根目录 /"},
            "username": {"type": "string", "description": "WebDAV 账号（密码从环境变量 WEBDAV_PASSWORD 读取）"},
        },
        "required": ["base_url", "username"],
    },
)
def cloud_webdav_list(base_url: str, username: str, path: str = "/") -> dict:
    try:
        scheme, netloc, base_path = _normalize_base(base_url)
        url = _join(base_path, path)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:">'
            '<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop>'
            '</d:propfind>'
        )
        with _client(base_url, username, _password()) as c:
            r = c.request("PROPFIND", url, content=body, headers={"Depth": "1"})
            if r.status_code not in (200, 207):
                return {"ok": False, "error": f"列目录失败: {_http_err(r)}"}
            entries = _parse_propfind(r.content)
        return {"ok": True, "base_url": f"{scheme}://{netloc}", "path": path or "/",
                "count": len(entries), "entries": entries}
    except Exception as e:  # noqa: BLE001
        return _err(e, "列目录失败")


def _parse_propfind(content: bytes) -> list:
    """解析 PROPFIND 207 响应，返回条目列表（name/is_dir/size/mtime）。"""
    entries = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return entries
    for resp in root.findall(f"{_DAV_NS}response"):
        href_el = resp.find(f"{_DAV_NS}href")
        if href_el is None or not href_el.text:
            continue
        href = href_el.text
        # 取最后一段做显示名（解码）
        name = href.rstrip("/").split("/")[-1]
        if not name:
            continue
        name = _unquote(name)
        propstat = resp.find(f"{_DAV_NS}propstat")
        is_dir = False
        size = 0
        mtime = ""
        if propstat is not None:
            prop = propstat.find(f"{_DAV_NS}prop")
            if prop is not None:
                rt = prop.find(f"{_DAV_NS}resourcetype")
                if rt is not None and rt.find(f"{_DAV_NS}collection") is not None:
                    is_dir = True
                size_el = prop.find(f"{_DAV_NS}getcontentlength")
                if size_el is not None and size_el.text:
                    try:
                        size = int(size_el.text)
                    except ValueError:
                        size = 0
                mt_el = prop.find(f"{_DAV_NS}getlastmodified")
                if mt_el is not None and mt_el.text:
                    mtime = mt_el.text
        entries.append({"name": name, "is_dir": is_dir, "size": size, "mtime": mtime})
    return entries


def _unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


@tool(
    "cloud_webdav_read",
    "读取局域网/个人云 WebDAV 上的文件内容（GET，文本，默认上限 2MB）。"
    "base_url/username 同 cloud_webdav_list，密码从环境变量 WEBDAV_PASSWORD 读取。",
    {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "WebDAV 根地址"},
            "path": {"type": "string", "description": "WebDAV 内文件路径，如 /文档/笔记.md"},
            "username": {"type": "string", "description": "WebDAV 账号"},
            "max_chars": {"type": "number", "description": "最多返回字符数，默认 20000"},
        },
        "required": ["base_url", "path", "username"],
    },
)
def cloud_webdav_read(base_url: str, path: str, username: str, max_chars: int = 20000) -> dict:
    try:
        scheme, netloc, base_path = _normalize_base(base_url)
        url = _join(base_path, path)
        with _client(base_url, username, _password()) as c:
            r = c.get(url)
            if r.status_code != 200:
                return {"ok": False, "error": f"读文件失败: {_http_err(r)}"}
            content = r.content
            if len(content) > _MAX_READ_BYTES:
                return {"ok": False, "error": f"文件超过读上限 {_MAX_READ_BYTES // 1024 // 1024}MB，拒绝读取"}
            text = content.decode("utf-8", errors="replace")
        truncated = len(text) > max_chars
        return {"ok": True, "base_url": f"{scheme}://{netloc}", "path": path,
                "size": len(content), "content": text[:max_chars], "truncated": truncated}
    except Exception as e:  # noqa: BLE001
        return _err(e, "读文件失败")


@tool(
    "cloud_webdav_write",
    "把文本内容写入局域网/个人云 WebDAV 文件（PUT，默认上限 10MB，自动建父目录）。"
    "base_url/username 同 cloud_webdav_list，密码从环境变量 WEBDAV_PASSWORD 读取。",
    {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "WebDAV 根地址"},
            "path": {"type": "string", "description": "WebDAV 内文件路径，如 /文档/笔记.md"},
            "content": {"type": "string", "description": "要写入的文本内容"},
            "username": {"type": "string", "description": "WebDAV 账号"},
        },
        "required": ["base_url", "path", "content", "username"],
    },
)
def cloud_webdav_write(base_url: str, path: str, content: str, username: str) -> dict:
    try:
        data = (content or "").encode("utf-8")
        if len(data) > _MAX_WRITE_BYTES:
            return {"ok": False, "error": f"内容超过写上限 {_MAX_WRITE_BYTES // 1024 // 1024}MB"}
        scheme, netloc, base_path = _normalize_base(base_url)
        url = _join(base_path, path)
        with _client(base_url, username, _password()) as c:
            # 先尝试建父目录（MKCOL 对已存在目录返回 405/301 可忽略）
            parent = posixpath.dirname(url)
            if parent and parent != "/":
                c.request("MKCOL", parent)
            r = c.put(url, content=data)
            if r.status_code not in (200, 201, 204):
                return {"ok": False, "error": f"写文件失败: {_http_err(r)}"}
        return {"ok": True, "base_url": f"{scheme}://{netloc}", "path": path,
                "size": len(data), "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return _err(e, "写文件失败")


@tool(
    "cloud_webdav_delete",
    "删除局域网/个人云 WebDAV 上的文件或空目录（需 confirm=\"DELETE\" 二次确认）。"
    "base_url/username 同 cloud_webdav_list，密码从环境变量 WEBDAV_PASSWORD 读取。",
    {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "WebDAV 根地址"},
            "path": {"type": "string", "description": "WebDAV 内文件或空目录路径"},
            "username": {"type": "string", "description": "WebDAV 账号"},
            "confirm": {"type": "string", "description": "二次确认口令，必须为 DELETE 才会执行"},
        },
        "required": ["base_url", "path", "username", "confirm"],
    },
)
def cloud_webdav_delete(base_url: str, path: str, username: str, confirm: str = "") -> dict:
    try:
        if confirm != "DELETE":
            return {"ok": False, "error": "未确认删除：请传入 confirm=\"DELETE\" 以二次确认"}
        scheme, netloc, base_path = _normalize_base(base_url)
        url = _join(base_path, path)
        with _client(base_url, username, _password()) as c:
            r = c.request("DELETE", url)
            if r.status_code not in (200, 204):
                return {"ok": False, "error": f"删除失败: {_http_err(r)}"}
        return {"ok": True, "base_url": f"{scheme}://{netloc}", "path": path,
                "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return _err(e, "删除失败")


@tool(
    "cloud_webdav_mkdir",
    "在局域网/个人云 WebDAV 上创建目录（MKCOL，可多级自动建父目录）。"
    "base_url/username 同 cloud_webdav_list，密码从环境变量 WEBDAV_PASSWORD 读取。",
    {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "WebDAV 根地址"},
            "path": {"type": "string", "description": "WebDAV 内要创建的目录路径，如 /文档/新目录"},
            "username": {"type": "string", "description": "WebDAV 账号"},
        },
        "required": ["base_url", "path", "username"],
    },
)
def cloud_webdav_mkdir(base_url: str, path: str, username: str) -> dict:
    try:
        scheme, netloc, base_path = _normalize_base(base_url)
        url = _join(base_path, path)
        created = []
        with _client(base_url, username, _password()) as c:
            # 逐级 MKCOL，已存在(405/301)则跳过，保证多级目录可建
            segs = url.split("/")
            cur = ""
            for seg in segs:
                if not seg:
                    continue
                cur += "/" + seg
                r = c.request("MKCOL", cur)
                if r.status_code in (200, 201):
                    created.append(cur)
                elif r.status_code in (405, 301, 302):
                    continue  # 已存在
                else:
                    return {"ok": False, "error": f"建目录失败: {_http_err(r)}（{cur}）"}
        return {"ok": True, "base_url": f"{scheme}://{netloc}", "path": path,
                "created": created}
    except Exception as e:  # noqa: BLE001
        return _err(e, "建目录失败")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("cloud_webdav 工具组已加载")
