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
import re
import socket
import sqlite3
import sys
import threading
import time
import uuid
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


class TaskManager:
    """异步任务管理：提交即返回 task_id，后台线程执行，前端轮询进度（不干等）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.tasks = {}

    def create(self) -> str:
        tid = uuid.uuid4().hex[:12]
        with self.lock:
            self.tasks[tid] = {"status": "running", "events": [], "reply": None,
                               "error": None, "created_at": time.time()}
        return tid

    def append_event(self, tid: str, ev: dict) -> None:
        with self.lock:
            if tid in self.tasks:
                self.tasks[tid]["events"].append(ev)

    def finish(self, tid: str, reply: str) -> None:
        with self.lock:
            if tid in self.tasks:
                self.tasks[tid].update(status="done", reply=reply)

    def fail(self, tid: str, error: str) -> None:
        with self.lock:
            if tid in self.tasks:
                self.tasks[tid].update(status="error", error=error)

    def get(self, tid: str) -> dict:
        with self.lock:
            t = self.tasks.get(tid)
            return dict(t) if t else {}

    def cleanup_old(self, max_age: float = 3600.0) -> None:
        """清理超时任务，防止内存膨胀。"""
        now = time.time()
        with self.lock:
            stale = [k for k, v in self.tasks.items()
                     if now - v.get("created_at", 0) > max_age and v.get("status") != "running"]
            for k in stale:
                self.tasks.pop(k, None)


_tasks = TaskManager()


def _run_task(tid: str, message: str) -> None:
    """后台线程执行一轮 turn，阶段事件实时入列。"""
    try:
        a = get_agent()
        with _turn_lock:  # 本地单用户，串行化 turn，避免历史交错
            reply = a.turn(message, stage_callback=lambda ev: _tasks.append_event(tid, ev))
        _tasks.finish(tid, reply)
    except Exception as e:  # noqa: BLE001
        _tasks.fail(tid, f"白绫处理失败: {e}")


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
        m = re.match(r"^/api/task/(\w+)$", path)
        if path in ("/", "/index.html"):
            self._serve_index()
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/config":
            self._handle_config_get()
        elif m:
            self._handle_task(m.group(1))
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/chat":
            self._handle_chat()
        elif path == "/api/config":
            self._handle_config_save()
        elif path == "/api/config/test":
            self._handle_config_test()
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

    # ---------- 聊天 API（异步：提交即返回 task_id，前端轮询 /api/task/<id>） ----------
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
        tid = _tasks.create()
        threading.Thread(target=_run_task, args=(tid, message), daemon=True).start()
        _tasks.cleanup_old()
        self._send_json(200, {"ok": True, "task_id": tid, "ready": True})

    def _handle_task(self, tid: str) -> None:
        t = _tasks.get(tid)
        if not t:
            self._send_json(404, {"ok": False, "error": "任务不存在或已过期"})
            return
        self._send_json(200, {"ok": True, **t})

    # ---------- AI 接入配置 API ----------
    def _handle_config_get(self) -> None:
        try:
            a = get_agent()
            self._send_json(200, {"ok": True, **a.llm_config_view()})
        except AgentNotReady as e:
            self._send_json(503, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"读取配置失败: {e}"})

    def _handle_config_save(self) -> None:
        data = self._read_json()
        try:
            a = get_agent()
        except AgentNotReady as e:
            self._send_json(503, {"ok": False, "error": str(e)})
            return
        # api_key 空/未传 = 保留原值（不清空既有密钥）
        try:
            r = a.reload_llm(
                base_url=data.get("base_url") or None,
                api_key=(data.get("api_key") or "").strip() or None,
                model=data.get("model") or None,
                temperature=data.get("temperature") if data.get("temperature") not in (None, "") else None,
                max_tokens=data.get("max_tokens") if data.get("max_tokens") not in (None, "") else None,
            )
        except Exception as e:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": f"配置无效: {e}"})
            return
        self._send_json(200, {"ok": True, "llm_ready": r.get("ok"), "error": r.get("error"),
                              **a.llm_config_view()})

    def _handle_config_test(self) -> None:
        """用给定配置试发一条消息（不保存），验证 AI 接入可用。
        表单字段留空 → 回退用当前已保存配置（玩家留空 key 也能测试已配置的接入）。"""
        data = self._read_json()
        from core.llm import LLMGateway
        try:
            a = get_agent()
            cur = a._load_llm_cfg()
        except AgentNotReady:
            cur = {}
        base = (data.get("base_url") or "").strip() or cur.get("base_url") or "https://api.deepseek.com"
        key = (data.get("api_key") or "").strip() or cur.get("api_key")
        model = (data.get("model") or "").strip() or cur.get("model") or "deepseek-chat"
        try:
            probe = LLMGateway(base_url=base, api_key=key, model=model)
        except Exception as e:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": f"配置无效: {e}"})
            return
        if not probe.ready:
            self._send_json(200, {"ok": False, "error": "LLM 未就绪：请填写有效的 API Key"})
            return
        resp = probe.chat([{"role": "user", "content": "回复 OK 两个字即可"}], tools=None, tool_choice="none")
        if resp.get("error"):
            self._send_json(200, {"ok": False, "error": resp["error"]})
        else:
            self._send_json(200, {"ok": True, "reply": (resp.get("content") or "")[:100]})


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
