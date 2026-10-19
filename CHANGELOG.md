# Changelog

本文件记录 Debata 所有重要变更，格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] — token 效率重构

### Added
- 新增非破坏性“模型视图”（`core/model_view.py`）：只裁剪发给模型的历史视图，历史/归档/事件库原样保留。默认只保留最近 2 个 `task_context`、2 条发送回执、2 条发送完成快照和 4 组 `no_action`。
- 新增按 provider/model 持久化的 token 校准（`core/token_calibration.py` + 实例目录 `token_calibration.json`），重启后不再退回 1.0。
- 新增 `scripts/benchmark_prompt_tokens.py`：只读对比重构前后的活跃历史、系统提示、工具 schema 与估算 prompt。
- 新增工具 schema `compact` 档位：核心字段直出，高级字段通过 `tool_search detail=full` 查看；默认 11 个 compact + 其余 stub。
- 新增发送成功后自动收尾（`behavior.auto_finish_after_send`），省掉一次 `no_action` 收尾模型调用。
- 用量面板新增“未命中输入 token / 估算误差 / 估算样本”。
- 新增 [后台运行指南](docs/background_running.md)：systemd 用户/系统级服务、tmux、screen、nohup、升级与排错。
- 新增 `scripts/install_systemd_user.sh` 一键安装脚本和 `scripts/systemd/` 单元模板。
- 新增简化服务命令：`debata start` / `stop` / `restart` / `status` / `log` / `enable` / `disable`，底层自动调用 systemd；首次 `debata start` 会自动安装服务。

### Changed
- README 中英文重写：补充 Token 效率、后台运行、数据目录、测试与安全说明；clone 地址更新为本项目仓库。
- 更新 getting_started / features / architecture / kv_cache_benchmark，加入模型视图、compact 工具、自动收尾与 2026-10 基准数据。
- 滚动摘要触发/目标默认从 75%/50%/30% 调整为 50%/30%/20%，并按“模型可见视图 + 固定开销”口径计算；128k 工作预算需要在设置页或实例配置中显式设置。
- KV 诊断默认改为 `summary` 轻量模式，不再每轮对完整消息与工具集做多次全量 JSON 序列化。
- 工具 schema 移除通用 `finish_after_success` 参数；执行层仍兼容旧调用。
- 发送类工具结果不再回显已发送正文，只保留 msg_id/order/状态等定位信息。
- token 估算改用规范 JSON（messages + tools）口径，并为小文本与工具 schema 增加缓存。

### Fixed
- 修正 `str(tools_schema)` 造成的工具 schema token 估算偏差。
- 修正 `cached_tokens > prompt_tokens` 等脏数据导致缓存命中率超过 100% 的问题。
- 避免旧运行时快照触发压缩、以及总结 Agent 重复读取旧快照的问题。
- 修正 `debata start/stop/restart` 中 systemctl 参数顺序错误（服务名必须在命令之后）。
- 修正端口被占用时启动崩溃的问题：现在会提示“可能已在运行”，并给出 `debata status/log` 与换端口命令。
- 修正 WebUI “对话”页加载超时：归档/事件改为一次性快速读取，会话分组去掉 O(n²) 扫描，合并结果增加缓存，大群对话先截取尾部窗口再归一化，前端请求超时提升到 60 秒。
- 修正真实数据下“对话”页大群（8,889 条记录）无法正常显示的问题。
- 修正没有 systemd 用户会话（容器 / WSL / 精简系统）时 `debata` 命令全部失败的问题：现在自动回退到 PID 文件 + 独立后台进程，`start/stop/restart/status/log` 仍可用；`enable` 会启动后台进程并说明开机自启的限制。
- 修正安装脚本在 `systemctl --user` 无法连接 bus 时直接报错退出的问题：现在检测用户 bus，跳过 unit 安装并启动内置后台模式。

### Measured
- 真实实例活跃历史：185,173 → 43,248 估算 token（-76.6%）。
- 工具 schema：full 档位 9,817 → compact 档位 4,740 token。
- 估算单次主 prompt：206,768 → 59,766（-71.1%）。
- WebUI 对话页：归档加载 15.5s → 1.9s；分组 183s → 1.1s；大群展示首次 5.27s → 0.74s（隔离基准）。

## [v0.9.0-alpha] — 2026-07-04

### Added
- PersonaAgent 新增对话后更新、周期维护、睡眠开始、进食开始、睡醒/进食结束结算和启动离线对账链路，让人格状态可以围绕真实运行事件持续推进。
- PersonaContextView / PersonaEpisodeBuffer 落地，向 PersonaAgent 提供事件、聊天现场、当前状态、对象画像、画像审计、短期影响、线索、待办和相关长期记忆组成的结构化上下文。
- episode buffer 增加明确边界：会话切换、闲置超时、进入或离开吃饭/睡觉/崩溃动作、片段数上限和字符数上限都会打开新 episode，避免上下文无限追加。
- 人格更新审计扩展到状态、短期影响、待办、线索、用户画像和关系更新，记录触发来源、会话、用户、前后快照和变更摘要。
- mind SQLite schema 升级到 version 2，补齐 persona_update_audits、人格状态、状态日志、用户画像、短期影响、待办、线索、睡眠记录、进食记录、轨迹和长期重要记忆等存储。
- 睡醒/进食结束时支持模型恢复评估；模型失败或离线对账场景使用公式兜底，并在日志中记录 recovery_source。
- 周期维护会清理过期短期项、线索和待办，过期待办会标记为 missed，并触发日常状态整理。
- 待办、线索和短期影响支持基于 ID 的创建、更新、关闭/删除；未知 ID 的更新会被审计为丢弃，避免错误写入。
- 用户画像和关系更新接入 PersonaAgent，支持好感、摘要、特征、互动次数和最近互动时间的持续维护。
- 人格后台页新增运行概况、实时状态、背景动向、状态日志、短期影响、待办/线索、用户画像、睡眠/进食和整理轨迹查看。
- 设置页新增人格管理开关、当前人格年龄覆盖、精力/饱腹模式、兜底恢复/衰减参数，以及 Persona / Social / Subconscious Agent 的后台配置入口。

### Changed
- 项目命名与数据存储从 Diana 收敛为 Debata，运行时数据库改为 `memory/<persona>/<persona>.db`。
- 人格上下文从零散状态文本扩展为结构化上下文视图，聊天现场改为有边界 episode，不再依赖无限追加的片段。
- 年龄相关描述以年龄档位、年龄提示和当前人格年龄覆盖为准，不再表达为自动年龄增长。
- 睡眠和进食恢复从“动作开始即同步恢复”调整为动作结束或离线结算时评估，并保留公式兜底来源。

### Fixed
- 本地 RAG 缺少 `sentence-transformers` 时改为启动后台依赖安装并提示重启生效，避免启动流程直接失效。
- GUI 模式启用 qasync timer 多路复用，并收敛 wakeup、NapCat 和 proactive 后台循环的重复启动风险，降低 Windows 原生 timer/句柄耗尽概率。
- 修复 PersonaAgent 相关 JSON 输出兼容性问题：复数字段稳定按数组解析，同时兼容 profile、relationship、effect、todo、cue 等单条旧字段。
- 修复短期影响、待办、线索更新时缺少 ID、引用未知 ID、空内容和重复待办导致的数据漂移问题。
- 修复待办状态兼容性问题，统一 complete / done / cancelled / deleted / missed 等状态归一和关闭判断。
- 修复进食记录缺少 record_id 的兼容迁移问题，为旧记录补齐可更新 ID。
- 收敛画像、关系、短期项、审计记录与结构化上下文之间的数据一致性问题。

## [v0.8.5-alpha] — 2026-06-11

### Added
- Linux/CLI 启动配置完善，补齐无头环境启动、路径与密钥处理链路
- 上下文压缩与工作窗口机制落地，提升长对话稳定性和上下文预算控制
- 聊天归档与实时聊天能力增强，私聊和群聊均可进行本地归档总结
- QQ/NapCat 适配与发送工具扩展，覆盖消息发送、回执处理和平台侧历史摘要
- RAG、长期记忆、工具预算与仪表盘能力整合，支持记忆注入、预算观测和图形化管理

### Changed
- 发布状态整理为 v0.8.5-alpha
- 启动配置、上下文管理、聊天工具、记忆系统和仪表盘相关变更按当前发布面重新归类

### Fixed
- 修正 Linux/CLI、NapCat 连接、发送回执、上下文压缩和仪表盘启动链路中的近期问题

## [v0.2.0-alpha] — 2026-05-24

### Added
- 289 项集成测试（pytest + pytest-asyncio）
- KV 缓存命中率实测验证
- NapCat 心跳/重连配置项（`ping_interval_seconds` 等）

### Changed
- `WhitelistConfig.mode` 值 `"all"` 改为 `"open"`
- `ProtocolType` 收窄为 `Literal["openai_compat", "anthropic"]`

### Removed
- V1 兼容代码全部清理（migrate / detect_legacy / `TOOL_USE_PROTOCOL` 别名等 11 处）

## [v0.1.0-alpha] — 2026-05-24

### Added
- V2 全栈重构：Core / Providers / Agents / Adapters / Tools / Memory / Features / AppConfig 七大模块
- NapCat OneBot V11 适配器（WebSocket 直连）
- 多 LLM 提供商：OpenAI 兼容协议 + Anthropic 原生协议
- 工具系统（消息/记忆/平台/控制/功能），Pydantic schema 自动派生 OpenAI tool 格式
- AES-256-GCM + RSA-2048 + keyring 三级加密配置管理
- 消息管线：合并窗口 + 中断检测 + 工具循环 + 总结
- 主动思考定时循环（proactive_loop）
- 人格系统（persona prompt + persona_gen_agent）
- 滑动窗口速率限制 + 好友/群申请验证流程
