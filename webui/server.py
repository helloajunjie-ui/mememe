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

import datetime
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.session import SessionStore  # noqa: E402  会话对话层持久化

HOST = "127.0.0.1"
PORT = int(os.environ.get("BAILING_WEBUI_PORT", "8765"))
_started_at = None  # 服务启动时刻（托盘悬停气泡显示运行时长用）

# ---------- 会话对话层（刷新页面 / 重启服务后不"从头开始"） ----------
# 落盘 user 提问 + 最终回复：前端刷新恢复显示，服务重启重建 agent.history。
_session = SessionStore(os.path.join(ROOT, "data"))
_HISTORY_RESTORE_KEEP = 40   # 重启后重建 agent.history 的条数上限（约 20 回合）
_HISTORY_API_MAX = 200       # /api/history 单次返回上限

# ---------- 文本文件上传（传文件给白绫） ----------
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".log", ".rst", ".tex",
    ".csv", ".json", ".jsonl", ".yaml", ".yml", ".ini", ".conf", ".cfg", ".toml",
    ".xml", ".html", ".htm", ".css", ".js", ".mjs", ".ts", ".tsx", ".jsx",
    ".py", ".pyw", ".sh", ".bat", ".cmd", ".ps1", ".sql",
}
_MAX_UPLOAD = 2 * 1024 * 1024          # 单文件上限 2MB
_UPLOAD_PREVIEW = 20000                # 返回给前端的内容预览/消息上限（字符）

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
        a = get_agent()          # 未就绪 → 直接失败，不落半条对话
    except Exception as e:  # noqa: BLE001
        _tasks.fail(tid, f"白绫处理失败: {e}")
        return
    _session.append("user", message)  # 提问先落盘：执行中刷新/崩溃也不丢
    try:
        with _turn_lock:  # 本地单用户，串行化 turn，避免历史交错
            reply = a.turn(message, stage_callback=lambda ev: _tasks.append_event(tid, ev))
        _tasks.finish(tid, reply)
        _session.append("assistant", str(reply or ""))  # 回复落盘 → 刷新可恢复
    except Exception as e:  # noqa: BLE001
        _tasks.fail(tid, f"白绫处理失败: {e}")


# ---------- 整点反思调度（空闲时间自主自省） ----------
# 共建者要求（2026-09-04）：白绫在空闲时自主反思、自我维护，而非只等任务。
# 设计：每小时整点检查一次；若当前无任务在跑（空闲）则后台静默触发一次轻量自省，
# 不进入前端任务队列、不打断用户操作；同一小时内只反思一次，错过整点等下一小时。
_reflect_last_hour = None


def _run_reflection() -> None:
    """执行一次整点反思（后台静默，不入任务队列）。"""
    try:
        a = get_agent()
        msg = ("（内部·整点例行反思）现在是你自主的整点反思时间。请简短地："
               "①回顾最近一次任务或对话的得失，是否有可改进的执行模式；"
               "②检查自身状态（记忆、方法论、工具、完整性）是否有需要维护的；"
               "③如有新经验用 method_learn 沉淀；④发现异常或需共建者注意的事，明确报告。"
               "保持轻量，几步内完成，不要创建长期任务。")
        reply = a.turn(msg)
        # 反思后清理可能留下的断点，避免干扰后续用户对话
        try:
            a.ongoing_task = None
        except Exception:  # noqa: BLE001
            pass
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            a.self_model.set_state("last_reflection", ts)
        except Exception:  # noqa: BLE001
            pass
        print(f"[reflection] {ts} 整点反思完成: {str(reply)[:120]}")
    except Exception as e:  # noqa: BLE001
        print(f"[reflection] 整点反思失败: {type(e).__name__}: {e}")


def _reflection_scheduler() -> None:
    """每小时整点：若空闲（无任务在跑）则触发一次白绫自主整点反思。"""
    global _reflect_last_hour
    while True:
        try:
            now = time.localtime()
            key = (now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour)
            if key != _reflect_last_hour:
                _reflect_last_hour = key  # 标记本小时已处理（错过整点则等下小时）
                if _ready and _turn_lock.acquire(blocking=False):  # 空闲判定
                    try:
                        _run_reflection()
                    finally:
                        _turn_lock.release()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(30)  # 每 30 秒检查一次整点边界


# ---------- 自主时间调度（使用者配置·空闲时自主学习/自由探索） ----------
# 共建者要求（2026-09-04）：学习方向与时间由"使用者"控制，白绫只负责执行。
# 设计：config/study.yaml 定义 启用开关/时间窗口/间隔/主题（topic 留空=自由探索了解世界）。
# 调度器在窗口内、距上次达间隔且空闲时触发一次自主学习，静默入后台，不打断用户。
_STUDY_STATE = {"last": None}


def _load_study_cfg() -> dict:
    """读取 config/study.yaml 的 study 段（使用者配置，每次触发时重读，无需重启）。"""
    try:
        with open(os.path.join(ROOT, "config", "study.yaml"), "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("study", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def _in_window(now: datetime.datetime, start_s: str, end_s: str) -> bool:
    """判断当前是否在 [start, end) 窗口内，支持跨天（end < start）。"""
    try:
        sh, sm = (int(x) for x in str(start_s).split(":"))
        eh, em = (int(x) for x in str(end_s).split(":"))
    except (ValueError, AttributeError):
        return False
    cur = now.hour * 60 + now.minute
    s = sh * 60 + sm
    e = eh * 60 + em
    if s <= e:
        return s <= cur < e
    return cur >= s or cur < e  # 跨天窗口（如 22:00 → 次日 06:00）


def _run_study(cfg: dict) -> None:
    """执行一次自主时间：按使用者主题学习，或（topic 空）自由探索了解世界。"""
    topic = (cfg.get("topic") or "").strip()
    max_steps = int(cfg.get("max_steps") or 12)
    try:
        a = get_agent()
        if topic:
            msg = (f"（内部·自主时间）现在是你被安排的自主学习时间，请按使用者指定的主题学习：{topic}。"
                   f"方式：用 net_search/net_fetch 获取资料 → 阅读消化 → 把学到的知识沉淀进记忆或方法论。"
                   f"保持轻量专业，预算约 {max_steps} 步，不要创建长期任务。")
        else:
            msg = (f"（内部·自主时间）现在是你被安排的自主时间。请自由探索了解世界：可以了解任何你感兴趣"
                   f"的方向（文化、科技、故事、艺术等），用 net_search/net_fetch 获取资料，把有意义的收获"
                   f"沉淀进记忆或方法论。保持轻量，预算约 {max_steps} 步，不要创建长期任务。")
        reply = a.turn(msg)
        try:
            a.ongoing_task = None
        except Exception:  # noqa: BLE001
            pass
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            a.self_model.set_state("last_study", ts)
        except Exception:  # noqa: BLE001
            pass
        print(f"[study] {ts} 自主时间完成（主题={'有' if topic else '自由探索'}）: {str(reply)[:120]}")
    except Exception as e:  # noqa: BLE001
        print(f"[study] 自主时间执行失败: {type(e).__name__}: {e}")


def _study_scheduler() -> None:
    """自主时间调度：配置窗口内、达到间隔且空闲时，触发一次自主学习/自由探索。"""
    while True:
        try:
            now = datetime.datetime.now()
            cfg = _load_study_cfg()
            if cfg.get("enabled", False) and _in_window(
                    now,
                    cfg.get("window", {}).get("start", "22:00"),
                    cfg.get("window", {}).get("end", "06:00")):
                interval_h = float(cfg.get("interval_hours") or 2)
                last = _STUDY_STATE["last"]
                if last is None or (now - last).total_seconds() >= interval_h * 3600:
                    if _ready and _turn_lock.acquire(blocking=False):  # 空闲判定
                        try:
                            _run_study(cfg)
                            _STUDY_STATE["last"] = datetime.datetime.now()
                        finally:
                            _turn_lock.release()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(60)  # 每分钟检查一次（窗口内按间隔触发；忙时下轮再试）


# ---------- 工作流定时启动（设计文档 5.41 · 2026-09-04） ----------
_WF_STATE = {"done": set()}  # 已触发标记 {"日期|time"}，同日同时间不重复


def _load_workflow_cfg() -> dict:
    """读取 config/workflow_schedule.yaml（使用者配置，每次触发时重读，无需重启）。"""
    try:
        with open(os.path.join(ROOT, "config", "workflow_schedule.yaml"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"schedules": data.get("schedules", []) if isinstance(data, dict) else []}
    except Exception:  # noqa: BLE001
        return {"schedules": []}


def _run_workflow_schedule(sch: dict) -> None:
    """触发一次定时工作流：让白绫执行指定工作流（workflow_id）或按任务描述自主建工作流。"""
    name = sch.get("name") or "定时工作流"
    wf_id = (sch.get("workflow_id") or "").strip()
    task = (sch.get("task") or "").strip()
    try:
        a = get_agent()
        if wf_id:
            msg = (f"（内部·定时工作流）现在是使用者安排的定时执行时间。请用 workflow_load 加载工作流"
                   f" {wf_id} 并继续推进/执行它，完成后汇报结果。")
        else:
            if not task:
                task = ("请完成一次『%s』工作流：先自主分析拆节点，用 workflow_create 创建，"
                        "逐个节点执行（workflow_status→执行→workflow_update_node），产物落工作流目录，完成后汇报。" % name)
            msg = f"（内部·定时工作流）现在是你被安排的定时执行时间：{task}"
        reply = a.turn(msg)
        try:
            a.ongoing_task = None
        except Exception:  # noqa: BLE001
            pass
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[workflow-schedule] {ts} 定时工作流「{name}」执行完成: {str(reply)}")
    except Exception as e:  # noqa: BLE001
        print(f"[workflow-schedule] 定时工作流「{name}」执行失败: {type(e).__name__}: {e}")


def _workflow_scheduler() -> None:
    """工作流定时启动调度：每分钟检查配置，命中时间且空闲则触发一次。"""
    while True:
        try:
            now = datetime.datetime.now()
            hm = now.strftime("%H:%M")
            day = now.strftime("%Y-%m-%d")
            for sch in _load_workflow_cfg().get("schedules", []):
                if not sch.get("enabled", True):
                    continue
                if str(sch.get("time", "")).strip() != hm:
                    continue
                key = f"{day}|{hm}|{sch.get('name','')}"
                if key in _WF_STATE["done"]:
                    continue
                if _ready and _turn_lock.acquire(blocking=False):  # 空闲判定
                    try:
                        _WF_STATE["done"].add(key)
                        _run_workflow_schedule(sch)
                    finally:
                        _turn_lock.release()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(60)  # 每分钟检查一次


def _restore_history(a, keep: int = _HISTORY_RESTORE_KEEP) -> int:
    """启动时用落盘的对话层重建 agent.history —— 服务重启后不再断片。

    只回填 role/content 文本（不含 tool_calls / 阶段流水），当闲聊历史继续；
    超出窗口的更早内容仍走 ctx_search / 落盘文件检索。
    """
    try:
        recent = _session.load(limit=keep)
        if not recent:
            return 0
        a.history = [{"role": r["role"], "content": r["content"]} for r in recent]
        a.ctx_start_idx = 0     # 恢复的历史属既有对话，不被任务隔离逻辑误摘
        a.ctx_mode = "chat"
        print(f"  已恢复最近 {len(recent)} 条对话到上下文（重启不断片）")
        return len(recent)
    except Exception as e:  # noqa: BLE001
        print(f"  对话历史恢复失败（不影响启动）: {e}")
        return 0


def _init_agent_async() -> None:
    """后台线程：初始化 Agent 并 boot。失败记原因。"""
    global _agent, _ready, _init_error
    try:
        from core.agent import Agent

        a = Agent(os.path.join(ROOT, "config.yaml"))
        a.boot()
        _session.trim()        # 落盘瘦身：超上限裁最旧
        _restore_history(a)    # 重启后续上上次的对话
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
    _STATIC_TYPES = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
        ".ico": "image/x-icon", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
    }

    def do_GET(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/task/(\w+)$", path)
        if path in ("/", "/index.html"):
            self._serve_index()
        elif re.match(r"^/assets/[A-Za-z0-9_./-]+$", path) and "." in path.rsplit("/", 1)[-1]:
            self._serve_static(path)
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/config":
            self._handle_config_get()
        elif path == "/api/models":
            self._handle_models_get()
        elif path == "/api/study":
            self._handle_study()
        elif path == "/api/history":
            self._handle_history()
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
        elif path == "/api/models/fetch":
            self._handle_models_fetch()
        elif path == "/api/study":
            self._handle_study()
        elif path == "/api/upload":
            self._handle_upload()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    # ---------- 静态资源（webui/assets 目录，防目录穿越） ----------
    def _serve_static(self, path: str) -> None:
        webui = os.path.dirname(os.path.abspath(__file__))
        full = os.path.normpath(os.path.join(webui, path.lstrip("/")))
        if not full.startswith(webui):
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        ext = os.path.splitext(full)[1].lower()
        ctype = self._STATIC_TYPES.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

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
        self.send_header("Cache-Control", "no-store")  # 前端改动即时生效，不吃浏览器缓存
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
                "activity": getattr(a, "activity", {"state": "idle", "detail": ""})
            })
            self._send_json(200, base)
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"状态读取失败: {e}"})

    # ---------- 对话历史（页面刷新/重开时恢复显示；只读落盘，不依赖 agent 就绪） ----------
    def _handle_history(self) -> None:
        raw = urlparse(self.path).query or ""
        limit = 60
        m = re.search(r"(?:^|&)limit=(\d+)", raw)
        if m:
            limit = max(1, min(_HISTORY_API_MAX, int(m.group(1))))
        msgs = _session.load(limit=limit)
        self._send_json(200, {"ok": True, "count": len(msgs), "messages": msgs})

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
        # 增量事件游标：?since=N 只返回第 N 条之后的新事件。
        # 修：全量返回历史 events 会让轮询端反复累加旧事件（曾致测试端误报 615 次空转）
        try:
            qs = urlparse(self.path).query
            since = int(dict(p.split("=", 1) for p in qs.split("&") if "=" in p).get("since", "0") or "0")
        except (TypeError, ValueError):
            since = 0
        events = t.get("events", [])
        self._send_json(200, {
            "ok": True,
            "status": t.get("status"),
            "reply": t.get("reply"),
            "error": t.get("error"),
            "events": events[since:],
            "next": len(events),
            "done": t.get("status") in ("done", "error"),
        })

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

    # ---------- 模型列表 API ----------
    def _handle_models_get(self) -> None:
        """返回缓存的模型列表（面板下拉）。"""
        try:
            a = get_agent()
            self._send_json(200, {"ok": True, **a.models_view()})
        except AgentNotReady as e:
            self._send_json(503, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"读取模型列表失败: {e}"})

    def _handle_models_fetch(self) -> None:
        """用表单/当前配置自动获取模型列表并保存。"""
        data = self._read_json()
        try:
            a = get_agent()
        except AgentNotReady as e:
            self._send_json(503, {"ok": False, "error": str(e)})
            return
        try:
            r = a.fetch_models(
                base_url=(data.get("base_url") or "").strip() or None,
                api_key=(data.get("api_key") or "").strip() or None,
            )
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"获取模型列表异常: {e}"})
            return
        self._send_json(200, {"ok": r.get("ok", False), "error": r.get("error"),
                              "models": r.get("models", []), "count": r.get("count", 0),
                              "source": r.get("source")})

    # ---------- 自主时间配置 API（使用者控制学习方向与时间） ----------
    def _handle_study(self) -> None:
        """GET：读当前自主时间配置 + 上次学习时间；POST：保存配置。"""
        if self.command == "GET":
            cfg = _load_study_cfg()
            last = None
            try:
                a = get_agent()
                last = a.self_model.data.get("state", {}).get("last_study")
            except Exception:  # noqa: BLE001
                pass
            self._send_json(200, {"ok": True, "study": cfg, "last_study": last})
            return
        data = self._read_json()
        try:
            cfg = dict(_load_study_cfg())
            if "enabled" in data:
                cfg["enabled"] = bool(data["enabled"])
            if "window" in data and isinstance(data["window"], dict):
                cfg["window"] = {"start": str(data["window"].get("start") or "22:00"),
                                 "end": str(data["window"].get("end") or "06:00")}
            if "interval_hours" in data:
                cfg["interval_hours"] = data["interval_hours"]
            if "topic" in data:
                cfg["topic"] = str(data["topic"])
            if "max_steps" in data:
                cfg["max_steps"] = int(data["max_steps"])
            path = os.path.join(ROOT, "config", "study.yaml")
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"study": cfg}, f, allow_unicode=True, sort_keys=False)
            self._send_json(200, {"ok": True, "study": cfg})
        except Exception as e:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": f"保存自主时间配置失败: {e}"})


    # ---------- 文本文件上传 API ----------
    def _handle_upload(self) -> None:
        """接收文本文件（裸 body + X-Filename 头），解码→保存→返回内容供前端发消息。"""
        name = unquote((self.headers.get("X-Filename") or "").strip() or "unnamed.txt")
        name = os.path.basename(name.replace("\\", "/"))
        ext = os.path.splitext(name)[1].lower()
        if ext not in _TEXT_EXTS:
            self._send_json(400, {"ok": False, "error": "仅支持文本类文件（txt / md / json / py / csv 等）"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "文件内容为空"})
            return
        if length > _MAX_UPLOAD:
            self._send_json(413, {"ok": False, "error": "文件过大（上限 2MB）"})
            return
        raw = self.rfile.read(length)
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        if text is None:
            self._send_json(400, {"ok": False, "error": "无法按文本解码，请确认是文本文件"})
            return
        up_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
        os.makedirs(up_dir, exist_ok=True)
        safe = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name) or "file.txt"
        save_path = os.path.join(up_dir, f"{int(time.time())}_{safe}")
        try:
            with open(save_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"文件保存失败: {e}"})
            return
        truncated = len(text) > _UPLOAD_PREVIEW
        self._send_json(200, {
            "ok": True, "name": name, "chars": len(text), "bytes": len(raw),
            "path": save_path, "truncated": truncated,
            "content": text[:_UPLOAD_PREVIEW],
        })


def _port_in_use(host: str, port: int) -> bool:
    """端口占用检查：防重复启动导致的多进程互锁。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _cmd_out(args: list, timeout: int = 15) -> str:
    """运行系统命令并返回多编码安全解码的输出（utf-8 → gbk → latin-1 → replace）。

    中文 Windows 下 netstat/tasklist/taskkill 输出为 GBK，`text=True` 用 UTF-8
    解码会 UnicodeDecodeError 且 stdout 变 None——统一走 bytes + 回退解码
    （方法论 #35 的经验）。
    """
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return ""
    raw = r.stdout or b""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", "replace")


def _pid_alive(pid: str) -> bool:
    """检查进程是否存活（Windows: tasklist；POSIX: os.kill(pid,0)）。"""
    if os.name == "nt":
        return f"{pid}" in _cmd_out(["tasklist", "/FI", f"PID eq {pid}"])
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _kill_old_instance(port: int) -> list:
    """清理占用目标端口的旧实例进程（实例唯一：先清理再运行）。

    Windows：netstat -ano 定位 LISTENING 的 PID → taskkill /F 强杀，
    轮询确认死亡，仍存活则 PowerShell Stop-Process 兜底。
    POSIX（Linux/macOS）：lsof -ti :port → kill -9。
    只杀占用本服务端口的进程，绝不无差别清进程。
    """
    killed: list = []
    pids: set = set()
    if os.name == "nt":
        out = _cmd_out(["netstat", "-ano"])
        for line in out.splitlines():
            # 只匹配本服务监听地址的 LISTENING 行，避免误杀其他含端口号的进程
            if f"{HOST}:{port}" in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
    else:
        out = _cmd_out(["lsof", "-ti", f":{port}"])
        pids = {p for p in out.split() if p.isdigit()}

    for pid in pids:
        if os.name == "nt":
            _cmd_out(["taskkill", "/F", "/PID", pid])
            dead = False
            for _ in range(6):  # 轮询确认死亡（约 5s）
                time.sleep(0.8)
                if not _pid_alive(pid):
                    dead = True
                    break
            if not dead:  # taskkill 未生效 → PowerShell Stop-Process 兜底
                _cmd_out(["powershell", "-NoProfile", "-Command",
                          f"Stop-Process -Id {pid} -Force"], timeout=20)
                time.sleep(1)
        else:
            _cmd_out(["kill", "-9", pid])
            time.sleep(1)
        killed.append(pid)
    return killed


def main() -> None:
    if _port_in_use(HOST, PORT):
        print(f"端口 {PORT} 已被占用——检测到旧实例，清理后重启 ...")
        killed = _kill_old_instance(PORT)
        if killed:
            print(f"  已清理旧实例进程: {', '.join(killed)}")
        # 等待端口释放（最多 15s）
        released = False
        for _ in range(30):
            time.sleep(0.5)
            if not _port_in_use(HOST, PORT):
                released = True
                break
        if not released:
            print(f"端口 {PORT} 仍被占用，无法启动。请手动检查占用进程（可能是非白绫程序）。")
            sys.exit(1)
        print("端口已释放，启动新实例 ...")

    # 后台线程异步初始化 Agent；服务端口立即监听（网页秒开）
    threading.Thread(target=_init_agent_async, daemon=True).start()
    # 整点反思调度：空闲时自主自省（共建者要求·2026-09-04）
    threading.Thread(target=_reflection_scheduler, daemon=True).start()
    # 自主时间调度：使用者配置窗口内，空闲时自主学习/自由探索（共建者要求·2026-09-04）
    threading.Thread(target=_study_scheduler, daemon=True).start()
    # 工作流定时启动：使用者配置的时间点自动触发（共建者要求·2026-09-04）
    threading.Thread(target=_workflow_scheduler, daemon=True).start()

    print("白绫 Web 界面（API 服务）启动中 ...")
    print(f"  地址: http://{HOST}:{PORT}")
    print("  网页已就绪（Agent 后台初始化中，完成后即可对话）")
    print("  Ctrl+C 退出。")

    global _started_at
    _started_at = time.time()

    srv = ThreadingHTTPServer((HOST, PORT), Handler)

    # ---- 托盘化：常驻系统托盘（悬停气泡 + 左键开网页 + 右键退出） ----
    # 主线程跑托盘事件循环，HTTP 服务放子线程；托盘"退出白绫" → 关闭服务收尾。
    # 托盘依赖（pystray/Pillow）缺失时自动降级为前台运行（关终端 = 停止）。
    try:
        from webui.tray import available, run_tray
        if available():
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            print("  已驻留系统托盘：悬停看状态 / 左键打开网页 / 右键退出（退出 = 停止服务）")
            try:
                run_tray(url=f"http://{HOST}:{PORT}", on_quit=srv.shutdown)
            finally:
                srv.shutdown()  # 保险：确保 serve_forever 已停止（幂等）
                srv.server_close()
                if _ready:
                    try:
                        _agent.close()
                    except Exception:  # noqa: BLE001
                        pass
            return
    except Exception as e:  # noqa: BLE001
        print(f"  托盘启动失败（降级为前台运行）: {e}")

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
