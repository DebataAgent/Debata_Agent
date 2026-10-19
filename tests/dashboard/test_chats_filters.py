"""聊天页会话签名与内联文本压缩回归测试。"""

from __future__ import annotations

from webui.chatlog.grouping import _conversation_list_signature
from webui.chatlog.text_format import _compact_inline_tokens


def test_chats_conversation_signature_tracks_visible_list_changes():
    conversations = [
        {"key": "group:1", "records": [{"content": "a"}], "preview": "a"},
        {"key": "system:global", "records": [{"content": "s"}], "preview": "s"},
    ]

    assert _conversation_list_signature(conversations) == [
        ("group:1", 1, "a"),
        ("system:global", 1, "s"),
    ]


def test_chats_conversation_signature_uses_full_record_count():
    conversations = [
        {
            "key": "group:1",
            "records": [{"content": str(i)} for i in range(325)],
            "preview": "latest",
        }
    ]

    assert _conversation_list_signature(conversations) == [
        ("group:1", 325, "latest")
    ]


def test_chats_compacts_long_links_and_paths():
    long_url = "https://multimedia.nt.qq.com.cn/download?" + ("a" * 120)
    long_path = "C:\\Users\\admin\\.qq-chat-exporter\\exports\\" + ("x" * 100) + ".txt"
    long_json = '{"status":"accepted","items":[' + ",".join('"x"' for _ in range(60)) + "]}"

    compact = _compact_inline_tokens(f"{long_url} {long_path}")
    compact_json = _compact_inline_tokens(long_json)

    assert "[URL multimedia.nt.qq.com.cn/" in compact
    assert "[路径 " in compact
    assert long_url not in compact
    assert long_path not in compact
    assert "[JSON对象" in compact_json
    assert "已折叠" in compact_json
    assert '"items"' not in compact_json
