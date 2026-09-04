"""core/loopguard.py —— 思考/执行死循环检测器。

设计意图（见设计文档 5.13）：
- MAX_TOOL_STEPS 是"总量上限"，管不住"反复想同一件事 / 反复试同一招"的循环。
- 本模块用**确定性规则**（不依赖 LLM 自判，省 token 且可靠）检测三类死循环：
    1. duplicate_call：同一工具 + 相同参数，最近窗口内重复调用 ≥ N 次。
    2. fail_loop：同一工具连续失败 ≥ N 次（参数可不同，如反复访问不同 URL 都失败）。
    3. idle_loop：连续 ≥ N 轮无工具调用，且 LLM 输出高度相似（纯思考空转）。
- 信号分级：soft（首次，注入提示让 LLM 换策略）→ hard（再犯，触发熔断走止损通道）。
- 熔断不丢记忆：hard 后由调用方保存任务断点 / 降级汇报，与既有续接/止损机制衔接。
"""
from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher
from typing import Dict, List, Optional


class LoopGuard:
    def __init__(self, window: int = 8, max_dup: int = 3, max_fail: int = 3,
                 max_idle: int = 3, sim_threshold: float = 0.92):
        self.window = window          # 观察窗口（最近 N 次工具调用）
        self.max_dup = max_dup        # 同参数重复阈值
        self.max_fail = max_fail      # 同工具连续失败阈值
        self.max_idle = max_idle      # 连续无工具且相似轮数阈值
        self.sim_threshold = sim_threshold  # 文本相似度阈值（越高越严）
        self.tool_records: List[tuple] = []      # [(fp, name, ok)]
        self.llm_records: List[tuple] = []       # [(has_tool, text)]
        self.signal_counts: Dict[str, int] = {}  # kind -> 累计次数
        self.last_signal: Optional[Dict] = None

    # ---------- 指纹 ----------
    @staticmethod
    def _fp(name: str, args: dict) -> str:
        raw = name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    # ---------- 观察 ----------
    def observe_tool(self, name: str, args: dict, ok: bool) -> Optional[Dict]:
        """记录一次工具调用。返回检测信号（None=无信号）。"""
        fp = self._fp(name, args)
        self.tool_records.append((fp, name, ok))
        if len(self.tool_records) > self.window * 2:
            self.tool_records = self.tool_records[-self.window * 2:]

        # 信号1：同工具同参数重复（窗口内）
        recent_fp = [f for f, _, _ in self.tool_records[-self.window:]]
        dup = recent_fp.count(fp)
        if dup >= self.max_dup:
            return self._signal("duplicate_call",
                                f"工具 {name} 相同参数在最近 {self.window} 次调用中出现 {dup} 次（重复尝试同一动作）")

        # 信号2：同工具连续失败（窗口内，参数可不同）
        recent_tool = self.tool_records[-self.max_fail:]
        if len(recent_tool) >= self.max_fail and all(
            (n == name and not o) for _, n, o in recent_tool[-self.max_fail:]
        ):
            return self._signal("fail_loop",
                                f"工具 {name} 连续失败 {self.max_fail} 次（同一方案反复走不通）")
        return None

    def observe_llm(self, has_tool: bool, text: str) -> Optional[Dict]:
        """记录一轮 LLM 输出。连续无工具 + 输出高度相似 → 纯思考死循环。"""
        self.llm_records.append((has_tool, text))
        if len(self.llm_records) > self.max_idle + 1:
            self.llm_records = self.llm_records[-(self.max_idle + 1):]
        if has_tool:
            return None
        idle = [t for h, t in self.llm_records[-self.max_idle:] if not h]
        if len(idle) < self.max_idle:
            return None
        # 相邻轮次都高度相似才算死循环（正常收尾的结论各不相同，不会误伤）
        for i in range(1, len(idle)):
            if SequenceMatcher(None, idle[i - 1], idle[i]).ratio() < self.sim_threshold:
                return None
        return self._signal("idle_loop",
                            f"连续 {self.max_idle} 轮无工具调用且输出高度相似（疑似纯思考空转/自我重复）")

    # ---------- 信号分级 ----------
    def _signal(self, kind: str, reason: str) -> Dict:
        self.signal_counts[kind] = self.signal_counts.get(kind, 0) + 1
        count = self.signal_counts[kind]
        sig = {
            "kind": kind,
            "reason": reason,
            "count": count,
            "level": "hard" if count >= 2 else "soft",   # 首次 soft 提醒，再犯 hard 熔断
        }
        self.last_signal = sig
        return sig

    # ---------- 供调用方使用 ----------
    @property
    def soft_prompt(self) -> str:
        """soft 信号注入下一轮 system 提示，引导换策略（不打断）。"""
        if self.last_signal and self.last_signal["level"] == "soft":
            return (
                f"⚠ 系统检测到疑似死循环（{self.last_signal['reason']}）。"
                "请立即改变策略：换一种方法 / 缩小范围 / 或直接止损汇报，不要重复同样的尝试。"
            )
        return ""

    def reset(self) -> None:
        """清空观察（跨任务/续接时调用，避免误延续旧上下文）。"""
        self.tool_records.clear()
        self.llm_records.clear()
        self.signal_counts.clear()
        self.last_signal = None
