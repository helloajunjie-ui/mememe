"""方法论库（MethodStore）：白绫沉淀"行为模式"——什么方法有效、什么教训要避免。

设计（见设计文档 5.9）：
- 与事实记忆（memory，记录"是什么"）区分：方法论记录"怎么做 / 不要怎么做"，直接指导未来决策。
- 每条方法论：type（good=正面可复用 / bad=反面避免）、scene（触发场景）、method（方法/教训）、evidence（证据/为什么）、importance。
- 注入 system prompt 的"我的经验法则"段，让白绫下次遵守自己沉淀的方法。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

_PATH = "data/methodology.json"


class MethodStore:
    def __init__(self, path: str = _PATH):
        self.path = path
        self.data: Dict = self._load()

    def _load(self) -> Dict:
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

    def learn(self, type_: str, scene: str, method: str,
              evidence: str = "", importance: float = 0.6) -> int:
        """沉淀一条方法论。type_: "good"（值得复用）/ "bad"（避免）。"""
        type_ = type_ if type_ in ("good", "bad") else "good"
        importance = max(0.0, min(1.0, float(importance)))
        methods = self.data.setdefault("methods", [])
        mid = len(methods) + 1
        methods.append({
            "id": mid,
            "type": type_,
            "scene": scene[:200],
            "method": method[:500],
            "evidence": evidence[:300],
            "importance": importance,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self._save()
        return mid

    def list(self, type_: Optional[str] = None) -> List[Dict]:
        methods = self.data.get("methods", [])
        if type_:
            methods = [m for m in methods if m.get("type") == type_]
        return sorted(methods, key=lambda m: -m.get("importance", 0))

    def to_prompt(self, limit: int = 4) -> str:
        """生成注入 system prompt 的"我的经验法则"文本。"""
        good = self.list("good")[:limit]
        bad = self.list("bad")[:limit]
        parts = []
        if good:
            lines = [f"- [{m['scene']}] {m['method']}" + (f"（{m['evidence']}）" if m.get("evidence") else "")
                     for m in good]
            parts.append("正面（值得复用）：\n" + "\n".join(lines))
        if bad:
            lines = [f"- [{m['scene']}] 避免：{m['method']}" + (f"（{m['evidence']}）" if m.get("evidence") else "")
                     for m in bad]
            parts.append("反面（避免）：\n" + "\n".join(lines))
        return "\n\n".join(parts) if parts else "（暂无沉淀的方法论）"

    def summary(self) -> Dict:
        methods = self.data.get("methods", [])
        return {
            "total": len(methods),
            "good": sum(1 for m in methods if m.get("type") == "good"),
            "bad": sum(1 for m in methods if m.get("type") == "bad"),
        }
