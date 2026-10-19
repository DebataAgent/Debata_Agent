"""总览页接口 —— 主模型活动、模型健康、渠道状态、用量统计。

对应原 GUI 的 ``ui/dashboard/overview_page.py``（4 张统计卡）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request

from .deps import get_host, require_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/overview", tags=["overview"])

USAGE_RANGES = ("today", "7d", "30d", "all")


def _provider_health(rt: Any) -> list[dict[str, Any]]:
    """把 ``rt.provider_health`` 摊平成前端好渲染的列表。"""
    out: list[dict[str, Any]] = []
    health = getattr(rt, "provider_health", None) or {}
    providers = getattr(rt, "providers", None) or {}
    for pid in sorted(set(providers) | set(health)):
        item = health.get(pid)
        if item is None:
            out.append({"id": pid, "status": "unknown", "text": "未检测",
                        "latency_ms": None, "message": ""})
            continue
        status = getattr(item, "status", "unknown")
        out.append(
            {
                "id": pid,
                "status": status,
                "text": _health_text(status),
                "latency_ms": getattr(item, "latency_ms", None),
                "message": getattr(item, "message", "") or "",
            }
        )
    return out


def _health_text(status: str) -> str:
    return {
        "ok": "正常",
        "healthy": "正常",
        "degraded": "较慢",
        "error": "出错了",
        "unconfigured": "未配置",
        "unknown": "未检测",
    }.get(status, status or "未检测")


def _usage_dict(rt: Any, range_name: str) -> dict[str, Any]:
    store = getattr(rt, "usage_stats", None)
    if store is None:
        return {"range": range_name, "available": False}
    try:
        summary = store.summarize(range_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取用量统计失败：%s", e)
        return {"range": range_name, "available": False, "error": str(e)}
    data = {"range": range_name, "available": True}
    for key in (
        "request_count",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "cache_creation_tokens",
        "total_tokens",
        "cache_hit_rate",
        "uncached_prompt_tokens",
        "estimated_prompt_tokens",
        "estimate_actual_prompt_tokens",
        "estimate_abs_error_tokens",
        "estimate_samples",
        "estimate_error_rate",
    ):
        data[key] = getattr(summary, key, 0)
    return data


@router.get("")
async def overview(request: Request, range: str = Query("today")) -> dict[str, Any]:
    """总览页一次性拿全（前端每秒轮询）。"""
    if range not in USAGE_RANGES:
        range = "today"
    host = get_host(request)
    rt = require_runtime(request)

    activity = dict(getattr(rt, "model_activity", None) or {})
    activity.setdefault("state", "idle")
    activity.setdefault("text", "空闲")
    activity.setdefault("model", "")
    activity.setdefault("agent", "主模型")

    status = host.status()
    status["setup_required"] = False
    return {
        "status": status,
        "activity": activity,
        "providers": _provider_health(rt),
        "usage": _usage_dict(rt, range),
        "server_time": time.time(),
    }


@router.get("/usage")
async def usage(request: Request, range: str = Query("today")) -> dict[str, Any]:
    if range not in USAGE_RANGES:
        range = "today"
    return _usage_dict(require_runtime(request), range)


__all__ = ["router"]
