"""重要记忆删除回归测试。"""

from __future__ import annotations

import pytest

from memory.important import ImportantMemoryManager


@pytest.mark.asyncio
async def test_important_memory_delete_by_id_is_exact(tmp_path):
    mgr = ImportantMemoryManager(tmp_path / "important.json")
    await mgr.load()
    await mgr.replace_all(
        [
            {"timestamp": "t1", "content": "喜欢红茶"},
            {"timestamp": "t2", "content": "也喜欢红茶蛋糕"},
        ]
    )

    deleted = await mgr.delete_by_id("t1")

    assert deleted is True
    assert [item["timestamp"] for item in mgr.items()] == ["t2"]
