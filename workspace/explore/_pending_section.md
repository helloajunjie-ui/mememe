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
