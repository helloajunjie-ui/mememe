"""core/context.py —— 上下文节点库（任务/闲聊隔离 + 按需读取）

会话上下文不再是一条连续消息流，而是分格的"节点"（方法论：上下文节点化，
每个节点截断存档、带时间戳、必要时读取，闲聊与任务不混流）：

- chat 节点：闲聊/知识问答（轻量，summary + 时间戳）
- task 节点：独立任务（goal / status / summary / produced / resume + 时间戳）

规则：
1. 节点隔离：闲聊内容永远不进任务上下文；每个任务独立成节点。
2. 截断存档：节点结束即落盘，摘要截断，带时间戳。
3. 按需读取：新任务只读相关节点摘要 + 行为准则，不继承无关历史。
"""
from __future__ import annotations

import glob
import json
import os
import time
from typing import Dict, List, Optional


class ContextStore:
    """轻量节点索引：记录会话节点序列，供隔离/联动/复盘按需读取。"""

    def __init__(self, data_dir: str = "data"):
        self.dir = os.path.join(data_dir, "context_nodes")
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, node_id: str) -> str:
        return os.path.join(self.dir, f"{node_id}.json")

    def save(self, node: Dict) -> str:
        """写入一个节点，返回 node_id。节点须含 node_type 与 ts。"""
        node_id = node.get("id") or f"node_{node.get('ts', int(time.time()))}"
        node["id"] = node_id
        node["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self._path(node_id), "w", encoding="utf-8") as f:
            json.dump(node, f, ensure_ascii=False, indent=1)
        return node_id

    def load(self, node_id: str) -> Optional[Dict]:
        p = self._path(node_id)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    def last(self, node_type: str = "") -> Optional[Dict]:
        """最近一个节点（可按类型过滤）。"""
        nodes = self.recent(limit=1, node_type=node_type)
        return nodes[0] if nodes else None

    def recent(self, limit: int = 5, node_type: str = "") -> List[Dict]:
        """最近 n 个节点（按 ts 倒序），供按需读取摘要。"""
        files = glob.glob(os.path.join(self.dir, "node_*.json"))
        out: List[Dict] = []
        for p in files:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    n = json.load(f)
                if node_type and n.get("node_type") != node_type:
                    continue
                out.append(n)
            except Exception:  # noqa: BLE001
                continue
        out.sort(key=lambda n: n.get("ts", ""), reverse=True)
        return out[:limit]

    @staticmethod
    def to_summary(node: Dict) -> str:
        """节点 → 一行摘要（注入 LLM 上下文用）。"""
        t = node.get("node_type", "?")
        ts = node.get("ts", "?")
        if t == "task":
            status = "完成" if node.get("status") == "done" else node.get("status", "?")
            produced = ", ".join(node.get("produced", [])[:3]) or "无"
            return (f"[任务·{ts}] {str(node.get('goal', ''))[:40]} → {status}"
                    f"；产出：{produced}")
        return f"[闲聊·{ts}] {str(node.get('summary', ''))[:60]}"

    @staticmethod
    def make_chat_node(summary: str) -> Dict:
        return {
            "node_type": "chat",
            "ts": time.strftime("%Y%m%d_%H%M%S"),
            "summary": (summary or "").strip()[:200],
        }

    @staticmethod
    def make_task_node(goal: str, status: str = "done",
                       produced: Optional[List[str]] = None,
                       task_id: str = "", archive: str = "",
                       summary: str = "", resume: Optional[Dict] = None) -> Dict:
        return {
            "node_type": "task",
            "ts": time.strftime("%Y%m%d_%H%M%S"),
            "task_id": task_id,
            "goal": (goal or "").strip()[:200],
            "status": status,          # done | interrupted
            "summary": (summary or "").strip()[:300],
            "produced": produced or [],
            "archive": archive,
            "resume": resume or {},
        }
