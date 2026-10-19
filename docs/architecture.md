# 架构总览

Debata_Agent 各模块职责与依赖关系。

---

## 设计原则

1. **依赖倒置**——所有跨模块边界用抽象接口（IAdapter / IProvider / ITool 等）
2. **依赖注入**——组件不持有全局状态，都通过构造器传入
3. **单一职责**——每个模块只做一件事
4. **可测试**——所有组件能用 fake 替身单独测试

---

## 数据流

```
用户消息
  ↓
IAdapter（NapCat WS / Discord / ...）
  ↓
EventBus（按事件类型分发）
  ↓ ↓ ↓
MessagePipeline / RecallHandler / RequestHandler
  ↓
（合并窗口 / 中断检测）
  ↓
ChatAgent.run(messages, tools, executor)
  ├─ IProvider.chat_completion() → 多轮工具循环
  └─ ToolRegistry.executor → 工具执行（按配置启用）
       ├─ messaging tools → MessagePipeline 异步发送队列
       ├─ memory tools → ImportantMemoryManager（scope / pinned 元数据）
       ├─ platform tools → IAdapter.list_friends / get_user_info / ...
       └─ feature tools → VisionService / WebSearchService / ...
  ↓
MessagePipeline.SendManager（真实发送 + 中断检测 + send_receipt）
  ↓
IAdapter.send_text() → 用户收到消息
```

---

## 模块矩阵

| 模块 | 依赖 | 暴露 | 测试覆盖 |
|------|------|------|---------|
| `app_config` | - | `RootConfig`, `AppPaths`, `SecretsManager` | ✅ |
| `adapters` | `app_config` | `IAdapter`, `NapCatAdapter`, `Target`, `IncomingMessage` | ✅ |
| `providers` | - | `IProvider`, `build_provider()`, 13 个预设 | ✅ |
| `memory` | - | `HistoryManager`, `ImportantMemoryManager` | ✅ |
| `agents` | `providers`, `memory`, `app_config` | `ChatAgent`, `Persona`, `build_messages()`, prompts | ✅ |
| `tools` | `adapters`, `memory`, `providers`, `app_config` | `ToolRegistry`, `ToolContext`, `build_default_registry()` | ✅ |
| `core` | 上面全部 | `Runtime`, `MessagePipeline`, `EventBus` | ✅ |
| `features` | `providers` | `VisionService`, `WebSearchService`, `WeatherService`, `IEmbeddingService`, `ITTSService` | ✅（Vision/WebSearch/Weather/Embedding 已实装；TTS 支持 Edge TTS / iFlyTek API / 本地 VoxCPM2 三种实现；QQ 语音转写走 NapCat 内置能力） |
| `plugins` | `features` | `PluginManager`, `PluginMeta`, `PluginStatus` | ✅（VoxCPM2 与本地 embedding 已接入） |
| `ui` | `core`, `app_config` | `WizardWindow`, `DashboardWindow`, `Tray` | ✅ |
| `utils` | - | `parse_raw_cq`, `MetricsProvider`, `get_time` | ✅ |

---

## KV 缓存友好的 system prompt 结构

> **核心规则（来自 DeepSeek 官方 harness 的 KV Cache 语义）**：provider 的前缀缓存按
> 「完整匹配的 token 块」命中。**system 里任何一块变了，从该块起（含其后的整段对话
> 历史）全部失效**。所以 system 必须是纯不可变的，所有易变内容一律追加在历史**之后**。
> 参考 `deepseek-harness/packages/core/agent-loop/README.md` 的 "KV Cache effect" 一节。

`agents/context_builder.build_combined_system_prompt()` 只拼**不可变**的内容：

```xml
<core_rules priority="critical">       — 永不变
<persona priority="high">              — 人格切换才变
<human_chat_patterns priority="high">  — 永不变（真人聊天形态）
<tool_use_protocol priority="high">    — 工具集 + memory_mode 变化才变
<conversation_protocol priority="high">— 永不变
<self_reflection priority="medium">    — 永不变
<qq_format priority="reference">       — 永不变
```

**易变块一律排在 history 之后**，由 `build_runtime_tail_blocks()` 统一构造，
**按稳定性递减**排列（越靠后变化时被牵连作废的 token 越少）：

```
[system]                            不可变骨架
[system]                            admin_info（有管理员时）
*history                            append-only，前缀缓存的主体
[user]  <task_context>              每轮变；必须紧贴 history（见下）
[user]  <long_term_memory>          事件驱动，且按会话 scope 过滤 → 换会话即变
[user]  <rolling_conversation_summary>  压缩提交时整段重写
[user]  <retrieved_conversation_context> 按 query 每轮变
[user]  user_event
```

两条容易踩的坑：

1. **`<long_term_memory>` 绝不能放回 system**。它由 `ImportantMemoryManager.text_for_context()`
   按 conversation scope + 群成员过滤，换个群回复就会变；而 history 是全会话统一时间线
   （跨会话共享同一前缀），放在历史之前会让**每次换群都作废整段历史**。实测该块在
   25119/27299/27932 三种大小间跳变时，命中率会从 ~97% 掉到 23%。
2. **`<task_context>` 必须紧贴 history**。它是唯一会被逐字节写回 history 的块
   （`metadata.kind=task_context_snapshot`），所以上一轮的 `[... history, task_context]`
   会整体成为本轮的公共前缀；若在它前面插入别的尾部块，公共前缀会退回到 history 末尾。

`agents/runner._kv_prompt_diagnostics()` 会把 `kv_prefix_stable_hash`（尾块之前的
可复用前缀哈希）、`kv_system_char_count`、`kv_tail_char_count` 等写进 usage 记录，
重启后可直接用它判断「这次为什么没命中」。

详见 [KV 缓存基准](kv_cache_benchmark.md) 和 `agents/context_builder.py` 注释。

---

## Task Contract 重注入

`AgentRunner.run(messages, task_contract=...)` 接受任务合约。每 `cfg.refocus_interval` 轮（默认 5）在 messages 末尾追加焦点提醒，对抗多步任务焦点漂移（76-89% 任务漂移率，研究结论）。

放在 messages 末尾**不破坏前缀缓存**。

---

## 主上下文与滚动摘要

`HistoryManager` 对真实聊天、运行时上下文和工具结果使用永久全量 append；主路径不再按“最近 N 条”裁剪工作历史。主模型调用前，`MessagePipeline` 会按 `behavior.context.max_context_tokens`（或推荐预算）预检装配后的上下文：

1. 未超预算：直接调用主模型。
2. 超过预算：触发滚动摘要，把较早的活跃 history 合入 summary，并推进活跃窗口起点。
3. 首次压缩后仍超预算或压缩失败：按更小的重试目标再次扩大压缩范围。
4. 仍无法满足预算：显式退出本轮并记录错误，而不是静默裁剪历史。

相关配置：

- `behavior.summarize.trigger_at_context_percent`：未显式配置 token 阈值时，按工作上下文预算推导触发点。
- `behavior.summarize.target_after_context_percent`：首次压缩后的活跃窗口目标。
- `behavior.summarize.retry_target_after_context_percent`：首次压缩仍不够时的重试压缩目标。
- `behavior.context.summary_token_budget`：滚动摘要注入主上下文的预算。

`range_start_messages`、`range_end_messages` 以及 `behavior.context` 中旧的“活跃历史保底 / 保留最近 N 条运行时记录”字段已经废弃；主动思考路由器使用独立的 `proactive_router_*` 预算字段，不参与主聊天历史裁剪。

压缩触发/目标按“**模型可见视图 token + 固定 system/tools 开销**”计算，而不是原始活跃历史；默认 `50/30/20`（触发/目标/重试）。模型视图见下一节。

---

## 模型视图（运行时噪声裁剪）

`core/model_view.py` 在**不修改落盘历史**的前提下，构造一份只给模型看的视图：

- 真实聊天、非 no_action 的工具调用/结果全部保留。
- `task_context_snapshot`、`send_receipt`、`send_done_snapshot` 各保留最近 N 个（默认 2/2/2）。
- `no_action` 按完整 assistant tool_call + tool result 成对保留最近 4 组。
- 超长 user 记录做确定性头尾截断（默认 8,000 字符），原始记录仍在 event store/archive 中。
- 发送类工具结果投影为 `msg_id/order/status` 等定位字段，不再回显已发送正文。

`PipelineWorkingContextMixin._select_working_history()` 返回视图；`_active_history_records()`
仍返回原始记录，供摘要、归档、`recall_history` 和 `get_recent_chat_messages` 使用。
压缩触发与目标统一按“视图 token + 固定 system/tools 开销”计算，避免旧快照触发压缩或
总结 Agent 重复读取噪声。

配置位于 `behavior.context.model_view`，回滚开关是 `enabled=false`。

## Token 计量

- `utils.token_budget.TokenEstimator` 用规范 JSON 估算 messages 与 tools（不再用 Python repr）。
- 小文本按 `(model, text)` LRU 缓存；工具 schema 按 registry 列表对象缓存。
- 每次真实调用记录 `estimated_prompt_tokens`、`prompt_estimate_ratio`、`calib_ratio`。
- `core/token_calibration.py` 按 `provider|model` 持久化校准系数与 MAPE，启动时恢复。
- 用量面板展示未命中输入 token 与估算误差；`cached_tokens` 会被 clamp 到 `prompt_tokens`。

## 工具 schema 档位

- `behavior.tool_schema_profile="compact"`（默认）：11 个高频工具直出核心字段；
  其余工具为 stub，需先 `tool_search`；高级可选字段通过 `tool_search detail=full` 查看。
- `"full"`：保留旧的 31 个 full schema 行为，用于回滚/排查。
- `finish_after_success` 已从 schema 移除；执行层兼容旧调用。发送成功后
  `behavior.auto_finish_after_send=true` 会直接结束本轮，避免额外一次 `no_action` 调用。

## 工具结果创建即定型

工具结果只在刚返回时做一次精简，写入 `history` 后不再回改。这样旧 `tool` record 的字节稳定，避免为了清理长结果而破坏 KV 缓存前缀。

入口：

- 工具内部做语义明确的精简，例如 `describe_image` 保存完整描述、`get_user_info` 丢弃二进制 buffer、`read_file` 分页。
- `tools.result_shrink.shrink_tool_result()` 在 `ToolRegistry` executor 出口做统一兜底，例如搜索结果、合并转发、Python 输出、超长未知工具结果。

相关配置位于 `behavior.context`：

- `tool_result_budgets`：按工具分别配置 inline、artifact 和 hard 上限。
- `tool_result_default_budget_tokens`：未单独配置工具的 inline 默认预算。
- `tool_result_default_hard_cap_tokens`：未单独配置工具的事故兜底上限。

设置页「Token预算」提供对应入口。旧的 `tool_result_soft_limit_tokens`、`tool_result_hard_cap_tokens` 和 `tool_result_soft_overrides` 只为兼容历史配置保留。

---

## 长期记忆双模式

`features.long_term_memory.mode`：

- **`file`**（默认）：AI 通过显式 `save_important_memory` / `update_important_memory` 工具维护重要记忆。零额外向量开销、完全透明。
- **`rag`**：同样保存重要记忆，并为会话历史维护向量索引；上下文注入时按语义检索相关历史。需要 `features.embedding` 启用。

切换模式时，`build_tool_use_protocol(memory_mode)` 会动态注入不同的工具说明，区分文件直写与 RAG 语义检索的使用方式。

重要记忆是一份全局存储，不按会话拆库。每条记忆有两个呈现层元数据：

- `scope`：`global` / `user:{qq}` / `group:{gid}`，只决定当前轮优先注入哪些记忆。
- `pinned`：置顶记忆永远注入，不受当前会话 scope 过滤；普通记忆受 `memory_token_budget` 控制。

`MessagePipeline` 会把当前 `conversation_id` 映射到记忆 scope：`private:123 → user:123`，`group:456 → group:456`。`save_important_memory` 默认使用当前会话 scope，除非工具参数显式指定。

### RAG 装配

`core/runtime.py::_setup_rag` 在 `mode=rag` 时按顺序装：

```
features.embedding.provider → 复用现有 LLM provider 的 base_url
                            → OpenAICompatEmbeddingService(base_url, key, model)
vector/<persona>/rag_memory.sqlite3
                            → SqliteVectorStore（cosine top-k 检索）
RagMemoryService            → 监听 HistoryManager 追加并启动归档/活跃历史 bootstrap
```

向量库是实例级独立文件，不并入 `memory/<persona>/<persona>.db`；旧 `memory/<persona>/rag_memory.sqlite3` 首次启用 RAG 时会复制到新路径，旧文件保留。
`message_pipeline` 拼上下文时用最后一条用户消息当 query 调 `retrieve_for_query()`：
先按当前 `conversation_id` 过滤候选，再做 cosine top-k，最后与重要记忆上下文合并，省 token 且减少跨群/私聊误召回。

向量算法（`memory/rag_store.cosine_similarity`）固定用余弦相似度；
不做归一化（外面传进来的向量保留原长度，cosine 公式自带归一化）。

---

## 本地归档总结

`recall_history` 和 `summarize_conversation` 读取本地 `ArchiveStore` + 当前活跃 `HistoryManager`：

- `recall_history` 返回匹配原文片段，用于找旧 msg_id、旧约定或精确上下文。
- `summarize_conversation(conversation_id?, range_hint?, goal?, max_tokens?)` 用总结模型整理本地归档，私聊和群聊都可用。

`summarize_chat_history` 是另一个工具：它通过 NapCat/QQ 服务器侧接口拉取指定群的近期群消息，只适合补充本地归档以外的群历史，不支持私聊。

---

## 插件机制

`plugins/` 给「重依赖 / 本地模型」类能力一个统一安装入口。Runtime 启动时扫描 `plugins/{name}/__plugin__.py`，
按 `features.{tts,embedding}.type=local` 决定要不要 build 实例并注入运行时。

```
plugins/{name}/
  __plugin__.py    # PLUGIN_META + build(config) -> service
  voxcpm_impl.py   # 真正的实现（lazy import 重依赖）
data/models/{name}/  # 模型文件，不入仓库
```

主程序只依赖 `features/` 的轻量接口（`ITTSService` / `IEmbeddingService`），
插件实现这些接口即可。完整规格见 [plugins/PLUGIN_SPEC.md](../plugins/PLUGIN_SPEC.md)。

UI 入口在仪表盘「模型管理」页：列表 + 详情 + 安装指引。实际启用与参数配置在设置页对应 feature 区域完成。

工具系统挂钩：TTS 启用时 `tools/feature_tools.send_voice_message` 才生效。
ASR 不出现在工具里。QQ/NapCat 渠道使用 NapCat 内置 `fetch_ptt_text`。

---

## 生命周期

```
main.py → Runtime.start()
  1. AppPaths + SecretsManager
  2. load_config()
  3. load_persona()
  4. HistoryManager + ImportantMemoryManager
  5. build providers (各 LLM)
  6. ChatAgent / ProactiveRouterAgent / SummaryAgent
  7. features 服务（vision/web_search/weather, 按配置）
  7.5 RAG（embedding + rag_store）若 mode=rag
  7.6 PluginManager.scan() + build TTS/Embedding（若 type=local）
  8. NapCatAdapter
  9. ToolRegistry（按 features 启用）
  10. State（UsageStats / ModelActivity / ProviderHealth）
  11. WakeupScheduler / PendingRequestStore / RateLimiter
  12. MessagePipeline（拼装上面全部）
  13. RecallHandler / RequestHandler
  14. ProactiveLoop
  15. EventBus（订阅 pipeline + handlers）
  16. adapter.start() / proactive_loop.start()

Runtime.wait_until_stop() → 等 SIGINT/SIGTERM

Runtime.shutdown() → 反序关闭
```

详见 `core/runtime.py`。

---

## 测试策略

- **单元 / 集成测试**（`tests/`）：每模块独立测试，同时覆盖完整 message pipeline、工具执行、发送队列、RAG、UI 配置等链路
- **Live 测试**（`tests/test_kv_cache_real.py`）：需真实 API 密钥，标记 `@pytest.mark.live`。默认不跑
- **KV 缓存命中率实测**：验证稳定 system 前缀、tools 参数、变化 task_context 等场景的缓存命中情况

跑测试：

```bash
uv sync --extra dev
uv run python -m pytest tests/ -q --ignore=tests/test_kv_cache_real.py
```

---

## 性能优化点

- **uvloop**（Linux/Mac）替代默认 asyncio
- **orjson** 替代 json
- **aiofiles** 替代同步文件 IO
- **JSONL 增量追加**（HistoryManager）替代每次重写整个 JSON
- **KV 缓存前缀稳定**（context_builder）
- **模型视图裁剪**（`core/model_view.py`）：旧运行时快照/回执/no_action 不再进入 prompt
- **工具 schema 分级**（`tools/base.py`）：compact + stub + `tool_search detail=full`
- **发送成功自动收尾**（`agents/runner.py`）：省掉一次 `no_action` 模型调用
- **Token 计量**（`utils/token_budget.py` + `core/token_calibration.py`）：规范 JSON、持久化校准、编码缓存
- **KV 诊断分级**（`agents/runner.py`）：默认 `summary`，不再每轮多次全量序列化
- **WebSocket 长连接 + 心跳**（NapCatAdapter）

基准脚本：`scripts/benchmark_prompt_tokens.py`（只读，不调用模型）。
详见 [KV 缓存基准](kv_cache_benchmark.md)。
