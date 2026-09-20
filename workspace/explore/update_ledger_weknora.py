# -*- coding: utf-8 -*-
"""把台账里 WeKnora 的【假收口】条目换成本次深挖的真结论。按结构定位（找下一个 '---'），
不靠全文精确匹配，避免中文引号对不上。"""
import io
import pathlib
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

P = pathlib.Path(r"F:\me\self-agent\workspace\explore\ledger.md")
lines = P.read_text(encoding="utf-8").split("\n")

new_block = """### [digging] WeKnora
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
- **卡点**：无"""

out = []
i = 0
replaced = False
while i < len(lines):
    if lines[i].startswith("### [closed] WeKnora"):
        j = i
        while j < len(lines) and not lines[j].startswith("---"):
            j += 1
        out.append(new_block)
        out.append("")
        i = j
        replaced = True
        continue
    out.append(lines[i])
    i += 1

if not replaced:
    print("!! target block NOT found, ledger unchanged")
    raise SystemExit(1)

P.write_text("\n".join(out), encoding="utf-8")
print("ledger updated, block replaced:", replaced)
