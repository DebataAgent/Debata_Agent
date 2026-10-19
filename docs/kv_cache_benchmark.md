# KV 缓存命中率实测报告

> 验证目标：让大 system prompt（人格 + 工具规范 + 真人聊天方法论 ≈ 5-8k token）
> 在多轮对话中被 KV 缓存命中，降低单轮成本 60%+。

## 跑测试

```bash
# 需要环境变量 DEEPSEEK_API_KEY（用户机器已设）
venv/Scripts/python -m pytest tests/test_kv_cache_real.py -m live -s
```

测试会真实调 DeepSeek API（约 20-30 次请求），消耗少量配额。

## 验证目标（硬门槛）

| 指标 | 阈值 | 含义 |
|---|---|---|
| **第 2 轮起单轮 hit_rate** | > 90% | 前缀缓存生效 |
| **整体命中率**（含首轮 miss） | > 80% | 设计有效 |
| **工具调用轮 hit_rate** | > 70% | tool_call 不破坏前缀 |
| **变化 task_context 后** | > 80% | 末尾追加不影响前缀 |

## 实测结果

> 2026-06-06 生产环境实测数据。Provider: DeepSeek（deepseek-v4-pro），12 次真实对话调用，
> 含 RAG 注入 + send_receipt + 工具循环。数据来源: `data/logs/model_usage.jsonl`。

### 真实运行数据（12 次调用）

| # | 对话轮 | prompt | cached | creation | completion | 命中率 |
|---|--------|--------|--------|----------|------------|--------|
| 1 | 首轮 | 16,728 | 16,256 | 472 | 71 | 97.2% |
| 2 | 工具循环 | 16,991 | 16,768 | 223 | 150 | 98.7% |
| 3 | 首轮 | 17,678 | 16,640 | 1,038 | 71 | 94.1% |
| 4 | 工具循环 | 18,132 | 17,664 | 468 | 395 | 97.4% |
| 5 | 首轮 | 18,985 | 18,048 | 937 | 83 | 95.1% |
| 6 | 工具循环 | 19,350 | 18,944 | 406 | 185 | 97.9% |
| 7 | 首轮 | 20,246 | 18,944 | 1,302 | 70 | 93.6% |
| 8 | 工具循环 | 20,474 | 20,224 | 250 | 164 | 98.8% |
| 9 | 首轮 | 21,419 | 20,224 | 1,195 | 71 | 94.4% |
| 10 | 工具循环 | 21,778 | 21,376 | 402 | 219 | 98.2% |
| 11 | 首轮 | 22,720 | 21,376 | 1,344 | 53 | 94.1% |
| 12 | 工具循环 | 22,981 | 22,656 | 325 | 261 | 98.6% |

**整体命中率：96.5%**（229,120 / 237,482）

### 关键发现

- **跨对话前缀继承生效**：即使是不同对话的「首轮」，缓存命中率也在 93.6%–97.2%。说明 DeepSeek 的磁盘缓存在不同会话之间仍然保留。
- **工具循环不破坏前缀**：工具循环内的第二轮 completion（loop2）命中率 97.4%–98.8%，说明工具结果追加到 messages 末尾不影响已缓存的前缀。
- **cache_creation_tokens 稳定**：每轮新增写入 223–1,344 tokens，对应对话中新产生的 assistant + tool 消息，与设计预期一致。
- **RAG 注入不影响命中**：所有调用均带 RAG 注入，前缀 8k/16k hash 在多轮间保持稳定。

## 已识别的"会破坏缓存"的操作清单

> 跑测试时若发现某轮命中率异常低，回查是否触发了这些情况：

- ✗ 每轮 rebuild 整个 persona prompt（如插入当前时间到 system 中）
- ✗ 在 messages 中间（不是末尾）追加新内容
- ✗ tool 段顺序在不同轮次发生变化（schema 字段无序生成）
- ✗ 切换 persona / 切换 provider 模型
- △ 滚动摘要成功提交后会移动 `active_start_index`，下一轮前缀会明确重建一次；
  `history.jsonl` 仍全量保留，不再通过 `truncate_head` 删除事实账本。

## 多 Provider 对比

| Provider | 数据来源 | 整体命中率 | 备注 |
|---|---|---|---|
| DeepSeek (deepseek-v4-pro) | 生产实测 | 96.5% | 磁盘缓存，跨对话继承生效 |
| GLM / Anthropic | 待实测 | — | 需对应 API 密钥才能跑 live 测试 |

## 成本估算（DeepSeek deepseek-v4-pro, 2026-06）

| 场景 | prompt_tokens | cached_tokens | 说明 |
|---|---|---|---|
| 无缓存（首轮全额） | ~17,000 | 0 | 理论最差情况 |
| 有缓存（生产实际） | ~20,000 | ~19,000 (95%+) | 实测数据 |
| **实际降本** | | | **约 90%+** |

按 DeepSeek 当前定价（缓存命中 $0.014/百万 token，未命中 $0.28/百万 token），
单轮有缓存时 prompt 成本约 $0.000014/轮，无缓存时约 $0.0048/轮。

## 2026-10 上下文治理后的本地估算

> 这一节不是 live API 命中率，而是对真实实例历史做的只读 prompt 估算，
> 用于说明上下文治理的降幅。实际缓存命中率仍需部署后重跑 live 测试。

数据来源：`~/.local/share/Debata_Agent/instances/default/memory/小堡/小堡.db`
（活跃窗口 568 条记录），脚本 `scripts/benchmark_prompt_tokens.py`。

| 指标 | 重构前 | 重构后 | 变化 |
|---|---:|---:|---:|
| 活跃历史 token | 185,173 | 43,248 | **-76.6%** |
| 工具 schema token（full → compact） | 9,817 | 4,740 | **-51.7%** |
| 估算单次 prompt | 206,768 | 59,766 | **-71.1%** |
| 模型可见记录 | 568 | 296 | -47.9% |

原因：

- 模型视图只保留最近 2 个 `task_context`、2 条发送回执、4 组 `no_action`；
  旧快照不再每轮重复发送。
- 工具 schema 从 31 个 full 降为 11 个 compact + 其余 stub。
- 发送成功后自动收尾，减少一次 `no_action` 模型调用。
- 压缩触发按“模型可见视图 + 固定开销”计算，避免旧快照触发压缩。

复测：

```bash
.venv/bin/python scripts/benchmark_prompt_tokens.py \
  --db ~/.local/share/Debata_Agent/instances/default/memory/小堡/小堡.db \
  --persona 小堡 --model deepseek-flash
```

## 下次更新

- 部署到生产后重跑 live KV cache 测试，确认 `kv_prefix_stable_hash` 与命中率
- 若调整 `model_view` 保留数量、工具 schema 档位或摘要阈值，重跑 benchmark
- 若 schema 结构调整（如 D 类整改后），需重跑确认未引入新破坏
