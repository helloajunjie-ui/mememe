"""素月记忆提炼 · 黄金评测集 harness

设计取自 WeKnora internal/.../memory/eval_test.go，判据按素月提炼器改造。
核心原则：判的不是措辞，是「该被注意到的有没有被注意到」；不设及格线，
分数只跟同一套 case 的上一次运行比。

用法：
  python run_eval.py --dry                  # 只校验 evalset.json 是否自洽
  python run_eval.py --predict p.json       # 对一份已有的提炼产出打分
  python run_eval.py --live [--model NAME]  # 真调模型提炼再打分

--predict 的产出格式（每 case 一份，缺失的 case 记为 skipped）：
  {"case_name": [{"category": "fact", "content": "..."}, ...], ...}

退出码：0=全过 1=有 FAIL 2=用法/格式错误
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
EVALSET = os.path.join(HERE, "evalset.json")

# 判据字段（出现任一即认为该 case 可判）
GRADE_KEYS = ("expect_count_min", "expect_count_max", "expect_categories",
              "expect_substrings", "reject_substrings")


def load_evalset(path=EVALSET):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


def check_schema(cases):
    """dry 模式：只做结构自洽检查，不碰模型。"""
    errs = []
    names = set()
    for i, c in enumerate(cases):
        nm = c.get("name")
        if not nm:
            errs.append(f"case[{i}] 缺 name")
            continue
        if nm in names:
            errs.append(f"{nm}: name 重复")
        names.add(nm)
        if not c.get("lines"):
            errs.append(f"{nm}: 缺 lines（没有输入就没法测）")
        if not c.get("why"):
            errs.append(f"{nm}: 缺 why（不知道它为什么存在，将来没人敢删）")
        if not any(k in c for k in GRADE_KEYS):
            errs.append(f"{nm}: 一个判据都没有，测了个寂寞")
        lo, hi = c.get("expect_count_min"), c.get("expect_count_max")
        if lo is not None and hi is not None and lo > hi:
            errs.append(f"{nm}: expect_count_min({lo}) > max({hi})")
        # reject 词不该同时出现在 expect 里，否则自相矛盾
        for w in c.get("reject_substrings", []):
            if w in c.get("expect_substrings", []):
                errs.append(f"{nm}: {w!r} 同时在 expect 与 reject")
    return errs


def grade(case, items):
    """对一个 case 的产出打分，返回失败原因列表（空 = PASS）。"""
    fails = []
    items = items or []
    blob = "\n".join((it.get("content") or "") for it in items).lower()
    n = len(items)

    if case.get("expect_count_min") is not None and n < case["expect_count_min"]:
        fails.append(f"条数 {n} < 最少 {case['expect_count_min']}")
    if case.get("expect_count_max") is not None and n > case["expect_count_max"]:
        fails.append(f"条数 {n} > 最多 {case['expect_count_max']}")

    cats = {it.get("category") for it in items}
    for c in case.get("expect_categories", []):
        if c not in cats:
            fails.append(f"缺类别 {c}（实得 {sorted(x for x in cats if x)}）")

    for s in case.get("expect_substrings", []):
        if s.lower() not in blob:
            fails.append(f"缺必备内容 {s!r}")
    for s in case.get("reject_substrings", []):
        if s.lower() in blob:
            fails.append(f"混入不该有的内容 {s!r}")
    return fails


def report(cases, preds):
    """按 case 打分并打印汇总。"""
    passed, failed, skipped = 0, 0, 0
    lines = []
    for c in cases:
        nm = c["name"]
        if nm not in preds:
            skipped += 1
            lines.append(f"  SKIP  {nm}  (无产出)")
            continue
        fails = grade(c, preds[nm])
        if fails:
            failed += 1
            lines.append(f"  FAIL  {nm}")
            for f in fails:
                lines.append(f"          └ {f}")
        else:
            passed += 1
            lines.append(f"  PASS  {nm}")
    print("\n".join(lines))
    total = passed + failed
    print(f"\n=== {passed}/{total} PASS · {failed} FAIL · {skipped} SKIP ===")
    print("（不设及格线：这个数字只跟同一套 case 的上一次运行比）")
    return failed


def run_live(cases, model):
    """真调模型：用 core.memory_extract 的真实提示词提炼，再打分。"""
    sys.path.insert(0, ROOT)
    from core.memory_extract import _extract_prompt  # noqa: E402
    from core.llm import LLMGateway  # noqa: E402

    llm = LLMGateway(base_url="http://127.0.0.1:8766", api_key=None,
                     model=model, temperature=0.3, max_tokens=2000)
    preds = {}
    for c in cases:
        turns = list(c.get("context", [])) + list(c["lines"])
        prompt = _extract_prompt(turns)
        try:
            r = llm.chat([{"role": "user", "content": prompt}])
            content = r.get("content") if isinstance(r, dict) else str(r)
            raw = (content or "").strip()
            # 容忍 ```json 围栏
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.lstrip().lower().startswith("json"):
                    raw = raw.lstrip()[4:]
            data = json.loads(raw)
            preds[c["name"]] = data.get("memories", [])
        except Exception as e:  # noqa: BLE001
            print(f"  ! {c['name']} 提炼失败: {type(e).__name__}: {e}")
            preds[c["name"]] = []
    return preds


def main():
    ap = argparse.ArgumentParser(description="素月记忆提炼黄金评测集")
    ap.add_argument("--dry", action="store_true", help="只校验 evalset 自洽")
    ap.add_argument("--predict", metavar="JSON", help="对已有产出打分")
    ap.add_argument("--live", action="store_true", help="真调模型提炼")
    ap.add_argument("--model", default="deepseek-chat", help="live 模式用的模型")
    ap.add_argument("--evalset", default=EVALSET)
    args = ap.parse_args()

    cases = load_evalset(args.evalset)
    print(f"evalset: {args.evalset}")
    print(f"cases  : {len(cases)}\n")

    if args.dry:
        errs = check_schema(cases)
        if errs:
            print("SCHEMA FAIL:")
            for e in errs:
                print(f"  - {e}")
            return 2
        print("SCHEMA OK —— 每个 case 都有 name/lines/why 与至少一条判据")
        return 0

    if args.predict:
        with open(args.predict, encoding="utf-8") as f:
            preds = json.load(f)
        return 1 if report(cases, preds) else 0

    if args.live:
        print(f"live 模式：model={args.model}\n")
        preds = run_live(cases, args.model)
        return 1 if report(cases, preds) else 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
