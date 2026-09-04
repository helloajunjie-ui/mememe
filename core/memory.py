"""记忆系统（Memory）：SQLite 持久化，事实/情景两类。

设计意图（见设计文档 4.2）：
- 工作记忆（会话内，由 agent 维护）+ 长期记忆（SQLite 持久化）。
- 检索：关键词 + 标签匹配 + importance 加权排序（v1 不做 embedding）。
- 统一 Schema：id/type/content/confidence/importance/created_at/last_access/access_count/tags/source。
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from typing import Any, Dict, List, Optional


class Memory:
    def __init__(self, db_path: str = "data/memory.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # check_same_thread=False：允许跨线程使用（webui 后台线程初始化 Agent，HTTP 线程调用 turn）。
        # 安全前提：调用方对 turn 串行化（webui _turn_lock），memory 单连接操作短、互斥。
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,                 -- fact / episode
                content TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,        -- 语义记忆置信度（fact）
                importance REAL DEFAULT 0.5,        -- 0~1，反思动态调整
                created_at TEXT NOT NULL,
                last_access TEXT NOT NULL,
                access_count INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',             -- JSON 数组
                source TEXT DEFAULT '',
                archived INTEGER DEFAULT 0
            )
            """
        )
        self.conn.commit()

    # ---------- 写入 ----------
    def add_fact(self, content: str, confidence: float = 0.8, importance: float = 0.6,
                 tags: Optional[List[str]] = None, source: str = "") -> int:
        return self._add("fact", content, confidence, importance, tags, source)

    def add_episode(self, content: str, importance: float = 0.5,
                    tags: Optional[List[str]] = None, source: str = "") -> int:
        return self._add("episode", content, 0.0, importance, tags, source)

    def _add(self, type_: str, content: str, confidence: float, importance: float,
             tags: Optional[List[str]], source: str) -> int:
        now = datetime.datetime.now().isoformat()
        import json

        cur = self.conn.execute(
            "INSERT INTO memories (type, content, confidence, importance, created_at, last_access, tags, source) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (type_, content, confidence, importance, now, now, json.dumps(tags or [], ensure_ascii=False), source),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---------- 检索 ----------
    def query(self, keyword: str, limit: int = 10, types: Optional[List[str]] = None) -> List[Dict]:
        """关键词 + 标签匹配，importance 加权排序。"""
        kw = f"%{keyword}%"
        sql = (
            "SELECT * FROM memories WHERE archived=0 AND "
            "(content LIKE ? OR tags LIKE ?)"
        )
        params: List[Any] = [kw, kw]
        if types:
            placeholders = ",".join("?" * len(types))
            sql += f" AND type IN ({placeholders})"
            params.extend(types)
        sql += " ORDER BY importance DESC, last_access DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        results = [dict(r) for r in rows]
        # 更新访问计数
        for r in results:
            self._touch(r["id"])
        return results

    def load_important(self, limit: int = 20) -> List[Dict]:
        """会话开始时按 importance 预加载。"""
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE archived=0 ORDER BY importance DESC, last_access DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _touch(self, mem_id: int) -> None:
        self.conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_access = ? WHERE id = ?",
            (datetime.datetime.now().isoformat(), mem_id),
        )
        self.conn.commit()

    # ---------- 更新 / 归档 ----------
    def update_importance(self, mem_id: int, delta: float) -> None:
        self.conn.execute(
            "UPDATE memories SET importance = MIN(1.0, MAX(0.0, importance + ?)) WHERE id = ?",
            (delta, mem_id),
        )
        self.conn.commit()

    def archive_stale(self, threshold_days: int = 90, importance_below: float = 0.3) -> int:
        """低重要性 + 长期未访问 → 归档。"""
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=threshold_days)).isoformat()
        cur = self.conn.execute(
            "UPDATE memories SET archived=1 WHERE importance < ? AND last_access < ? AND archived=0",
            (importance_below, cutoff),
        )
        self.conn.commit()
        return cur.rowcount

    def summary(self) -> Dict:
        cur = self.conn.execute(
            "SELECT type, COUNT(*) as n FROM memories WHERE archived=0 GROUP BY type"
        )
        return {r["type"]: r["n"] for r in cur.fetchall()}

    def close(self) -> None:
        self.conn.close()


if __name__ == "__main__":
    mem = Memory()
    print("summary:", mem.summary())
