"""首次配置向导 —— 步骤元数据、流转逻辑与文案（Qt-free）。

模块构成（按职责）：
    steps         —— 步骤元数据：每步的 id、标题、副标题、所需字段
    copy          —— 所有用户可见的中文文案（按 key 引用）
    flow          —— 步骤流转逻辑：决定下一步去哪、可不可跳过
    persona_render —— 人格摘要的纯文本渲染

原位于 ``ui/wizard/``，WebUI 化时搬迁至此；Qt 组件（window / components /
step_views）随 ``ui/`` 一并删除，由 ``webui/static/js/pages/setup.js`` 重建。
"""

from __future__ import annotations

from .copy import COPY
from .flow import (
    WIZARD_PATH_CUSTOM,
    WIZARD_PATH_RECOMMENDED,
    is_last_step,
    next_step,
    prev_step,
    progress,
)
from .steps import STEPS, Step, StepId

__all__ = [
    "COPY",
    "STEPS",
    "Step",
    "StepId",
    "WIZARD_PATH_CUSTOM",
    "WIZARD_PATH_RECOMMENDED",
    "is_last_step",
    "next_step",
    "prev_step",
    "progress",
]
