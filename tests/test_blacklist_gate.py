"""黑名单入站拦截测试。

覆盖 WebUI「黑名单」页背后的核心保证：
    - 命中黑名单的消息在 ``MessagePipeline.enqueue`` 里就被丢弃，
      不会进入 chat_timeline（因而也不会入库、不会触发模型调用）
    - QQ 黑名单与群黑名单互相独立
    - ``enabled=False`` 时名单保留但不生效
    - ``apply_blacklist`` 是热更新：调用后立刻改变拦截行为，无需重建 pipeline
"""

from __future__ import annotations

import pytest

from app_config.schema import BlacklistConfig

from .integration_pipeline.helpers import _msg

pytestmark = pytest.mark.asyncio


def _conversations(pipeline) -> list[str]:
    return list(pipeline.chat_timeline.conversation_ids())


async def test_blacklisted_private_sender_is_dropped(build_pipeline):
    pipeline, _provider, _adapter, _history, _important = await build_pipeline([])

    pipeline.apply_blacklist(BlacklistConfig(enabled=True, qq_ids=[10001]))

    await pipeline.enqueue(_msg(user_id="10001", text="你还在吗"))
    assert _conversations(pipeline) == [], "被拉黑的私聊消息不该进入时间线"

    await pipeline.enqueue(_msg(user_id="20002", text="我是路人"))
    assert _conversations(pipeline) == ["private:20002"]


async def test_blacklisted_group_is_dropped(build_pipeline):
    pipeline, _provider, _adapter, _history, _important = await build_pipeline([])

    pipeline.apply_blacklist(BlacklistConfig(enabled=True, group_ids=[987654]))

    await pipeline.enqueue(_msg(group_id="987654", text="群里说话"))
    assert _conversations(pipeline) == [], "被拉黑的群消息不该进入时间线"

    await pipeline.enqueue(_msg(group_id="111111", text="别的群"))
    assert _conversations(pipeline) == ["group:111111"]


async def test_group_blacklist_also_drops_its_members(build_pipeline):
    """群被拉黑时，群成员的发言同样丢弃（不需要逐个拉黑成员）。"""
    pipeline, _provider, _adapter, _history, _important = await build_pipeline([])

    pipeline.apply_blacklist(BlacklistConfig(enabled=True, group_ids=[5555]))

    await pipeline.enqueue(_msg(user_id="30003", group_id="5555", text="hi"))
    assert _conversations(pipeline) == []


async def test_disabled_blacklist_keeps_names_but_stops_filtering(build_pipeline):
    pipeline, _provider, _adapter, _history, _important = await build_pipeline([])

    pipeline.apply_blacklist(BlacklistConfig(enabled=True, qq_ids=[10001]))
    await pipeline.enqueue(_msg(user_id="10001", text="第一条"))
    assert _conversations(pipeline) == []

    # 关掉开关，名单内容保留
    pipeline.apply_blacklist(BlacklistConfig(enabled=False, qq_ids=[10001]))
    assert pipeline.blacklist.qq_ids == [10001]
    assert pipeline._blacklist_qq_ids == set()

    await pipeline.enqueue(_msg(user_id="10001", text="第二条"))
    assert _conversations(pipeline) == ["private:10001"], "关掉开关后应重新放行"


async def test_apply_blacklist_is_hot(build_pipeline):
    """同一套 pipeline 实例上反复切换，行为立即跟随，不需要重启。"""
    pipeline, _provider, _adapter, _history, _important = await build_pipeline([])

    assert pipeline._blacklist_qq_ids == set()

    pipeline.apply_blacklist(BlacklistConfig(enabled=True, qq_ids=[10001, 10002]))
    assert pipeline._blacklist_qq_ids == {10001, 10002}

    pipeline.apply_blacklist(BlacklistConfig(enabled=True, qq_ids=[10002]))
    assert pipeline._blacklist_qq_ids == {10002}

    await pipeline.enqueue(_msg(user_id="10001", text="已解除"))
    await pipeline.enqueue(_msg(user_id="10002", text="仍在名单"))
    assert _conversations(pipeline) == ["private:10001"]


async def test_blacklist_takes_priority_over_open_whitelist(build_pipeline):
    """白名单设为 open（对所有人开放）时，黑名单依然拦得住。"""
    from app_config.schema import WhitelistConfig

    pipeline, _provider, _adapter, _history, _important = await build_pipeline([])

    pipeline.whitelist = WhitelistConfig(mode="open")
    pipeline._whitelist_qq_ids = set()
    pipeline._whitelist_group_ids = set()
    pipeline.apply_blacklist(BlacklistConfig(enabled=True, qq_ids=[10001]))

    await pipeline.enqueue(_msg(user_id="10001", text="开放也别想"))
    await pipeline.enqueue(_msg(user_id="20002", text="正常"))
    assert _conversations(pipeline) == ["private:20002"]
