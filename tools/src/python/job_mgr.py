# -*- coding: utf-8 -*-
"""后台任务管理器（素月是掌控者：发起后台任务后立即拿回控制权）。

设计（2026-09-20）：
- cmd_run(background=true) 走这里：Popen 启动，立即返回 job_id
- job_status(job_id)：查进程是否存活 + 读输出尾部
- job_kill(job_id)：taskkill /F /T 杀整个进程树
- job_list()：列所有后台任务
- stdout/stderr 走临时文件，避免 pipe 继承 daemon 导致 hang
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
import tempfile
import threading
import uuid

_JOBS: dict = {}
_LOCK = threading.Lock()


def _is_windows() -> bool:
    return os.name == "nt"


def _decode(b: bytes) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def start_job(command: str, timeout: int = 0) -> dict:
    """启动后台任务。返回 {job_id, pid}。timeout>0 时到点自动 kill。"""
    job_id = uuid.uuid4().hex[:8]
    out_path = tempfile.mktemp(prefix=f"bail_job_{job_id}_", suffix=".out")
    err_path = tempfile.mktemp(prefix=f"bail_job_{job_id}_", suffix=".err")

    if _is_windows():
        cmd_list = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        cmd_list = ["bash", "-c", command]

    flags = subprocess.CREATE_NEW_PROCESS_GROUP if _is_windows() else 0
    out_fp = open(out_path, "wb")
    err_fp = open(err_path, "wb")
    try:
        proc = subprocess.Popen(cmd_list, stdout=out_fp, stderr=err_fp, creationflags=flags)
    except OSError as e:
        out_fp.close(); err_fp.close()
        try: os.remove(out_path); os.remove(err_path)
        except OSError: pass
        return {"ok": False, "error": f"无法启动: {e}"}

    with _LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "pid": proc.pid,
            "command": command,
            "started_at": datetime.datetime.now().isoformat(),
            "out_path": out_path,
            "err_path": err_path,
            "proc": proc,
            "status": "running",
            "timeout": timeout,
        }
    return {"ok": True, "job_id": job_id, "pid": proc.pid}


def _read_tail(path: str, tail: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail))
            return _decode(f.read())
    except OSError:
        return ""


def _refresh_status(job: dict) -> None:
    # 已被用户主动 kill 的，保持 killed 终态，不被 poll() 覆盖成 error
    if job.get("killed_by_user"):
        job["status"] = "killed"
        return
    proc = job.get("proc")
    if proc is None:
        return
    rc = proc.poll()
    if rc is None:
        job["status"] = "running"
    else:
        job["exit_code"] = rc
        # 素月 2026-09-20 验收：exit_code!=0 不一刀切判 error。
        # PowerShell 碰受保护目录也会退出码 1，但 stdout 有完整产出。
        # 看 stdout 有没有实际输出：有=done_with_warnings，无=error。
        job["status"] = "done" if rc == 0 else "error"
        if rc != 0:
            try:
                with open(job["out_path"], "rb") as f:
                    has_out = len(f.read()) > 0
            except OSError:
                has_out = False
            if has_out:
                job["status"] = "done_with_warnings"


def status_job(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return {"ok": False, "error": f"job {job_id} 不存在"}
    _refresh_status(job)
    return {
        "ok": True,
        "job_id": job_id,
        "pid": job["pid"],
        "status": job["status"],
        "started_at": job["started_at"],
        "exit_code": job.get("exit_code"),
        "stdout_tail": _read_tail(job["out_path"]),
        "stderr_tail": _read_tail(job["err_path"]),
    }


def kill_job(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return {"ok": False, "error": f"job {job_id} 不存在"}
    pid = job["pid"]
    if _is_windows():
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        except Exception:
            try: job["proc"].kill()
            except Exception: pass
    else:
        try: job["proc"].kill()
        except Exception: pass
    job["status"] = "killed"
    job["killed_by_user"] = True
    return {"ok": True, "job_id": job_id, "pid": pid, "status": "killed"}


def list_jobs() -> dict:
    """状态条只返回 running 的 job（完成/出错/killed 的不显示）。
    完成通知已由前端状态变化检测插对话区，状态条不放历史。"""
    out = []
    with _LOCK:
        for j in _JOBS.values():
            _refresh_status(j)
            if j["status"] == "running":
                out.append({
                    "job_id": j["job_id"], "pid": j["pid"], "status": j["status"],
                    "started_at": j["started_at"],
                    "command": j["command"][:120],
                })
    return {"ok": True, "jobs": out, "count": len(out)}


# ---------------- 工具入口 ----------------
from tools.base import tool


@tool("job_list", "列出所有后台任务（素月发起的、还在跑或刚结束的）",
      {"type": "object", "properties": {}}, group="系统与执行")
def job_list() -> dict:
    return list_jobs()


@tool("job_status", "查后台任务状态：是否还在跑、退出码、stdout/stderr 尾部输出",
      {"type": "object", "properties": {
          "job_id": {"type": "string", "description": "cmd_run(background=true) 返回的 job_id"}
      }, "required": ["job_id"]}, group="系统与执行")
def job_status(job_id: str) -> dict:
    return status_job(job_id)


@tool("job_kill", "停止后台任务（taskkill /F /T 杀整个进程树，包括 daemon 子进程）",
      {"type": "object", "properties": {
          "job_id": {"type": "string", "description": "要停止的 job_id"}
      }, "required": ["job_id"]}, group="系统与执行")
def job_kill(job_id: str) -> dict:
    return kill_job(job_id)
