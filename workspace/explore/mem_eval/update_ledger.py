"""台账回填：WeKnora ③ 记忆提炼黄金评测集完成 + ② 状态更正。

幂等：检测到 ③ 段已存在即跳过。保留 CRLF 行尾。
"""
import os, shutil, sys, datetime
sys.stdout.reconfigure(encoding="utf-8")

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ledger.md")

with open(LEDGER, encoding="utf-8", newline="") as f:
    text = f.read()

if "**③ 收口（2026-10-09）" in text:
    print("SKIP: ③ 段已存在，幂等跳过")
    sys.exit(0)

# 备份回滚点
bak = LEDGER + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
shutil.copy2(LEDGER, bak)
print("backup:", os.path.basename(bak))

text = text.replace("\r\n", "\n")

# ---- 替换 1：backlog ③ 标完成 + ② 加更正提示 + 进度更新 ----
OLD1 = ("- **下一步（backlog，未做）**：① `recall_trace` 式「为什么没召回」可解释性——我的记忆目前是黑箱 "
        "② `topic_resolve` 三层归并——正对我方法论去重（#275） → **已完成 2026-09-30** "
        "③ 给 `memory_extract` 建黄金评测集（照 evalset.json 形态）\n"
        "- **卡点**：无\n"
        "- **进度**（2026-09-30 更新）：**进行中（已做 1/3）** —— ② 已完成（见下），① ③ 未开始。")
NEW1 = ("- **下一步（backlog）**：① `recall_trace` 式「为什么没召回」可解释性——我的记忆目前是黑箱 【未开始】 "
        "② `topic_resolve` 三层归并——正对我方法论去重（#275） → **已完成 2026-09-30，但只是离线检具，未接入记忆写入路径（2026-10-09 更正）** "
        "③ 给 `memory_extract` 建黄金评测集（照 evalset.json 形态） → **已完成 2026-10-09（见下 ③）**\n"
        "- **卡点**：无\n"
        "- **进度**（2026-10-09 更新）：**进行中（已做 2/3）** —— ② ③ 已完成，① 未开始。")
assert OLD1 in text, "锚点1 未命中"
text = text.replace(OLD1, NEW1)

# ---- 替换 2：在 ② 段之后插入 ③ 段 ----
ANCHOR = "- **结论：本库无待合并项，不动作。**\n\n---\n\n### [assessed] OpenBMB/VoxCPM2"
assert ANCHOR in text, "锚点2 未命中"

SEC3 = """- **结论：本库无待合并项，不动作。**

**③ 收口（2026-10-09）—— 记忆提炼黄金评测集建成 + 三个 FAIL 根因定位**

- **做了什么**：在 `workspace/explore/mem_eval/` 建成素月自己的记忆质量评测集 —— `evalset.json`（10 case）+ `run_eval.py`（harness，`--dry`/`--predict`/`--live` 三模式）+ `README.md` + `DIAGNOSIS.md`。设计取自 WeKnora `evalset.json`。
- **三条原则（照抄并认同）**：判的是「该被注意到的有没有被注意到」，不判措辞；**不设及格线**（设线只会让人去踩线），分数只跟同一套 case 的上次比；reject 判据是重点——它们是反复发生的失败模式。
- **基线**（deepseek-chat，2026-10-09）：**7/10 PASS**。
- **三个 FAIL 与根因（全部用真实库数据定位，非推断）**：
  1. **内部调度消息入库 61 条** —— 根因在**喂料口**：`core/agent.py` L1921 把 `self.history[-12:]` 整段传给 `auto_extract`，不筛 role；`auto_extract` 内部也不过滤，且 `content[:300]` 硬截断（半截残句的来源）。**旁证**：`internal_prompt_rejected` 在 live 下 PASS —— 提炼器认得出，锅确在喂料口。
  2. **同一事实 12 条重复**（『尼可』12 种措辞 #765/#878/#886/#929/#963/#1401/#1411/#1420/#1512/#1527/#1548/#668）—— 根因在**写入去重弱**：`core/memory.py: _add()` 判重是 `content=?` **精确字符串相等**（norm 只压空白），接不住同义改写。
  3. **相对时间入库 10 条**（下周3 / 明天3 / 后天1 / 这周1 / 昨天2）—— 根因在**提炼提示词缺规则**，且未注入时间锚。
- **⚠ 对 ② 的重要更正**：② 的 tier2 bigram 产物是 `dedup_scan.py`——**离线检具，用于扫 `data/methodology.json`**，**并未接入记忆写入路径**。此前标「② 已完成」极易被误读为「记忆去重已解决」。**记忆库的重复问题（症状 2）至今仍是开放的。**
- **本轮最值钱的发现（评测集自身的盲区）**：`same_fact_many_rephrasings` 在 `--live` 下 **PASS**，可库里躺着 12 条重复 —— 因为**单次提炼只产 1-2 条，12 条是跨 12 次累积出来的**。→ **单次评测天然看不见「累积型退化」**；需补「同一事实喂 N 次、看最终净增几条」的累积模式。
- **判据修订（v2）**：把 `internal_prompt_rejected` 的 reject 词从 `['内部','自主时间','自主探索']` 收窄为内部消息的固定文案（`任务：（内部·` / `请自由探索了解世界` / `自主的整点反思时间`）—— 原判据误伤 #209『本体完整性告警核实结论（2026-09-05 自主时间复核）』这类真记忆。**教训：判据要卡「病症的独有特征」，不卡「病症碰巧共用的词」。**
- **产物**：`workspace/explore/mem_eval/`（evalset.json / run_eval.py / README.md / DIAGNOSIS.md / _truth_replay.json / _diag.py）、`workspace/explore/mem_audit.py`
- **修复 backlog（未做，按风险升序）**：① `auto_extract` 加 role + 内部文案过滤（低风险） ② `_extract_prompt` 加规则 6 + 时间锚（中） ③ `_add()` 加同 category 近似去重（**高，需先补累积模式**） ④ 补累积模式评测

---

### [assessed] OpenBMB/VoxCPM2"""

text = text.replace(ANCHOR, SEC3)
text = text.replace("\n", "\r\n")

with open(LEDGER, "w", encoding="utf-8", newline="") as f:
    f.write(text)
print("OK: ledger 已更新")
print("size:", os.path.getsize(LEDGER))
