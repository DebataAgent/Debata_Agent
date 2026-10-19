"""新特性集成测试：模型视图、发送自动收尾、compact 工具 schema。"""

from __future__ import annotations

import pytest

from tests.integration_pipeline.helpers import (
    _ai_no_action,
    _ai_send_private,
    _drain_pipeline,
    _msg,
)


def _snapshot(kind: str, tag: str, index: int) -> dict:
    return {
        "role": "user",
        "content": f"<{tag}>\n旧运行时 {index}\n</{tag}>",
        "metadata": {"kind": kind},
        "conversation_id": "group:1",
    }


@pytest.mark.asyncio
async def test_model_view_prunes_runtime_noise_but_keeps_raw_history(build_pipeline):
    pipeline, provider, _, history, _ = await build_pipeline(
        [_ai_no_action()],
        model_view_enabled=True,
    )
    await history.add_user_message("真实旧消息 KEEP", conversation_id="group:1")
    for index in range(6):
        await history.add_records(
            [
                _snapshot("task_context_snapshot", "task_context", index),
                _snapshot("send_done_snapshot", "send_status", index),
            ]
        )
    await history.add_user_message("真实当前消息", conversation_id="private:123")

    await pipeline.enqueue(_msg(user_id="123", text="触发一轮", message_id="m-view"))
    await _drain_pipeline(pipeline)

    messages = provider.calls[-1]["messages"]
    task_snapshots = [
        message
        for message in messages
        if isinstance(message.get("metadata"), dict)
        and message["metadata"].get("kind") == "task_context_snapshot"
    ]
    status_snapshots = [
        message
        for message in messages
        if isinstance(message.get("metadata"), dict)
        and message["metadata"].get("kind") == "send_done_snapshot"
    ]
    assert len(task_snapshots) <= 2
    assert len(status_snapshots) <= 2
    joined_history = "\n".join(
        str(record.get("content") or "") for record in await history.records()
    )
    assert "旧运行时 0" in joined_history
    assert "真实旧消息 KEEP" in joined_history

    joined_prompt = "\n".join(str(message.get("content") or "") for message in messages)
    assert "真实旧消息 KEEP" in joined_prompt
    assert "真实当前消息" in joined_prompt


@pytest.mark.asyncio
async def test_pipeline_auto_finishes_after_successful_send(build_pipeline):
    pipeline, provider, adapter, history, _ = await build_pipeline(
        [_ai_send_private(target_qq="123", content="收到")],
        auto_finish_after_send=True,
    )

    await pipeline.enqueue(_msg(user_id="123", text="你好", message_id="m-auto"))
    await _drain_pipeline(pipeline, max_wait=2.0)

    assert [content for _, content in adapter.sent] == ["收到"]
    assert len(provider.calls) == 1
    tool_results = [
        record
        for record in await history.records()
        if record.get("role") == "tool"
        and record.get("tool_call_id") == "tc-1"
    ]
    assert tool_results
    assert '"status": "sent"' in tool_results[-1]["content"] or (
        '"status":"sent"' in tool_results[-1]["content"]
    )


@pytest.mark.asyncio
async def test_compaction_uses_view_slice_and_archives_raw_records(build_pipeline):
    from core.model_view import runtime_kind

    class FakeSummaryAgent:
        def __init__(self) -> None:
            self.history_slice: list[dict] = []

        async def summarize_rolling(
            self,
            history_slice,
            existing_summary_text,
            existing_important_text,
        ):
            self.history_slice = list(history_slice)
            return {"summary_text": "视图摘要", "new_important": []}

    pipeline, _, _, history, _ = await build_pipeline(
        [],
        model_view_enabled=True,
    )
    agent = FakeSummaryAgent()
    pipeline.summary_agent = agent

    await history.add_user_message("真实旧消息", conversation_id="private:123")
    for index in range(8):
        await history.add_records(
            [_snapshot("task_context_snapshot", "task_context", index)]
        )
    await history.add_user_message("真实最新消息", conversation_id="private:123")
    original_count = len(await history.records())

    result = await pipeline._maybe_summarize(
        force=True,
        target_after_tokens=1,
        reason="test_view_compaction",
    )

    assert result.success
    assert agent.history_slice
    # 总结 Agent 只接收真实聊天，不读取旧运行时快照。
    assert all(runtime_kind(record) is None for record in agent.history_slice)
    assert any(
        "真实旧消息" in str(record.get("content") or "")
        for record in agent.history_slice
    )
    archived = await pipeline.archive.records()
    assert any(
        "真实旧消息" in str(record.get("content") or "") for record in archived
    )
    # 归档层只保存真实聊天；运行时快照仍保留在原始 history 中，只是不再进入活跃窗口。
    assert all(runtime_kind(record) is None for record in archived)
    raw_records = await history.records()
    assert len(raw_records) >= original_count
    assert sum(
        1
        for record in raw_records
        if runtime_kind(record) == "task_context_snapshot"
    ) == 8
    assert any(
        "真实旧消息" in str(record.get("content") or "")
        for record in raw_records
    )
    assert pipeline.rolling_summary.active_start_index() > 0


@pytest.mark.asyncio
async def test_pipeline_can_use_compact_tool_schema(build_pipeline):
    pipeline, provider, _, _, _ = await build_pipeline(
        [_ai_no_action()],
        tool_schema_profile="compact",
    )

    await pipeline.enqueue(_msg(user_id="123", text="看看 schema", message_id="m-schema"))
    await _drain_pipeline(pipeline)

    tools = provider.calls[-1]["tools"]
    by_name = {tool["function"]["name"]: tool for tool in tools}
    send_props = by_name["send_private_messages"]["function"]["parameters"]["properties"]
    assert "targets" in send_props
    assert "reviewed_until_seq" not in send_props
    upload_props = by_name["upload_file"]["function"]["parameters"]["properties"]
    assert "_tool_search_required" in upload_props
