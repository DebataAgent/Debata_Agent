<div align="center">

# Debata_Agent

### **或许，`/v1/chat/completions` 后面的，是个人？**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![WebUI](https://img.shields.io/badge/UI-WebUI%20%28FastAPI%29-blueviolet.svg)](#)
[![Tests](https://img.shields.io/badge/tests-1697%20passed-brightgreen.svg)](#测试与验证)

</div>

---

> 「虚拟角色不只是个会回复消息的程序，而是一个**真实的，能和你聊天的，有个性、有内心的**人。」

Debata_Agent 不绑定任何特定角色。你给它一个人格，它就活成那个人。
框架名取自项目第一个被实现的角色 Debata。

<div align="center">
<img src="personas/debata/角色人设主图.png" width="600" alt="Debata 人设图">

*Debata —— 17 岁，短黑发，灰色卫衣。对亲近的人毒舌，但心里十分在意。*
</div>

## 目录

- [与一般 AI 机器人的差异](#与一般-ai-机器人的差异)
- [2026-10 Token 效率重构](#2026-10-token-效率重构)
- [核心能力](#核心能力)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [后台运行](#后台运行)
- [配置与数据目录](#配置与数据目录)
- [项目结构](#项目结构)
- [测试与验证](#测试与验证)
- [文档](#文档)
- [设计决策](#设计决策)
- [安全与隐私](#安全与隐私)
- [License 与致谢](#license-与致谢)

---

## 与一般 AI 机器人的差异

| 维度   | 一般 AI 机器人 | Debata_Agent |
|------|------------|--------------|
| 上下文  | 每个对话独立     | **全会话统一时间线 + 状态/关系上下文** |
| 回复   | 一条长消息      | 拆成 3-7 条短消息，60% 不超过 12 字 |
| 节奏   | 永远秒回       | 可以「没看到」「刚下课」「隔几小时再回」 |
| 边界   | 「好的！」      | 「？」「嗯」「6」「算了」 |
| 风格   | 对所有人一样     | 对长辈 / 死党 / 陌生人 / 群友自动切换 |
| 主动性  | 被动回复       | 自主判断要不要开口 |
| 记忆   | 只记最近几条     | 重要记忆 + 本地归档 + 可选 RAG 向量召回 |
| 配置   | 改代码        | 浏览器向导 + 设置页即时保存 |

## 2026-10 Token 效率重构

这一轮重构的重点不是换语言，而是**把 token 花在真正有用的上下文上**。
换 Go/C# 不会减少 LLM 的账单；减少重复的旧运行时快照、压缩工具 schema、避免多余的模型轮次才会。

基于真实实例的只读基准（`scripts/benchmark_prompt_tokens.py`）：

| 指标 | 重构前 | 重构后 | 变化 |
|---|---:|---:|---:|
| 活跃历史 token | 185,173 | 43,248 | **-76.6%** |
| 工具 schema token | 9,817 | 4,740 | **-51.7%** |
| 估算单次 prompt | 206,768 | 59,766 | **-71.1%** |
| 模型可见记录 | 568 | 296 | -47.9% |

主要改动：

- **非破坏性模型视图**（`core/model_view.py`）：只裁剪发给模型的历史视图，历史、归档、事件库原样保留。旧 `task_context`、发送回执、`no_action` 配对不再无限堆积。
- **工具 schema 分级**：默认 `compact`，高频工具直出核心字段，低频/高风险工具为 stub，按需 `tool_search detail=full`。
- **发送成功后自动收尾**：不再为了一次收尾额外调用 `no_action`，减少模型轮次。
- **Token 计量更准**：规范 JSON 估算 messages + tools，按 provider/model 持久化校准系数，用量面板显示未命中输入和估算误差。
- **运行时更轻**：KV 诊断默认轻量模式；文本编码、工具 schema、工具诊断都有缓存。

配置开关和回滚方式见 [配置与数据目录](#配置与数据目录) 与 [架构总览](docs/architecture.md)。

## 核心能力

- **真人聊天形态**：短句瀑布、拆条发送、不打句号、不机械告别；允许跑题、改口、忘事。
- **关系矩阵**：同一个人格，对死党、长辈、陌生人、群友使用不同语气和边界。
- **人格状态与后台心智**：年龄档位、精力、饱腹、心情、社交需求；睡醒/进食结束触发恢复评估，失败时公式兜底。
- **结构化上下文**：事件、聊天现场、状态、用户画像、短期影响、线索、待办和相关长期记忆参与聊天决策。
- **长期记忆**：文件模式零配置；RAG 模式后台建立向量索引，按语义召回相关历史。
- **14 个内置 Provider 预设**：OpenAI / Anthropic Claude / DeepSeek / 智谱 GLM / 通义千问 / 火山方舟 / Gemini / Moonshot / 硅基流动 / OpenRouter / Groq / Together / xAI / 小米 MiMo；也可接任意 OpenAI / Anthropic 兼容中转站。
- **多 Agent 分工**：主聊天、主动思考、历史总结、人格管理、社交决策、潜意识整理可分别绑定不同模型。
- **WebUI**：首次配置向导 + 9 个页面（总览 / 对话 / 记忆 / 人格后台 / 日志 / 角色 / 模型管理 / 黑名单 / 设置），设置即时保存。
- **可选能力**：视觉理解、TTS、天气、联网搜索、本地 VoxCPM2 / sentence-transformers 插件；QQ 语音转写走 NapCat 内置能力。
- **安全**：API 密钥使用 AES-256-GCM + RSA-2048 加密，优先存系统 keyring；WebUI 默认令牌鉴权。
- **后台运行**：systemd 用户/系统服务、tmux、screen、nohup 四套方案，含一键安装脚本。

## 环境要求

- **Python** 3.11+（推荐 3.12）
- **uv**（依赖与虚拟环境管理）
- **NapCat**（QQ 协议端，[安装指南](https://napneko.github.io/guide/start-install)）
- **LLM API Key**（推荐 DeepSeek：中文好、价格低、缓存命中后便宜）
- Windows / macOS / Linux 均可；服务器可以纯命令行运行

## 快速开始

### 1. 获取代码并安装依赖

```bash
git clone https://github.com/binglang001/Debata_Agent.git
cd Debata_Agent

uv sync
```

如果使用 SSH：

```bash
git clone git@github.com:binglang001/Debata_Agent.git
cd Debata_Agent
uv sync
```

### 2. 启动 WebUI

```bash
uv run main.py
```

终端会打印访问地址和访问令牌：

```text
================================================================
  Debata_Agent WebUI 已启动
================================================================
  监听        : 0.0.0.0:8000
  访问地址    : http://127.0.0.1:8000
  访问地址    : http://192.168.1.23:8000
  访问令牌    : xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
================================================================
```

浏览器打开任一地址，首次启动会自动进入配置向导：

1. 选择主模型（14 个内置预设，或自定义兼容端点）
2. 选择/导入人格
3. 配置 NapCat 连接与入站策略
4. 打开主动思考 / 滚动摘要 / 人格生成等后台 Agent

### 3. 常用参数

```bash
uv run main.py --port 9000          # 换端口
uv run main.py --host 127.0.0.1     # 只允许本机访问
uv run main.py --token 我的固定令牌   # 固定 WebUI 令牌
uv run main.py --no-auth            # 关闭鉴权（仅完全可信内网）
uv run main.py --no-gui             # 纯命令行，不启动 WebUI
uv run main.py --no-gui --setup     # 纯命令行配置向导
uv run main.py --napcat             # 只重新配置 NapCat
uv run main.py --test-adapter       # 测试 NapCat 连接 5 秒后退出
```

## 后台运行

一条命令安装并启动，之后用 `debata` 管理：

```bash
cd ~/Debata_Agent
bash scripts/install_systemd_user.sh     # 一次性安装并启动

debata start       # 启动（首次会自动安装服务）
debata stop        # 停止
debata log         # 实时查看日志
debata enable      # 开机自启（并立即启动）
debata disable     # 取消开机自启
debata status      # 运行状态
debata restart     # 重启
```

- `debata enable` 会尝试开启 linger；如果提示失败，执行 `sudo loginctl enable-linger "$USER"`。
- 没有 systemd 用户会话（容器 / WSL / 精简系统）时，会自动切换为内置后台模式：`start/stop/restart/status/log` 仍可用；`enable` 会启动后台进程，但无法设置开机自启，请使用容器重启策略或 cron `@reboot`。
- 默认同时启动 WebUI 和 QQ Runtime；只跑 headless 用 `bash scripts/install_systemd_user.sh --headless`。
- 如果 `debata` 不在 PATH，用 `./debata start`。
- NapCat 是独立进程，需要单独保持运行。

完整说明（系统级服务、tmux / screen / nohup、升级、令牌、排错、安全建议）见
**[后台运行指南](docs/background_running.md)**。

## 配置与数据目录

默认数据目录：

```text
~/.local/share/Debata_Agent/instances/default/
├── config.yaml          # 主配置（gitignore）
├── secrets.enc          # 加密后的 API Key
├── logs/debata.log      # 应用日志
├── memory/              # 人格历史、重要记忆、用量记录
├── vector/              # RAG 向量库
├── workspace/           # AI 可读写的工作目录
├── providers/presets/   # 用户自定义 Provider 预设
└── webui_token.txt      # WebUI 访问令牌
```

兼容模式（项目根目录存在 `.debata-dev-data-root`）下仍使用项目内的 `data/`；
也可以用 `DEBATA_DATA_ROOT` 指定数据根。旧数据首次启动会自动迁移。

常用配置（WebUI → 设置）：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `behavior.context.max_context_tokens` | 实例配置 131072 | 工作上下文预算，不是模型硬上限 |
| `behavior.summarize.trigger_at_context_percent` | 50 | 活跃视图达到预算 50% 时触发滚动摘要 |
| `behavior.summarize.target_after_context_percent` | 30 | 压缩后的活跃窗口目标 |
| `behavior.tool_schema_profile` | `compact` | 工具 schema 档位；`full` 为旧行为，可回滚 |
| `behavior.context.model_view.enabled` | `true` | 非破坏性模型视图；关闭后恢复完整历史 |
| `behavior.auto_finish_after_send` | `true` | 发送成功后自动结束本轮工具循环 |
| `behavior.kv_prompt_diagnostics` | `summary` | KV 诊断级别；`full` 用于排查缓存问题 |

## 项目结构

```text
Debata_Agent/
├── adapters/               渠道适配器（NapCat）
├── providers/              LLM 提供商、14 个预设与能力矩阵
├── agents/                 主聊天 / 主动思考 / 总结 / 人格管理 / 社交 / 潜意识
├── mind/                   人格状态、用户画像、关系、线索、待办、审计
├── memory/                 历史、重要记忆、归档、RAG、用量存储
├── tools/                  AI 工具、schema 分级、结果压缩
├── features/               视觉 / TTS / 天气 / 搜索 / Embedding
├── core/                   事件总线、消息管道、Runtime、模型视图、Token 校准
├── app_config/             配置、路径、密钥加密
├── webui/                  WebUI（FastAPI 后端 + 静态前端）
├── plugins/                本地模型插件（VoxCPM2 / embedding）
├── utils/                  CQ 解析、token 估算、代理、时间
├── personas/               人格目录（debata 入 git，个人人格 gitignore）
├── scripts/                基准脚本、systemd 安装脚本与模板
├── docs/                   教程、架构、后台运行与开发文档
├── tests/                  单元 / 集成测试
└── main.py                 入口（默认启动 WebUI）
```

## 测试与验证

```bash
# 安装开发依赖
uv sync --extra dev

# 全量测试（排除需要真实 API Key 的 live 测试）
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_kv_cache_real.py

# 代码检查
.venv/bin/ruff check .
```

当前本地验证结果：**1697 passed**，`ruff check .` 通过（Linux / Python 3.12）。
CI 矩阵定义在 [.github/workflows/test.yml](.github/workflows/test.yml)（Ubuntu + Windows，Python 3.11 / 3.12）。

Prompt token 基准（只读，不调用模型）：

```bash
.venv/bin/python scripts/benchmark_prompt_tokens.py \
  --db ~/.local/share/Debata_Agent/instances/default/memory/小堡/小堡.db \
  --persona 小堡 --model deepseek-flash
```

## 文档

- [图文教程](docs/getting_started.md) — 从零开始，截图指引每一步
- [后台运行指南](docs/background_running.md) — systemd / tmux / screen / nohup
- [功能列表](docs/features.md) — 真人聊天、记忆、人格、WebUI
- [角色编写指南](docs/persona_writing_guide.md) — 怎么写出会「活」的人格
- [架构总览](docs/architecture.md) — 模块依赖、模型视图、Token 计量与工具 schema
- [KV 缓存基准](docs/kv_cache_benchmark.md) — 真实命中率与 2026-10 重构后的估算
- [提供商开发](docs/provider_development.md) — 接新的 LLM
- [适配器开发](docs/adapter_development.md) — 接新的聊天平台
- [WebUI 说明](docs/webui.md) — 页面结构、接口一览、二次开发
- [UI 风格指南](docs/ui_style_guide.md) — 设计原则与配色规范

## 设计决策

几条不打算妥协的事：

- **单一 system + XML 分区**：稳定区前置，让 KV 缓存命中率更高，省 token + 省延迟。
- **易变内容放历史之后**：`task_context`、长期记忆、滚动摘要、RAG 都追加在 append-only 历史之后，避免作废整段前缀缓存。
- **模型视图与落盘历史分离**：旧运行时快照可以不出现在 prompt 里，但绝不删除原始历史/归档。
- **工具分级而非全量暴露**：高频工具直出，低频工具按需 `tool_search`，降低固定 schema 开销。
- **「真人不完美」即合法**：跑题、改口、忘事不是 bug，是体感。
- **加密密钥不要密码**：密钥存 OS keyring + AES-GCM，密码学保证强度。
- **常用配置尽量 UI 可改**：设置页即时保存；Persona 文件仍然保留手写空间。
- **黑名单优于白名单**：不想让 AI 接话的人点名拉黑即可，不必收紧整体开放策略。

## 安全与隐私

- `config.yaml`、`secrets.enc`、`data/`、非 debata 人格、日志、workspace 均被 `.gitignore` 排除，不会进入仓库。
- WebUI 默认启用令牌鉴权；`--no-auth` 只适合完全可信的内网。
- 公网访问请使用 Nginx / Caddy 反向代理 + HTTPS，或只监听 `127.0.0.1`。
- 系统级服务请使用专用用户运行，不要用 root 跑长期服务。
- 聊天记录、人格、配置属于个人数据；迁移或分享前请先备份并脱敏。

## License 与致谢

本项目由 [binglang001/Debata_Agent](https://github.com/binglang001/Debata_Agent)
及其贡献者共同维护，保留原始版权与协议声明。

[Apache 2.0](LICENSE) — 自由商用 / 修改 / 分发，保留版权与协议声明即可。

致谢：

- [NapCat](https://github.com/NapNeko/NapCatQQ) — 现代 QQ 协议端
- [FastAPI](https://fastapi.tiangolo.com/) — WebUI 的 HTTP / WebSocket 框架
- [Uvicorn](https://www.uvicorn.org/) — ASGI 服务器
- [uv](https://github.com/astral-sh/uv) — Python 依赖与虚拟环境管理
- 所有提交 Issue / PR 以及参与测试的贡献者

---

如果这个项目对你有用，欢迎给项目一个 Star，也欢迎提交 Issue / PR。
