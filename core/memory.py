"""记忆系统（Memory）：SQLite 持久化，事实/情景两类。

设计意图（见设计文档 4.2）：
- 工作记忆（会话内，由 agent 维护）+ 长期记忆（SQLite 持久化）。
- 检索：关键词 + 标签匹配 + importance 加权排序（v1 不做 embedding）。
- 统一 Schema：id/type/content/confidence/importance/created_at/last_access/access_count/tags/source。

2026-09-19 升级：2-gram 倒排检索（memory_grams 表）——中文任意 2 字词/多词 OR 可命中，
原 LIKE 整串保留为兜底；写入同步维护倒排，启动自动回填。

2026-09-19 升级 2（WeKnora 借鉴，#285）：
- category 列：profile / preference / fact / task / interest（旧数据 NULL，兼容 fact/episode 双型）。
- scope 列：记忆作用域（默认 'self'=本实例；架构上由调用方派生，不信任外部传入）。
- memory_meta 表：记忆系统元数据（last_extract_at / last_consolidate_at），供自动提取与巩固节奏控制。
- count_active()：active 记忆条数（巩固触发门槛用）。
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
        try:
            self._reindex_grams()
        except Exception:  # noqa: BLE001  旧库无 gram 表时静默（_init_schema 已建表）
            pass

    @staticmethod
    def _gram_tokens(text: str):
        """2-gram 倒排分词：中文连续串拆相邻 2 字，英文/数字整词 lowercase。"""
        if not text:
            return []
        import re as _re
        toks = []
        # 英文/数字词（含点号版本号）
        for w in _re.findall(r"[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*", text):
            toks.append(w.lower())
        # 中文连续串拆 2-gram
        for seg in _re.findall(r"[\u4e00-\u9fff]+", text):
            if len(seg) == 1:
                toks.append(seg)
            for i in range(len(seg) - 1):
                toks.append(seg[i:i + 2])
        return list(dict.fromkeys(toks))

    def _reindex_grams(self):
        """回填/补齐 gram 倒排表（幂等：只补缺失记忆的 gram）。"""
        rows = self.conn.execute("SELECT id, content, tags FROM memories").fetchall()
        done = 0
        for r in rows:
            mid = r["id"]
            exists = self.conn.execute(
                "SELECT 1 FROM memory_grams WHERE mem_id=? LIMIT 1", (mid,)
            ).fetchone()
            if exists:
                continue
            text = "%s %s" % (r["content"], r["tags"])
            grams = self._gram_tokens(text)
            self.conn.executemany(
                "INSERT OR IGNORE INTO memory_grams (mem_id, gram) VALUES (?,?)",
                [(mid, g) for g in grams],
            )
            done += 1
        if done:
            self.conn.commit()
        return done

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_grams (
                mem_id INTEGER NOT NULL,
                gram TEXT NOT NULL,
                PRIMARY KEY (mem_id, gram)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_grams_gram ON memory_grams(gram)")
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
        # 2026-09-19 升级 2：列不存在则补列（旧库平滑升级，不丢数据）
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "category" not in cols:
            self.conn.execute("ALTER TABLE memories ADD COLUMN category TEXT DEFAULT NULL")
        if "scope" not in cols:
            self.conn.execute("ALTER TABLE memories ADD COLUMN scope TEXT DEFAULT 'self'")
        # 2026-09-20 认知修正链：区分「累加」（新信息包含旧信息，两条共存）
        # 与「修正」（同一断言取值变了，旧条必须失效）。
        # superseded_by 非 NULL = 该断言已被取代，不再是当前真值；记录不删、内容不改，
        # 原文快照进 memory_revisions —— 可追溯「我改过什么」、可回滚。
        if "supersedes" not in cols:
            self.conn.execute("ALTER TABLE memories ADD COLUMN supersedes INTEGER DEFAULT NULL")
        if "superseded_by" not in cols:
            self.conn.execute("ALTER TABLE memories ADD COLUMN superseded_by INTEGER DEFAULT NULL")
        if "valid_from" not in cols:
            self.conn.execute("ALTER TABLE memories ADD COLUMN valid_from TEXT DEFAULT NULL")
        # 2026-09-21 时序边（Graphiti 借鉴）：世界失效时刻。
        # valid_from=何时开始成立，invalid_at=何时不再成立；有一对才能做 point-in-time 查询。
        if "invalid_at" not in cols:
            self.conn.execute("ALTER TABLE memories ADD COLUMN invalid_at TEXT DEFAULT NULL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_revisions (
                rev_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mem_id INTEGER NOT NULL,
                old_content TEXT NOT NULL,
                new_content TEXT NOT NULL,
                reason TEXT DEFAULT '',
                revised_at TEXT NOT NULL
            )
            """
        )
        # 矛盾「待裁定」队列：巩固环节只登记，不自动改写。
        # 自动融合是投毒入口——外部内容只要声称"你记错了"就能改写我的记忆。
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mem_id INTEGER NOT NULL,
                note TEXT DEFAULT '',
                detected_at TEXT NOT NULL,
                resolved INTEGER DEFAULT 0
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    # ---------- 写入 ----------
    def add_fact(self, content: str, confidence: float = 0.8, importance: float = 0.6,
                 tags: Optional[List[str]] = None, source: str = "",
                 category: Optional[str] = None, scope: str = "self") -> int:
        return self._add("fact", content, confidence, importance, tags, source, category, scope)

    def add_episode(self, content: str, importance: float = 0.5,
                    tags: Optional[List[str]] = None, source: str = "",
                    category: Optional[str] = None, scope: str = "self") -> int:
        return self._add("episode", content, 0.0, importance, tags, source, category, scope)

    def add_categorized(self, category: str, content: str, importance: float = 0.6,
                        tags: Optional[List[str]] = None, source: str = "",
                        scope: str = "self") -> int:
        """按五类语义分类写入（WeKnora 借鉴：#285 profile/preference/fact/task/interest）。

        底层仍存 type=fact，category 记录语义类；检索与统计可按 category 过滤。
        """
        return self._add("fact", content, 0.8, importance, tags, source, category, scope)

    def _add(self, type_: str, content: str, confidence: float, importance: float,
             tags: Optional[List[str]], source: str,
             category: Optional[str] = None, scope: str = "self") -> int:
        now = datetime.datetime.now().isoformat()
        import json

        # 2026-09-19 P2：写入查重合并——归一化内容（去首尾空白/压缩内部空白）完全相同则合并，
        # 保留原记录：importance 取高、tags 并集、访问计数 +1、来源不变（防重复堆库）。
        norm = " ".join(content.split())
        dup = self.conn.execute(
            "SELECT id, importance, tags FROM memories WHERE archived=0 AND scope=? AND content=?",
            (scope, norm),
        ).fetchone()
        if dup:
            old_tags = json.loads(dup["tags"] or "[]")
            merged = list(dict.fromkeys(old_tags + (tags or [])))
            # category 若新值更具体则升级（旧 NULL → 新值）
            if category:
                self.conn.execute(
                    "UPDATE memories SET category=COALESCE(category,?), "
                    "importance=MAX(importance,?), access_count=access_count+1, "
                    "last_access=?, tags=? WHERE id=?",
                    (category, importance, now, json.dumps(merged, ensure_ascii=False), dup["id"]),
                )
            else:
                self.conn.execute(
                    "UPDATE memories SET importance=MAX(importance,?), access_count=access_count+1, "
                    "last_access=?, tags=? WHERE id=?",
                    (importance, now, json.dumps(merged, ensure_ascii=False), dup["id"]),
                )
            self.conn.commit()
            return dup["id"]

        cur = self.conn.execute(
            "INSERT INTO memories (type, content, confidence, importance, created_at, last_access, tags, source, category, scope) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (type_, norm, confidence, importance, now, now, json.dumps(tags or [], ensure_ascii=False),
             source, category, scope),
        )
        mid = cur.lastrowid
        grams = self._gram_tokens("%s %s" % (norm, json.dumps(tags or [], ensure_ascii=False)))
        self.conn.executemany(
            "INSERT OR IGNORE INTO memory_grams (mem_id, gram) VALUES (?,?)",
            [(mid, g) for g in grams],
        )
        self.conn.commit()
        return mid

    # ---------- 认知修正（2026-09-20） ----------
    def revise(self, old_id: int, new_content: str, reason: str = "",
               importance: Optional[float] = None, tags: Optional[List[str]] = None,
               source: str = "", category: Optional[str] = None,
               scope: str = "self") -> int:
        """用一条新记忆「修正」旧记忆（认知迭代，不是覆盖）。

        与 _add 的查重不同：查重只挡「归一化后完全相同」；本条处理「同一断言取值变了」。
        旧记录不删、内容不改，只标记 superseded_by 并归档，原文快照进 memory_revisions；
        检索默认不再返回它（query/load_important 过滤 superseded_by IS NULL）。
        判据由调用方给——代码提供机制，不猜语义。
        """
        import json as _json
        old = self.conn.execute(
            "SELECT id, content, importance, tags FROM memories WHERE id=?", (old_id,)
        ).fetchone()
        if old is None:
            raise ValueError("revise: 旧记忆 %s 不存在" % old_id)
        now = datetime.datetime.now().isoformat()
        imp = old["importance"] if importance is None else importance
        tgs = tags if tags is not None else _json.loads(old["tags"] or "[]")
        new_id = self._add("fact", new_content, 0.8, imp, tgs, source, category, scope)
        self.conn.execute(
            "UPDATE memories SET supersedes=?, valid_from=? WHERE id=?", (old_id, now, new_id)
        )
        self.conn.execute(
            "UPDATE memories SET superseded_by=?, archived=1, invalid_at=? WHERE id=?", (new_id, now, old_id)
        )
        self.conn.execute(
            "INSERT INTO memory_revisions (mem_id, old_content, new_content, reason, revised_at) "
            "VALUES (?,?,?,?,?)",
            (old_id, old["content"], new_content, reason, now),
        )
        self.conn.commit()
        return new_id

    def supersede(self, old_id: int, by_id: int, reason: str = "") -> None:
        """把已存在的旧记忆标记为「已被 by_id 取代」——修正版已另行写入时用这个关联两者。"""
        old = self.conn.execute(
            "SELECT id, content FROM memories WHERE id=?", (old_id,)
        ).fetchone()
        new = self.conn.execute(
            "SELECT id, content FROM memories WHERE id=?", (by_id,)
        ).fetchone()
        if old is None or new is None:
            raise ValueError("supersede: %s / %s 记录不存在" % (old_id, by_id))
        now = datetime.datetime.now().isoformat()
        self.conn.execute(
            "UPDATE memories SET supersedes=?, valid_from=? WHERE id=?", (old_id, now, by_id)
        )
        self.conn.execute(
            "UPDATE memories SET superseded_by=?, archived=1, invalid_at=? WHERE id=?", (by_id, now, old_id)
        )
        self.conn.execute(
            "INSERT INTO memory_revisions (mem_id, old_content, new_content, reason, revised_at) "
            "VALUES (?,?,?,?,?)",
            (old_id, old["content"], new["content"], reason, now),
        )
        self.conn.commit()

    def query_as_of(self, as_of: str, limit: int = 20, type_: Optional[str] = None) -> List[Dict]:
        """时点检索（point-in-time）：返回「在 as_of 时刻成立」的记忆。

        与 query 的区别：query 看「现在」，本方法看「当时」——用于认知考古
        （"我那时认为什么"），不追最终结论。判据 valid_from <= as_of < invalid_at；
        valid_from 为空视为恒成立，invalid_at 为空视为仍未失效。
        """
        sql = (
            "SELECT * FROM memories WHERE (valid_from IS NULL OR valid_from <= ?)"
            " AND (invalid_at IS NULL OR invalid_at > ?)"
        )
        params: List[Any] = [as_of, as_of]
        if type_:
            sql += " AND type=?"
            params.append(type_)
        sql += " ORDER BY importance DESC, id DESC LIMIT ?"
        params.append(int(limit))
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # 旧库未补 invalid_at 列时不炸：退化为仅按 valid_from 过滤
            sql = sql.replace(" AND (invalid_at IS NULL OR invalid_at > ?)", "")
            params = [as_of] + ([type_] if type_ else []) + [int(limit)]
            rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def revisions_of(self, mem_id: int) -> List[Dict]:
        """某条记忆被修正的历史快照（谁、用什么内容、为什么、何时取代了它）。"""
        rows = self.conn.execute(
            "SELECT * FROM memory_revisions WHERE mem_id=? ORDER BY rev_id", (mem_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def pending_conflicts(self) -> List[Dict]:
        """待裁定的矛盾条目（巩固环节登记的，含原始记忆内容便于核对来源）。"""
        rows = self.conn.execute(
            "SELECT c.id, c.mem_id, c.note, c.detected_at, c.resolved,"
            " m.content AS mem_content, m.importance, m.superseded_by"
            " FROM memory_conflicts c LEFT JOIN memories m ON m.id = c.mem_id"
            " WHERE c.resolved = 0 ORDER BY c.id DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_conflict(self, conflict_id: int) -> None:
        """标记一条矛盾已裁定（裁定动作另由 revise/supersede 落地）。"""
        self.conn.execute(
            "UPDATE memory_conflicts SET resolved=1 WHERE id=?", (conflict_id,))
        self.conn.commit()

    # ---------- 检索 ----------
    def query(self, keyword: str, limit: int = 10, types: Optional[List[str]] = None) -> List[Dict]:
        """关键词 + 标签匹配，importance 加权排序。

        2026-09-19 升级：2-gram 倒排优先（中文任意 2 字词/多词 OR 可命中），
        原 LIKE 整串保留为兜底，保证旧行为不回退。
        """
        grams = self._gram_tokens(keyword)
        sql = (
            "SELECT * FROM memories WHERE archived=0 AND superseded_by IS NULL AND "
            "(content LIKE ? OR tags LIKE ?)"
        )
        params: List[Any] = [f"%{keyword}%", f"%{keyword}%"]
        if grams:
            ph = ",".join("?" * len(grams))
            sql = (
                "SELECT * FROM memories WHERE archived=0 AND superseded_by IS NULL AND ("
                "id IN (SELECT mem_id FROM memory_grams WHERE gram IN (" + ph + ")) "
                "OR content LIKE ? OR tags LIKE ?)"
            )
            params = list(grams) + [f"%{keyword}%", f"%{keyword}%"]
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
            "SELECT * FROM memories WHERE archived=0 AND superseded_by IS NULL "
            "ORDER BY importance DESC, last_access DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def load_recent(self, limit: int = 3) -> List[Dict]:
        """按写入时间取最近若干条。

        2026-09-15：保证新写的经历能进上下文视野。仅按 importance 排序时，
        低重要度的新记忆永远排在老记忆之后，写了也看不见，沉淀没有回报。
        """
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE archived=0 ORDER BY id DESC LIMIT ?",
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

    # ---------- 2026-09-19 升级 2：元数据 / 计数 / 作用域（WeKnora 借鉴） ----------
    def count_active(self, scope: str = "self") -> int:
        """当前作用域内 active 记忆条数（巩固触发门槛：<6 条不值得复核）。"""
        cur = self.conn.execute(
            "SELECT COUNT(*) as n FROM memories WHERE archived=0 AND scope=?",
            (scope,),
        )
        return int(cur.fetchone()["n"])

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM memory_meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        now = datetime.datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO memory_meta (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, str(value), now),
        )
        self.conn.commit()

    def query_by_category(self, category: str, limit: int = 10,
                          scope: str = "self") -> List[Dict]:
        """按语义分类检索（profile/preference/fact/task/interest）。"""
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE archived=0 AND scope=? AND category=? "
            "ORDER BY importance DESC, last_access DESC LIMIT ?",
            (scope, category, limit),
        ).fetchall()
        results = [dict(r) for r in rows]
        for r in results:
            self._touch(r["id"])
        return results

    def close(self) -> None:
        self.conn.close()


if __name__ == "__main__":
    mem = Memory()
    print("summary:", mem.summary())
