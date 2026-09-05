# -*- coding: utf-8 -*-
"""cloud_webdav 工具组最小连通性测试：本地 mock WebDAV 服务验证 5 个操作。

用 Python 标准库 http.server 实现最小 WebDAV（PROPFIND/GET/PUT/DELETE/MKCOL + Basic 认证），
在 127.0.0.1 随机端口起服务，调用 cloud_webdav 工具函数验证列目录/读/写/删/建目录闭环。
"""
import base64
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from xml.etree import ElementTree as ET

# 让 tools.base 可导入
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.src.python import cloud_webdav  # noqa: E402

USER = "testuser"
PASS = "testpass"
_DAV = "{DAV:}"


class MockWebDAV(BaseHTTPRequestHandler):
    """内存版最小 WebDAV：文件存 dict {path: bytes}，目录存 set。"""

    def log_message(self, *a):
        pass

    def _check_auth(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            u, _, p = decoded.partition(":")
            return u == USER and p == PASS
        except Exception:
            return False

    def _send(self, code, body=b"", ctype="text/xml; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        if code == 401:
            # 401 后关闭连接，避免 httpx 读到连接重置而非干净响应
            self.close_connection = True

    def do_PROPFIND(self):
        if not self._check_auth():
            return self._send(401)
        path = self.path.rstrip("/") or "/"
        # 目录集合
        dirs = self.server.dirs
        files = self.server.files
        if path not in dirs and path not in files:
            return self._send(404)
        # 构造 207 multistatus
        resp = ET.Element(f"{_DAV}multistatus")
        # 自身
        self._add_resp(resp, path, is_dir=(path in dirs), size=len(files.get(path, b"")))
        # 子项（Depth:1）
        prefix = path + "/" if path != "/" else "/"
        for d in sorted(dirs):
            if d.startswith(prefix) and "/" not in d[len(prefix):]:
                self._add_resp(resp, d, is_dir=True, size=0)
        for f, data in sorted(files.items()):
            if f.startswith(prefix) and "/" not in f[len(prefix):]:
                self._add_resp(resp, f, is_dir=False, size=len(data))
        body = ET.tostring(resp, encoding="utf-8")
        self._send(207, body)

    def _add_resp(self, parent, path, is_dir, size):
        r = ET.SubElement(parent, f"{_DAV}response")
        href = ET.SubElement(r, f"{_DAV}href")
        href.text = path
        ps = ET.SubElement(r, f"{_DAV}propstat")
        prop = ET.SubElement(ps, f"{_DAV}prop")
        rt = ET.SubElement(prop, f"{_DAV}resourcetype")
        if is_dir:
            ET.SubElement(rt, f"{_DAV}collection")
        if not is_dir:
            cl = ET.SubElement(prop, f"{_DAV}getcontentlength")
            cl.text = str(size)
        st = ET.SubElement(ps, f"{_DAV}status")
        st.text = "HTTP/1.1 200 OK"

    def do_GET(self):
        if not self._check_auth():
            return self._send(401)
        path = self.path.rstrip("/") or "/"
        if path in self.server.files:
            return self._send(200, self.server.files[path], "text/plain; charset=utf-8")
        return self._send(404)

    def do_PUT(self):
        if not self._check_auth():
            return self._send(401)
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        path = self.path.rstrip("/") or "/"
        self.server.files[path] = data
        return self._send(201)

    def do_DELETE(self):
        if not self._check_auth():
            return self._send(401)
        path = self.path.rstrip("/") or "/"
        if path in self.server.files:
            del self.server.files[path]
            return self._send(204)
        if path in self.server.dirs:
            # 仅空目录可删
            prefix = path + "/"
            if any(f.startswith(prefix) for f in self.server.files) or \
               any(d.startswith(prefix) for d in self.server.dirs):
                return self._send(409)
            self.server.dirs.discard(path)
            return self._send(204)
        return self._send(404)

    def do_MKCOL(self):
        if not self._check_auth():
            return self._send(401)
        path = self.path.rstrip("/") or "/"
        if path in self.server.dirs:
            return self._send(405)
        self.server.dirs.add(path)
        return self._send(201)


def main():
    # 预置一个文件
    srv = HTTPServer(("127.0.0.1", 0), MockWebDAV)
    srv.dirs = {"/"}
    srv.files = {"/hello.txt": "你好，WebDAV".encode("utf-8")}
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    os.environ["WEBDAV_PASSWORD"] = PASS
    base = f"http://127.0.0.1:{port}"

    results = {}
    # 1. 列目录
    r = cloud_webdav.cloud_webdav_list(base_url=base, username=USER, path="/")
    results["list_root"] = r
    # 2. 建目录
    r = cloud_webdav.cloud_webdav_mkdir(base_url=base, username=USER, path="/docs")
    results["mkdir"] = r
    # 3. 写文件
    r = cloud_webdav.cloud_webdav_write(base_url=base, username=USER, path="/docs/note.md",
                                        content="# 测试笔记\n内容")
    results["write"] = r
    # 4. 读文件（含中文）
    r = cloud_webdav.cloud_webdav_read(base_url=base, username=USER, path="/hello.txt")
    results["read_hello"] = r
    # 5. 列 docs 目录
    r = cloud_webdav.cloud_webdav_list(base_url=base, username=USER, path="/docs")
    results["list_docs"] = r
    # 6. 删除文件
    r = cloud_webdav.cloud_webdav_delete(base_url=base, username=USER, path="/docs/note.md",
                                         confirm="DELETE")
    results["delete_file"] = r
    # 7. 删除未确认（应拒绝）
    r = cloud_webdav.cloud_webdav_delete(base_url=base, username=USER, path="/docs/note.md")
    results["delete_noconfirm"] = r
    # 8. 错误密码（应 401）
    os.environ["WEBDAV_PASSWORD"] = "wrong"
    r = cloud_webdav.cloud_webdav_list(base_url=base, username=USER, path="/")
    results["bad_auth"] = r
    os.environ["WEBDAV_PASSWORD"] = PASS

    srv.shutdown()
    print(json.dumps(results, ensure_ascii=False, indent=2))

    # 断言
    ok = True
    checks = [
        ("list_root", lambda r: r.get("ok") and r["count"] >= 1),
        ("mkdir", lambda r: r.get("ok")),
        ("write", lambda r: r.get("ok")),
        ("read_hello", lambda r: r.get("ok") and "WebDAV" in r.get("content", "")),
        ("list_docs", lambda r: r.get("ok") and any(e["name"] == "note.md" for e in r["entries"])),
        ("delete_file", lambda r: r.get("ok")),
        ("delete_noconfirm", lambda r: not r.get("ok")),
        ("bad_auth", lambda r: not r.get("ok") and "401" in r.get("error", "")),
    ]
    for name, fn in checks:
        passed = fn(results[name])
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
