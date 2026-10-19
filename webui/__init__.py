"""Debata_Agent WebUI —— 取代原 PySide6 桌面的浏览器界面。

模块构成（按职责）：
    server       —— FastAPI 应用装配 + uvicorn 启动
    runtime_host —— Runtime 生命周期宿主（后台线程事件循环）
    auth         —— 局域网访问令牌鉴权
    logbuffer    —— 日志环形缓冲 + WebSocket 实时推送
    api/         —— 各页面对应的 HTTP / WebSocket 路由
    chatlog/     —— 会话记录合并与渲染（原 ui/dashboard/chats/，Qt-free）
    wizard/      —— 首次配置向导的步骤定义与文案（原 ui/wizard/，Qt-free）
    theme        —— 色板常量（原 ui/theme/qss.py 的纯数据部分）
    copy         —— 全部用户可见中文文案（原 ui/dashboard/copy.py）

原 PySide6 的 ``ui/`` 目录已整体移除，其中与渲染无关的纯逻辑搬迁至此，
见 ``docs/webui.md``。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
