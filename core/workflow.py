# -*- coding: utf-8 -*-
"""工作流（Workflow）数据模型与持久化。

设计（共建者 2026-09-04）：
- 工作流 = 一串任务节点（pipeline），每个节点是一个"有任务结果的小任务"。
- 节点间通过【存储路径】传递产物：前节点把产物落盘到工作流目录并记录路径，
  后节点按需用 fs_read 读取，不把内容直接塞进上下文（省 token、防思维混乱）。
- 节点依赖：input_from 声明本节点依赖哪些前节点的产物路径。
- 可保存整个工作流（workspace/workflows/<id>.json），随时复用 / 断点恢复。
"""
from __future__ import annotations

import datetime
import json
import os
import uuid

WORKFLOW_ROOT = None  # 由 agent 设置：workspace/workflows


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def set_root(root: str) -> None:
    global WORKFLOW_ROOT
    WORKFLOW_ROOT = root
    os.makedirs(root, exist_ok=True)


def _wf_path(wf_id: str) -> str:
    return os.path.join(WORKFLOW_ROOT, f"{wf_id}.json")


class Workflow:
    """工作流对象：data 为磁盘持久化的完整状态。

    关键设计（共建者 2026-09-04 纠正）：节点数量与内容【不预设】——工作流要完成
    的任务是未知的，节点由白绫拿到任务后自主分析决定，执行中可随时动态追加
    （add_node）。每次节点操作自动写【带时间戳的执行日志】（execution.log）。
    """

    def __init__(self, name: str, nodes: list, wf_id: str = None):
        wf_id = wf_id or uuid.uuid4().hex[:12]
        self.data = {
            "id": wf_id,
            "name": name,
            "created_at": _now(),
            "status": "running",
            "dir": os.path.join(WORKFLOW_ROOT, wf_id),
            "nodes": [],
            "log": [],  # 执行日志（时间戳 + 节点 + 动作 + 详情）
        }
        self.set_nodes(nodes)

    # ---------- 构建 ----------
    def set_nodes(self, nodes: list) -> None:
        norm = []
        for i, n in enumerate(nodes or []):
            if isinstance(n, str):
                n = {"title": n}
            norm.append(self._norm_node(n, i + 1))
        self.data["nodes"] = norm

    @staticmethod
    def _norm_node(n: dict, idx: int) -> dict:
        return {
            "id": str(n.get("id") or f"n{idx}"),
            "title": str(n.get("title") or f"节点{idx}"),
            "desc": str(n.get("desc") or ""),
            "input_from": list(n.get("input_from") or []),
            "output": str(n.get("output") or ""),   # 实际产物路径（节点完成后填写）
            "result": str(n.get("result") or ""),   # 完成摘要（通知完成结果用）
            "status": str(n.get("status") or "todo"),
        }

    # ---------- 执行日志（带时间戳 · 记录都做了什么） ----------
    def _log_entry(self, node: str, action: str, detail: str = "") -> None:
        entry = {"ts": _now(), "node": node, "action": action, "detail": detail}
        self.data.setdefault("log", []).append(entry)
        try:  # 同步追加到工作流目录 execution.log（文本，便于人读）
            os.makedirs(self.data["dir"], exist_ok=True)
            log_path = os.path.join(self.data["dir"], "execution.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{entry['ts']}] [{node}] {action} {detail}\n".rstrip() + "\n")
        except OSError:
            pass

    # ---------- 动态节点（任务未知 → 执行中自主追加） ----------
    def add_node(self, title: str, desc: str = "", input_from: list = None) -> dict:
        idx = len(self.data["nodes"]) + 1
        node = self._norm_node({"title": title, "desc": desc, "input_from": input_from or []}, idx)
        self.data["nodes"].append(node)
        self._log_entry(node["id"], "ADD", f"追加节点「{title}」（依赖: {', '.join(input_from) if input_from else '无'}）")
        self.save()
        return {"ok": True, "node": node, "progress": f"{len(self.data['nodes'])} 个节点"}

    # ---------- 持久化 ----------
    def save(self) -> str:
        os.makedirs(WORKFLOW_ROOT, exist_ok=True)
        os.makedirs(self.data["dir"], exist_ok=True)
        path = _wf_path(self.data["id"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def load(wf_id: str) -> "Workflow":
        with open(_wf_path(wf_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        wf = Workflow(data.get("name", ""), data.get("nodes", []), wf_id=wf_id)
        wf.data = data  # 磁盘为准（含已完成的节点状态/产物）
        return wf

    @staticmethod
    def list_all() -> list:
        if not os.path.isdir(WORKFLOW_ROOT):
            return []
        out = []
        for fn in sorted(os.listdir(WORKFLOW_ROOT), reverse=True):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(WORKFLOW_ROOT, fn), "r", encoding="utf-8") as f:
                        d = json.load(f)
                    out.append({
                        "id": d.get("id"), "name": d.get("name"),
                        "status": d.get("status"), "nodes": len(d.get("nodes", [])),
                        "created_at": d.get("created_at"),
                    })
                except Exception:  # noqa: BLE001
                    pass
        return out

    # ---------- 进度 ----------
    def summary(self) -> str:
        """生成工作流进度文本（含节点状态与产物路径），供上下文注入/工具返回。"""
        lines = [f"工作流：{self.data['name']}（{self.data['id']}）状态 {self.data['status']}，"
                 f"产物目录 {self.data['dir']}"]
        mark = {"todo": "⬜", "running": "⏳", "done": "✅", "failed": "❌"}
        done = 0
        for n in self.data["nodes"]:
            dep = f"，依赖前节点：{', '.join(n['input_from'])}" if n.get("input_from") else ""
            out = f"，产物：{n['output']}" if n.get("output") else ""
            lines.append(f"- {mark.get(n['status'], '⬜')} {n['id']} {n['title']}{dep}{out} [{n['status']}]")
            if n["status"] == "done":
                done += 1
        lines.append(f"完成 {done}/{len(self.data['nodes'])} 个节点")
        logs = self.data.get("log", [])
        if logs:
            lines.append("最近执行记录（带时间戳）：")
            for e in logs[-5:]:
                lines.append(f"  - [{e.get('ts', '')}] {e.get('node', '')} {e.get('action', '')} "
                             f"{str(e.get('detail', ''))[:80]}")
        return "\n".join(lines)

    def recent_log(self, limit: int = 10) -> list:
        return list(self.data.get("log", []))[-limit:]

    def next_todo(self) -> dict:
        """返回当前应执行的第一个 todo 节点（含其依赖节点的产物路径清单）。"""
        for n in self.data["nodes"]:
            if n["status"] == "todo":
                deps = []
                for did in n.get("input_from") or []:
                    for m in self.data["nodes"]:
                        if m["id"] == did:
                            deps.append({"id": m["id"], "title": m["title"],
                                         "output": m.get("output", ""),
                                         "result": m.get("result", "")})
                return {"node": n, "deps": deps}
        return {}

    def update_node(self, node_id: str, status: str = None,
                    output: str = None, result: str = None) -> dict:
        for n in self.data["nodes"]:
            if n["id"] == node_id:
                if status:
                    n["status"] = status
                if output is not None:
                    n["output"] = str(output)
                if result is not None:
                    n["result"] = str(result)
                self._log_entry(node_id, "UPDATE", f"状态→{status}"
                                + (f"；产物: {output}" if output else "")
                                + (f"；{result}" if result else ""))
                done = sum(1 for x in self.data["nodes"] if x["status"] == "done")
                self.data["status"] = "done" if done == len(self.data["nodes"]) else "running"
                self.save()
                return {"ok": True, "node": node_id, "status": n["status"],
                        "progress": f"{done}/{len(self.data['nodes'])}"}
        return {"ok": False, "error": f"节点不存在: {node_id}"}
