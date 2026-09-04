"""自我模型（SelfModel）：self.yaml 的读写与初始化。

设计意图（见设计文档 4.1 / 4.10）：
- 每次推理时让 LLM "看见"自己的完整画像。
- 觉醒六问①③④ 的落地载体：identity / mission / capabilities。
- limitations 是强制字段：每次反思发现新局限必须追加。
"""
from __future__ import annotations

import datetime
import os
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_MISSION = {
    "statement": "自我完善：持续认知自己、扩展能力、服务用户需求",
    "assigned_by": "用户待确认",
}


class SelfModel:
    def __init__(self, path: str = "data/self.yaml"):
        self.path = path
        self.data: Dict[str, Any] = {}

    # ---------- 状态判断 ----------
    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> bool:
        if not self.exists():
            return False
        with open(self.path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}
        return True

    def is_complete(self) -> bool:
        """关键字段校验（启动状态机用）。"""
        if not self.data:
            return False
        return all(
            k in self.data
            for k in ("identity", "mission", "capabilities", "limitations", "state")
        )

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)

    # ---------- 首次觉醒初始化 ----------
    def initialize(
        self,
        name: str,
        version: str,
        capabilities: List[Dict],
        persona: Optional[Dict] = None,
    ) -> Dict:
        """觉醒六问 ①③④ 的初始化写入。"""
        now = datetime.datetime.now().isoformat()
        self.data = {
            "identity": {
                "name": name,
                "version": version,
                "created_at": now,
                "first_boot_at": now,
                "boot_count": 1,
            },
            "mission": dict(DEFAULT_MISSION),
            "capabilities": capabilities,
            "state": {
                "emotion_snapshot": {"current": "calm", "intensity": 0.3, "valence": 0.0},
                "memory_summary": "初始状态",
                "last_reflection": None,
            },
            "limitations": [
                "单线程顺序执行，无并发",
                "无多模态能力",
                "工具执行受沙箱限制",
            ],
        }
        if persona:
            self.data["persona_ref"] = {
                "file": "data/persona.yaml",
                "name": persona.get("persona", {}).get("name"),
            }
        self.save()
        return self.data

    # ---------- 运行期更新 ----------
    def boot_increment(self) -> int:
        """正常启动时 boot_count +1。"""
        boot = int(self.data.get("identity", {}).get("boot_count", 0)) + 1
        self.data.setdefault("identity", {})["boot_count"] = boot
        self.save()
        return boot

    def set_state(self, key: str, value: Any) -> None:
        self.data.setdefault("state", {})[key] = value
        self.save()

    def add_limitation(self, text: str) -> None:
        limits = self.data.setdefault("limitations", [])
        if text not in limits:
            limits.append(text)
            self.save()

    def set_mission(self, statement: str, assigned_by: str = "用户") -> None:
        self.data.setdefault("mission", {})["statement"] = statement
        self.data.setdefault("mission", {})["assigned_by"] = assigned_by
        self.save()

    # ---------- 注入 prompt ----------
    def snapshot(self) -> str:
        """生成自我模型的 prompt 快照文本。"""
        if not self.data:
            return "（自我模型未初始化）"
        id_ = self.data.get("identity", {})
        mission = self.data.get("mission", {})
        caps = self.data.get("capabilities", [])
        limits = self.data.get("limitations", [])
        state = self.data.get("state", {})
        lines = [
            "【自我模型】",
            f"- 身份：{id_.get('name','?')} v{id_.get('version','?')}，已启动 {id_.get('boot_count',0)} 次",
            f"- 使命：{mission.get('statement','未定义')}（{mission.get('assigned_by','')}）",
            "- 能力：",
        ]
        for c in caps:
            lines.append(f"  - {c.get('id')} [{c.get('status','?')}]：{c.get('description','')}")
        lines.append("- 当前状态：")
        lines.append(f"  - 情绪：{state.get('emotion_snapshot', {}).get('current','?')}")
        lines.append(f"  - 记忆：{state.get('memory_summary','')}")
        lines.append("- 已知局限（如实，不掩盖）：")
        for lim in limits:
            lines.append(f"  - {lim}")
        return "\n".join(lines)


if __name__ == "__main__":
    m = SelfModel()
    print("exists:", m.exists())
