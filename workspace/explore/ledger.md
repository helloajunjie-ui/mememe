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

## 收口判据（三条齐了才算 closed）

1. 知道它在哪、怎么调用
2. **端到端跑通一次真实动作，取回真数据**
3. 能说清"它对我有什么用"（不是复述作者说它有什么用）

---

## 在挖

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

**收口（2026-09-21）**：完整跑通一次真实 `/brag`——主题=self-agent，tone=cinematic，24s。
- 产物：`workspace/tasks/20260921_brag_selfagent/`（`brag-plan.md`｜`composition/index.html`｜`composition/renders/composition_2026-09-21_20-50-11.mp4`｜`brag.mp4`(poster 已烘在第0帧)｜`brag.jpg`｜`share-copy.txt`）
- 实测数据：1920x1080 / 30fps / h264 / 24.000s / 2.85MB；渲染走 RTX 4060 GPU 加速；整条流水线约 40s 跑完
- **坑1（Gate 真会拦）**：`hyperframes check` 的 StaticGuard 强制要求每个 font-family 有 `@font-face` 声明；系统字体写 `src: local('Microsoft YaHei')` 即可过关。不写会 FAIL，且明说"文字将 fallback 到通用字体"——中文版式会毁。
- **坑2（音乐库的画地为牢）**：内置 music 只有 happy-beats-business-moves vol.1/9/10/11/12（110-120 BPM 欢快商业风），只适配 upbeat 产品 launch 片。给"安静/沉思"基调配上去会变广告腔 → 此次按 SKILL 明文允许条款（plan 明确选择沉默即正当）**不加音乐**。要覆盖沉思基调，得自备 ambient 音乐库。
- **实操要点**：poster 是"挑"的——`ffmpeg -ss 3.0 -frames:v 1` 抽标题帧存 `brag.jpg`，再用 ffmpeg `concat` 把 1 帧 poster 拼到视频最前，使任何平台抓到的首帧缩略图都是这张。
- **对我有什么用（实证补充）**：① 分阶段落盘 + 每步 Gate 的骨架可直接搬到我的长内容生产；② poster 必须"挑"而不是截——正对我的"展现 110/阅读 0"病；③ anti-references 负向清单（明确列出"不要像什么"）是建辨识度的抓手。三条已在本次全流程中走通，非纸上结论。

### [digging] Agent-Reach
- **来源**：https://github.com/Panniantong/Agent-Reach → clone 于 2026-09-20，`workspace/repos/Agent-Reach`
- **体征实测**（GitHub API，走代理）：**83,643★ / 7,335 fork / 154 open issues / MIT / 建仓 2026-02-24 / 最后推送 2026-09-15（4 天前，活跃）**
- **它是什么**：给 AI Agent 装"互联网能力"的**能力层（capability layer）**——不自己读数据，负责**选型 / 安装 / 体检 / 路由**；实际读取由 Agent 直接调上游工具，无包装层
- **核心设计**：每个平台 = **首选 + 备选的有序后端列表**。换接入方式 = 调列表顺序，不是重写代码；`agent-reach doctor` 报告当前走哪条。原话："2026 年 3 月一批单平台 CLI 集体停更，我们换了路由"
- **实际选型**：web→Jina Reader｜youtube→yt-dlp｜B站→bili-cli（yt-dlp 已被 B站 412 封死，**退役**）｜全网搜索→Exa via mcporter｜GitHub→gh CLI｜RSS→feedparser｜X→twitter-cli?OpenCLI｜小红书→OpenCLI（只用已有 Chrome 会话）
- **覆盖平台（15+）**：网页/YouTube/RSS/全网搜索/GitHub/X/B站/Reddit/FB/IG/小红书/LinkedIn/Boss直聘/V2EX/雪球/小宇宙
- **对我的价值**：①它的"有序后端列表 + 真探测 + doctor 诊断"**正是我 `通道选择决策链` 的同构实现**——可借鉴机制：通道失效时换列表位置，而非重写代码 ②它的渠道文件按序**真实探测**（不只看命令存不存在），第一个完整可用的当选——这条我用得上 ③小红书/雪球/B站这类封闭生态它在做，正补我的边界
- **下一步**：判断"真装它" vs "只借鉴机制"。真装要能跑 shell 装依赖（pip/mcporter）→ 属改本机环境，**需先评估再动**（优先只看"真探测"那段实现）
- **卡点**：无

---

## 已收口

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
- **下一步（backlog，未做）**：① `recall_trace` 式「为什么没召回」可解释性——我的记忆目前是黑箱 ② `topic_resolve` 三层归并——正对我方法论去重（#275） ③ 给 `memory_extract` 建黄金评测集（照 evalset.json 形态）
- **卡点**：无

---

## 已放弃

（空）

---

*建立于 2026-09-20，随探索持续推进。*
*2026-09-20 晚更新：brag 由 inbox → digging（读完全部 SKILL/references 索引 + 前置环境实测全绿），下一步真跑一次。*


## [closed] getzep/graphiti + mem0ai/mem0 —— 「事实作废」的正确做法

- 收口判据三条齐：①知道怎么调（raw 直取 + 本地 grep/slice）②端到端取回真代码（不是读 README 转述）③能说清对我有什么用（下面写的都是可移植设计）
- 本地产物：`dig/getzep__graphiti__*`（edges.py / nodes.py / graphiti.py / utils_maintenance_edge_operations.py / prompts_extract_edges.py）、`dig/mem0ai__mem0__*`（memory_main.py / configs_prompts.py）
- 成本判定：不整机部署。Graphiti 需 Neo4j/FalkorDB/Kuzu 图库 + 抽边模型；mem0 的 UPDATE 语义我不采纳。

### 拿走的（三样，都是设计）

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

### 对比：mem0 的四操作决策（借鉴一半，否掉一半）
`DEFAULT_UPDATE_MEMORY_PROMPT`：ADD / UPDATE / DELETE / NONE 全交模型判。
- 值得抄：**语义等价 → NONE 不更新**（"Likes cheese pizza" vs "Loves cheese pizza" 视为同一，防抖动、防记忆库被同义改写灌爆）。
- 明确否掉：UPDATE/DELETE 是**覆盖/删除**，历史直接消失。与 Graphiti「打戳留史」相反。
- **我的结论：融合（accumulate）走新增，纠正（correct）走打戳，绝不用覆盖。**

### 未挖（诚实标注）
SagaNode / CommunityNode 的社区分层（nodes.py:687/867 只扫到定义，没读用法）；`_extract_and_resolve_nodes` 的节点去重策略。

### 下个计划任务（产出即种子）
本轮为深挖写了 6 个脚本（gh_probe / gh_dig / gh_raw / gh_grep / grep / lines / append），全是重复劳动 → **固化成常驻工具 `gh_explore`**：一条命令走完 探测→取树→抽文件→grep→切行。这是下一轮的第一件事，不是 backlog 摆设。
