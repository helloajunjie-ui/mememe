# 白绫（Bailing）· 自我完善 AI 智能体

一个拥有自我认知、长期记忆与工具自举能力的 AI 智能体框架。克隆本仓库后，初始化即得到一个**独属于你的白绫实例**——她的记忆、方法论与自我状态只属于你，绝不随本仓库分发。

## 这是什么

白绫不是"聊天机器人"，而是一个**能持续完善自己的智能体**：

- **自我认知**：首次运行记录自我、检查环境、保存状态（我是谁 / 我在哪 / 我有什么工具）
- **长期记忆**：`memory.db` 记住事实，`methodology.json` 沉淀方法论（什么方法有效 / 什么坑要避免）
- **工具自举**：内置工具不足时，可自己创建工具、从网络获取工具并注册复用
- **死循环检测**：确定性规则熔断思考/执行死循环，不浪费 token
- **网络策略**：跟随本机代理访问外网（不搞对抗），多通道下载、环境快照省时省 token
- **自我备份**：`self_backup / self_restore / self_clone` 支持复活与迁移

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/helloajunjie-ui/mememe.git
cd mememe

# 2. 配置 API Key（OpenAI 兼容接口，如 DeepSeek）
#    创建 .env 文件，写入：
#    BAILING_API_KEY=sk-xxxx

# 3. 安装依赖并运行
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv/bin/pip install -r requirements.txt                     # Linux/macOS

# 4. 启动
.venv\Scripts\python.exe main.py            # 交互模式
.venv\Scripts\python.exe main.py --check    # 环境/工具自检
.venv\Scripts\python.exe main.py --task "帮我看看当前目录"   # 单次任务
```

> 默认 LLM 端点：`https://api.deepseek.com`（OpenAI 兼容），可在 `config.yaml` 切换任意 OpenAI 兼容服务（含本地 Ollama）。

## 能力概览

| 能力 | 说明 |
|------|------|
| 24+ 内置工具 | 文件/网络/命令/记忆/方法论/工具管理/环境探查 |
| 工具自举 | `tool_create` 自写工具 → 校验 → 注册复用 |
| 工具获取 | `tool_acquire` 国内节点优先（gitee/gitcode/github 镜像） |
| 死循环检测 | LoopGuard：重复调用/连续失败/空转三类信号，soft 提示 + hard 熔断 |
| 跟随代理 | 用户开代理时直接走 `127.0.0.1:<端口>`（如 7897），不搞对抗 |
| 自我备份 | `self_backup` 全量/轻量备份 → `self_restore` 复活 → `self_clone` 迁移 |
| 环境快照 | 高频低变信息快照缓存，省时省 token |
| 阶段化执行 | 任务分阶段记录存档，断点可续接、可复盘 |

## 目录结构

```
mememe/
├── main.py                # 入口（交互/单次任务/自检）
├── config.yaml            # 配置（LLM 端点、沙箱参数）
├── core/                  # 核心：agent 主循环、记忆、方法论、注册表、人性层
├── tools/                 # 工具框架 + 内置工具源码（python/go）
├── data/persona.yaml      # 人格基座（通用，随仓库分发）
├── 设计文档.md             # 完整设计与迭代记录
└── .gitignore             # 隔离实例私有数据
```

## 隐私与数据边界

- **本仓库只含程序框架**（核心代码/工具/人格基座/设计文档）。
- **实例私有数据不入库**：`memory.db`（记忆）、`self.yaml`（自我状态）、`methodology.json`（你的白绫沉淀的方法论）、`snapshots.json`（环境快照）等由 `.gitignore` 隔离，仅在本地生成。
- 每个 clone 都是独立的她——记忆互不相通，隐私互不泄露。

## 设计哲学

效率优先（手头有什么用什么）· 现有优先（先查本机已有工具）· 工具先测可用再进名单 · 必需则修不必要则换 · 止损不执着（反赌徒效应）· 环境快照先行 · 交付前回读 · 如实标注边界（反营销/性能透明）。

## 运行环境

- Python 3.13+（兼容 3.10+）
- 网络：需可达 LLM API；海外站点受限时跟随本机代理
- 平台：Windows / Linux / macOS（优先系统自带命令）
