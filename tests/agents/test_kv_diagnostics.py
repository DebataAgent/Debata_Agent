"""KV 诊断分级测试。"""

from __future__ import annotations

from agents.runner import _kv_prompt_diagnostics


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "真实消息"},
        {
            "role": "user",
            "content": '<task_context priority="medium">旧快照</task_context>',
        },
        {"role": "assistant", "content": ""},
        {"role": "tool", "tool_call_id": "t1", "content": '{"ok":true}'},
        {"role": "user", "content": "<send_receipt>回执</send_receipt>"},
    ]


def _tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "a",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "b",
                "description": "",
                "parameters": {
                    "type": "object",
                    "properties": {"_tool_search_required": {"type": "boolean"}},
                },
            },
        },
    ]


def test_summary_mode_counts_without_full_serialization_fields():
    diag = _kv_prompt_diagnostics(
        _messages(),
        _tools(),
        loop=2,
        mode="summary",
    )
    assert diag["kv_diagnostics_mode"] == "summary"
    assert diag["kv_loop"] == 2
    assert diag["kv_message_count"] == 6
    assert diag["kv_task_context_block_count"] == 1
    assert diag["kv_send_receipt_block_count"] == 1
    assert diag["kv_tools_full_count"] == 1
    assert diag["kv_tools_stub_count"] == 1
    assert "kv_prefix_8k_hash" not in diag
    assert diag["kv_prefix_stable_hash"]


def test_off_mode_is_zero_work_marker():
    diag = _kv_prompt_diagnostics(_messages(), _tools(), loop=1, mode="off")
    assert diag == {"kv_diagnostics_mode": "off", "kv_loop": 1}


def test_full_mode_keeps_legacy_hash_fields():
    diag = _kv_prompt_diagnostics(_messages(), _tools(), loop=1, mode="full")
    assert diag["kv_diagnostics_mode"] == "full"
    assert "kv_prefix_8k_hash" in diag
    assert "kv_prefix_stable_hash" in diag


def test_tools_diagnostics_cache_is_reused():
    tools = _tools()
    cache: dict = {}
    first = _kv_prompt_diagnostics(
        _messages(),
        tools,
        loop=1,
        mode="summary",
        tools_diag_cache=cache,
    )
    second = _kv_prompt_diagnostics(
        _messages(),
        tools,
        loop=2,
        mode="summary",
        tools_diag_cache=cache,
    )
    assert first["kv_tools_hash"] == second["kv_tools_hash"]
    assert first["kv_tools_char_count"] == second["kv_tools_char_count"]
    assert cache
