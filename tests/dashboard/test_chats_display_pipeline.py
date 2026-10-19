"""对话页 DisplayItem 管线测试。

被测的是从原 GUI ``ui/dashboard/chats_page.py`` 原样搬运过来的那半套纯逻辑
（见 ``webui/chatlog/display_pipeline.py`` 的模块注释）。这些规则是长期调出来的，
测试的作用是防止搬运/后续改动把它们悄悄改掉：

    - 工具调用与它的返回要配成一项（返回挂在 tool_results 下）
    - 发送回执里的 outbound 不能与真实记录重复显示
    - 四条过滤开关 + 只看图片/文件 的语义
    - 输出必须能 JSON 序列化（要经 HTTP 返回）
"""

from __future__ import annotations

import json

from webui.chatlog.display_pipeline import (
    DEFAULT_VISIBLE_RECORD_LIMIT,
    build_display_items,
    display_item_to_json,
    display_items_json,
    normalize_history_records,
)
from webui.chatlog.models import DisplayItem

PERSONA = "debata"


def _msg(role: str, content: str, **extra) -> dict:
    rec = {"role": role, "content": content, "conversation_id": "private:10001"}
    rec.update(extra)
    return rec


def _kinds(items: list[DisplayItem]) -> list[str]:
    return [i.kind for i in items]


# ============================================================
# 基本归一化
# ============================================================


def test_user_message_becomes_inbound():
    items = normalize_history_records([_msg("user", "你好")], persona_name=PERSONA)
    assert _kinds(items) == ["inbound_message"]
    assert items[0].text == "你好"


def test_assistant_message_becomes_note():
    """历史里的 assistant 文本是"备注"——角色真正发出去的话来自发送回执。"""
    items = normalize_history_records([_msg("assistant", "记住这件事")], persona_name=PERSONA)
    assert _kinds(items) == ["assistant_note"]


def test_tool_message_becomes_tool_result():
    items = normalize_history_records(
        [_msg("tool", "查询结果", tool_call_id="call_1")], persona_name=PERSONA
    )
    assert _kinds(items) == ["tool_result"]
    assert items[0].related_tool_call_id == "call_1"


def test_empty_records():
    assert normalize_history_records([], persona_name=PERSONA) == []


def test_item_ids_are_unique():
    records = [_msg("user", f"消息 {i}") for i in range(5)]
    items = normalize_history_records(records, persona_name=PERSONA)
    ids = [i.item_id for i in items]
    assert len(ids) == len(set(ids))


# ============================================================
# 工具调用 ↔ 结果配对
# ============================================================


def test_tool_result_attaches_to_its_call():
    """带 related_tool_call_id 的工具结果要挂到对应调用下，而不是另起一项。"""
    records = [
        _msg("user", "查一下天气"),
        {
            "role": "assistant",
            "content": "",
            "conversation_id": "private:10001",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"北京"}'},
                }
            ],
        },
        _msg("tool", "晴，25 度", tool_call_id="call_1"),
    ]
    items = normalize_history_records(records, persona_name=PERSONA)
    calls = [i for i in items if i.kind == "tool_call"]
    assert len(calls) == 1, f"应只产生一个 tool_call，实际 {_kinds(items)}"
    # 结果被挂到调用下，因此顶层不再出现独立的 tool_result
    assert [i.kind for i in calls[0].tool_results] == ["tool_result"]
    assert calls[0].tool_results[0].text == "晴，25 度"


def test_orphan_tool_result_stays_top_level():
    """没有对应调用的结果（比如调用记录被截断）不能凭空消失。"""
    items = normalize_history_records(
        [_msg("tool", "孤立的返回", tool_call_id="missing")], persona_name=PERSONA
    )
    assert _kinds(items) == ["tool_result"]


# ============================================================
# 过滤
# ============================================================


def _mixed_records() -> list[dict]:
    return [
        _msg("user", "你好"),
        _msg("assistant", "内部备注"),
        _msg("system", "系统提示"),
        _msg("tool", "工具结果"),
    ]


def test_filter_show_chat_only():
    items = build_display_items(
        _mixed_records(), persona_name=PERSONA,
        show_chat=True, show_system=False, show_tools=False, show_reasoning=False,
    )
    assert set(_kinds(items)) <= {"inbound_message", "outbound_message"}
    assert _kinds(items) == ["inbound_message"]


def test_filter_hides_chat():
    items = build_display_items(
        _mixed_records(), persona_name=PERSONA,
        show_chat=False, show_system=True, show_tools=True, show_reasoning=True,
    )
    assert "inbound_message" not in _kinds(items)


def test_filter_tools_and_system_are_separate_switches():
    """工具与系统是两个独立开关，不能一起开关。"""
    only_tools = build_display_items(
        _mixed_records(), persona_name=PERSONA,
        show_chat=False, show_system=False, show_tools=True, show_reasoning=False,
    )
    assert _kinds(only_tools) == ["tool_result"]

    only_system = build_display_items(
        _mixed_records(), persona_name=PERSONA,
        show_chat=False, show_system=True, show_tools=False, show_reasoning=False,
    )
    assert set(_kinds(only_system)) == {"system_event", "assistant_note"}


def test_search_text_filters_content():
    records = [_msg("user", "今天天气不错"), _msg("user", "明天要下雨")]
    items = build_display_items(records, persona_name=PERSONA, search_text="下雨")
    assert [i.text for i in items] == ["明天要下雨"]


def test_search_text_is_empty_by_default():
    items = build_display_items(_mixed_records(), persona_name=PERSONA)
    assert len(items) >= 4


# ============================================================
# 只看图片/文件（含表情包）
# ============================================================


def test_media_only_keeps_image_records():
    records = [
        _msg("user", "普通文本"),
        _msg("user", "[图片]"),
        _msg("user", "看看 /data/a.png 这个"),
    ]
    items = build_display_items(records, persona_name=PERSONA, media_only=True)
    texts = [i.text for i in items]
    assert "普通文本" not in texts
    assert "[图片]" in texts


def test_media_only_keeps_emoji_labels():
    """表情包以 [表情包: 名字] 文本形式存在，必须算作媒体。

    这条同时锁住 webui/chatlog/display_items.py 的 _EMOJI_LABEL_RE——
    它是"只看图片/文件"能筛出表情包的唯一依据。
    """
    records = [
        _msg("user", "普通文本"),
        {"role": "assistant", "content": "[表情包: 无语]", "conversation_id": "private:10001"},
    ]
    items = build_display_items(records, persona_name=PERSONA, media_only=True)
    assert [i.text for i in items] == ["[表情包: 无语]"]


def test_media_only_keeps_fullwidth_colon_variant():
    records = [{"role": "assistant", "content": "[表情包：无语]", "conversation_id": "private:10001"}]
    items = build_display_items(records, persona_name=PERSONA, media_only=True)
    assert len(items) == 1


def test_media_only_drops_plain_chat():
    records = [_msg("user", "就是一句普通的话")]
    assert build_display_items(records, persona_name=PERSONA, media_only=True) == []


# ============================================================
# JSON 序列化（HTTP 要用）
# ============================================================


def test_display_item_to_json_is_serialisable():
    items = normalize_history_records(
        [_msg("user", "你好", timestamp="2026-01-01T00:00:00")], persona_name=PERSONA
    )
    payload = display_item_to_json(items[0])
    assert payload["kind"] == "inbound_message"
    assert payload["text"] == "你好"
    # raw 体积大且含内部字段，不下发
    assert "raw" not in payload
    json.dumps(payload, ensure_ascii=False)  # 不抛异常即可


def test_display_items_json_nests_tool_results():
    records = [
        {
            "role": "assistant",
            "content": "",
            "conversation_id": "private:10001",
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "search", "arguments": "{}"}}
            ],
        },
        _msg("tool", "结果", tool_call_id="c1"),
    ]
    items = normalize_history_records(records, persona_name=PERSONA)
    payload = json.loads(json.dumps(display_items_json(items), ensure_ascii=False))
    calls = [x for x in payload if x["kind"] == "tool_call"]
    assert len(calls) == 1
    assert len(calls[0]["tool_results"]) == 1
    assert calls[0]["tool_results"][0]["text"] == "结果"


def test_json_payload_has_all_frontend_fields():
    """前端的渲染依赖这些键，缺一个就会出现"来源不明"的空白。"""
    items = normalize_history_records([_msg("user", "你好")], persona_name=PERSONA)
    payload = display_item_to_json(items[0])
    for key in (
        "id", "kind", "timestamp", "speaker_label", "speaker_id", "role_label",
        "text", "summary", "severity", "collapsed_by_default",
        "related_tool_call_id", "related_message_id", "tool_results",
    ):
        assert key in payload, f"少了下发字段 {key}"


# ============================================================
# 分页常量
# ============================================================


def test_default_limit_matches_original_gui():
    """原 GUI chats_page.py:489 是 300，前端步进也是 300。"""
    assert DEFAULT_VISIBLE_RECORD_LIMIT == 300
