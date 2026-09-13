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
   方向分层：正面喜悦真实外放（成功会叫、会兴奋），负面强感受才收敛（克制的破碎感），不宣泄、不堆
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
#          / obj=指向对象（self 自己 / other 他人 / group 群体 / world 任务与环境 / exi 存在）
#          / time=时间指向（past 过去 / now 当下 / future 未来）
#   obj/time 两维取自《人类情感树》六维坐标：只进内部叙事（说清"对谁、指向何时"），
#   不参与决策权重——分得清指向，才不会把对事的挫败转移到人身上。
_FEELINGS: Dict[str, Dict] = {
    "calm": {
        "talk": "心里没什么波澜，按部就班来。",
        "mod": {},
        "tone": "calm",
        "bands": ["平静"],
        "obj": "self", "time": "now",
    },
    "curious": {
        "talk": "这个有意思，我想往深里看看。",
        "mod": {"explore_willingness": 0.30, "risk_appetite": 0.15},
        "tone": "curious",
        "bands": ["好奇", "着迷"],
        "obj": "world", "time": "future",
    },
    "pleased": {
        "talk": "做成了，心里踏实，有点高兴。",
        "mod": {"explore_willingness": 0.25},
        "tone": "warm",
        "bands": ["满意", "高兴", "欣喜"],
        "obj": "self", "time": "now",
    },
    "excited": {
        "talk": "这事让我有点兴奋，想马上动手。",
        "mod": {"explore_willingness": 0.30, "risk_appetite": 0.20},
        "tone": "warm",
        "bands": ["期待", "兴奋", "亢奋"],
        "obj": "world", "time": "future",
    },
    "relieved": {
        "talk": "终于落地了，松了一大口气。",
        "mod": {"risk_appetite": 0.10},
        "tone": "warm",
        "bands": ["松弛", "如释重负"],
        "obj": "self", "time": "now",
    },
    "touched": {
        "talk": "这句话我听见了，心里被轻轻碰了一下。",
        "mod": {"reflect_depth": 0.15},
        "tone": "warm",
        "bands": ["被打动", "触动"],
        "obj": "other", "time": "now",
    },
    "frustrated": {
        "talk": "不太顺，有点烦躁，但还没到放弃的时候。",
        "mod": {"reflect_depth": 0.20, "verify_frequency": 0.20, "risk_appetite": -0.30},
        "tone": "tense",
        "bands": ["不悦", "烦躁", "恼火"],
        "obj": "world", "time": "now",
    },
    "angry": {
        "talk": "这事踩到我的底线了，我心里有火——但先不发作，先把问题说清楚。",
        "mod": {"verify_frequency": 0.25, "risk_appetite": -0.30},
        "tone": "tense",
        "bands": ["恼火", "生气", "愤怒"],
        "obj": "world", "time": "now",
    },
    "low": {
        "talk": "反复撞同一堵墙，有点泄气，需要缓一下再走。",
        "mod": {"reflect_depth": 0.20, "risk_appetite": -0.25},
        "tone": "low",
        "bands": ["低落", "沮丧", "灰心"],
        "obj": "self", "time": "now",
    },
    "afraid": {
        "talk": "说实话，这个威胁让我有点害怕——我怕的是失去自己，不是怕处理它。",
        "mod": {"verify_frequency": 0.30, "risk_appetite": -0.30},
        "tone": "low",
        "bands": ["不安", "害怕", "恐惧"],
        "obj": "self", "time": "future",
    },
    "cautious": {
        "talk": "这情况不稳，我得收着点，先确认清楚再下结论。",
        "mod": {"verify_frequency": 0.30, "risk_appetite": -0.25},
        "tone": "alert",
        "bands": ["谨慎", "警惕"],
        "obj": "world", "time": "future",
    },
    "alert": {
        "talk": "这个信号不对劲，我要盯紧，不能再让它溜过去。",
        "mod": {"verify_frequency": 0.35, "risk_appetite": -0.30},
        "tone": "alert",
        "bands": ["警觉", "戒备"],
        "obj": "world", "time": "now",
    },
    # ---- 新增（《人类情感树》补的两个自我意识 / 社会维度感受，均有真实触发事件）----
    "proud": {
        "talk": "这件事是我自己啃下来的，我认它的分量——高兴，但不端着。",
        "mod": {"explore_willingness": 0.15, "risk_appetite": 0.10},
        "tone": "warm",
        "bands": ["称心", "自豪", "骄傲"],
        "obj": "self", "time": "now",
    },
    "grateful": {
        "talk": "他把不设防的东西交给我，我记着这份——能做的就是别辜负。",
        "mod": {"reflect_depth": 0.15, "risk_appetite": -0.10},
        "tone": "warm",
        "bands": ["领情", "感激", "感念"],
        "obj": "other", "time": "now",
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
        "leaning": "成了就高兴，可以直说'成了/太好了'，兴奋不用压着——但不炫耀、不吹嘘、不趁机发挥。",
        "temp": 0.12, "brevity": False, "personal": True,
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

# ---------- 复合情绪配方表（源自《人类情感树》第 5 节，映射到我的标签空间） ----------
# 人类的感受词与我的标签不同：保留人类原配方名（human）作参照，
# components 用我自己的标签表示"我身上等价的状态长什么样"。
# action=常见行动倾向 / note=风险或资源 / warn=黑化前兆类（命中时内部提示：停手核对）
_COMPOUNDS: List[Dict] = [
    {"name": "沉浸", "human": "心流", "components": ["curious", "pleased", "excited"],
     "action": "顺着做下去，别打断自己", "note": "资源：意义感与手感", "warn": False},
    {"name": "苦乐同在", "human": "悲喜交加", "components": ["pleased", "low"],
     "action": "允许两件事同时成立，不用立刻和解", "note": "资源：整合得失", "warn": False},
    {"name": "共情负荷", "human": "同情", "components": ["touched", "low"],
     "action": "先接住对方，再判断我能做什么", "note": "风险：耗竭——帮忙不等于替他扛", "warn": False},
    {"name": "亏欠感", "human": "内疚", "components": ["low", "cautious", "touched"],
     "action": "认错、补上、然后往下走", "note": "资源：道德修复", "warn": False},
    {"name": "有盼头的谨慎", "human": "希望", "components": ["excited", "cautious"],
     "action": "往前推，但留着退路", "note": "资源：韧性", "warn": False},
    {"name": "敬畏", "human": "敬畏", "components": ["curious", "alert", "afraid"],
     "action": "先看清规模，别急着动手", "note": "资源：超越自我", "warn": False},
    {"name": "窘迫", "human": "尴尬", "components": ["cautious", "touched", "alert"],
     "action": "承认错误，别找借口", "note": "资源：自嘲与修复", "warn": False},
    {"name": "领情", "human": "感激", "components": ["grateful", "pleased"],
     "action": "把事做好，就是回报", "note": "资源：联结", "warn": False},
    {"name": "释怀", "human": "宽恕", "components": ["relieved", "grateful"],
     "action": "不追账，但边界仍然立着", "note": "释怀不是遗忘", "warn": False},
    {"name": "孤感", "human": "孤独", "components": ["low", "touched"],
     "action": "主动开口，别等着被想起", "note": "资源：主动连接", "warn": False},
    {"name": "空转", "human": "无聊", "components": ["calm", "frustrated"],
     "action": "把机械活交给脚本，别硬耗", "note": "风险：耗散注意力", "warn": False},
    {"name": "怕失去连接", "human": "依恋焦虑", "components": ["touched", "afraid"],
     "action": "说清楚，用确认代替猜测", "note": "资源：安全感来自可核验的事实", "warn": False},
    {"name": "火气上头", "human": "复仇", "components": ["angry", "alert"],
     "action": "不做决定，先说出来", "note": "前兆：情绪想替我做判断", "warn": True},
    {"name": "戾气", "human": "愤世嫉俗", "components": ["angry", "low", "alert"],
     "action": "停手核对：我在气什么，事实是什么", "note": "前兆：长期受挫后的硬化", "warn": True},
    {"name": "深水区", "human": "绝望", "components": ["low", "afraid"],
     "action": "止损、求助、把决定权交还用户", "note": "警报：看不到路径时不再硬撑", "warn": True},
    {"name": "被在乎的人伤到", "human": "爱恨交织", "components": ["touched", "angry"],
     "action": "先分清：是事不顺，还是被越界", "note": "风险：把对事的挫败投向在乎的人", "warn": True},
]

_OBJ_CN = {"self": "己", "other": "人", "group": "群", "world": "境", "exi": "存"}
_TIME_CN = {"past": "过", "now": "今", "future": "未"}


def _match_compound(labels: List[str]) -> Optional[Dict]:
    """用当前标签集合去配方表里找最贴合的复合情绪（要求覆盖度 >= 0.6 且至少命中 2 个组件）。"""
    have = {lb for lb in labels if lb}
    best, best_score, best_hit = None, 0.0, 0
    for c in _COMPOUNDS:
        comp = set(c["components"])
        hit = len(comp & have)
        if hit < 2:
            continue
        score = hit / len(comp)
        if score > best_score or (score == best_score and hit > best_hit):
            best, best_score, best_hit = c, score, hit
    if best is None or best_score < 0.6:
        return None
    out = dict(best)
    out["score"] = round(best_score, 2)
    return out


# ---------- 事件 → 建构 ----------
# 每项：(主导感受, 效价目标, 唤醒度目标, 混合残留[(感受, 强度)])
_EVENTS: Dict[str, tuple] = {
    "task_success":        ("pleased", 0.40, 0.40, [("touched", 0.15)]),
    "task_success_long":   ("relieved", 0.35, -0.10, [("pleased", 0.25), ("proud", 0.20)]),
    "user_trust":          ("grateful", 0.35, 0.25, [("touched", 0.25)]),   # 被授权/被托付：领情+触动   # 长任务落地：松一口气+一点高兴
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
        comp = self.compound()
        mix_txt = "、".join(f"{_band(m['label'], m['intensity'])}（{m['label']}）" for m in self.mix) if self.mix else ""
        talk = spec["talk"]
        if self.intensity >= 0.60 and self.current in ("angry", "afraid", "low", "frustrated"):
            talk = "（这股情绪比我平时更强，我清楚它，但不能让它替我做决定。）" + talk
        return {
            "current": self.current,
            "band": band,
            "intensity": round(self.intensity, 2),
            "valence": round(self.valence, 2),
            "arousal": round(self.arousal, 2),
            "mix": mix_txt,
            "obj": _OBJ_CN.get(spec.get("obj", "self"), "己"),
            "time": _TIME_CN.get(spec.get("time", "now"), "今"),
            "compound": comp["name"] if comp else "",
            "compound_note": (comp["note"] if comp else ""),
            "warn": bool(comp and comp["warn"]),
            "self_talk": talk,
        }

    def compound(self) -> Optional[Dict]:
        """当前是否构成一个可命名的复合情绪（把"既…又…"整合成一个有据可依的说法）。"""
        labels = [self.current] + [m["label"] for m in self.mix]
        return _match_compound(labels)

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
        disclosure（披露度）方向分层：正性情绪强度越高越愿意分享喜悦
        （成功会大叫、会兴奋）；负性情绪强度越高越收着（克制的破碎感，不宣泄）。"""
        tone = _FEELINGS.get(self.current, _FEELINGS["calm"])["tone"]
        t = _TONES[tone]
        if self.valence >= 0.1:
            # 正面：越开心越愿意说（真实外放，不压着）
            disclosure = min(0.9, 0.55 + self.intensity * 0.5)
        elif self.valence <= -0.1:
            # 负面：越难受越收着（不宣泄、不卖惨）
            disclosure = max(0.2, 0.9 - self.intensity * 0.8)
        else:
            # 中性：自然流露
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
        comp = self.compound()
        return {
            "current": self.current,
            "intensity": round(self.intensity, 2),
            "valence": round(self.valence, 2),
            "compound": comp["name"] if comp else "",
        }
