"""WebUI HTTP / WebSocket 路由包。

每个模块对应原 PySide6 仪表盘的一个页面：

    system.py    登录、运行状态、首次配置向导、重启退出（无对应页面，WebUI 新增）
    overview.py  总览      <- ui/dashboard/overview_page.py
    chats.py     对话      <- ui/dashboard/chats_page.py
    memory.py    记忆      <- ui/dashboard/memory_page.py
    mind.py      人格后台  <- ui/dashboard/persona_mind_page.py
    logs.py      日志      <- ui/dashboard/logs_page.py
    personas.py  角色      <- ui/dashboard/personas_page.py
    models.py    模型管理  <- ui/dashboard/models_page.py
    emoji.py     表情包    <- ui/dashboard/emoji_section.py（WebUI 独立成页）
    providers.py 提供商增删改 <- ui/dashboard/settings/model.py 的 CRUD 部分
    settings.py  设置 + 黑名单（黑名单为 WebUI 新增功能）
"""

from __future__ import annotations

from . import (
    chats,
    emoji,
    logs,
    memory,
    mind,
    models,
    overview,
    personas,
    providers,
    settings,
    system,
)

# 注册顺序即 OpenAPI 里的分组顺序
ALL_ROUTERS = [
    system.router,
    overview.router,
    chats.router,
    memory.router,
    mind.router,
    logs.router,
    personas.router,
    models.router,
    emoji.router,
    providers.router,
    settings.router,
]

__all__ = ["ALL_ROUTERS"]
