"""API 路由共用的依赖与工具。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def get_host(request: Request) -> Any:
    return request.app.state.host


def get_hub(request: Request) -> Any:
    return request.app.state.hub


def get_auth(request: Request) -> Any:
    return request.app.state.auth


def require_runtime(request: Request) -> Any:
    """取已就绪的 Runtime，未就绪时抛 503（前端据此跳首次配置页）。"""
    host = request.app.state.host
    rt = host.rt
    if rt is None or host.state != "running":
        raise HTTPException(
            status_code=503,
            detail={
                "reason": host.state,
                "message": host.last_error or "Runtime 尚未就绪",
            },
        )
    return rt


def get_path_in(obj: Any, path: str, default: Any = None) -> Any:
    """按 ``a.b.c`` 路径取值，任一层缺失返回 default。"""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(part, None)
        else:
            cur = getattr(cur, part, None)
    return default if cur is None else cur


def set_path_in(obj: Any, path: str, value: Any) -> None:
    """按 ``a.b.c`` 路径赋值；中间层不存在时抛 KeyError。

    pydantic 模型禁止给未声明字段赋值，所以这里对缺失的中间层直接报错，
    而不是偷偷造一个 —— 免得配置里出现 schema 不认识的野字段。
    """
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(f"配置路径不存在：{path}（在 {part} 处断开）")
            cur = cur[part]
        else:
            if not hasattr(cur, part):
                raise KeyError(f"配置路径不存在：{path}（在 {part} 处断开）")
            cur = getattr(cur, part)
    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
    else:
        if not hasattr(cur, last):
            raise KeyError(f"配置路径不存在：{path}")
        setattr(cur, last, value)


__all__ = [
    "get_auth",
    "get_host",
    "get_hub",
    "get_path_in",
    "require_runtime",
    "set_path_in",
]
