"""对话页合并结果缓存测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from webui.chatlog import records as records_module


class FakeHistory:
    def __init__(self) -> None:
        self._length = 1
        self._records = [{"role": "user", "content": "hello"}]

    async def length(self) -> int:
        return self._length

    async def records(self) -> list[dict]:
        return list(self._records)


@pytest.mark.asyncio
async def test_chat_page_cache_hits_and_invalidates_on_history_growth(monkeypatch):
    records_module.clear_chat_page_cache()
    merge_calls: list[int] = []

    def fake_merge(**_kwargs):
        merge_calls.append(1)
        return []

    monkeypatch.setattr(
        records_module,
        "_merge_and_tag_chat_page_records",
        fake_merge,
    )
    history = FakeHistory()
    runtime = SimpleNamespace(
        history=history,
        event_store=None,
        archive=None,
        pipeline=None,
    )

    assert await records_module._load_chat_page_records(runtime) == []
    assert await records_module._load_chat_page_records(runtime) == []
    assert len(merge_calls) == 1

    history._length = 2
    assert await records_module._load_chat_page_records(runtime) == []
    assert len(merge_calls) == 2

    records_module.clear_chat_page_cache()
