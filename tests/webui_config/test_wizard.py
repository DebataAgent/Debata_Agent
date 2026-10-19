"""首次配置向导步骤元数据与流转测试。"""

from __future__ import annotations

from webui.wizard.flow import next_step
from webui.wizard.steps import STEPS, StepId


def test_wizard_embedding_step_is_fixed_in_flow():
    assert next_step(
        StepId.FEATURES,
        "recommended",
        {"long_term_memory_mode": "file", "persona_source": "builtin"},
    ) == StepId.EMBEDDING


def test_wizard_embedding_step_metadata_uses_rag_enhancement_copy():
    text = STEPS[StepId.EMBEDDING].subtitle

    assert "重要记忆始终启用" in text
    assert "RAG 历史向量召回增强" in text
    assert "普通文件模式更轻" not in text
    assert "RAG 模式会用 embedding" not in text


# ============================================================
# /api/setup/step：前端不再自己判断流转
# ============================================================


class _FakeRt:
    pass


async def _step(**body):
    from types import SimpleNamespace

    from webui.api.system import setup_step

    return await setup_step(SimpleNamespace(), body)


async def _walk(path: str, context: dict | None = None) -> list[tuple[str, list[int], bool]]:
    context = context or {}
    seen: list[tuple[str, list[int], bool]] = []
    current = "welcome"
    for _ in range(12):
        out = await _step(current=current, path=path, direction="next", context=context)
        seen.append((out["current"], out["progress"], out["is_last"]))
        if not out["next"]:
            break
        current = out["next"]
    return seen


async def test_recommended_path_walks_seven_steps():
    steps = [row[0] for row in await _walk("recommended")]
    assert steps == [
        "welcome", "main_model_quick", "features", "embedding",
        "adapter", "persona", "summary",
    ]


async def test_custom_path_walks_eight_steps():
    steps = [row[0] for row in await _walk("custom")]
    assert steps == [
        "welcome", "main_model_custom", "other_agents", "features",
        "embedding", "adapter", "persona", "summary",
    ]


async def test_persona_create_branch_is_inserted():
    """选了"生成人格"就多出一步 PERSONA_CREATE，进度总数也跟着变。"""
    steps = [row[0] for row in await _walk("custom", {"persona_source": "create"})]
    assert "persona_create" in steps
    assert steps.index("persona_create") == steps.index("persona") + 1
    assert steps[-1] == "summary"


async def test_progress_reaches_total_on_last_step():
    rows = await _walk("recommended")
    index, total = rows[-1][1]
    assert index == total
    assert rows[-1][2] is True


async def test_prev_from_adapter_goes_to_embedding():
    out = await _step(current="adapter", path="recommended", direction="prev")
    assert out["prev"] == "embedding"


async def test_prev_from_welcome_is_none():
    out = await _step(current="welcome", path="recommended", direction="prev")
    assert out["prev"] is None


async def test_step_returns_metadata_for_next():
    out = await _step(current="welcome", path="recommended", direction="next")
    assert out["next"] == "main_model_quick"
    assert out["next_meta"]["id"] == "main_model_quick"
    assert out["next_meta"]["title"]


async def test_unknown_step_is_400():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _step(current="nope", path="recommended", direction="next")
    assert excinfo.value.status_code == 400


async def test_unknown_path_is_400():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _step(current="welcome", path="warp", direction="next")
    assert excinfo.value.status_code == 400


async def test_context_must_be_object():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _step(current="welcome", path="recommended", context=["not", "a", "dict"])
    assert excinfo.value.status_code == 400
