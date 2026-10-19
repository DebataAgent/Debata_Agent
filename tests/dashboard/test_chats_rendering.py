"""聊天页发送工具调用可读化回归测试。"""

from __future__ import annotations

from webui.chatlog.text_format import _format_tool_call_for_display


def test_chats_formats_send_tool_call_readably():
    text = _format_tool_call_for_display(
        {
            "function": {
                "name": "send_group_message",
                "arguments": (
                    '{"group_id":1039163467,"targets":['
                    '{"content":"好好好 不说了","order":1,"delay":0.5},'
                    '{"content":"那我先待机","order":2,"delay":0.6}]}'
                ),
            }
        }
    )

    assert text == "在群 1039163467 发送消息：好好好 不说了（0.5s）；那我先待机（0.6s）"
