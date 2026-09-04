"""信息快照缓存层（SnapshotCache）：避免重复获取常用信息，省时间与 token。

设计（见设计文档 5.34 信息快照与缓存）：
- 把"获取成本高、变化频率低"的常用信息（系统工具清单、环境信息等）缓存到 data/snapshots.json。
- 读取：快照在 TTL 内 → 直接返回（from_cache=True）；过期或 force_refresh → 调 fetch_fn 重新获取并更新快照。
- 原则：默认用快照，显式要最新才刷新（效率优先）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Tuple


class SnapshotCache:
    def __init__(self, path: str = "data/snapshots.json"):
        self.path = path
        self.data: Dict[str, Dict] = self._load()

    def _load(self) -> Dict[str, Dict]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save(self) -> None:
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def get(self, key: str, fetch_fn: Callable[[], Any],
            ttl_seconds: int = 21600, force_refresh: bool = False) -> Tuple[Any, str, bool]:
        """返回 (value, fetched_at_iso, refreshed)。

        - 快照存在且未过期 → 返回快照，refreshed=False。
        - 过期 / force_refresh → 调 fetch_fn 重新获取并更新快照，refreshed=True。
        """
        now = datetime.datetime.now()
        entry = self.data.get(key)
        if not force_refresh and entry:
            try:
                fetched = datetime.datetime.fromisoformat(entry["fetched_at"])
                if (now - fetched).total_seconds() < entry.get("ttl_seconds", ttl_seconds):
                    return entry["value"], entry["fetched_at"], False
            except (KeyError, ValueError):
                pass
        value = fetch_fn()
        fetched_at = now.isoformat(timespec="seconds")
        self.data[key] = {"value": value, "fetched_at": fetched_at, "ttl_seconds": ttl_seconds}
        self._save()
        return value, fetched_at, True

    def summary(self) -> Dict[str, Any]:
        out = {}
        for k, v in self.data.items():
            out[k] = {"fetched_at": v.get("fetched_at"), "ttl_seconds": v.get("ttl_seconds")}
        return out
