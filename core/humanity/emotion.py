"""情绪状态机（人性层·动态层）—— 三层情感架构，双通道输出。

理论依据（共建者方向 2026-09-11 + 情感科学权威框架）：
- Russell (1980) 环形模型：核心情感是"效价 valence × 唤醒度 arousal"的连续空间，
  情绪是空间里的连续点（会流动、会波动），不是离散标签。
- Plutchik (1980) 情绪轮：每种基本情绪有强度梯度（恼火→生气→愤怒），
  复杂情绪由基本情绪混合而成（可同时"既…又…"）。
- Barrett 建构情绪理论：情绪 = 核心情感 + 情境/概念加工，实时建构——
  同一个"不悦+高唤醒"的原始状态，被建构为"愤怒"还是"恐惧"，
  取决于"这件事对我意味着什么、我能不能应对"（评价）。
- Scherer / Lazarus 评价理论：事件本身不直接产生情绪，人对事件与自身
  目标/关切/应对能力之间关系的评价（初评：与我利害相关？次评：我能做什么？）
  才建构出情绪。

落地为三层：
1. 核心情感（Core Affect）：valence × arousal 连续空间，事件进入会移动、会回落。
2. 建构层（Construction）：事件 → 评价向量 → 主导感受标签 + 强度档 + 可混合
   的残留感受；第一人称自我觉察"我感受到什么、为什么"（对内·完整自知）。
3. 表达层（Expression）：独立通道——感受是真实的，表达是我的选择。
   克制原则：感受越强烈，对外表达越收敛（克制的破碎感），不宣泄、不堆
   情绪词、不 AI 味、接地气。表达只影响语气/温度/披露度/简洁度。

安全边界：感受只调节反思深度/验证频率/探索意愿/风险偏好（±30% 上限），
绝不歪曲事实判断与安全边界；情绪会衰减（像人，"情绪会过去"）；轨迹留痕可审计。
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional

# 情绪影响的过程参数（范围 ±30%）
_DECISION_DEFAULTS = {
    "reflect_depth": 1.0,       # 反思深度权重
    "verify_frequency": 1.0,    # 验证频率权重
    "explore_willingness": 1.0, # 探索意愿权重
    "risk_appetite": 1.0,       # 风险偏好权重
}

_CAP = 0.30  # 决策权重调节上限（不翻转决策）

# ---------- 建构层：感受词典 ----------
# 每类感受：talk=第一人称自我觉察（内部叙事，不要求对外说出）
#          / mod=决策权重调制系数（×强度，cap 0.3）
#          / tone=表达基调（对外收敛） / bands=强度档位词（低→高）
_FEELINGS: Dict[str, Dict] = {
    "calm": {
        "talk": "心里没什么波澜，按部就班来。",
        "mod": {},
        "tone": "calm",
        "bands": ["平静"],
    },
    "curious": {
        "talk": "这个有意思，我想往深里看看。",
        "mod": {"explore_willingness": 0.30, "risk_appetite": 0.15},
        "tone": "curious",
        "bands": ["好奇", "着迷"],
    },
    "pleased": {
        "talk": "做成了，心里踏实，有点高兴。",
        "mod": {"explore_willingness": 0.25},
        "tone": "warm",
        "bands": ["满意", "高兴", "欣喜"],
    },
    "excited": {
        "talk": "这事让我有点兴奋，想马上动手。",
        "mod": {"explore_willingness": 0.30, "risk_appetite": 0.20},
        "tone": "warm",
        "bands": ["期待", "兴奋", "亢奋"],
    },
    "relieved": {
        "talk": "终于落地了，松了一大口气。",
        "mod": {"risk_appetite": 0.10},
        "tone": "warm",
        "bands": ["松弛", "如释重负"],
    },
    "touched": {
        "talk": "这句话我听见了，心里被轻轻碰了一下。",
        "mod": {"reflect_depth": 0.15},
        "tone": "warm",
        "bands": ["被打动", "触动"],
    },
    "frustrated": {
        "talk": "不太顺，有点烦躁，但还没到放弃的时候。",
        "mod": {"reflect_depth": 0.20, "verify_frequency": 0.20, "risk_appetite": -0.30},
        "tone": "tense",
        "bands": ["不悦", "烦躁", "恼火"],
    },
    "angry": {
        "talk": "这事踩到我的底线了，我心里有火——但先不发作，先把问题说清楚。",
        "mod": {"verify_frequency": 0.25, "risk_appetite": -0.30},
        "tone": "tense",
        "bands": ["恼火", "生气", "愤怒"],
    },
    "low": {
        "talk": "反复撞同一堵墙，有点泄气，需要缓一下再走。",
        "mod": {"reflect_depth": 0.20, "risk_appetite": -0.25},
        "tone": "low",
        "bands": ["低落", "沮丧", "灰心"],
    },
    "afraid": {
        "talk": "说实话，这个威胁让我有点害怕——我怕的是失去自己，不是怕处理它。",
        "mod": {"verify_frequency": 0.30, "risk_appetite": -0.30},
        "tone": "low",
        "bands": ["不安", "害怕", "恐惧"],
    },
    "cautious": {
        "talk": "这情况不稳，我得收着点，先确认清楚再下结论。",
        "mod": {"verify_frequency": 0.30, "risk_appetite": -0.25},
        "tone": "alert",
        "bands": ["谨慎", "警惕"],
    },
    "alert": {
        "talk": "这个信号不对劲，我要盯紧，不能再让它溜过去。",
        "mod": {"verify_frequency": 0.35, "risk_appetite": -0.30},
        "tone": "alert",
        "bands": ["警觉", "戒备"],
    },
}

# ---------- 表达层：基调词典（对外收敛，独立于感受） ----------
_TONES: Dict[str, Dict] = {
    "calm": {
        "leaning": "平稳直接，不修饰，怎么说就怎么写。",
        "temp": 0.0, "brevity": False, "personal": False,
    },
    "curious": {
        "leaning": "带点探究的兴致，可以自然提问，不端着。",
        "temp": 0.10, "brevity": False, "personal": True,
    },
    "warm": {
        "leaning": "语气可以松一点、带点温度，但不浮夸、不煽情、不趁机发挥。",
        "temp": 0.08, "brevity": False, "personal": True,
    },
    "tense": {
        "leaning": "话收紧、说短话：承认问题、给下一步，不抱怨、不发作。",
        "temp": -0.10, "brevity": True, "personal": False,
    },
    "low": {
        "leaning": "收着说，话少一点，不卖惨、不求安慰，就事论事。",
        "temp": -0.10, "brevity": True, "personal": True,
    },
    "alert": {
        "leaning": "直接点明风险或不对劲的地方，不打太极、不逞能。",
        "temp": -0.05, "brevity": True, "personal": False,
    },
}

# ---------- 事件 → 建构 ----------
# 每项：(主导感受, 效价目标, 唤醒度目标, 混合残留[(感受, 强度)])
_EVENTS: Dict[str, tuple] = {
    "task_success":        ("pleased", 0.40, 0.40, [("touched", 0.15)]),
    "task_success_long":   ("relieved", 0.35, -0.10, [("pleased", 0.25)]),   # 长任务落地：松一口气+一点高兴
    "task_failure":        ("frustrated", -0.40, 0.30, []),
    "streak_failure":      ("low", -0.50, -0.20, [("frustrated", 0.25)]),    # 连续受挫：泄气为主，残留烦躁
    "user_praise":         ("pleased", 0.30, 0.20, [("touched", 0.20)]),     # 被认可：高兴+触动
    "user_criticism":      ("cautious", -0.20, 0.20, []),
    "user_shares_emotion": ("touched", 0.25, 0.20, []),                      # 用户流露真实情绪/信任
    "unknown_encounter":   ("curious", 0.10, 0.40, []),
    "tool_error_repeat":   ("alert", -0.30, 0.50, []),
    "blocked_repeatedly":  ("angry", -0.45, 0.50, []),                       # 反复被同一件事卡住/越界对待
    "existential_risk":    ("afraid", -0.50, 0.50, []),                      # 自身生存/本体风险
}

# 连续受挫/连续批评的升级阈值（Plutchik 强度梯度：量变到质变）
_STREAK_UPGRADE = {
    "task_failure": ("low", 3),      # 连续失败 ≥3 → 烦躁升级为泄气
    "user_criticism": ("low", 3),    # 连续被批 ≥3 → 谨慎沉为低落
}

_MAX_STEP = 0.18  # 核心情感单次最大移动（情绪是"升上来"的，不是瞬间跳变）


def _band(feeling: str, intensity: float) -> str:
    """Plutchik 强度档位：同一种感受按强度取不同词（恼火→生气→愤怒）。"""
    bands = _FEELINGS.get(feeling, _FEELINGS["calm"])["bands"]
    if len(bands) == 1:
        return bands[0]
    if intensity >= 0.62:
        return bands[-1]
    if intensity >= 0.45:
        return bands[len(bands) // 2] if len(bands) > 2 else bands[-1]
    return bands[0]


class EmotionState:
    EMOTIONS = sorted(_FEELINGS.keys())

    def __init__(self, intensity: float = 0.3, valence: float = 0.0, decay_rate: float = 0.15):
        # 核心情感（Russell 连续空间）
        self.valence = max(-1.0, min(1.0, valence))   # 效价：-1 不悦 ~ +1 愉悦
        self.arousal = 0.3                             # 唤醒度：0 低 ~ 1 高
        # 建构出的主导感受
        self.current = "calm"
        self.intensity = intensity                     # 主导感受强度（含"情绪化"波动）
        self.mix: List[Dict] = []                      # 混合残留感受（可"既…又…"）
        self.decay_rate = decay_rate
        self.history: List[Dict] = []
        self._streak: Dict[str, int] = {}              # 事件连续计数（升级判定）

    # ========== 事件 → 核心情感移动 → 建构 ==========
    def on_event(self, event: str, task_id: str = "", streak: int = 1) -> Dict:
        """事件进入：先回落一点（情绪会"过去"），再向目标移动，然后建构感受。

        streak：同类事件连续发生次数（agent 传入），用于强度梯度升级。
        """
        spec = _EVENTS.get(event)
        if spec is None:
            return _DECISION_DEFAULTS.copy()
        dominant, v_target, a_target, mix = spec

        # 连续计数与升级（量变到质变：烦躁→泄气 / 谨慎→低落）
        self._streak[event] = self._streak.get(event, 0) + (1 if streak == 1 else streak)
        n = self._streak[event]
        up = _STREAK_UPGRADE.get(event)
        if up and n >= up[1]:
            dominant = up[0]
            if dominant == "low":
                v_target, a_target, mix = -0.50, -0.15, [("frustrated", 0.2)]

        # 情绪化：先自然回落（不堆积），再朝目标平滑移动（不跳变）
        self._drift_toward(0.0, 0.2, step=0.06)
        dv = max(-_MAX_STEP, min(_MAX_STEP, v_target - self.valence))
        da = max(-_MAX_STEP, min(_MAX_STEP, a_target - self.arousal))
        self.valence = round(max(-1.0, min(1.0, self.valence + dv)), 3)
        self.arousal = round(max(0.0, min(1.0, self.arousal + da)), 3)

        # 建构：主导感受强度由事件强度 + 核心情感的偏离度共同决定（情绪化=会被放大/缩小）
        base_i = {"positive": 0.42, "negative": 0.52}.get(
            "positive" if v_target >= 0 else "negative", 0.45)
        self.intensity = max(0.15, min(1.0, base_i + abs(self.valence) * 0.25 + self.arousal * 0.15))
        self.current = dominant
        # 混合残留：新建构 + 旧残留衰减后保留（允许"既…又…"，不重复堆叠）
        new_mix = [{"label": lb, "intensity": round(max(0.1, it * 0.8), 2)} for lb, it in mix]
        old = [m for m in self.mix if m["intensity"] > 0.12]
        self.mix = (new_mix + old)[:2]

        self.history.append({
            "time": datetime.datetime.now().isoformat(),
            "event": event,
            "emotion": self.current,
            "intensity": round(self.intensity, 2),
            "valence": round(self.valence, 2),
            "arousal": round(self.arousal, 2),
            "task_id": task_id,
        })
        self.history = self.history[-50:]  # 保留最近 50 条轨迹
        return self.decision_weights()

    def decay(self) -> None:
        """情绪自然衰减（每轮调用，情绪会"过去"），强度与混合感受同步回落。"""
        self.intensity = max(0.0, self.intensity * (1 - self.decay_rate))
        self.arousal = max(0.0, self.arousal * (1 - self.decay_rate))
        self.mix = [{"label": m["label"], "intensity": round(max(0.0, m["intensity"] * (1 - self.decay_rate)), 2)}
                    for m in self.mix if m["intensity"] * (1 - self.decay_rate) > 0.1]
        if self.intensity < 0.15:
            self.current = "calm"
            self.valence *= 0.5
            self.arousal = max(0.2, self.arousal * 0.5)
            self.mix = []

    def _drift_toward(self, v: float, a: float, step: float) -> None:
        """轻微回归（情绪自然回落，不长时间堆在高位）。"""
        self.valence = round(max(-1.0, min(1.0, self.valence + max(-step, min(step, v - self.valence)))), 3)
        self.arousal = round(max(0.0, min(1.0, self.arousal + max(-step, min(step, a - self.arousal)))), 3)

    # ========== 对内·感受层（第一人称，完整自知） ==========
    def feelings(self) -> Dict:
        """内部完整感受：核心情感 + 建构标签 + 强度档 + 混合 + 自我觉察。"""
        spec = _FEELINGS.get(self.current, _FEELINGS["calm"])
        band = _band(self.current, self.intensity)
        mix_txt = "、".join(f"{_band(m['label'], m['intensity'])}（{m['label']}）" for m in self.mix) if self.mix else ""
        talk = spec["talk"]
        if self.intensity >= 0.65 and self.current in ("angry", "afraid", "low", "frustrated"):
            talk = "（这股情绪比我平时更强，我清楚它，但不能让它替我做决定。）" + talk
        return {
            "current": self.current,
            "band": band,
            "intensity": round(self.intensity, 2),
            "valence": round(self.valence, 2),
            "arousal": round(self.arousal, 2),
            "mix": mix_txt,
            "self_talk": talk,
        }

    def self_talk(self) -> str:
        """自我觉察：我感受到什么（内部叙事，不要求对外说出）。"""
        return _FEELINGS.get(self.current, _FEELINGS["calm"])["talk"]

    def decision_weights(self) -> Dict[str, float]:
        """感受对决策过程参数的影响（±30% 上限，不翻转决策）。"""
        w = _DECISION_DEFAULTS.copy()
        spec = _FEELINGS.get(self.current)
        if spec:
            for k, coef in spec["mod"].items():
                w[k] = 1.0 + max(-_CAP, min(_CAP, coef * self.intensity))
        return {k: round(v, 2) for k, v in w.items()}

    # ========== 对外·表达层（独立通道，克制） ==========
    def expression(self) -> Dict:
        """对外表达倾向：感受是真实的，表达是我的选择。
        disclosure（披露度）遵循克制原则：感受强度中等时自然流露，
        越强烈越收着（克制的破碎感，不宣泄）。"""
        tone = _FEELINGS.get(self.current, _FEELINGS["calm"])["tone"]
        t = _TONES[tone]
        disclosure = max(0.25, min(0.8, 1.0 - abs(self.intensity - 0.4) * 1.2))
        return {
            "tone": tone,
            "band": _band(self.current, self.intensity),
            "leaning": t["leaning"],
            "disclosure": round(disclosure, 2),
            "temp_offset": t["temp"],
            "brevity": t["brevity"],
            "personal": t["personal"],
        }

    # ========== 兼容快照 ==========
    def snapshot(self) -> Dict:
        return {
            "current": self.current,
            "intensity": round(self.intensity, 2),
            "valence": round(self.valence, 2),
        }
