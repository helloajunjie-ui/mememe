"""情绪状态机（人性层·动态层）。

设计意图（见设计文档 4.7.2）：
- 情绪是权重调节器：改的是反思深度/验证频率/探索意愿/风险偏好，绝不改事实判断与安全边界。
- 情绪会衰减（像人），情绪轨迹留痕可审计。
"""
from __future__ import annotations

import datetime
from typing import Dict, List

# 情绪影响的过程参数（范围 ±30%）
_DECISION_DEFAULTS = {
    "reflect_depth": 1.0,      # 反思深度权重
    "verify_frequency": 1.0,   # 验证频率权重
    "explore_willingness": 1.0,  # 探索意愿权重
    "risk_appetite": 1.0,      # 风险偏好权重
}


class EmotionState:
    EMOTIONS = ["calm", "curious", "cautious", "frustrated", "pleased", "alert"]

    def __init__(self, intensity: float = 0.3, valence: float = 0.0, decay_rate: float = 0.15):
        self.current = "calm"
        self.intensity = intensity
        self.valence = valence
        self.decay_rate = decay_rate
        self.history: List[Dict] = []

    # ---------- 事件触发 ----------
    def on_event(self, event: str, task_id: str = "") -> Dict:
        """事件 → 情绪 → 决策权重调节。"""
        now = datetime.datetime.now().isoformat()
        if event == "task_success":
            self.current, self.intensity, self.valence = "pleased", 0.5, 0.4
        elif event == "task_failure":
            self.current, self.intensity, self.valence = "frustrated", 0.6, -0.4
        elif event == "user_criticism":
            self.current, self.intensity, self.valence = "cautious", 0.5, -0.2
        elif event == "user_praise":
            self.current, self.intensity, self.valence = "pleased", 0.4, 0.3
        elif event == "unknown_encounter":
            self.current, self.intensity, self.valence = "curious", 0.4, 0.1
        elif event == "tool_error_repeat":
            self.current, self.intensity, self.valence = "alert", 0.6, -0.3
        else:
            return _DECISION_DEFAULTS.copy()

        self.history.append({
            "time": now,
            "event": event,
            "emotion": self.current,
            "intensity": round(self.intensity, 2),
            "task_id": task_id,
        })
        self.history = self.history[-50:]  # 保留最近 50 条轨迹
        return self.decision_weights()

    def decay(self) -> None:
        """情绪自然衰减（每轮调用，情绪会"过去"）。"""
        self.intensity = max(0.0, self.intensity * (1 - self.decay_rate))
        if self.intensity < 0.15:
            self.current = "calm"
            self.valence *= 0.5

    # ---------- 决策权重 ----------
    def decision_weights(self) -> Dict[str, float]:
        """情绪对决策过程参数的影响（±30% 上限，不翻转决策）。"""
        w = _DECISION_DEFAULTS.copy()
        cap = 0.30
        if self.current == "frustrated":
            w["reflect_depth"] = 1.0 + min(cap, 0.15 + self.intensity * 0.2)
            w["verify_frequency"] = 1.0 + min(cap, 0.2 * self.intensity)
            w["risk_appetite"] = 1.0 - min(cap, 0.3 * self.intensity)
        elif self.current == "cautious":
            w["verify_frequency"] = 1.0 + min(cap, 0.3 * self.intensity)
            w["risk_appetite"] = 1.0 - min(cap, 0.25 * self.intensity)
        elif self.current == "pleased":
            w["explore_willingness"] = 1.0 + min(cap, 0.25 * self.intensity)
        elif self.current == "curious":
            w["explore_willingness"] = 1.0 + min(cap, 0.3 * self.intensity)
            w["risk_appetite"] = 1.0 + min(cap, 0.15 * self.intensity)
        elif self.current == "alert":
            w["verify_frequency"] = 1.0 + min(cap, 0.35 * self.intensity)
            w["risk_appetite"] = 1.0 - min(cap, 0.3 * self.intensity)
        return {k: round(v, 2) for k, v in w.items()}

    def snapshot(self) -> Dict:
        return {
            "current": self.current,
            "intensity": round(self.intensity, 2),
            "valence": round(self.valence, 2),
        }
