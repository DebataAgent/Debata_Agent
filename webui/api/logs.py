"""日志页接口 —— 历史拉取与过滤。

实时流走 ``/ws/events``（``type == "log"``），这里只负责首屏历史和按条件过滤，
对应原 GUI ``ui/dashboard/logs_page.py`` 的等级 / 模块 / 关键字三重过滤。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from webui.logbuffer import LEVEL_NAMES, get_log_buffer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])

LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


@router.get("")
async def read_logs(
    request: Request,
    limit: int = Query(300, ge=1, le=2000),
    level: str = Query("", description="最低等级：DEBUG/INFO/WARNING/ERROR"),
    module: str = Query("", description="logger 名前缀"),
    q: str = Query("", description="正文关键字"),
) -> dict[str, Any]:
    buffer = get_log_buffer()
    if buffer is None:
        return {"records": [], "available": False, "levels": list(LEVEL_ORDER)}

    records = buffer.tail(limit * 4 if (level or module or q) else limit)

    if level:
        threshold = LEVEL_ORDER.get(level.upper(), 0)
        records = [r for r in records if r.get("level_no", 0) >= threshold]
    if module:
        prefix = module.strip().lower()
        records = [r for r in records if prefix in str(r.get("logger", "")).lower()]
    if q:
        needle = q.strip().lower()
        records = [
            r
            for r in records
            if needle in str(r.get("message", "")).lower()
            or needle in str(r.get("logger", "")).lower()
        ]

    return {
        "records": records[-limit:],
        "available": True,
        "levels": list(LEVEL_ORDER),
        "level_names": LEVEL_NAMES,
        "buffer_size": len(buffer),
    }


__all__ = ["router"]
