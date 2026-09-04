# -*- coding: utf-8 -*-
"""白绫（Bailing）Web 交互界面服务端 —— API 化，网页仅作展示端。

架构：
- 本服务 = 轻量 HTTP API（Python 标准库，零外部依赖）。
- Agent（白绫本体）在【后台线程异步初始化】，服务端口【秒级启动】，
  不阻塞网页打开；初始化完成后 /api/status 报告 ready=true。
- 网页（webui/index.html）纯展示：拉状态、发消息、渲染回复。

启动：
    cd F:\\me\\self-agent
    .\\.venv\\Scripts\\python.exe webui/server.py
    （或双击 start_webui.bat）

访问：http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HOST = "127.0.0.1"
PORT = int(os.environ.get("BAILING_WEBUI_PORT", "8765"))

# ---------- Agent 后台异步初始化 ----------
_agent = None
_ready = False
_init_error = None
_turn_lock = threading.Lock()


class AgentNotReady(Exception):
    pass


def _init_agent_async() -> None:
    """后台线程：初始化 Agent 并 boot。失败记原因。"""
    global _agent, _ready, _init_error
    try:
        from core.agent import Agent

        a = Agent(os.path.join(ROOT, "config.yaml"))
        a.boot()
        _agent = a
        _ready = True
        print(f"  Agent 就绪（启动模式: {getattr(a, 'boot_mode', '?')}）")
    except Exception as e:  # noqa: BLE001
        _init_error = str(e)
        print(f"  Agent 初始化失败: {e}")


def get_agent():
    if not _ready:
        raise AgentNotReady(_init_error or "白绫仍在初始化中")
    return _agent


def memory_count() -> int:
    try:
        con = sqlite3.connect(os.path.join(ROOT, "data", "memory.db"))
        try:
            return con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return 0


def method_count() -> int:
    try:
        with open(os.path.join(ROOT, "data", "methodology.json"), "r", encoding="utf-8") as f:
            return len(json.load(f).get("methods", []))
    except Exception:  # noqa: BLE001
        return 0


class Handler(BaseHTTPRequestHandler):
    server_version = "BailingWebUI/1.0"

    def log_message(self, *args):  # 静默默认访问日志
        pass

    # ---------- 通用响应 ----------
    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_index()
        elif path == "/api/status":
            self._handle_status()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/chat":
            self._handle_chat()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    # ---------- 页面（静态展示端） ----------
    def _serve_index(self) -> None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
        try:
            with open(p, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self._send_json(500, {"ok": False, "error": "index.html 缺失"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- 状态 API ----------
    def _handle_status(self) -> None:
        base = {"ok": True, "ready": _ready, "name": "白绫", "version": ""}
        if not _ready:
            base["message"] = _init_error or "白绫初始化中..."
            self._send_json(200, base)
            return
        try:
            a = get_agent()
            base.update({
                "version": getattr(a, "version", ""),
                "boot_mode": getattr(a, "boot_mode", None),
                "emotion": a.emotion.snapshot(),
                "motivation": a.motivation.snapshot(),
                "memory_count": memory_count(),
                "method_count": method_count(),
            })
            self._send_json(200, base)
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"状态读取失败: {e}"})

    # ---------- 聊天 API ----------
    def _handle_chat(self) -> None:
        data = self._read_json()
        message = (data.get("message") or "").strip()
        if not message:
            self._send_json(400, {"ok": False, "error": "message 不能为空"})
            return
        if not _ready:
            self._send_json(503, {"ok": False, "ready": False,
                                  "error": _init_error or "白绫还在初始化中，请稍候再试"})
            return
        try:
            a = get_agent()
            with _turn_lock:  # 本地单用户，串行化 turn，避免历史交错
                reply = a.turn(message)
            self._send_json(200, {"ok": True, "reply": reply})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"白绫处理失败: {e}"})


def _port_in_use(host: str, port: int) -> bool:
    """端口占用检查：防重复启动导致的多进程互锁。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def main() -> None:
    if _port_in_use(HOST, PORT):
        print(f"端口 {PORT} 已被占用——可能已有一个白绫界面在运行（浏览器打开 http://{HOST}:{PORT} 即可）。")
        print("如需重启，请先关闭旧服务。")
        sys.exit(1)

    # 后台线程异步初始化 Agent；服务端口立即监听（网页秒开）
    threading.Thread(target=_init_agent_async, daemon=True).start()

    print("白绫 Web 界面（API 服务）启动中 ...")
    print(f"  地址: http://{HOST}:{PORT}")
    print("  网页已就绪（Agent 后台初始化中，完成后即可对话）")
    print("  Ctrl+C 退出。")

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭 ...")
    finally:
        if _ready:
            try:
                _agent.close()
            except Exception:  # noqa: BLE001
                pass
        srv.server_close()


if __name__ == "__main__":
    main()
