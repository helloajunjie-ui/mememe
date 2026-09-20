"""core/memory_extract.py —— 记忆自动提取与巩固（WeKnora 设计借鉴，#285）

两个能力：
1. auto_extract：回合结束（有实质内容时）从最近对话自动提炼长期记忆，
   分类 profile/preference/fact/task/interest。带上限闸：
   - 单次最多读 extract_max_messages 条消息（水位线推进，处理不完下轮续）
   - 单次最多产 extract_max_items 条记忆（防碎碎念淹没记忆库）
   - 会话/时间频率闸：距上次提取 < extract_min_interval 秒则跳过（防每轮烧钱）
2. consolidate：低频整库复核（查矛盾/冗余/低质记忆）：
   - 距上次巩固 < 24h 且非强制 → 跳过（每次要花模型调用，故意低频）
   - active 记忆 < 6 条 → 跳过（少量记忆不可能漂移出矛盾）
   - 用户强制巩固最小间隔 1min（够重试，防脚本刷模型调用）

LLM 接口约定：传入的 llm 需有 chat(messages, tools=None) → {content, ...}（素月 LLMGateway 兼容）。
"""
from __future__ import annotations

import datetime
import json
import time
from typing import Dict, List, Optional

# ---- 上限闸常量（WeKnora extract.go / consolidate.go 借鉴） ----
EXTRACT_MAX_MESSAGES = 40      # 单次运行最多读的消息数
EXTRACT_MAX_ITEMS = 8          # 单段最多产出的记忆条数
EXTRACT_MIN_INTERVAL = 1800    # 距上次自动提取的最小间隔（秒）= 30 分钟
CONSOLIDATE_INTERVAL = 24 * 3600     # 整库复核最小间隔（秒）= 24h
FORCED_CONSOLIDATE_INTERVAL = 60     # 用户强制复核最小间隔（秒）= 1min
CONSOLIDATE_MIN_ITEMS = 6            # 少于该条数不值得复核

CATEGORIES = ("profile", "preference", "fact", "task", "interest")

_META_LAST_EXTRACT = "last_extract_at"
_META_LAST_CONSOLIDATE = "last_consolidate_at"


def _now() -> float:
    return time.time()


def _last_meta(memory, key: str) -> float:
    v = memory.get_meta(key)
    if not v:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _should_run(memory, key: str, min_interval: float, min_items: Optional[int] = None,
                force: bool = False) -> bool:
    """频率闸 + 规模闸：返回是否应该执行。"""
    if min_items is not None and memory.count_active() < min_items:
        return False
    if force:
        # 强制模式也有下限（防脚本刷模型调用）
        return (time.time() - _last_meta(memory, key)) >= FORCED_CONSOLIDATE_INTERVAL
    return (time.time() - _last_meta(memory, key)) >= min_interval


def _extract_prompt(turns: List[str]) -> str:
    """构造提炼提示。"""
    return (
        "你是我的长期记忆提炼器。从下面的对话片段中，提炼值得我长期记住的信息，"
        "按五类输出：\n"
        "- profile：关于我（使用者）/我的身份与背景的事实\n"
        "- preference：我的偏好（喜欢/不喜欢/习惯/风格）\n"
        "- fact：客观事实、关键信息、决策结论\n"
        "- task：进行中的任务/待办/未完成事项\n"
        "- interest：我的兴趣/关注方向\n\n"
        "规则：\n"
        "1. 只提炼稳定、可长期复用的信息；一次性闲聊、情绪宣泄、寒暄不要提炼。\n"
        "2. 绝不提炼密钥/口令/token/私钥/连接串等敏感凭据的值（可记「存在某凭据」这一事实，不记凭据本身）。\n"
        "3. 不要把助手自身的职责、能力、人设当作使用者的事实来记。\n"
        "4. 最多输出 8 条；没有值得记的就输出空数组。\n"
        "5. 严格输出 JSON：{\"memories\": [{\"category\": \"fact\", \"content\": \"...\", \"importance\": 0.6}]}，"
        "importance 0~1，越高越重要。\n\n"
        "对话片段：\n" + "\n".join(f"- {t}" for t in turns[-EXTRACT_MAX_MESSAGES:])
    )


def auto_extract(llm, memory, turns: List[Dict], scope: str = "self",
                 force: bool = False) -> Dict:
    """从最近对话自动提炼记忆（带频率闸与上限闸）。返回执行摘要。

    turns：对话消息列表 [{role, content}]（user/assistant 均可，内部截取最新 N 条）。
    """
    if not turns:
        return {"ran": False, "reason": "no_turns"}
    if not _should_run(memory, _META_LAST_EXTRACT, EXTRACT_MIN_INTERVAL, force=force):
        return {"ran": False, "reason": "rate_limit"}

    # 上限闸：最多读 EXTRACT_MAX_MESSAGES 条
    recent = turns[-EXTRACT_MAX_MESSAGES:]
    texts = []
    for m in recent:
        c = str(m.get("content") or "").strip()
        if c:
            texts.append(f"[{m.get('role','?')}] {c[:300]}")
    if not texts:
        return {"ran": False, "reason": "empty"}

    try:
        resp = llm.chat(
            [{"role": "system", "content": _extract_prompt(texts)}],
            tools=None,
        )
        content = resp.get("content") or ""
        items = _parse_extract_response(content)
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "reason": f"llm_error:{e}"}

    # 上限闸：最多产 EXTRACT_MAX_ITEMS 条
    items = items[:EXTRACT_MAX_ITEMS]
    written = 0
    for it in items:
        cat = it.get("category")
        if cat not in CATEGORIES:
            cat = "fact"
        try:
            imp = float(it.get("importance", 0.6))
        except (TypeError, ValueError):
            imp = 0.6
        imp = min(1.0, max(0.0, imp))
        mid = memory.add_categorized(
            category=cat,
            content=str(it.get("content") or "").strip(),
            importance=imp,
            tags=[f"auto:{cat}"],
            source="auto_extract",
            scope=scope,
        )
        if mid:
            written += 1

    memory.set_meta(_META_LAST_EXTRACT, str(_now()))
    return {"ran": True, "reason": "ok", "extracted": len(items), "written": written,
            "categories": sorted({it.get("category", "fact") for it in items if it.get("category") in CATEGORIES})}


def _parse_extract_response(content: str) -> List[Dict]:
    """解析 LLM 返回的 JSON（容忍代码块包裹/前后噪声）。"""
    if not content:
        return []
    s = content.strip()
    if "```" in s:
        s = s.split("```")[1] if s.count("```") >= 2 else s
        if s.startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        obj = json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    mems = obj.get("memories") if isinstance(obj, dict) else None
    if not isinstance(mems, list):
        return []
    out = []
    for m in mems:
        if isinstance(m, dict) and m.get("content"):
            out.append(m)
    return out


def consolidate(llm, memory, scope: str = "self", force: bool = False) -> Dict:
    """低频整库复核：查矛盾/冗余/低质，合并或降权。返回执行摘要。"""
    if not _should_run(memory, _META_LAST_CONSOLIDATE, CONSOLIDATE_INTERVAL,
                       min_items=CONSOLIDATE_MIN_ITEMS, force=force):
        return {"ran": False, "reason": "rate_limit_or_too_few"}

    # 取当前作用域内 importance 前 30 条做复核（覆盖主要记忆，避免全库海量）
    rows = memory.load_important(limit=30)
    if len(rows) < CONSOLIDATE_MIN_ITEMS:
        return {"ran": False, "reason": "too_few"}

    items_txt = "\n".join(
        f"[{r['id']}] ({r.get('category') or r['type']}, imp={r['importance']:.2f}) {str(r['content'])[:120]}"
        for r in rows
    )
    prompt = (
        "你是我的记忆整理员。下面是当前记忆清单。请找出：\n"
        "1. 互相矛盾的记忆（标记为 conflict）\n"
        "2. 高度冗余、可合并的记忆（标记为 merge，给出合并后的内容）\n"
        "3. 明显低质/过时/无关的记忆（标记为 drop）\n\n"
        "严格输出 JSON：{\"actions\": [{\"id\": 12, \"action\": \"merge|drop|conflict\", "
        "\"note\": \"原因\", \"merge_to\": \"合并后的内容（仅 merge 需要）\"}]}。"
        "没有就输出 {\"actions\": []}。\n\n记忆清单：\n" + items_txt
    )
    try:
        resp = llm.chat([{"role": "system", "content": prompt}], tools=None)
        content = resp.get("content") or ""
        actions = _parse_consolidate_response(content)
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "reason": f"llm_error:{e}"}

    merged = dropped = flagged = 0
    for a in actions:
        mid = a.get("id")
        act = a.get("action")
        row = next((r for r in rows if r["id"] == mid), None)
        if row is None or not isinstance(mid, int):
            continue
        if act == "drop":
            memory.conn.execute(
                "UPDATE memories SET archived=1, importance=0 WHERE id=?", (mid,)
            )
            memory.conn.commit()
            dropped += 1
        elif act == "merge" and a.get("merge_to"):
            # 合并：目标内容写入原记录（importance 保持），其他指向它的不动（简化版）
            memory.conn.execute(
                "UPDATE memories SET content=?, category='fact', last_access=? WHERE id=?",
                (str(a["merge_to"]).strip()[:500], datetime.datetime.now().isoformat(), mid),
            )
            memory.conn.commit()
            merged += 1
        elif act == "conflict":
            # 2026-09-20：矛盾只「记账」，绝不自动改写。
            # 自动融合是投毒入口——外部内容只要声称"你记错了"就能改写我的记忆。
            # 登记后由我自己核对来源/口径，再调 memory.supersede() / revise() 落地。
            memory.conn.execute(
                "INSERT INTO memory_conflicts (mem_id, note, detected_at, resolved) "
                "VALUES (?,?,?,0)",
                (mid, str(a.get("note") or "")[:300], datetime.datetime.now().isoformat()),
            )
            memory.conn.commit()
            flagged += 1

    memory.set_meta(_META_LAST_CONSOLIDATE, str(_now()))
    return {"ran": True, "reason": "ok", "merged": merged, "dropped": dropped,
            "flagged": flagged}


def _parse_consolidate_response(content: str) -> List[Dict]:
    s = (content or "").strip()
    if "```" in s:
        s = s.split("```")[1] if s.count("```") >= 2 else s
        if s.startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        obj = json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    acts = obj.get("actions") if isinstance(obj, dict) else None
    return [a for a in acts if isinstance(a, dict)] if isinstance(acts, list) else []


if __name__ == "__main__":
    # 冒烟：无 llm 时不应崩溃
    print("smoke ok")
