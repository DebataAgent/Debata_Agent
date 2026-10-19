"""测试短期影响（effect）的数量上限与取样顺序。

背景：effect 由模型每轮自行生成，不封顶会无界增长（生产实测 69 条、
约 1 条/2.6 分钟、每条存活十几小时）。除了让角色被陈旧情绪淹没，它还会让
人格维护 prompt 每轮全量回灌几十 K 字符并持续打断前缀缓存。
"""

from __future__ import annotations

from agents.persona.helpers import _cap_cues, _cap_effects
from mind.types import Cue, Effect


def _effect(eid: str, *, created_at: float, effect_type: str = "mood_residue") -> Effect:
    return Effect(
        id=eid,
        name=f"e{eid}",
        effect_type=effect_type,
        intensity=50.0,
        prompt_hint=f"hint-{eid}",
        source_detail="src",
        created_at=created_at,
        expires_at=created_at + 36000,
    )


def test_cap_keeps_newest_per_type():
    effects = [_effect(f"e{i}", created_at=float(i)) for i in range(20)]
    kept, removed = _cap_effects(effects, 10)

    assert len(kept) == 10
    assert len(removed) == 10
    # 保留最新的 10 条（created_at 10..19）
    assert {e.id for e in kept} == {f"e{i}" for i in range(10, 20)}
    assert {i for i in removed} == {f"e{i}" for i in range(10)}


def test_cap_preserves_original_order():
    """调用方依赖「最旧在前」的渲染顺序，收敛后不能被打乱。"""
    effects = [_effect(f"e{i}", created_at=float(i)) for i in range(5)]
    kept, _ = _cap_effects(effects, 3)
    assert [e.created_at for e in kept] == [2.0, 3.0, 4.0]


def test_cap_is_per_effect_type():
    effects = [
        _effect("a1", created_at=1.0, effect_type="mood_residue"),
        _effect("a2", created_at=2.0, effect_type="mood_residue"),
        _effect("a3", created_at=3.0, effect_type="mood_residue"),
        _effect("b1", created_at=1.0, effect_type="body_sense"),
        _effect("b2", created_at=2.0, effect_type="body_sense"),
    ]
    kept, removed = _cap_effects(effects, 2)

    assert set(removed) == {"a1"}
    assert len(kept) == 4


def test_cap_disabled_when_non_positive():
    effects = [_effect(f"e{i}", created_at=float(i)) for i in range(30)]
    kept, removed = _cap_effects(effects, 0)
    assert len(kept) == 30
    assert removed == []


def test_cap_noop_when_under_limit():
    effects = [_effect(f"e{i}", created_at=float(i)) for i in range(3)]
    kept, removed = _cap_effects(effects, 10)
    assert len(kept) == 3
    assert removed == []


def test_cap_is_deterministic_for_equal_created_at():
    """created_at 相同时用 id 兜底，保证结果可复现（影响 prompt 字节稳定性）。"""
    effects = [_effect(f"e{i}", created_at=1.0) for i in range(5)]
    first_kept, first_removed = _cap_effects(list(effects), 3)
    second_kept, second_removed = _cap_effects(list(reversed(effects)), 3)
    assert {e.id for e in first_kept} == {e.id for e in second_kept}
    assert set(first_removed) == set(second_removed)


# ---------------------------------------------------------------------------
# 线索（cue）封顶：与 effect 同源问题，但按 conversation_id 分组
# ---------------------------------------------------------------------------


def _cue(cid: str, *, created_at: float, conversation: str = "group:1") -> Cue:
    return Cue(
        id=cid,
        cue_type="pending_intent",
        summary=f"s-{cid}",
        conversation_id=conversation,
        created_at=created_at,
        expires_at=created_at + 36000,
    )


def test_cap_cues_is_per_conversation():
    """话多的群不能把其它会话的线索挤掉。"""
    cues = [_cue(f"a{i}", created_at=float(i), conversation="group:A") for i in range(15)]
    cues += [_cue(f"b{i}", created_at=float(i), conversation="group:B") for i in range(3)]

    kept, removed = _cap_cues(cues, 10)

    assert len(kept) == 13  # A 留 10 + B 留 3
    assert set(removed) == {f"a{i}" for i in range(5)}
    assert {c.id for c in kept if c.conversation_id == "group:B"} == {"b0", "b1", "b2"}


def test_cap_cues_keeps_newest():
    cues = [_cue(f"c{i}", created_at=float(i)) for i in range(20)]
    kept, _ = _cap_cues(cues, 10)
    assert {c.id for c in kept} == {f"c{i}" for i in range(10, 20)}


def test_cap_cues_disabled_when_non_positive():
    cues = [_cue(f"c{i}", created_at=float(i)) for i in range(30)]
    kept, removed = _cap_cues(cues, 0)
    assert len(kept) == 30
    assert removed == []


def test_cap_cues_preserves_order():
    cues = [_cue(f"c{i}", created_at=float(i)) for i in range(5)]
    kept, _ = _cap_cues(cues, 3)
    assert [c.created_at for c in kept] == [2.0, 3.0, 4.0]


def test_cap_cues_global_limit_drops_globally_oldest():
    """只有每会话上限时，低频会话的旧线索会无限期留着 —— 全局上限补这一刀。"""
    cues = [_cue("old1", created_at=1.0, conversation="private:9")]
    cues += [_cue(f"b{i}", created_at=float(100 + i), conversation="group:B") for i in range(15)]

    # 每会话上限不删 private:9 的那条（该会话只有 1 条），全局上限才删得掉
    kept_pc, removed_pc = _cap_cues(cues, 10, 0)
    assert "old1" in {c.id for c in kept_pc}

    kept, removed = _cap_cues(cues, 10, 5)
    assert len(kept) == 5
    assert "old1" in removed
    assert {c.id for c in kept} == {f"b{i}" for i in range(10, 15)}


def test_cap_cues_global_limit_keeps_newest():
    cues = [_cue(f"c{i}", created_at=float(i)) for i in range(30)]
    kept, _ = _cap_cues(cues, 0, 20)
    assert {c.id for c in kept} == {f"c{i}" for i in range(10, 30)}


def test_cap_cues_both_disabled_is_noop():
    cues = [_cue(f"c{i}", created_at=float(i)) for i in range(30)]
    kept, removed = _cap_cues(cues, 0, 0)
    assert len(kept) == 30
    assert removed == []
