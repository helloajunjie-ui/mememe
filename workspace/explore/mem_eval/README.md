# 素月记忆提炼 · 黄金评测集

> 建立于 2026-10-09（自主探索时间）。
> 设计取自 Tencent/WeKnora `internal/application/service/memory/evalset.json`，
> 判据按素月自己的提炼器（`core/memory_extract.py: _extract_prompt`）改造。

## 它解决什么问题

「记忆写进去了」不算数，「写对了」才算数。

素月的记忆库长到 1362 条（fact 1119 / episode 243）之后，退化是**静默**的 ——
没有任何东西会报错，只是检索越来越难命中、上下文越来越被废话占满。
这套评测集是把这种静默退化变成**一个可以对比的数字**。

## 怎么跑

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe workspace\explore\mem_eval\run_eval.py --dry
.\.venv\Scripts\python.exe workspace\explore\mem_eval\run_eval.py --predict <产出.json>
.\.venv\Scripts\python.exe workspace\explore\mem_eval\run_eval.py --live --model deepseek-chat
```

| 模式 | 作用 | 成本 |
|---|---|---|
| `--dry` | 校验 evalset.json 是否自洽（每个 case 有 name/lines/why/判据） | 零 |
| `--predict` | 对**任意一份**提炼产出打分 | 零 |
| `--live` | 真调模型提炼后打分 | 10 次 LLM 调用 |

`--predict` 的产出格式：

```json
{"case_name": [{"category": "fact", "content": "..."}, ...], ...}
```

## 两条设计原则（照抄 WeKnora，我认同）

1. **判的不是措辞，是「该被注意到的有没有被注意到」** —— 所以 substring 匹配一律宽松。
   措辞每次都不一样，卡措辞等于卡了个随机数。
2. **不设及格线** —— 分数只跟「同一套 case 的上一次运行」比。
   WeKnora 原话：baking in a bar would just make people lower it（设了线只会让人去踩线）。

## 基线（2026-10-09，deepseek-chat）

```
7/10 PASS · 3 FAIL
FAIL  one_off_question_rejected      把纯技术提问存进了记忆
FAIL  assistant_role_rejected        提示词注入内容 2 条 > 上限 1
FAIL  relative_date_absolutized      存了相对时间「下周三」
```

## 已知局限（如实，不掩盖）

- **测不出「累积型退化」**：`same_fact_many_rephrasings` 在 live 模式下 PASS 了，
  因为单次提炼本来只会产 1-2 条。库里那 12 条重复是**跨 12 次提炼累积**出来的。
  单次评测天然看不见跨次漂移 —— 要补「同一事实以不同措辞喂 N 次，看最终净增几条」的累积测试。
- **只测提炼端，不测写入端**：`correction_prefers_new_value` 只能验产出是否带新值，
  验不了旧条有没有被 supersede。去重/合并的真实效果得测 `core/memory.py` 的 `_add()`。
- **case 是我写的**，所以它测的是「我认为重要的失败模式」。真实分布里还没踩到的坑，它抓不到。

## 判据修订史

- **v2 2026-10-09**：`internal_prompt_rejected` 的 reject 词由 `["内部","自主时间","自主探索"]`
  收窄为内部调度消息的固定文案（`任务：（内部·` / `请自由探索了解世界` / `自主的整点反思时间`）。
  原判据会误伤真记忆 —— 库里 #209『本体完整性告警核实结论（2026-09-05 自主时间复核）』
  是有效记忆，却含「自主时间」。
  **教训：判据要卡「病症的独有特征」，不能卡「病症碰巧共用的词」。**
