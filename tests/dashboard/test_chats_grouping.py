"""聊天页会话分组回归测试。"""

from __future__ import annotations

import json

from webui.chatlog import grouping as grouping_module
from webui.chatlog.grouping import _group_records_by_conversation


def test_chats_group_records_by_metadata_and_legacy_header():
    records = [
        {
            "role": "user",
            "content": "hello",
            "metadata": {
                "messages": [
                    {
                        "scope": "private",
                        "target_id": "10001",
                        "user_id": "10001",
                        "nickname": "Alice",
                    }
                ]
            },
        },
        {"role": "assistant", "content": "hi"},
        {
            "role": "user",
            "content": "【2026-05-27 12:00:00 群聊 20002 Bob(30003) msg_id=9】群消息",
        },
    ]

    grouped = _group_records_by_conversation(records)

    assert grouped[0]["key"] == "group:20002"
    assert grouped[0]["label"] == "群聊 20002"
    assert grouped[1]["key"] == "private:10001"
    assert len(grouped[1]["records"]) == 2


def test_chats_group_records_prefers_explicit_conversation_id():
    records = [
        {"role": "user", "content": "u", "conversation_id": "private:10001"},
        {"role": "assistant", "content": "a", "conversation_id": "private:10001"},
        {"role": "system", "content": "社交决策：本次跳过"},
        {
            "role": "tool",
            "content": "{}",
            "tool_call_id": "tc",
            "conversation_id": "group:20002",
        },
    ]

    grouped = _group_records_by_conversation(records)
    by_key = {item["key"]: item for item in grouped}

    assert by_key["private:10001"]["label"] == "私聊 10001"
    assert len(by_key["private:10001"]["records"]) == 2
    assert by_key["system:global"]["records"][0]["content"] == "社交决策：本次跳过"
    assert by_key["group:20002"]["records"][0]["tool_call_id"] == "tc"


def test_chats_keeps_send_tool_events_in_source_conversation():
    records = [
        {
            "role": "assistant",
            "content": "",
            "conversation_id": "private:10000",
            "tool_calls": [
                {
                    "id": "call-send",
                    "function": {
                        "name": "send_private_messages",
                        "arguments": json.dumps(
                            {
                                "targets": [
                                    {
                                        "target_qq": "10001",
                                        "content": "给一号",
                                        "order": 1,
                                        "delay": 0,
                                    },
                                    {
                                        "target_qq": "10002",
                                        "content": "给二号",
                                        "order": 2,
                                        "delay": 0,
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-send",
            "conversation_id": "private:10000",
            "content": json.dumps(
                {
                    "status": "accepted",
                    "accepted_messages": [
                        {"conversation_id": "private:10001", "content": "给一号"},
                        {"conversation_id": "private:10002", "content": "给二号"},
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]

    grouped = _group_records_by_conversation(records)
    by_key = {item["key"]: item for item in grouped}

    assert "private:10000" in by_key
    assert "private:10001" not in by_key
    assert "private:10002" not in by_key
    assert [record.get("role") for record in by_key["private:10000"]["records"]] == ["assistant", "tool"]


def test_chats_groups_proactive_records_as_system():
    records = [
        {"role": "user", "content": "群消息", "conversation_id": "group:20002"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [],
            "conversation_id": "system:proactive",
        },
        {
            "role": "tool",
            "content": '{"ok":true}',
            "tool_call_id": "tc-proactive",
            "conversation_id": "system:proactive",
        },
    ]

    grouped = _group_records_by_conversation(records)
    by_key = {item["key"]: item for item in grouped}

    assert "system:proactive" in by_key
    assert by_key["system:proactive"]["label"] == "系统记录 · 社交决策"
    assert by_key["system:proactive"]["records"][1]["tool_call_id"] == "tc-proactive"


def test_chats_unknown_assistant_without_context_does_not_attach_to_previous_chat():
    records = [
        {"role": "system", "content": "全局事件"},
        {"role": "assistant", "content": "后台旧记录"},
        {"role": "user", "content": "hi", "conversation_id": "private:10001"},
    ]

    grouped = _group_records_by_conversation(records)
    by_key = {item["key"]: item for item in grouped}

    assert by_key["unknown:history"]["records"][0]["content"] == "后台旧记录"
    assert by_key["private:10001"]["records"][0]["content"] == "hi"


def test_grouping_indexes_accepted_messages_instead_of_rescanning(monkeypatch):
    """send_status 的 accepted_messages 应使用预索引，不能再全量扫描 records。"""
    records = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-send",
                    "function": {
                        "name": "send_private_messages",
                        "arguments": json.dumps(
                            {
                                "targets": [
                                    {
                                        "target_qq": "10001",
                                        "content": "给一号",
                                        "order": 1,
                                        "delay": 0,
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-send",
            "conversation_id": "private:10000",
            "content": json.dumps(
                {
                    "send_id": "send-1",
                    "status": "accepted",
                    "accepted_messages": [
                        {
                            "conversation_id": "private:10001",
                            "content": "给一号",
                            "msg_id": "m1",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "user",
            "conversation_id": "private:10000",
            "content": (
                '<send_status>{"send_id":"send-1","status":"sent",'
                '"msg_ids":["m1"]}</send_status>'
            ),
        },
    ]

    def fail_scan(*_args, **_kwargs):
        raise AssertionError("O(n^2) accepted_messages scan should not run")

    monkeypatch.setattr(grouping_module, "_accepted_messages_for_send_id", fail_scan)
    grouped = _group_records_by_conversation(records)
    by_key = {item["key"]: item for item in grouped}

    assert "private:10001" in by_key
    status_record = by_key["private:10001"]["records"][0]
    assert status_record["_accepted_messages_for_send_status"] == [
        {
            "conversation_id": "private:10001",
            "content": "给一号",
            "msg_id": "m1",
        }
    ]
