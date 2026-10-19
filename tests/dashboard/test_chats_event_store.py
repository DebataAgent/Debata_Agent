"""聊天页事件存储合并与投影回归测试。"""

# ruff: noqa: E402, I001

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from core.chat_timeline import ChatTimelineStore
from webui.chatlog.records import (
    EVENT_STORE_CHAT_PAGE_EVENT_TYPES,
    _load_chat_page_records,
)

from tests.dashboard.helpers import (
    FakeEventStore,
    StaticRecordStore,
    dashboard_runtime,
    qq_event,
    runtime_event,
    timeline_record,
)

@pytest.mark.asyncio
async def test_chats_loads_event_store_qq_records_before_timeline_and_archive_duplicates(tmp_paths):
    rt = dashboard_runtime(tmp_paths)
    rt.event_store = FakeEventStore(
        [
            qq_event(
                1,
                "qq_message_received",
                content="EventStore 入站",
                msg_id="same-in",
            ),
            qq_event(
                2,
                "qq_message_sent",
                content="EventStore 出站",
                msg_id="same-out",
            ),
        ]
    )
    timeline = ChatTimelineStore()
    timeline.append(
        timeline_record(
            conversation_id="private:10001",
            direction="inbound",
            text="timeline 重复入站",
            msg_id="same-in",
            timestamp=1_780_000_011.0,
            time_text="2026-06-08 23:07:21",
        )
    )
    timeline.append(
        timeline_record(
            conversation_id="private:10001",
            direction="outbound",
            text="timeline 新消息",
            msg_id="timeline-only",
            timestamp=1_780_000_012.0,
            time_text="2026-06-08 23:07:22",
            sender_name="我",
            sender_id="999",
        )
    )
    rt.pipeline = SimpleNamespace(chat_timeline=timeline)
    rt.archive = StaticRecordStore(
        [
            {
                "role": "user",
                "direction": "inbound",
                "conversation_id": "private:10001",
                "content": "archive 重复入站",
                "msg_id": "same-in",
            },
            {
                "role": "user",
                "direction": "inbound",
                "conversation_id": "private:10001",
                "content": "archive 旧消息",
                "msg_id": "archive-only",
            },
        ]
    )
    rt.history = StaticRecordStore(
        [
            {"role": "user", "content": "history 普通聊天不应作为气泡主来源"},
            {"role": "system", "content": "系统补充"},
        ]
    )

    records = await _load_chat_page_records(rt)

    assert [call["event_type"] for call in rt.event_store.calls] == list(
        EVENT_STORE_CHAT_PAGE_EVENT_TYPES
    )
    assert all(call["limit"] > 0 and call["order"] == "desc" for call in rt.event_store.calls)
    assert rt.event_store.wait_projected_calls == []
    by_msg_id = {
        item.get("msg_id"): item
        for item in records
        if item.get("msg_id")
    }
    assert by_msg_id["same-in"]["_source"] == "event_store"
    assert by_msg_id["same-in"]["content"] == "EventStore 入站"
    assert by_msg_id["same-out"]["_source"] == "event_store"
    assert by_msg_id["timeline-only"]["_source"] == "chat_timeline"
    assert by_msg_id["archive-only"]["content"] == "archive 旧消息"
    assert "archive 重复入站" not in [item["content"] for item in records]
    assert "history 普通聊天不应作为气泡主来源" not in [item["content"] for item in records]
    assert "系统补充" in [item["content"] for item in records]

@pytest.mark.asyncio
async def test_chats_event_store_failure_falls_back_to_existing_sources(tmp_paths):
    rt = dashboard_runtime(tmp_paths)
    rt.event_store = FakeEventStore(fail=True)
    timeline = ChatTimelineStore()
    timeline.append(
        timeline_record(
            conversation_id="private:10001",
            direction="inbound",
            text="实时 fallback",
            msg_id="tl-fallback",
            timestamp=1_780_000_001.0,
            time_text="2026-06-08 23:07:19",
        )
    )
    rt.pipeline = SimpleNamespace(chat_timeline=timeline)
    rt.archive = StaticRecordStore(
        [
            {
                "role": "user",
                "direction": "inbound",
                "conversation_id": "private:10001",
                "content": "归档 fallback",
                "msg_id": "arch-fallback",
            }
        ]
    )
    rt.history = StaticRecordStore([{"role": "system", "content": "系统 fallback"}])

    records = await _load_chat_page_records(rt)

    assert [item["content"] for item in records] == [
        "归档 fallback",
        "实时 fallback",
        "系统 fallback",
    ]

@pytest.mark.asyncio
async def test_chats_event_store_ignores_non_qq_events(tmp_paths):
    rt = dashboard_runtime(tmp_paths)
    rt.event_store = FakeEventStore(
        [
            {
                "event_id": 1,
                "event_type": "history_record_appended",
                "conversation_id": "private:10001",
                "payload": {"content": "不应投影"},
            },
            qq_event(
                2,
                "qq_message_received",
                content="应投影",
                msg_id="visible-in",
            ),
        ],
        include_mismatched=True,
    )
    rt.history = StaticRecordStore([])

    records = await _load_chat_page_records(rt)

    assert [item["content"] for item in records] == ["应投影"]
    assert records[0]["event_id"] == 2

@pytest.mark.asyncio
async def test_chats_event_store_projects_received_and_sent_fields_by_event_id_order(tmp_paths):
    rt = dashboard_runtime(tmp_paths)
    rt.event_store = FakeEventStore(
        [
            qq_event(
                2,
                "qq_message_sent",
                conversation_id="private:10001",
                content="后发送",
                msg_id="out-1",
                timestamp_unix=100.0,
                payload={"target_scope": "private", "target_id": "10001", "self_id": "999"},
            ),
            qq_event(
                1,
                "qq_message_received",
                conversation_id="group:20002",
                content="先收到",
                msg_id="in-1",
                timestamp_unix=200.0,
                payload={
                    "group_id": "20002",
                    "user_id": "10001",
                    "sender_name": "Alice",
                    "target_id": "20002",
                    "self_id": "999",
                },
            ),
        ]
    )
    rt.history = StaticRecordStore([])

    records = await _load_chat_page_records(rt)

    assert [item["content"] for item in records] == ["先收到", "后发送"]
    received, sent = records
    assert received["id"] == "event:1"
    assert received["event_id"] == 1
    assert received["role"] == "user"
    assert received["direction"] == "inbound"
    assert received["qq_visible"] is True
    assert received["conversation_id"] == "group:20002"
    assert received["msg_id"] == "in-1"
    assert received["_source"] == "event_store"
    assert received["sender_name"] == "Alice"
    assert received["user_id"] == "10001"
    assert received["target_id"] == "20002"
    assert received["group_id"] == "20002"
    assert received["self_id"] == "999"
    assert received["_sort_layer"] == "event_store"
    assert received["_sort_kind"] == "event_id"
    assert received["_sort_value"] == 1.0
    assert "_sort_ts" not in received
    assert sent["id"] == "event:2"
    assert sent["role"] == "assistant"
    assert sent["direction"] == "outbound"
    assert sent["conversation_id"] == "private:10001"
    assert sent["msg_id"] == "out-1"
    assert sent["target_id"] == "10001"
    assert sent["self_id"] == "999"

@pytest.mark.asyncio
async def test_chats_event_store_runtime_events_dedupe_semantic_duplicates(tmp_paths):
    rt = dashboard_runtime(tmp_paths)
    rt.event_store = FakeEventStore(
        [
            runtime_event(
                1,
                "tool_call_started",
                payload={"tool_name": "write_file", "tool_call_id": "tc-dup"},
                tool_call_id="tc-dup",
            ),
            runtime_event(
                2,
                "tool_call_started",
                payload={"tool_name": "write_file", "tool_call_id": "tc-dup"},
                tool_call_id="tc-dup",
            ),
            runtime_event(
                3,
                "send_batch_accepted",
                payload={"send_id": "send-dup", "status": "accepted"},
                external_id="send-dup",
            ),
            runtime_event(
                4,
                "send_batch_accepted",
                payload={"send_id": "send-dup", "status": "accepted"},
                external_id="send-dup",
            ),
            runtime_event(
                5,
                "send_message_started",
                payload={
                    "send_id": "send-dup",
                    "status": "started",
                    "order": 0,
                    "target_conversation_id": "private:10001",
                },
                external_id="send-dup",
            ),
            runtime_event(
                6,
                "send_message_started",
                payload={
                    "send_id": "send-dup",
                    "status": "started",
                    "order": 0,
                    "target_conversation_id": "private:10001",
                },
                external_id="send-dup",
            ),
            runtime_event(
                7,
                "send_message_started",
                payload={
                    "send_id": "send-dup",
                    "status": "started",
                    "order": 1,
                    "target_conversation_id": "private:10001",
                },
                external_id="send-dup",
            ),
        ]
    )
    rt.history = StaticRecordStore([])

    records = await _load_chat_page_records(rt)

    assert [(record["event_id"], record["event_type"]) for record in records] == [
        (1, "tool_call_started"),
        (3, "send_batch_accepted"),
        (5, "send_message_started"),
        (7, "send_message_started"),
    ]

@pytest.mark.asyncio
async def test_chats_event_store_runtime_events_do_not_backfill_from_history(tmp_paths):
    system_content = "系统补充"
    system_hash = hashlib.sha256(system_content.encode("utf-8")).hexdigest()
    rt = dashboard_runtime(tmp_paths)
    rt.event_store = FakeEventStore(
        [
            runtime_event(
                1,
                "tool_call_started",
                payload={
                    "tool_name": "write_file",
                    "tool_call_id": "tc-1",
                    "args": {"path": "event.md"},
                },
                tool_call_id="tc-1",
            ),
            runtime_event(
                2,
                "tool_result_received",
                payload={
                    "tool_name": "write_file",
                    "tool_call_id": "tc-1",
                    "ok": True,
                    "result": {"ok": True, "content": "EventStore 完整工具返回"},
                    "result_hash": "d" * 64,
                },
                tool_call_id="tc-1",
            ),
            runtime_event(
                3,
                "send_receipt_recorded",
                payload={"send_id": "send-1", "status": "succeeded"},
                external_id="send-1",
            ),
            runtime_event(
                4,
                "system_note_recorded",
                payload={
                    "conversation_id": "private:10001",
                    "content_length": len(system_content),
                    "content_hash": system_hash,
                    "content": system_content,
                    "preview": system_content,
                },
            ),
        ]
    )
    rt.history = StaticRecordStore(
        [
            {
                "role": "assistant",
                "content": "",
                "conversation_id": "private:10001",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "function": {"name": "write_file", "arguments": '{"path":"full.md"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc-1",
                "conversation_id": "private:10001",
                "content": '{"ok":true,"content":"完整工具返回"}',
            },
            {
                "role": "system",
                "conversation_id": "private:10001",
                "content": '<send_receipt>\n{"send_id":"send-1","status":"succeeded"}\n</send_receipt>',
            },
            {"role": "system", "conversation_id": "private:10001", "content": system_content},
            {"role": "system", "conversation_id": "private:10001", "content": "旧 history fallback"},
        ]
    )

    records = await _load_chat_page_records(rt)

    blob = json.dumps(records, ensure_ascii=False)
    assert "EventStore 完整工具返回" in blob
    assert not any(
        record.get("content") == '{"ok":true,"content":"完整工具返回"}'
        for record in records
    )
    assert '{"path":"full.md"}' not in blob
    assert all(record.get("_source") == "event_store" for record in records[:4])
    assert records[-1]["content"] == "旧 history fallback"
    assert records[-1].get("_source") is None
