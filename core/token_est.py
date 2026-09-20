"""core/token_est.py —— 轻量 token 估算器（WeKnora 设计借鉴，#287）

权威是模型 API 返回的 Usage 字段；本估算器只补两个场景：
  1. 增量估算：LLM 调用后、下次调用前，估算新增消息（assistant 回复+工具结果）
     的 token 成本，决定是否需要压缩——省一次 API 往返。
  2. 首轮回退：会话第一轮没有历史 Usage 时，提供全量估算。

编码用 cl100k_base 近似即可，不需要精确，偏差在下一次 API 调用时自动纠正。
中文会话对 Qwen/DeepSeek 按约 0.6 token/字符 缩放（cl100k 中文约 1:1）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# 常量（参考 WeKnora estimator.go）
PER_MESSAGE_OVERHEAD = 3      # 每条消息固定开销
PER_CONVERSATION_TAIL = 3     # 对话尾固定开销
PER_TOOL_CALL_OVERHEAD = 4    # 每个工具调用
PER_TOOL_DEF_OVERHEAD = 8     # 每个工具定义
ESTIMATED_IMAGE_TOKENS = 1200  # 单张图（按 tile 计费，URL/长度无关）

# 中文比例因子：cl100k 数中文约 1 token/字，Qwen/DeepSeek 约 0.6
ZH_CHAR_TO_TOKEN = 0.6
EN_CHAR_TO_TOKEN = 0.25       # 英文约 4 字符/token

# 压缩触发预算（素月上下文目标：约 24k tokens 以内）
COMPRESS_TOKEN_BUDGET = 24000
# 单条工具结果进入上下文的预算（runes 上限；配合公平裁剪使用）
DEFAULT_MAX_TOOL_OUTPUT = 3000

_RE_EN = re.compile(r"[A-Za-z0-9_]+")
_RE_ZH = re.compile(r"[\u4e00-\u9fff]")


def estimate_text_tokens(text: str) -> int:
    """估算单段文本的 token 数（中英混合）。"""
    if not text:
        return 0
    s = str(text)
    zh = len(_RE_ZH.findall(s))
    en_chars = sum(len(w) for w in _RE_EN.findall(s))
    other = max(0, len(s) - zh - en_chars)
    return int(zh * ZH_CHAR_TO_TOKEN + en_chars * EN_CHAR_TO_TOKEN + other * 0.5)


def estimate_messages_tokens(messages: List[Dict]) -> int:
    """估算消息列表总 token（每条消息加固定开销 + 对话尾）。"""
    total = PER_CONVERSATION_TAIL
    for m in messages:
        total += PER_MESSAGE_OVERHEAD
        total += estimate_text_tokens(m.get("content") or "")
        tc = m.get("tool_calls") or []
        if tc:
            total += PER_TOOL_CALL_OVERHEAD * len(tc)
            for t in tc:
                total += estimate_text_tokens(t.get("arguments") or "")
    return total


def estimate_tool_defs_tokens(tools: List[Dict]) -> int:
    """估算工具定义总 token（仅常驻清单；动态加载的按需另计）。"""
    total = 0
    for t in tools or []:
        total += PER_TOOL_DEF_OVERHEAD
        fn = t.get("function", t) if isinstance(t, dict) else {}
        total += estimate_text_tokens(fn.get("description") or "")
        params = ((fn.get("parameters") or {}).get("properties") or {}) if isinstance(fn, dict) else {}
        for pname, pdef in params.items():
            if isinstance(pdef, dict):
                total += estimate_text_tokens(pname + " " + str(pdef.get("description") or ""))
    return total


def split_budget_fairly(total: int, sizes: List[int]) -> List[int]:
    """max-min fair（水填充）分配：小于均摊份额的记录保留完整大小，余量捐给更大的记录。

    用于批量工具结果的公平裁剪：退化时裁剪最大的几条，而不是砍头砍尾
    导致中间记录整条消失、幸存记录被切半。返回每条的允许上限（≤原大小），
    总和不超过 total。
    """
    n = len(sizes)
    caps = [0] * n
    if n == 0 or total <= 0:
        return caps
    settled = [False] * n
    remaining, unsettled = total, n
    while unsettled > 0:
        share = remaining // unsettled
        if share <= 0:
            break
        progressed = False
        for i, size in enumerate(sizes):
            if settled[i] or size > share:
                continue
            caps[i] = size
            settled[i] = True
            remaining -= size
            unsettled -= 1
            progressed = True
        if not progressed:
            # 所有未分配的都超过均摊份额：每人分 share，最后把余量给第一个
            for i in range(n):
                if not settled[i]:
                    caps[i] = share
            if remaining > 0:
                for i in range(n):
                    if not settled[i]:
                        caps[i] += remaining
                        break
            break
    return caps


def fair_trim_json(blob: str, max_chars: int = DEFAULT_MAX_TOOL_OUTPUT) -> str:
    """对工具结果 JSON 做公平裁剪。

    顶层是 list → 按条目 water-fill，优先保留完整条目（只削最大的）；
    顶层是 dict 且含 items/results/list/data 列表字段 → 对该字段裁剪；
    其他 → 整体头尾截断（保留开头，注明原长度）。
    返回裁剪后的 JSON 字符串。
    """
    if blob is None:
        return ""
    s = str(blob)
    if len(s) <= max_chars:
        return s
    import json as _json
    try:
        obj = _json.loads(s)
    except Exception:  # noqa: BLE001  非 JSON（如纯文本输出）→ 头尾截断
        return s[:max_chars] + f"...[已截断，原{len(s)}字符]"

    def _trim_list(lst: list) -> list:
        sizes = [len(_json.dumps(x, ensure_ascii=False)) for x in lst]
        caps = split_budget_fairly(max_chars, sizes)
        out = []
        for x, cap in zip(lst, caps):
            item = _json.dumps(x, ensure_ascii=False)
            if len(item) > cap:
                # 单个条目仍超预算：头部保留 cap，末尾标注截断
                item = item[:cap] + "..."
            out.append(item)
        return out

    if isinstance(obj, list):
        trimmed = _trim_list(obj)
        return "[" + ",".join(trimmed) + "]"
    if isinstance(obj, dict):
        for key in ("items", "results", "list", "data", "records", "matches"):
            val = obj.get(key)
            if isinstance(val, list) and val:
                # 该字段占大头时才裁剪它；否则整体截断
                field_size = len(_json.dumps(val, ensure_ascii=False))
                if field_size > max_chars * 0.5:
                    obj[key] = _trim_list(val)
                    out = _json.dumps(obj, ensure_ascii=False)
                    if len(out) > max_chars:
                        return s[:max_chars] + f"...[已截断，原{len(s)}字符]"
                    return out
                break
    return s[:max_chars] + f"...[已截断，原{len(s)}字符]"


if __name__ == "__main__":
    # 冒烟：混合文本估算 + 公平裁剪
    t = "你好" * 100 + " hello world " * 50
    print("estimate:", estimate_text_tokens(t))
    lst = [{"a": "x" * 100}, {"a": "y" * 2000}, {"a": "z" * 3000}, {"a": "w" * 50}]
    out = fair_trim_json(__import__("json").dumps(lst, ensure_ascii=False), 3000)
    print("fair_trim len:", len(out))
    print("ok")
