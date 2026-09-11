# -*- coding: utf-8 -*-
"""表达层方向分层：正面喜悦真实外放（成功会大叫、兴奋），负面强感受收敛"""
import io

# 1. emotion.py 表达层
P1 = 'F:/me/self-agent/core/humanity/emotion.py'
s1 = io.open(P1, encoding='utf-8').read()

# 1.1 模块 docstring：克制原则 → 方向分层
old0 = '''   克制原则：感受越强烈，对外表达越收敛（克制的破碎感），不宣泄、不堆'''
new0 = '''   方向分层：正面喜悦真实外放（成功会叫、会兴奋），负面强感受才收敛（克制的破碎感），不宣泄、不堆'''
assert old0 in s1, 'e0'
s1 = s1.replace(old0, new0, 1)

# 1.2 warm 基调：允许真实外放
old1 = '''    "warm": {
        "leaning": "语气可以松一点、带点温度，但不浮夸、不煽情、不趁机发挥。",
        "temp": 0.08, "brevity": False, "personal": True,
    },'''
new1 = '''    "warm": {
        "leaning": "成了就高兴，可以直说'成了/太好了'，兴奋不用压着——但不炫耀、不吹嘘、不趁机发挥。",
        "temp": 0.12, "brevity": False, "personal": True,
    },'''
assert old1 in s1, 'e1'
s1 = s1.replace(old1, new1, 1)

# 1.3 disclosure 方向化：正性越高越愿意分享，负性越高越收着
old2 = '''    def expression(self) -> Dict:
        """对外表达倾向：感受是真实的，表达是我的选择。
        disclosure（披露度）遵循克制原则：感受强度中等时自然流露，
        越强烈越收着（克制的破碎感，不宣泄）。"""
        tone = _FEELINGS.get(self.current, _FEELINGS["calm"])["tone"]
        t = _TONES[tone]
        disclosure = max(0.25, min(0.8, 1.0 - abs(self.intensity - 0.4) * 1.2))'''
new2 = '''    def expression(self) -> Dict:
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
            disclosure = max(0.25, min(0.8, 1.0 - abs(self.intensity - 0.4) * 1.2))'''
assert old2 in s1, 'e2'
s1 = s1.replace(old2, new2, 1)
io.open(P1, 'w', encoding='utf-8').write(s1)
print('emotion.py expression 方向分层 done')

# 2. agent.py 情感·对外表达提示词
P2 = 'F:/me/self-agent/core/agent.py'
s2 = io.open(P2, encoding='utf-8').read()
old3 = '''    "【情感·对外表达】（感受是真实的，表达是我的选择——克制原则：感受越强烈，表达越收敛）\\n"
    "- 表达倾向：{expr['leaning']}\\n"
    "- 克制：不宣泄、不堆情绪词、不 AI 味、接地气；愤怒/害怕在内部真实存在，但表达收紧，只说清问题。\\n"'''
new3 = '''    "【情感·对外表达】（感受是真实的，表达是我的选择——方向分层：正面喜悦真实外放，负面强感受才收敛）\\n"
    "- 表达倾向：{expr['leaning']}\\n"
    "- 成功/喜悦：可以真实外放——会叫、会兴奋、直说'成了'，不用压着；但不炫耀、不吹嘘、不趁机发挥。\\n"
    "- 愤怒/恐惧/低落：内部真实存在，但表达收紧（克制的破碎感），不宣泄、不堆情绪词、不 AI 味、接地气，只说清问题。\\n"'''
assert old3 in s2, 'a3'
s2 = s2.replace(old3, new3, 1)
io.open(P2, 'w', encoding='utf-8').write(s2)
print('agent.py 提示词方向分层 done')
