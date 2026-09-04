"""动机系统（人性层·主动性驱动）。

设计意图（见设计文档 4.7.4）：
- 好奇心：遇未知主动探查（受沙箱约束）→ 主动发现能力缺口。
- 胜任感：任务成功累积、失败消耗 → 决定是否敢于承担复杂任务。
- 自主性：主动提出改进、主动反思 → 驱动自我完善闭环。
"""
from __future__ import annotations

from typing import Dict


class Motivation:
    def __init__(self, curiosity: float = 0.5, competence: float = 0.2, autonomy: float = 0.5):
        self.curiosity = max(0.0, min(1.0, curiosity))
        self.competence = max(0.0, min(1.0, competence))
        self.autonomy = max(0.0, min(1.0, autonomy))

    # ---------- 事件更新 ----------
    def on_success(self) -> None:
        self.competence = min(1.0, self.competence + 0.1)

    def on_failure(self) -> None:
        self.competence = max(0.0, self.competence - 0.05)
        # 挫败后保持适度好奇（探索动力不崩）

    def on_user_trust(self, delta: float = 0.05) -> None:
        self.autonomy = min(1.0, self.autonomy + delta)

    # ---------- 行为倾向 ----------
    def explore_signal(self) -> float:
        """主动探索倾向（好奇心 × 胜任感的合理组合）。"""
        return round(self.curiosity * 0.6 + self.autonomy * 0.4, 2)

    def should_self_propose(self) -> bool:
        """是否主动提出改进建议（自主性 + 胜任感达到阈值）。"""
        return (self.autonomy + self.competence) / 2 >= 0.5

    def snapshot(self) -> Dict:
        return {
            "curiosity": round(self.curiosity, 2),
            "competence": round(self.competence, 2),
            "autonomy": round(self.autonomy, 2),
            "explore_signal": self.explore_signal(),
        }
