"""模型视图裁剪测试：只改 prompt 视图，不动落盘历史。"""

from __future__ import annotations

import copy
import json

from core.model_view import (
    ModelViewPolicy,
    apply_model_view,
    build_model_view,
    model_view_drop_indices,
    no_action_pair_indices,
    runtime_kind,
)


def _runtime(kind: str, text: str, *, i: int = 0) -> dict:
    tag = {
        "task_context_snapshot": "task_context",
        "send_receipt": "send_receipt",
        "send_done_snapshot": "send_status",
    }[kind]
    return {
        "role": "user",
        "content": f"<{tag}>{text}-{i}</{tag}>",
        "metadata": {"kind": kind},
    }


def _no_action_pair(i: int) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"na-{i}",
                    "type": "function",
                    "function": {"name": "no_action", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": f"na-{i}",
            "content": '{"ok":true,"no_action":true}',
        },
    ]


def _send_pair(i: int) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"send-{i}",
                    "type": "function",
                    "function": {"name": "send_private_messages", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": f"send-{i}",
            "content": json.dumps(
                {
                    "ok": True,
                    "status": "sent",
                    "qq_visible": True,
                    "sent": [
                        {
                            "order": 1,
                            "conversation_id": "private:1",
                            "content": "已经发送的正文",
                            "delay": 0,
                            "msg_id": "m1",
                            "time": "2026-01-01 00:00:00",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _records() -> list[dict]:
    records: list[dict] = [{"role": "user", "content": "真实旧消息"}]
    for i in range(5):
        records.append(_runtime("task_context_snapshot", "ctx", i=i))
    for i in range(3):
        records.append(_runtime("send_receipt", "receipt", i=i))
    for i in range(3):
        records.append(_runtime("send_done_snapshot", "done", i=i))
    for i in range(5):
        records.extend(_no_action_pair(i))
    records.extend(_send_pair(1))
    records.append({"role": "assistant", "content": "真实回复"})
    return records


def test_model_view_keeps_recent_runtime_and_complete_no_action_pairs():
    records = _records()
    original = copy.deepcopy(records)
    policy = ModelViewPolicy(
        task_context_keep=2,
        send_receipt_keep=2,
        send_status_keep=2,
        no_action_keep=4,
    )

    view = build_model_view(records, policy=policy)

    assert records == original, "视图不能修改原始历史"
    assert view.raw_indices == sorted(view.raw_indices)
    counts = {
        kind: sum(1 for record in view.records if runtime_kind(record) == kind)
        for kind in (
            "task_context_snapshot",
            "send_receipt",
            "send_done_snapshot",
        )
    }
    assert counts == {
        "task_context_snapshot": 2,
        "send_receipt": 2,
        "send_done_snapshot": 2,
    }
    pairs = 0
    index = 0
    while index < len(view.records):
        pair = no_action_pair_indices(view.records, index)
        if pair:
            pairs += 1
            index = max(pair) + 1
            continue
        index += 1
    assert pairs == 4
    assert view.dropped_runtime == 5
    assert view.dropped_no_action_pairs == 1
    assert "真实旧消息" in [record.get("content") for record in view.records]
    assert "真实回复" in [record.get("content") for record in view.records]


def test_model_view_does_not_leave_orphan_tool_records():
    view = build_model_view(_records())
    assistant_ids = set()
    for record in view.records:
        if record.get("role") == "assistant":
            for tool_call in record.get("tool_calls") or []:
                assistant_ids.add(str(tool_call.get("id") or ""))
    for record in view.records:
        if record.get("role") == "tool":
            assert str(record.get("tool_call_id") or "") in assistant_ids


def test_model_view_projects_send_results_and_caps_long_records():
    records = [
        {"role": "user", "content": "A" * 400 + "B" * 400},
        *_send_pair(1),
    ]
    view = build_model_view(
        records,
        policy=ModelViewPolicy(max_record_chars=300),
    )

    capped = view.records[0]["content"]
    assert "model view truncated" in capped
    assert capped.startswith("A")
    assert capped.endswith("B")
    assert records[0]["content"] == "A" * 400 + "B" * 400

    tool_payload = json.loads(view.records[2]["content"])
    assert tool_payload["sent"][0]["msg_id"] == "m1"
    # 保留短预览/定位字段，去掉时间戳等与后续判断无关的字段。
    assert tool_payload["sent"][0]["content"] == "已经发送的正文"
    assert tool_payload["sent"][0]["delay"] == 0
    assert "time" not in tool_payload["sent"][0]
    assert view.projected_tool_results == 1


def test_model_view_is_idempotent():
    policy = ModelViewPolicy(max_record_chars=300)
    records = [{"role": "user", "content": "X" * 1000}, *_send_pair(1)]
    once = apply_model_view(records, policy=policy)
    twice = apply_model_view(once, policy=policy)
    assert once == twice


def test_model_view_disabled_returns_input_unchanged():
    records = _records()
    view = build_model_view(records, policy=ModelViewPolicy(enabled=False))
    assert view.records == records
    assert view.raw_indices == list(range(len(records)))
    assert model_view_drop_indices(
        records,
        policy=ModelViewPolicy(enabled=False),
    ) == set()


def test_malformed_no_action_pair_is_not_dropped():
    records = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "bad",
                    "type": "function",
                    "function": {"name": "no_action", "arguments": "{}"},
                }
            ],
        },
    ]
    policy = ModelViewPolicy(no_action_keep=0)
    view = build_model_view(records, policy=policy)
    assert view.records == records
    assert view.dropped_no_action_pairs == 0
