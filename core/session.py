# -*- coding: utf-8 -*-
"""core/session.py —— 会话对话层持久化（让"刷新/重启后从新开始"不再发生）

问题背景（2026-09-13 共建者反馈「每次刷新都是从新开始了」）：
    根因两层——
    1) 前端消息只活在 DOM 里，无任何持久化，刷新即空白；
    2) agent.history 是纯内存 List[Dict]，进程一停就归零；而 webui 服务
       重启（启动.bat 会清理旧实例）是常态操作。
    结果：用户看到"从头开始"，我也真的断片。

本模块只负责「对话可见层」：
    - 记 user 提问 + 最终 assistant 回复（不含 tool_calls / system 注入 / 阶段流水）；
    - append-only JSONL，一行一条，崩溃最多丢最后一行；
    - 既是前端刷新后恢复显示的素材，也是服务重启后重建 agent.history 的最小依据。

与任务全量存档（workspace/.../messages.jsonl，含工具中间态）职责分离：
那份供检索复盘，这份供「对话可见层」恢复。

边界：本文件产物属私有实例数据（data/session/），不入公开仓库。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

DEFAULT_KEEP = 5000  # 落盘保留上限（行）：超出后裁剪最旧的，避免无限膨胀


class SessionStore:
    """对话层存储：data/session/chat_history.jsonl（append-only）"""

    def __init__(self, data_dir: str = "data", filename: str = "chat_history.jsonl"):
        self.dir = os.path.join(data_dir, "session")
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError:
            pass
        self.path = os.path.join(self.dir, filename)
        self._lock = threading.Lock()

    # ---------- 写 ----------
    def append(self, role: str, content: str, ts: str = "", attachments: Optional[List[Dict]] = None) -> bool:
        """追加一条对话。role 仅接受 user/assistant；空内容忽略。
        attachments：可选，用户附件 [{kind, name, path, content}]，持久化供前端恢复显示。"""
        if role not in ("user", "assistant"):
            return False
        content = (content or "").strip()
        if not content:
            return False
        rec = {"ts": ts or time.strftime("%Y-%m-%d %H:%M:%S"),
               "role": role, "content": content}
        if attachments:
            rec["attachments"] = attachments
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False

    # ---------- 读 ----------
    def load(self, limit: int = 0) -> List[Dict]:
        """读取对话（时间正序）。limit>0 时只取最近 limit 条。坏行跳过，不抛。"""
        if not os.path.exists(self.path):
            return []
        out: List[Dict] = []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("role") in ("user", "assistant") and rec.get("content"):
                        out.append({"ts": rec.get("ts", ""), "role": rec["role"],
                                    "content": rec["content"],
                                    "attachments": rec.get("attachments") or []})
        except OSError:
            return []
        return out[-limit:] if limit and limit > 0 else out

    def count(self) -> int:
        return len(self.load())

    def trim(self, keep: int = DEFAULT_KEEP) -> int:
        """把落盘裁剪到最近 keep 条（原子替换），返回裁掉的条数。"""
        all_msgs = self.load()
        if len(all_msgs) <= keep:
            return 0
        kept = all_msgs[-keep:]
        tmp = self.path + ".tmp"
        try:
            with self._lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    for rec in kept:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                os.replace(tmp, self.path)
        except OSError:
            return 0
        return len(all_msgs) - len(kept)
