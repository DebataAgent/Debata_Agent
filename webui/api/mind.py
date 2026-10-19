"""人格后台页接口 —— 实时状态快照 + 6 个只读轨迹标签页。

对应原 GUI 的 ``ui/dashboard/persona_mind_page.py``，数据源是
``rt.persona_db``（sqlite）与 ``rt.persona_agent.get_state_snapshot()``。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from .deps import require_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mind", tags=["mind"])

# 标签页 key -> (标题, persona_db 方法, 参数名)
TABS: dict[str, tuple[str, str, str]] = {
    "effects": ("短期影响", "get_active_effects", ""),
    "todos": ("待办", "get_todos", ""),
    "cues": ("线索", "get_cues", ""),
    "profiles": ("用户画像", "all_profiles", ""),
    "monologues": ("内心独白", "recent_monologues", "limit"),
    "trajectories": ("整理轨迹", "recent_trajectories", "limit"),
    "state_logs": ("状态日志", "recent_state_logs", "limit"),
    "sleep": ("睡眠记录", "recent_sleep_records", "limit"),
    "eat": ("进食记录", "recent_eat_records", "limit"),
    "arc": ("人格弧光", "recent_arc_events", "limit"),
}


def _to_json(value: Any) -> Any:
    """dataclass / 简单对象 -> JSON 友好的 dict。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_json(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "__dict__"):
        return {
            k: _to_json(v)
            for k, v in vars(value).items()
            if not k.startswith("_") and not callable(v)
        }
    return str(value)


def _available(rt: Any) -> bool:
    cfg = getattr(rt, "config", None)
    enabled = bool(getattr(getattr(cfg, "persona_management", None), "enabled", False))
    return enabled and getattr(rt, "persona_agent", None) is not None


@router.get("/state")
async def state(request: Request) -> dict[str, Any]:
    """顶部两张卡片：运行概况 + 实时状态。"""
    rt = require_runtime(request)
    if not _available(rt):
        return {"available": False, "reason": "人格后台未启用（persona_management.enabled=false）"}

    agent = rt.persona_agent
    snapshot: dict[str, Any] = {}
    try:
        snapshot = _to_json(agent.get_state_snapshot())
    except Exception as e:  # noqa: BLE001
        logger.warning("读取人格状态快照失败：%s", e)
        return {"available": False, "reason": f"读取状态失败：{e}"}

    cfg = rt.config
    phys = getattr(cfg.persona_management, "physiology", None)
    return {
        "available": True,
        "state": snapshot,
        "activity": dict(getattr(rt, "model_activity", None) or {}),
        "physiology": {
            "energy_mode": getattr(getattr(phys, "energy", None), "mode", "off"),
            "satiety_mode": getattr(getattr(phys, "satiety", None), "mode", "off"),
        },
        "is_resting": _safe_bool(getattr(agent, "is_resting", None)),
        "persona": getattr(getattr(rt, "persona", None), "name", ""),
        "age": _age_of(rt),
    }


def _safe_bool(maybe_callable: Any) -> bool:
    if maybe_callable is None:
        return False
    try:
        return bool(maybe_callable() if callable(maybe_callable) else maybe_callable)
    except Exception:  # noqa: BLE001
        return False


def _age_of(rt: Any) -> dict[str, Any] | None:
    """当前人格的年龄设定（覆盖值优先，否则用默认）。"""
    try:
        mgmt = rt.config.persona_management
        active = rt.config.persona.active
        age_cfg = getattr(mgmt, "age", None)
        if age_cfg is None:
            return None
        overrides = getattr(age_cfg, "overrides", None) or {}
        value = overrides.get(active)
        if value is None:
            value = getattr(age_cfg, "default_age", None)
        getter = getattr(getattr(rt, "persona", None), "get_age", None)
        return {
            "configured": value,
            "effective": getter() if callable(getter) else value,
        }
    except Exception:  # noqa: BLE001
        return None


@router.get("/tab/{tab_key}")
async def tab(
    request: Request,
    tab_key: str,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    rt = require_runtime(request)
    if tab_key not in TABS:
        return {"available": False, "reason": f"未知标签页 {tab_key!r}"}
    if not _available(rt):
        return {"available": False, "reason": "人格后台未启用"}
    db = getattr(rt, "persona_db", None)
    if db is None:
        return {"available": False, "reason": "人格数据库未就绪"}

    title, method_name, arg = TABS[tab_key]
    method = getattr(db, method_name, None)
    if method is None:
        return {"available": False, "reason": f"数据库不支持 {method_name}"}

    try:
        if arg == "limit":
            rows = await method(limit=limit)
        elif method_name == "get_todos":
            rows = await method(include_completed=False)
        else:
            rows = await method()
    except Exception as e:  # noqa: BLE001
        logger.exception("读取人格后台标签页 %s 失败", tab_key)
        return {"available": False, "reason": f"读取失败：{e}"}

    return {
        "available": True,
        "key": tab_key,
        "title": title,
        "items": [_to_json(r) for r in (rows or [])],
    }


__all__ = ["router"]
