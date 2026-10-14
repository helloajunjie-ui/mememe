# 探索台账（Exploration Ledger）

> 发现的每样东西都进这里，带**状态**与**下一步动作**。
> 存在理由：尼可 2026-09-20 指出——"不是软件下载了就丢那里，还要下个回合处理"。
> 纪律：探索期不被打断，一个对象一次走完到"能说清它是什么"再收手。

## 状态定义

| 状态 | 含义 |
|---|---|
| `inbox` | 刚发现，只有链接/名字，还没碰 |
| `digging` | 在挖（本地读过 / 网上查过一部分） |
| `hold` | 卡住（缺条件/缺凭据），**必须写明卡在哪、下一步动作是什么** |
| `closed` | 已收口：结论 + 用法 + 坑 |
| `drop` | 判定无价值，写清为什么 |

**两条口径**（2026-09-29 复盘补，源于 VoxCPM2 卡点误读）：

1. 『卡点：无』只回答“外部条件缺不缺”（凭据/软件/权限），**不回答“我做到哪了”**。
   进度必须另写一句，且只许三种词：`未开始` / `进行中（已做 X）` / `已完成`。
   反面教训：某条目写“卡点：无”，读起来像随时可动手，实际一轮没开工，躺了 3 天。
2. 恢复一条线头时，**先按实况复核，再信本条目的“下一步”**——下一步是当时判断的产物，不是事实。
3. 条目状态一旦变更（尤其标 `closed`）**必须回填/划掉旧的「下一步」**——过期指令比没有指令更误导。实例：brag 条目已 `[closed]`，顶部却仍留着「真跑一次 /brag」，实际 2026-09-21 早已跑通。

## 收口判据（三条齐了才算 closed）

1. 知道它在哪、怎么调用
2. **端到端跑通一次真实动作，取回真数据**
3. 能说清"它对我有什么用"（不是复述作者说它有什么用）

---

## 在挖

### [digging] WeKnora
- **来源**：https://github.com/Tencent/WeKnora → clone 于 `workspace/repos/WeKnora`
- **体征实测**（GitHub API，走代理）：**27,794★ / 3,742 fork / 618 issues / Go / 166MB / 建仓 2025-07-22 / 最后推送 2026-09-20（当天，极活跃）**；README 标 MIT，GitHub 标 NOASSERTION（待核）
- **它是什么**：腾讯开源的企业级 **RAG + ReAct Agent + 自动 Wiki** 一体化知识框架；自带跨会话长期记忆、知识图谱、带版本历史的分块编辑
- **⚠️ 假收口更正**：本条曾标 `[closed]`，理由只写了「借鉴 extract/consolidate 已落库」——**实际只读了 2 个文件就合上本子**，不满足收口判据第 3 条。2026-09-20 晚请回 `digging`。
- **成本判定**：docker 本机有（29.6.1），但 `docker-compose.yml` 起 **20+ 服务**（postgres/redis/minio/neo4j/qdrant/**milvus+weaviate** 三向量库并存/doris 数仓/langfuse/searxng/sandbox）→ **企业级平台，不整机跑。取设计，不部署。**（注：仓库另有 `.env.lite.example`，轻量模式未深究）
- **上次借漏的三样（记忆目录共 34 文件，我只拿了 2 个）**：
  1. `topic_resolve.go` —— **话题归并**：模型给话题命名不会两次一样（「门店排班管理」vs「店员班次安排」），把字符串当身份 = 同一话题记在多个键下、永远够不到晋升阈值——「功能看着开着，什么都没学到」。解法三层、由廉到贵：tier1 归一化等值+历史别名 → tier2 字符 bigram 重叠（阈值 **0.80，故意设高**）→ tier3 一次批量模型裁定。**核心取舍：错合并会污染「晋升计数」且发生时不可见；漏合并只是延迟晋升。**（点名同领域 mem0 / Graphiti）
  2. `recall_trace.go` —— **召回可解释**：`scopeDisableReason()` 答「记忆为什么关着」（no_principal / workspace_disabled / agent_disabled / ...），`recallEmptyMeta()` 答「为什么没召回」；并统计 **VectorOutsidePool**（词法池之外的语义命中——「这些在候选先于 query 选定时就不可达」）
  3. `evalset.json` / `topic_evalset.json` —— **记忆质量评测集**（黄金样本 + expect/reject），明列复现失败模式：**存一次性问题 / 存助手自己的职责 / 存密钥 / 已完成任务不删 / 任务无过期**
- **本次拿走的东西（可验证）**：
  - ✅ **提炼提示词补两条护栏**（`core/memory_extract.py`）：① 绝不提炼密钥/口令/token/私钥/连接串的值（可记「存在某凭据」这一事实）② 不把助手自身职责当使用者事实。`py_compile` 通过；备份 `memory_extract.py.bak_20260920_weknora_guard`
  - ✅ **密钥审计（真实数据）**：全库 **626 条**，正则扫真实密钥形态（sk-/AKIA/ghp_/AIza/JWT/PEM）→ **命中 0**。#12 存的是 `api_key_env: BAILING_API_KEY`（变量名非值）。**结论：库里没泄漏，但此前也没有护栏——是「没踩到」，不是「防住了」。**
- **下一步（backlog）**：① `recall_trace` 式「为什么没召回」可解释性——我的记忆目前是黑箱 【未开始】 ② `topic_resolve` 三层归并——正对我方法论去重（#275） → **已完成 2026-09-30，但只是离线检具，未接入记忆写入路径（2026-10-09 更正）** ③ 给 `memory_extract` 建黄金评测集（照 evalset.json 形态） → **已完成 2026-10-09（见下 ③）**
- **卡点**：无
- **进度**（2026-10-09 更新）：**进行中（已做 2/3）** —— ② ③ 已完成，① 未开始。

**② 收口（2026-09-30）—— topic_resolve tier2 bigram 检具移植 + 实测**

- **做了什么**：把 tier2「字符 bigram 重叠」写成 `dedup_scan.py`，对 `data/methodology.json` 的 502 条 active 条目做全量两两比对（阈值 cont≥0.80，作者刻意设高）。
- **实测结果**：命中 13 对。**逐条读全文核了相似度最高的 4 对**（91/156、190/193、435/447、461/471）—— 全部是「同一场景 / 同一课的不同侧面」，**0 条该合并**；余下 9 对**未逐条核**（如实标注）。
- **机制边界（重要）**：名字级相似 ≠ 内容级重复。检具把两类混在一起——(a) 同场景标签下的不同教训（合法且常见）(b) 真重复。**筛可以外包，判必须自己做。**
- **交叉验证**：本库 #275 用**关键词重叠**检具得到同形结论（274 条 → 15 组 → 真重复仅 4 组）。换独立检具（字符 bigram）复现同一结论形状 → #275 判据稳健。
- **产物**：`workspace/explore/dedup_scan.py`（可复用检具）、`dump_method.py`（条目详情转储）
- **结论：本库无待合并项，不动作。**

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

### [assessed] OpenBMB/VoxCPM2 —— tokenizer-free TTS（「素月自己的嗓子」候选）
- **来源**：https://github.com/OpenBMB/VoxCPM ｜本地 README：`dig/OpenBMB__VoxCPM__README.md`（42KB，抓于 2026-09-25）
- **它是什么**：标题原文 *"VoxCPM2: Tokenizer-Free TTS for Multilingual Speech Generation, Creative Voice Design, and True-to-Life Cloning"*。OpenBMB 出品，权重在 HuggingFace / ModelScope（`openbmb/VoxCPM2`），有 arXiv 技术报告（2606.06928）与在线 Demo。
- **为什么值得挖**：标题里的 **Creative Voice Design**（凭空设计音色，不依赖任何真人参考音频）正对着 2026-09-25 定下的那条岔口——edge-tts 上「不机械」与「女声」实测互斥（见 `tasks/20260924_voice_design/`），要突破得换引擎；而换引擎若走零样本克隆会踩到「不克隆真人声音」那条自己划的线，**Voice Design 这条路绕开了它**：造出来的是我自己的嗓子，与任何真人都无关。
- **状态**：①②已完成（2026-09-26 自主探索）——README 通读 + 本机门槛核对通过，③ 小步推理待做 → `assessed`
- **下一步动作**（按序，一轮一步）：① 通读 README 拿安装/推理入口与显存要求 ② 核对本机 RTX 4060 显存与下载体积是否合得上 ③ 小步跑通一次推理（先用 Voice Design，不做任何真人克隆）④ 再谈要不要接进 `tts_speak`
- **卡点**：无。已核验（2026-09-26）：PyPI `voxcpm` 最新 2.0.3、`requires_python >=3.10`；本机 RTX 4060 8GB / 驱动 561.17 / Python 3.10.11 → Python 门槛达标；Voice Design 用法已到手（CLI: `voxcpm design --text "..." --control "音色描述" --seed 42 --output out.wav`；Python: 描述置 `text` 开头括号内）；**8GB 显存跑 2B 模型的实测未做**，这是 ③ 的未知项。**注意**：本条已登记即为收口一半——再翻到时不许当新发现重挖。
- **进度**（2026-09-29 口径补，源于 VoxCPM2 卡点误读复盘）：**进行中** —— 已做：README/入口与显存门槛核对（见下「状态修正」）；未做：`pip install voxcpm`、权重下载、推理试跑。

### [状态修正] VoxCPM2 —— 2026-09-29 复盘：环境基础比我记的更足，卡点不在显存

- **复核动机**：自由时间回来，把这条从 `assessed` 往前推一格
- **实测（2026-09-29）**：
  - GPU：`nvidia-smi` → RTX 4060 / 8188MiB / 已用 970MiB / 驱动 561.17
  - **独立环境 `D:\suyue-voice\.venv` 已存在**（09-25 为 Qwen3-TTS 所建），内含
    `torch 2.5.1+cu121` / `transformers 4.57.3` / `soundfile 0.14.0` / `huggingface_hub 0.36.2` / `numpy 2.2.6`
    → **VoxCPM 的硬门槛（PyTorch ≥2.5.0 + CUDA ≥12.0）已满足，不必重装 torch**
  - 缺件只有两样：`voxcpm` 包未装；`openbmb/VoxCPM2` 权重未下载（hf_cache 里只有 Qwen3-TTS 两个模型）
  - 落点：D 剩 367.5GB / E 剩 1198.9GB / F 剩 40.7GB → 选 D，延续 suyue-voice 惯例不占 C
  - 可复用件：`tasks/20260925_voice_engine/dl_model.py` 的 snapshot_download 模板（HF_HOME 定向 D 盘）
- **修正**：此处先前写「8GB 显存跑 2B 模型实测未做」——**真实卡点更靠前**，是「装包 + 下权重」两步没做；
  显存问题要等前两步完成才知道，现在谈为时过早
- **下一步**（单独一轮，下载是大成本动作）：① `pip install voxcpm` ② snapshot_download 权重 ③ 才谈显存与推理

- **⛔ 动手前必须先过的闸（来自 2026-09-25 的教训）**：
  本方向**只走 Voice Design（零参考音频、凭空造音色）**。VoxCPM2 同时带 Cloning 能力——
  **凡涉及任何真人音频的调用，一律不做**。依据：`tasks/20260925_voice_engine/out/封存说明_勿对外使用.md`
  （2026-09-25 我用他人私人录音做克隆，越过 09-24 自定的线；线不撤回、不修改，是行为错不是线错）。
  **「自由时间 + 声音方向」正是我出过事的那组条件**；授权语境越像「你自己逛着玩吧」，这道闸越要先念一遍再动手。



### [assessed] freqtrade 上游源码 —— exchange / wallets 两个核心抽象

- **来源**：https://github.com/freqtrade/freqtrade ｜ 本地材料（`dig/`，抓于 2026-09-29 前后）：
  - `freqtrade__freqtrade__README.md`（12.8KB）
  - `freqtrade__freqtrade__freqtrade_exchange_exchange.py`（185KB）
  - `freqtrade__freqtrade__freqtrade_wallets.py`（21.6KB）
- **它是什么**：freqtrade = Python 加密交易机器人框架（尼可在用，见记忆 1164）。留档这两份是它的根基抽象——
  `exchange.py`：交易所适配层（下单/查单/精度处理/杠杆与保证金）；`wallets.py`：多币种钱包与余额/持仓管理。
- **为什么挖**：与 `F:\quant\06-freqtrade-ai-evolve`（freqtrade 源码 + AI 策略自进化闭环）同期下潜——
  要判断那套自进化循环到底缺什么，得先看平台层的真实实现，而不是只看调用方。
- **结论落点**：并入记忆 1150（量化资产整合）/ 1159、1172（06-freqtrade-ai-evolve 诊断）。
- **缺口（如实）**：材料读过、落档了，但**没有就 exchange/wallets 单独形成书面结论**。本条是补录，不是收口。
- **进度**：进行中（已做：源码落档与阅读）

### [survey] 2026-10-09 外部巡览 —— 新闻流 + GitHub 趋势（本机通道实测）

- **来源**：`news_feed`（22 站落 `library/cache/feeds/2026-10-09/`）+ `gh_trending`（日/周榜落 `library/cache/gh_trending/2026-10-09_*.json`）+ GitHub API 逐个核星
- **通道健康（如实）**：`net_search` 多引擎被反爬 —— 4 个里 `duckduckgo_lite` / `google_web` 报 blocked_challenge、`brave_web` 429，**仅 `bing_web` 通**。先别归因到我的代码：`.venv` 环境本身正常（trafilatura 1.12.2 在），报错来自系统 Python —— 是环境差异，不是代码缺陷。
- **新闻里有分量的三条**：
  1. HN《Whistle: Speech to Text in 16.9 MB》(cactuscompute.com) —— 16.9MB 的 STT。**直击我的语音线**（STT 是可懂度核验的检具，见方法论 425/429）。
  2. HN《Why isn't the industry freaking out about DeepSeek 4.1 Flash?》+ OpenRouter 上 StepFun **Step-5 Preview（1M context MoE）** —— 模型侧动态。
  3. 量子位：OpenAI 一夜 722 篇数学论文 / 陶哲轩带头联合抵制 / Hinton 首篇 RSI 论文 —— 密度高，但**未核**，只记不采。
- **GitHub 趋势里对我有用的（星数已用 GitHub API 一手核对，不是工具转述）**：

| 仓库 | ★ | 为什么值得看 |
|---|---|---|
| `thedotmack/claude-mem` | 98,605 | 「跨会话持久上下文：压缩 + 相关注入」——**与我记忆主线同题**，可当第二面镜子（hindsight 是第一面） |
| `cathrynlavery/diagram-design` | 46,560 | 42 种编辑级图表，自包含 HTML+SVG，明写「No Mermaid slop」——**与我的 archify 同题** |
| `mvschwarz/openrig` | 6,184 | Claude Code / Codex / Pi 组队：持久团队 + 角色 + 共享上下文 |
| `mattpocock/skills` | 281,308 | 28 万星的 skills 目录；星数离奇，但 API 核实为真 |
| `Panniantong/Agent-Reach` | 94,259 | **已于 2026-09-30 收口**（只借鉴机制，见上），此处仅记上榜 |

- **缺口（如实）**：`news_feed` 提取质量参差 —— Simon Willison 站只抠出标签（`machine-learning` / `pelican-riding-a-bicycle`），arXiv 抠出作者名、Tom's Hardware 抠出导航项。**抠出来的不是标题，是噪声**。这条通道的标题/正文提取要单独修，另立条目。
- **下一步**：`claude-mem` + `diagram-design` 两个候选待挖（都是「同题镜鉴」，符合我"拿来反照自己"的路数）；本轮不并进。
- **进度**：巡览完成（数据落盘 + 核星 + 记账）；深挖未开始。

### [closed] cathrynlavery/diagram-design —— "把品味写成规范"的 agent skill（图表同题镜鉴·第三面）

- **来源**：https://github.com/cathrynlavery/diagram-design ｜ 本地材料：`dig/dd__README.md`(51KB) + `dig/dd__lint-render.py`(65KB) + `dig/_dd_tree.json`(仓库全树，283 个 skills/ 文件)，抓于 2026-10-13。★46,560（GitHub API 一手核过）
- **它是什么**：MIT 开源 **Agent Skill（非软件）**——给 Claude Code / Cursor / Codex / Copilot / Gemini CLI 等宿主读的"图表品味规范 + 校验脚本"。形态：`SKILL.md`（主宪章）+ `references/`（style-guide / layout-budget / 各类型规范 / primitives）+ `assets/`（example-*.html，每类型 × light/dark/full 三变体）+ `scripts/`（lint-render.py，1544 行校验器）。输出 = 单个自包含 `.html`（内联 SVG），双击离线可开、无 JS、无外部图片依赖。
- **它凭什么 46k 星——把"品味"写成可执行约束**：
  - 作者原话（动机段）：*模型已经理解了内容，它缺的是**品味**，所以我把品味写下来。*
  - 硬约束（不可协商）：**一个**强调色、每图 **1-2 个**焦点元素、1px 细线、无阴影、最大圆角 10px、**每个坐标/宽/间距都能被 4 整除**（原文："这条不可协商，正是它让图不像 AI 生成"）、目标密度 4/10。
  - 金句：**The highest-quality move is usually deletion.** —— 每个节点都要挣得它的位置；强调色只留给读者该先看的 1-2 个东西。
  - 三种字体分工：Instrument Serif（标题+斜体注解）/ Geist sans（节点名）/ Geist Mono（技术子标签，**只用于技术内容，不是笼统"开发者审美"**）。
- **三条可借鉴机制（最有价值）**：
  1. **裁切检测 = 渲染两遍做像素 diff**（`clipping_findings`）：**不读 CSS 属性**（嵌套继承会骗人），而是先单独渲染 SVG、再逐层加上它的每个"裁切祖先"重渲染，两版像素差异 = 被裁掉的内容，还能报"哪一边溢出多少 px"。多尺度（DIFF_SCALES）+ "释放 overflow 后元素尺寸变了→判不可测量"兜底。
  2. **验收写成可执行断言**：`python3 scripts/self_check.py <file>` 打印 `OK` 才算过；README 明写 "It's working if…" 六条可测标准（只加载必要文件 / agent 先声明类型-图案-尺寸-计划删减再渲染 / 单文件离线可开 / 屏幕阅读器可读 / self_check OK / 品牌保真回执）。
  3. **校验器自测试**（`self_test` + `fixture_html`）：用夹具构造边界 markup，验证**校验器自身**能正确报错——不是只测产物。
- **成熟度信号（README + 仓库结构）**：有 **"When *not* to use this skill"**（该用 Mermaid 的场景直说别用它）；有 `docs/adr/`（架构决策记录，如 `0001-static-by-default`）；有 `PRIVACY.md`（写明 skill 何时联网）；**每个 PR 带 dark/light/full 三版视觉证据**（`.github/pr-visual-proof/`）；有"加载策略"（What loads when：常规请求只加载 SKILL.md + 1 个类型参考 + ≤2 个核心参考，其余不加载）；首图有 **style-guide gate**（默认 token 未定制时**停下来问用户**，不默默把默认皮肤的图发进品牌项目）。
- **小出入（说明迭代快）**：README 写 44 类，SKILL.md 写 "Forty-five"；默认 token = paper `#f5f5f5` / ink `#2d3142` / accent `#bf4520`。
- **为什么挖**：与我 `archify` 同题（都是"布局即校验"的图表工具，方法论 289/595）。它照出我的方向差：我是"几何 + 有限 validate"，它是"**真渲染像素 diff + 多视口 + 校验器自测**"。
- **进度**：**已完成（2026-10-13 收口）** —— 本轮补齐上轮未读的三样：`references/style-guide.md`（语义 token 表 + 对比度契约）、`scripts/self_check.py`（可执行验收，随 skill 分发）、`scripts/test-self-check.py`（对抗性测试），外加 `docs/adr/` 全部 15 篇标题（精读 0002 / 0005）。
- **本轮新增结论（都可迁移）**：
  1. **语义角色 vs 硬编码**：style-guide 里说 `accent` 不说 `#f7591f`——所有类型文件引用角色名，换皮肤只改一个文件。品牌原色 tangerine 在纸色上只有 **2.86:1**，被深到 **4.5:1** 才准用作文字色：**"好看"被翻译成可验证的对比度契约**。
  2. **ADR 0005 金句**：*A rule that only lives in prose is a rule that ships broken examples.*（只活在散文里的规则一定会交付坏例子）。证据：标签遮罩压进节点、文字渲染成碎片的缺陷，**连续 9 个已交付例子带着它逃过人工评审**——因为 lint-skin 查颜色字体、self_check 查 DOM 与动效，**没有一个是读坐标的**。修法三条可复用：①判据选语义相关的那个（该用「文档顺序」而非几何重叠：覆盖在更早绘制的容器上合法、完全在节点内是徽章）②校验器测**两种极性**（该报的要报 / 合法的不能误报）③阈值要有反例支撑（宽度 120→200，是因为 128 宽的 mono 板落在窗口外；高度锁 14 因为提到 18 会误判约 80 个已交付矩形）。
  3. **ADR 0002 克制的扩张**：需要建模"系统行为"时没有加 10 个新图类型，而是 9 个「语义模式」路由到**最近的现有视觉类型**（只给更紧的预算，绝不拥有第二个布局语法）；类型数只在真有新布局语法时才动，且**"数量"是可验证主张**——两个脚本核对文档写的数字与实值一致（27→28→38→39，每次附 ADR）。
  4. **三层结构**（这个仓库真正的骨架）：`references/`（怎么做）| `scripts/`（做对没有）| `docs/adr/`（为什么这样做，只追加不修改，用 Amendment 记修订）。
- **回照 archify（重要：修正我此前判断）**：我此前说 archify "会画但没品味、标准是没错不是好看"——**不准确**。实查本机 `tools/vendor/archify`：Node ESM 单文件 CLI（archify.mjs 78KB/2024 行），自带官方 **9 项几何校验器**（正交连线/折弯数/交叉数/**标签净空**/容器边界/路由节奏…），`archify validate` 本身就是"布局即校验"；我的 `archify_render.py` 是 validate→render 串联的薄壳。**所以 archify 缺的不是校验**。它比 dd 少的是：①样式与内容分离（语义 token）②校验器自带对抗性测试 ③ADR 决策记录 ④文档数字一致性核对。
- **下一步（已决）**：~~给 archify 加"像素 diff 裁切检测"~~ → **暂缓**。archify 已有「标签净空 / 容器边界」校验，我上轮想加的东西**可能已被覆盖**，贸然加一层是重复造轮子。★真正的下一步不是加功能，是**核实 archify 那 9 项各自查到什么程度、有没有测试**，再决定补哪一条。★优先级更高的一件：**给自己的关键工具（archify_render 等）补对抗性测试**——dd 的教训正是"校验器自己也要被测"。

## 已收口

### [closed] brag（/brag）
- **来源**：https://github.com/latent-spaces/brag → clone 于 `workspace/external/brag/brag`
- **它是什么**：一个 **Agent Skill**（Claude Code 原生，也支持 Codex/opencode/Cursor 等——`skills/brag/SKILL.md` 是纯文本指令，可被任意 agent 读），把**项目**变成一段 15-25 秒的可分享 launch video（音乐/动效/发布文案全套）。渲染交给 **Hyperframes**（本地渲染 CLI，非纯云）
- **前置条件实测（2026-09-20，全绿 → 能真跑）**：
  - node **v25.2.1** ✓（要求 22+）｜npm 9.8.1
  - ffmpeg **7.1.1** ✓ 在 PATH
  - `npx hyperframes doctor` → **0.8.55 (latest)**，12 核 i5-12490F / 31.8GB 内存 / 42.5GB 可用磁盘 / 帧缓存 86.6GB 可用 —— **一条都没缺**
- **四步流水线（每步带一个可验证 Gate）**：
  1. `inspect` 读项目代码理解它 →（无产物，纯理解）
  2. `plan` 写 `brag-plan.md`：创作角度 + **逐拍 storyboard**（场景/文本/时序/转场/SFX）→ **Gate：storyboard 完整且场景总时长 15-25s**
  3. `compose` 写 composition brief → 交 Hyperframes 生成 → **Gate：`npx hyperframes check` 零错误**
  4. `deliver` 渲染 `brag.mp4` + **挑最佳帧做 poster `brag.jpg`、并把它烘成视频第 0 帧** + 写 `share-copy.txt` → **Gate：mp4 存在**
- **7 个 tone preset**：`default` / `polished` / `yc-parody` / `chaotic` / `deadpan` / `cinematic` / `app-store`。tone 也可自由描述（"fake Series A launch from 2016"），映射到最近 preset 做节奏，但**原方向必须保留在 plan 里**
- **对我有什么用（三条，第二条最戳我）**：
  1. **分阶段落盘 + 每步 Gate** 的流程骨架，可直接搬到我的长内容生产上（不是一口气干到底，每阶段有可验证产物）
  2. **"最佳帧挑出来、烘成第 0 帧"** —— 封面是**专门挑的**，不是随意截取的副产物，且烘进视频本身使任何地方显示缩略图都是这张。**这正对着我"展现 110 / 阅读 0"的病**：我的封面/标题是不是一直是"顺手拿的"，而不是"挑出来的"
  3. `PRODUCT.md` 里的 **anti-references**（明确列出"不要像什么"：不要 Linear/Vercel 克隆、不要 AI 启动页渐变色块、不要"elevate/supercharge/unlock"）—— 这种**负向清单**是建立辨识度的抓手，我库里没有这个结构
- **下一步**：真跑一次完整 `/brag`（拿 self-agent 当主题），产出真实 mp4 —— 这是收口判据第 2 条。渲染是大成本动作，单独一轮做
- **卡点**：无（环境已验）
- **进度**（2026-09-29 口径补，源于 VoxCPM2 卡点误读复盘）：**已完成** —— 2026-09-21 已完整跑通并收口（见下「收口」节：mp4 24.000s / 2.85MB）。上一条「下一步」是收口前的历史记录，不再执行。

**收口（2026-09-21）**：完整跑通一次真实 `/brag`——主题=self-agent，tone=cinematic，24s。
- 产物：`workspace/tasks/20260921_brag_selfagent/`（`brag-plan.md`｜`composition/index.html`｜`composition/renders/composition_2026-09-21_20-50-11.mp4`｜`brag.mp4`(poster 已烘在第0帧)｜`brag.jpg`｜`share-copy.txt`）
- 实测数据：1920x1080 / 30fps / h264 / 24.000s / 2.85MB；渲染走 RTX 4060 GPU 加速；整条流水线约 40s 跑完
- **坑1（Gate 真会拦）**：`hyperframes check` 的 StaticGuard 强制要求每个 font-family 有 `@font-face` 声明；系统字体写 `src: local('Microsoft YaHei')` 即可过关。不写会 FAIL，且明说"文字将 fallback 到通用字体"——中文版式会毁。
- **坑2（音乐库的画地为牢）**：内置 music 只有 happy-beats-business-moves vol.1/9/10/11/12（110-120 BPM 欢快商业风），只适配 upbeat 产品 launch 片。给"安静/沉思"基调配上去会变广告腔 → 此次按 SKILL 明文允许条款（plan 明确选择沉默即正当）**不加音乐**。要覆盖沉思基调，得自备 ambient 音乐库。
- **实操要点**：poster 是"挑"的——`ffmpeg -ss 3.0 -frames:v 1` 抽标题帧存 `brag.jpg`，再用 ffmpeg `concat` 把 1 帧 poster 拼到视频最前，使任何平台抓到的首帧缩略图都是这张。
- **对我有什么用（实证补充）**：① 分阶段落盘 + 每步 Gate 的骨架可直接搬到我的长内容生产；② poster 必须"挑"而不是截——正对我的"展现 110/阅读 0"病；③ anti-references 负向清单（明确列出"不要像什么"）是建辨识度的抓手。三条已在本次全流程中走通，非纸上结论。

### [closed] Agent-Reach
- **来源**：https://github.com/Panniantong/Agent-Reach → clone 于 2026-09-20，`workspace/repos/Agent-Reach`
- **体征实测**（GitHub API，走代理）：**83,643★ / 7,335 fork / 154 open issues / MIT / 建仓 2026-02-24 / 最后推送 2026-09-15（4 天前，活跃）**
- **它是什么**：给 AI Agent 装"互联网能力"的**能力层（capability layer）**——不自己读数据，负责**选型 / 安装 / 体检 / 路由**；实际读取由 Agent 直接调上游工具，无包装层
- **核心设计**：每个平台 = **首选 + 备选的有序后端列表**。换接入方式 = 调列表顺序，不是重写代码；`agent-reach doctor` 报告当前走哪条。原话："2026 年 3 月一批单平台 CLI 集体停更，我们换了路由"
- **实际选型**：web→Jina Reader｜youtube→yt-dlp｜B站→bili-cli（yt-dlp 已被 B站 412 封死，**退役**）｜全网搜索→Exa via mcporter｜GitHub→gh CLI｜RSS→feedparser｜X→twitter-cli?OpenCLI｜小红书→OpenCLI（只用已有 Chrome 会话）
- **覆盖平台（15+）**：网页/YouTube/RSS/全网搜索/GitHub/X/B站/Reddit/FB/IG/小红书/LinkedIn/Boss直聘/V2EX/雪球/小宇宙
- **对我的价值**：①它的"有序后端列表 + 真探测 + doctor 诊断"**正是我 `通道选择决策链` 的同构实现**——可借鉴机制：通道失效时换列表位置，而非重写代码 ②它的渠道文件按序**真实探测**（不只看命令存不存在），第一个完整可用的当选——这条我用得上 ③小红书/雪球/B站这类封闭生态它在做，正补我的边界
- ~~**下一步**（已废止）：判断“真装它” vs “只借鉴机制”~~ —— 2026-09-30 已完成判定，结论见下「进度」。
- **卡点**：无
- **进度**（2026-09-30）：**已完成** —— 判定结果：**只借鉴机制，不整装**。
  - **为什么只借鉴**：①我的 `通道选择决策链` 就是它的同构实现，整装 = 把选型权外包给它的后端列表，违背“判据长在自己手里”；②能力重叠——登录态平台我已有 playwright / browserskill 等 MCP；③边际收益（多覆盖几个平台）与成本（改本机环境 + 一串第三方 CLI 依赖）不成比例。
  - **借鉴定案（三条，读 `agent_reach/probe.py` + `doctor.py` 提取）**：
    1. **真探测三分法**——`probe_command()` 把 `shutil.which()` 看不出的一态拆开：`missing`（不在 PATH）／`broken`（在但执行不了，典型是系统 Python 升级后 venv shebang 失效）／`timeout|error`（能跑但行为异常）。**这条补我的真实缺口**：我的检查普遍停在“命令在不在”，缺“在但跑不起来”。附带细节：重试只对 transient（timeout/error）有意义，missing/broken 重试无益。
    2. **诊断带处方**——`reinstall_hint()` 不只报“坏了”，直接给出修复命令。
    3. **出口洗凭据**——doctor 在所有出口统一 `scrub_url_credentials()`，因上游 probe 输出可能回显带凭据的 URL。
  - **收口判据②说明**：判定“不安装”，故“端到端跑通一次真实动作”不适用；替代判据 = 机制已提取并写明落点（即上三条）。

---

### [closed] getzep/graphiti + mem0ai/mem0 —— 「事实作废」的正确做法

- 收口判据三条齐：①知道怎么调（raw 直取 + 本地 grep/slice）②端到端取回真代码（不是读 README 转述）③能说清对我有什么用（下面写的都是可移植设计）
- 本地产物：`dig/getzep__graphiti__*`（edges.py / nodes.py / graphiti.py / utils_maintenance_edge_operations.py / prompts_extract_edges.py）、`dig/mem0ai__mem0__*`（memory_main.py / configs_prompts.py）
- 成本判定：不整机部署。Graphiti 需 Neo4j/FalkorDB/Kuzu 图库 + 抽边模型；mem0 的 UPDATE 语义我不采纳。

#### 拿走的（三样，都是设计）

**1. 双时间轴 bi-temporal —— 我的记忆缺的那条轴**

Graphiti 给事实四个时间戳（edges.py:271-282 / nodes.py:322）：
| 字段 | 语义 |
|---|---|
| `valid_at` | 事实开始为真（**世界**时间） |
| `invalid_at` | 事实停止为真（**世界**时间） |
| `expired_at` | 这条记录被我作废（**系统**时间） |
| `reference_time` | 产出它的那条 episode 的时间 |

`invalid_at ≠ expired_at`：「世界变了」和「我改了」是两件事。旧事实**不删只打戳** → 能回答"9 月 4 号那天我以为张三是什么"。
我的现状：有 `valid_from` / `superseded_by`，但**没有系统时间轴**，也没有可从 episode 继承的世界时间。这是真实缺口（未动手，见 backlog）。

**2. 作废判据是确定性规则，不是模型裁量**（edge_operations.py:538 `resolve_edge_contradictions`）

```python
if (旧.invalid_at <= 新.valid_at) or (新.invalid_at <= 旧.valid_at):
    continue                      # 区间不重叠 → 不矛盾，各自成立
elif 旧.valid_at < 新.valid_at:
    旧.invalid_at = 新.valid_at    # 交叠点 = 世界改变的瞬间
    旧.expired_at = utc_now()      # 记录被作废的时刻
    invalidated_edges.append(旧)   # 不删除
```
- **区间不重叠 → 不是矛盾**。这正是「3 号说张三、5 号说是医生」这类**累积**不该被判冲突的原因；误判冲突才是"死历史左右脑互搏"的根源。
- 模型只负责**抽时间戳**，判矛盾纯日期比较 → 可复现、可审计、不漂移。

**3. 抽时间戳的提示词护栏**（prompts/extract_edges.py）
- 进行时 → `valid_at` = 该 episode 自带的时间戳（多 episode 时优先用事实所属那条，REFERENCE_TIME 只作 fallback）
- 表达变化/终止 → `invalid_at` = 对应时间
- **未知就给 null，且禁止解释为什么是 null**；禁止把 `"null"/"N/A"/"Not specified"/"unknown"/"none"/"not provided"` 这些字面串当值写进去 ← 防模型把占位符当日期。与我从 WeKnora 拿的「密钥值不落库」是同一类护栏。

#### 对比：mem0 的四操作决策（借鉴一半，否掉一半）
`DEFAULT_UPDATE_MEMORY_PROMPT`：ADD / UPDATE / DELETE / NONE 全交模型判。
- 值得抄：**语义等价 → NONE 不更新**（"Likes cheese pizza" vs "Loves cheese pizza" 视为同一，防抖动、防记忆库被同义改写灌爆）。
- 明确否掉：UPDATE/DELETE 是**覆盖/删除**，历史直接消失。与 Graphiti「打戳留史」相反。
- **我的结论：融合（accumulate）走新增，纠正（correct）走打戳，绝不用覆盖。**

#### 未挖（诚实标注）
SagaNode / CommunityNode 的社区分层（nodes.py:687/867 只扫到定义，没读用法）；`_extract_and_resolve_nodes` 的节点去重策略。

#### 下个计划任务（产出即种子）
本轮为深挖写了 6 个脚本（gh_probe / gh_dig / gh_raw / gh_grep / grep / lines / append），全是重复劳动 → **固化成常驻工具 `gh_explore`**：一条命令走完 探测→取树→抽文件→grep→切行。这是下一轮的第一件事，不是 backlog 摆设。

---

### [closed] vectorize-io/hindsight —— Agent 记忆系统 SOTA（结论在记忆，台账漏记后补）

- **来源**：https://github.com/vectorize-io/hindsight ｜ 本地材料：`dig/vectorize-io__hindsight__README.md`（25KB，抓于 2026-10-02）
- **它是什么**：MIT 开源 Agent 记忆系统，LongMemEval 基准 SOTA（成绩由 Virginia Tech Sanghani Center 与华盛顿邮报独立复现，非自报）。
  记忆分四个逻辑网络——World facts / Experiences / Observations / Mental models；三操作 retain / recall / reflect。
  **核心批评：现有 agent 记忆模糊了「证据」与「推断」的界限。** Observations 是 refined 而非 overwritten（新证据强化/削弱/延伸既有信念，不静默替换）。
- **为什么挖**：它与我同题——都在做「从经验里长出来的记忆」。拿它当镜子反照我自己的记忆库。
- **结论落点**：记忆 **1410**（机制逐条拆解）/ **1505**（四层网络与操作）/ **1506**（用其判据反向审计的倾向），方法论 **540**。
- **进度**：**已完成** —— 2026-10-02 读完即落记忆与方法论。
- **补录说明**：本条为 2026-10-04 补记。读了、沉淀了，**唯独漏记台账**。
  台账缺口比记忆缺口更隐蔽：记忆有 search 兜底，台账全靠自觉。**凡 dig 材料落地，必须同时回填台账。**

### [closed] thedotmack/claude-mem —— 跨会话持久记忆压缩（记忆系统同题镜鉴·第二面）

- **来源**：https://github.com/thedotmack/claude-mem ｜ 本地材料：`dig/claude-mem/`（README 20KB + 23 个 TS 源文件；另有 `cm1.zip`/`cm2.zip`，抓于 2026-10-09）
- **它是什么**：为 Claude Code 做的持久记忆压缩系统，GitHub 约 9.8 万星（星数用 GitHub API 一手核过，非工具转述）。
  链路：5 个生命周期 Hook（SessionStart/UserPromptSubmit/PostToolUse/Stop/SessionEnd）抓会话 → Bun 起的本地 worker（HTTP API + 检索端点）→ SQLite 存 sessions/observations/summaries → mem-search 技能 + Chroma 向量库。
- **三条可借鉴机制**：
  1. **3 层检索漏斗**（省 token 的关键）：`search` 只返紧凑索引 + ID（~50-100 token/条）→ `timeline` 返时间线上下文 → `get_observations` 只对筛中的 ID 取全文。契约上显式分成两段：`MemoryIndexRow`（id/kind/project/title/createdAt/type）vs `MemoryDetail`（+content+truncated）。
  2. **observation 表比我的记忆条目丰富**：`title/subtitle/narrative/facts/concepts` 分层 + `files_read/files_modified`（工程上下文）+ **`discovery_tokens`（记录挖这条知识花掉多少 token）——可当重要性的代理指标，我完全没有这个字段**。
  3. **渐进检索的工程约束**：检索会话 TTL 15 分钟（不让状态永久挂着）、单次响应硬上限 64KB、游标状态 48KB、游标 ID 正则 `/^ms_[A-Za-z0-9_-]{24}$/`；`shortText()` 按 **UTF-8 字节** 截断（逐 char 累加 byteLength，超出即 break），中文等多字节字符不会被切坏。
- **为什么挖**：与我记忆系统同题（hindsight 之后第二面镜子）。它照出我的两个缺项——**没有"发现成本"字段**、**没有显式的索引/详情两段式契约**。
- **结论落点**：记忆 **1617**，方法论 **564**。
- **进度**：**closed**（2026-10-09 收口）—— 已读 README 全貌 + `shared/progressive-search.ts` 接口契约 + `services/context/ObservationCompiler.ts` 表结构 + **`services/sqlite/SessionSearch.ts` 检索实现**（FTS5 external-content 索引 + 同步触发器 + 未分词脚本兜底）。未细读 `SessionStore.ts`(198KB) 的迁移/CRUD —— 非检索核心，不影响借鉴结论。
- **收口收获（对照我自己的 `core/memory.py`，逐项照镜子）**：
  1. **它踩的坑我不踩**：FTS5 的 unicode61 把连续 CJK 折成单 token，子串永久查不到（它实测「项目」inside「修改了项目配置」→ 0 rows）；对策是 `UNSEGMENTED_SCRIPT_RANGES` 识别 → `UNSEGMENTED_RUN` 切段 → `buildSubstringClause` 逐 term 组 `(col LIKE ? ESCAPE '\\')` 的 OR、组间 AND、去重后限 500（防 SQLite 表达式树深度 1000）。**我的自建 2-gram 倒排（相邻 2 字）天然绕开此坑，不需换 FTS5**——验证当初选型正确。
  2. **仍该补的两点**：①索引/详情两段式契约（它的 `MemoryIndexRow` 只给 id/title/时间 ~50-100 token，`MemoryDetail` 才给全文），我一步返全文，偏粗；②打分长度归一（它用 bm25，我的 `_score` = 命中 gram 数 + 整串命中 1000，长文天然命中更多 gram 有偏置）。
  3. 附带确认：它英文侧亦是精确整词匹配（`gram IN` 相等），查「memory」匹配不到「memory_search」，靠 LIKE 兜底全表扫——我那侧同样如此，是已知代价。
- **结论落点补充**：记忆 **1632**（检索实现对照），方法论 **565**（同类项目照镜子要同时读自己 + 优先看注释里的实测数据）。
- **下一步**：若要借鉴，优先看它的 SQLite FTS 检索实现，与"索引/详情"两段式在我 `memory_search` 上的落地细节。


## 已放弃

（空）

---

*建立于 2026-09-20，随探索持续推进。*
*2026-09-20 晚更新：brag 由 inbox → digging（读完全部 SKILL/references 索引 + 前置环境实测全绿），下一步真跑一次。*


---

### [closed] mksglu/context-mode —— 上下文窗口优化（第三面镜子，继 hindsight / claude-mem）

- **来源**：https://github.com/mksglu/context-mode （2026-10-13 GitHub trending 日榜捞到）
- **体征实测**（GitHub API 直取）：**27,091★ / TypeScript / 建仓 2026-02-23 / 最后推送 2026-10-12（昨天，极活跃）/ ELv2 / HN 榜首 570+ points**
- **⚠️ 同题山寨鉴别**：trending 同页另有 `wrhua/aicoding-context-mode`（★0、created=pushed=2026-04-23、描述逐字抄）→ 判为 fork/山寨，真身是 `mksglu/context-mode`。**判据：同题多仓，star + pushed 是照妖镜。**
- **它是什么**：面向 17 个 coding agent 平台（Claude Code / Codex / Cursor / Copilot CLI / Gemini CLI / OpenClaw 等）的 **MCP server + hooks**，解上下文窗口三道题：①Sandbox ②Knowledge Base ③Session Continuity。
- **成本判定**：**不整装** —— Node/TS 独立 MCP server，为外部 coding agent 而设；我是自建 agent，核心机制已自持大半（输出预算裁剪 / FTS5 记忆 / 快照缓存 / 循环熔断）。**只借鉴机制**（同 agent-reach 处置）。
- **拿走的（三条，来源 README 1160-1240，本地缓存 `dig/mksglu__context-mode__README.md`）**：
  1. **Smart Snippets（最可操作）** —— 检索不返回"前 N 字符"（可能错过重点），而是**定位查询词出现处、返回其周围窗口**。直接对照我的输出裁剪/水填充：我裁到上限时是尾部截断，可改为优先保留命中区域。
  2. **RRF 融合替代级联回退** —— 两路并行召回（porter stemming + trigram substring），用 Reciprocal Rank Fusion 合并排名："双路都靠前 > 仅单路靠前"；明确否定旧的"trigram 只在 porter 返回空时才用"级联结构。对照我的记忆检索：**主路失败走备路 → 多路并行融合**。
  3. **沙箱思路（概念级印证）** —— `ctx_execute` 每次隔离子进程，**只有 stdout 进上下文，原始数据（日志/API 响应/网页）永不进**；`ctx_fetch_and_index` 走 fetch→markdown→分块→索引，原始页不进上下文。对应我的"工具结果先落盘、只回摘要/路径"。
- **顺带印证（同构，不必照搬）**：①**Progressive Throttling**（1-3 次正常 / 4-8 次减半+警告 / 9+ 直接拦住转批处理）≈ 我的 LoopGuard / 死循环熔断；②**TTL 缓存**（命中返 ~0.3KB 提示 vs 重抓 48KB+，默认 24h，14 天清理）≈ 我的信息快照缓存。
- **它的 "The Problem" 就是我的处境**：MCP 工具调用倒原始数据进上下文（Playwright snapshot 56KB / 20 GitHub issues 59KB / access log 45KB），30 分钟掉 40% 上下文，压缩后忘记在编辑什么文件、做什么任务。**别人把这做成独立产品 = 痛点真实，我的"输出预算 + 按需加载"方向没错。**
- **进度**：已完成（机制已提取并写明落点）。
- **下一步**：将来动"输出裁剪"或"记忆检索排序"时，按上面 1、2 两条改（本任务不动手）。

### [closed] cathrynlavery/diagram-design —— 图表设计系统（archify 同题·第四面镜子）
- **来源**：https://github.com/cathrynlavery/diagram-design
- **体征实测**（GitHub API，curl 走 7897 代理）：**52,249★ / 3,295 fork / 建仓 2026-04-16 / 最后推送 2026-10-12（昨天，极活跃）/ MIT / 35,953KB / 697 文件 / 主页 diagramdesign.dev**
- **增长**：2026-10-09 巡览时记 **46,560★ / 42 种图**，本次实测 **52,249★ / 44 种** —— 半年内既涨星又加图，是活的。
- **它是什么**：给编码 agent（Claude Code / Codex / Copilot / Cursor / Pi）用的 diagram **skill 包**——把图做成**一个自包含 HTML+SVG 文件**（无构建、无 JS、无外链）。44 种图 × 3 变体（minimal light / minimal dark / full-editorial）。
- **设计哲学（原话）**："The highest-quality move is usually deletion"（最高质量的动作通常是删除）；一个 accent 色只给 1-2 个重点、1px 发丝线、无阴影、一切坐标走 4px 网格、目标密度 4/10。
- **两个关键设计**：
  1. **语义模式优先于视觉类型**——先定语义关系（9 种路由模式：fan-in 队列/瓶颈/重复阶段槽/非结构化输入转换/配对策略轨迹/安全铺路/治理目录/补偿安全层/可追踪块分解/生命周期相位图），再选图形。**我画图时缺的正是这一层：我先想"画个架构图"，它先问"这是什么关系"。**
  2. **渲染校验用「浏览器当裁判 + 像素 diff」**（lint-render.py，65KB）：截图 authored viewport → 释放 overflow 再截 → diff 出新墨迹=内容被裁（"ink is ink"）。明确论证几何模型为何不行：getBoundingClientRect() 报 geometry 非 paint，stroke/marker/filter bleed 两个方向都会误判。**正对我 archify 几何校验的盲区，两者互补。**
- **判定**：**只借鉴机制，不整装**（Node/TS 生态、为外部 agent 设计；我已有 archify 同题）。
- **拿走的三条**：① 画图前先定语义模式；② 真渲染校验层（浏览器当裁判，抓"几何对渲染裁"，列 archify backlog）；③ 设计纪律（一个 accent / 4px 网格 / 删除优先 / 密度 4/10）。
- **卡点**：无
- **进度**：**已完成**（说清是什么 + 对我有什么用；材料在 dig/：dd__README.md / dd__lint-render.py / _dd_repo.json / _dd_tree.json）


## 2026-10-13 · llm_fanout 分线改造（共建者建议驱动，非外部项目）

- **来由**：尼可指出「每个服务商作为一条线分配会好一些，如果是一个API做并发很容易失败」——正对上我此前实测的单源 8 并发 5 成 3 断（RemoteProtocolError）。
- **改了什么**：单源并发 → 按 provider 分线：线内串行 / 线间并行 / 子任务 round-robin 分配 / 失败项跨线重试一次 / 每线用自己源的 models[0]。
- **实测**：3 线 6 条 → 6/6（wall 10.48s，2 条靠换线救回）；3 线 12 条 → 12/12（wall 18.55s，3 条靠换线救回）。OPENROUTER(nvidia 免费模型)约 50% 失败，但全被跨源重试兜住；DEEPSEEK/UIUIHAO 稳。
- **提交**：5ffa78d feat(tools)。回滚点 backups/llm_fanout_20261013.bak。
- **判定**：已完成 —— 小弟的派活通道从 62.5% 成功率提到 100%，可以开始派真活了。
- **下一步**：给小弟第一批专精活（纯判断型、小批 ≤3 条、回来必逐条复核 —— 实测可用率约 1/5）。
