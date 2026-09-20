"""MCP 后台任务管理器。

和 job_mgr 对称，但管理的是 Python Thread（MCP 调用是 HTTP 请求，不是子进程）。
素月调慢 MCP 工具（tt_*、bsk_*）时传 background=true，立即返回 job_id，
过一会用 mcp_job_status 读结果。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Optional

_JOBS: Dict[str, dict] = {}
_LOCK = threading.Lock()
_COUNTER = 0


def start_mcp_job(server: str, tool: str, args: dict) -> str:
    """发起 MCP 后台调用，立即返回 job_id。"""
    global _COUNTER
    with _LOCK:
        _COUNTER += 1
        job_id = f"mcp_{int(time.time())}_{_COUNTER}"
        _JOBS[job_id] = {
            "job_id": job_id,
            "server": server,
            "tool": tool,
            "args": args,
            "status": "running",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "result": None,
            "error": None,
            "cancel_event": threading.Event(),
        }

    def _run():
        from core.mcp import get_mcp_manager
        from core.agent import unwrap_tool_result
        mcp = get_mcp_manager(os.path.join(os.getcwd(), "config", "mcp.json"))
        try:
            r = mcp.call(server, tool, args, cancel_event=_JOBS[job_id]["cancel_event"])
            # MCP 结果被 registry 包成 {"ok": True, "result": "<内层JSON>"}，必须穿透信封再判，
            # 否则内层 ok:False（如 bsk 超时）会被外层 ok:True 掩盖成 done（2026-09-20 实测）。
            ok, err = unwrap_tool_result(r)
            with _LOCK:
                _JOBS[job_id]["result"] = r
                _JOBS[job_id]["error"] = err or None
                if _JOBS[job_id]["cancel_event"].is_set():
                    _JOBS[job_id]["status"] = "killed"
                elif ok:
                    _JOBS[job_id]["status"] = "done"
                else:
                    _JOBS[job_id]["status"] = "error"
        except Exception as e:
            with _LOCK:
                _JOBS[job_id]["result"] = {"ok": False, "error": str(e)}
                _JOBS[job_id]["error"] = str(e)
                _JOBS[job_id]["status"] = "error"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return job_id


def status_mcp_job(job_id: str) -> dict:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return {"ok": False, "error": f"mcp job {job_id} 不存在"}
        return {
            "ok": True,
            "job_id": job_id,
            "server": j["server"],
            "tool": j["tool"],
            "status": j["status"],
            "started_at": j["started_at"],
            "result": j["result"],
            "error": j.get("error"),
        }


def kill_mcp_job(job_id: str) -> dict:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return {"ok": False, "error": f"mcp job {job_id} 不存在"}
        j["cancel_event"].set()
        if j["status"] == "running":
            j["status"] = "killed"
        return {"ok": True, "job_id": job_id, "status": j["status"]}


def list_running() -> list:
    with _LOCK:
        return [
            {"job_id": j["job_id"], "server": j["server"], "tool": j["tool"],
             "status": j["status"], "started_at": j["started_at"]}
            for j in _JOBS.values() if j["status"] == "running"
        ]


# ---------------- 工具入口 ----------------
from tools.base import tool

@tool("mcp_job_status", "查 MCP 后台任务结果（mcp 工具传 background=true 发起后用此查）",
      {"type": "object", "properties": {
          "job_id": {"type": "string", "description": "MCP 后台任务 id"}
      }, "required": ["job_id"]}, group="系统与执行")
def mcp_job_status(job_id: str) -> dict:
    return status_mcp_job(job_id)

@tool("mcp_job_kill", "终止还在跑的 MCP 后台任务",
      {"type": "object", "properties": {
          "job_id": {"type": "string", "description": "要终止的 MCP 任务 id"}
      }, "required": ["job_id"]}, group="系统与执行")
def mcp_job_kill(job_id: str) -> dict:
    return kill_mcp_job(job_id)

@tool("mcp_job_list", "列出进行中的 MCP 后台任务",
      {"type": "object", "properties": {}}, group="系统与执行")
def mcp_job_list() -> dict:
    return {"ok": True, "jobs": list_running()}
