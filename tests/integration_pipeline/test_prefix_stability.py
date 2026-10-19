"""前缀稳定性集成测试（不依赖 NapCat）。

用假 adapter + 假 provider 驱动真实的 MessagePipeline，直接检查 provider 实际
收到的 messages 是否满足**append-only 前缀关系** —— 这正是 DeepSeek 磁盘缓存能
命中的唯一前提：system 不变、历史只追加、易变块全在尾部。

一旦这里退化，线上表现就是"命中量恒等于 system 大小、整段历史每轮重算"。
"""

from __future__ import annotations

import json

import pytest

from providers.base import CompletionResult, ToolCall, normalize_messages
from tests.integration_pipeline.helpers import _ai_no_action, _drain_pipeline, _msg


def _save_memory(step: int) -> CompletionResult:
    """一步不需要审批的记忆写入工具调用，用于构造多步工具循环。"""
    return CompletionResult(
        tool_calls=[
            ToolCall(
                id=f"tc-{step}",
                name="save_important_memory",
                arguments=json.dumps(
                    {"memory_text": f"循环第 {step} 步", "scope": "user:123"}
                ),
            )
        ],
        finish_reason="tool_calls",
    )


def _prefix_len(prev: list[dict], nxt: list[dict]) -> int:
    """两个 messages 序列从头开始逐条相同的条数。"""
    count = 0
    for a, b in zip(prev, nxt, strict=False):
        if a != b:
            break
        count += 1
    return count


@pytest.mark.asyncio
async def test_system_message_is_constant_across_turns(build_pipeline):
    pipeline, provider, _, _, _ = await build_pipeline([_ai_no_action(), _ai_no_action()])

    await pipeline.enqueue(_msg(user_id="123", text="第一轮"))
    await _drain_pipeline(pipeline)
    first_system = normalize_messages(provider.calls[-1]["messages"])[0]["content"]

    await pipeline.enqueue(_msg(user_id="123", text="第二轮"))
    await _drain_pipeline(pipeline)
    second_system = normalize_messages(provider.calls[-1]["messages"])[0]["content"]

    assert first_system == second_system


@pytest.mark.asyncio
async def test_turn_one_messages_are_a_prefix_of_turn_two(build_pipeline):
    """上一轮发出的整串 messages 必须逐字节成为下一轮的前缀。

    这是缓存命中的核心不变量：只要成立，上一轮的 prompt 就能整体复用，
    命中量随历史增长；一旦被打破（例如在历史中间插入了易变块），命中量会
    塌回 system 大小。
    """
    pipeline, provider, _, _, _ = await build_pipeline([_ai_no_action(), _ai_no_action()])

    await pipeline.enqueue(_msg(user_id="123", text="第一轮消息"))
    await _drain_pipeline(pipeline)
    turn1 = normalize_messages(provider.calls[-1]["messages"])

    await pipeline.enqueue(_msg(user_id="123", text="第二轮消息"))
    await _drain_pipeline(pipeline)
    turn2 = normalize_messages(provider.calls[-1]["messages"])

    assert len(turn2) >= len(turn1)
    assert turn2[: len(turn1)] == turn1, (
        f"前缀在第 {_prefix_len(turn1, turn2)} 条消息处断裂；"
        f"turn1={len(turn1)} 条, turn2={len(turn2)} 条"
    )


@pytest.mark.asyncio
async def test_tool_loop_is_append_only_within_a_turn(build_pipeline):
    """同一轮的工具循环内，后一次调用的 messages 必须以前一次为前缀。"""
    pipeline, provider, _, _, _ = await build_pipeline(
        [_save_memory(1), _save_memory(2), _ai_no_action()]
    )

    await pipeline.enqueue(_msg(user_id="123", text="走一轮多步工具循环"))
    await _drain_pipeline(pipeline, max_wait=2.0)

    calls = [normalize_messages(c["messages"]) for c in provider.calls]
    assert len(calls) >= 3, f"应当发生多次模型调用，实际 {len(calls)}"
    for idx in range(1, len(calls)):
        prev, cur = calls[idx - 1], calls[idx]
        assert cur[: len(prev)] == prev, f"第 {idx} 次调用破坏了 append-only 前缀"


@pytest.mark.asyncio
async def test_volatile_blocks_only_appear_after_history(build_pipeline):
    """易变块（记忆/摘要/召回）不得出现在历史之前，否则会作废整段历史缓存。"""
    from agents.context_builder import TAIL_BLOCK_MARKERS

    pipeline, provider, _, _, _ = await build_pipeline([_ai_no_action()])
    await pipeline.enqueue(_msg(user_id="123", text="看看载荷结构"))
    await _drain_pipeline(pipeline)

    messages = normalize_messages(provider.calls[-1]["messages"])
    system_msgs = [m for m in messages if m["role"] == "system"]
    for msg in system_msgs:
        for marker in TAIL_BLOCK_MARKERS:
            assert marker not in msg["content"], f"{marker} 不应出现在 system 里"
