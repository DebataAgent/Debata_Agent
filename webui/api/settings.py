"""设置页接口 —— 声明式字段读写、provider/密钥管理、黑名单。

写盘统一走 ``RuntimeHost.save_config``（等价原 GUI 的 ``_save_now``）：
    - ``needs_restart=False`` 的字段：热更新内存配置 + 立即落盘
    - ``needs_restart=True``  的字段：落盘后由前端提示「需重启 Runtime」

黑名单额外做了一次热应用（``pipeline.apply_blacklist``），保存即生效、无需重启。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from app_config import user_error_text
from webui.config_edit import apply_validated
from webui.settings_spec import SETTINGS_GROUPS, field_map

from . import providers as providers_api
from .deps import get_path_in, require_runtime, set_path_in

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ============================================================
# 声明式字段
# ============================================================


@router.get("")
async def read_settings(request: Request) -> dict[str, Any]:
    """按声明表回读全部字段的当前值。"""
    rt = require_runtime(request)
    cfg = rt.config
    groups: list[dict[str, Any]] = []
    for group in SETTINGS_GROUPS:
        fields = []
        for field in group["fields"]:
            entry = dict(field)
            entry["value"] = get_path_in(cfg, field["path"], field.get("default"))
            fields.append(entry)
        groups.append({k: v for k, v in group.items() if k != "fields"} | {"fields": fields})

    return {
        "groups": groups,
        "paths": _paths_info(rt),
        "secrets_index": _secrets_index(rt),
    }


def _paths_info(rt: Any) -> dict[str, str]:
    paths = rt.paths
    out = {}
    for attr in ("PROJECT_ROOT", "DATA_DIR", "CONFIG_FILE", "LOGS_DIR", "MEMORY_DIR",
                 "PERSONAS_DIR", "WORKSPACE_DIR", "EMOJI_DIR",
                 "PROVIDER_PRESETS_DIR", "USER_PROVIDER_PRESETS_DIR"):
        value = getattr(paths, attr, None)
        if value is not None:
            out[attr] = str(value)
    return out


def _secrets_index(rt: Any) -> list[str]:
    """已保存的密钥 ID 列表（绝不返回密钥本身）。"""
    try:
        return sorted(rt.secrets.list_ids())
    except Exception:  # noqa: BLE001
        try:
            return sorted(rt.secrets._data.keys())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return []


class SettingPatch(BaseModel):
    path: str = Field(..., description="如 behavior.rate_limit.enabled")
    value: Any = None


@router.patch("")
async def patch_setting(
    request: Request, payload: Annotated[SettingPatch, Body()]
) -> dict[str, Any]:
    """改一个字段并立即落盘。"""
    rt = require_runtime(request)
    host = request.app.state.host
    spec = field_map().get(payload.path)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"不认识的配置项：{payload.path}")

    value = _coerce(payload.value, spec)
    cfg = rt.config
    try:
        # 借 pydantic 校验一遍，避免写出非法组合（如枚举值拼错）。
        # 必须改在副本上：直接改内存再校验的话，校验失败虽然回了 400，
        # 坏值却留在 rt.config 里，会被下一次落盘顺手写进 config.yaml。
        apply_validated(cfg, lambda draft: set_path_in(draft, payload.path, value))
    except ValidationError as e:
        raise HTTPException(
            status_code=400, detail=f"设置值不合法：{user_error_text(e)}"
        ) from e
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"配置路径不存在：{e}") from e

    await asyncio.to_thread(host.save_config)
    await _apply_hot(rt, payload.path, value)
    logger.info("配置已更新 %s = %r", payload.path, value)

    return {
        "ok": True,
        "path": payload.path,
        "value": value,
        "needs_restart": bool(spec.get("needs_restart", True)),
        "message": "已保存",
    }


def _coerce(value: Any, spec: dict[str, Any]) -> Any:
    kind = spec.get("type")
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if kind in {"int", "int_or_none"}:
        if value in (None, "", "null") and kind == "int_or_none":
            return None
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "list_int":
        if value in (None, ""):
            return []
        if isinstance(value, str):
            parts = [p for p in value.replace("，", ",").split(",") if p.strip()]
            return [int(p.strip()) for p in parts]
        return [int(v) for v in value]
    if kind == "list_str":
        if value in (None, ""):
            return []
        if isinstance(value, str):
            # 逗号或换行都当作分隔符：用户从别处粘贴列表时两种都常见
            parts = re.split(r"[,\n，]", value)
            return [p.strip() for p in parts if p.strip()]
        return [str(v) for v in value]
    if kind == "json":
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400, detail=f"不是合法的 JSON：{e.msg}（第 {e.lineno} 行）"
                ) from e
        return value
    if kind == "enum":
        allowed = {opt[0] for opt in spec.get("options", [])}
        if allowed and value not in allowed:
            raise HTTPException(status_code=400, detail=f"取值必须是 {sorted(allowed)} 之一")
        return value
    return value


async def _apply_hot(rt: Any, path: str, value: Any) -> None:
    """能立即生效的顺手应用掉。"""
    if path == "app.log_level":
        try:
            logging.getLogger().setLevel(getattr(logging, str(value).upper(), logging.INFO))
        except Exception:  # noqa: BLE001
            logger.debug("热更新日志级别失败", exc_info=True)
    elif path.startswith("adapters.") and ".blacklist" in path:
        _apply_blacklist_hot(rt)


def _apply_blacklist_hot(rt: Any) -> None:
    pipeline = getattr(rt, "pipeline", None)
    if pipeline is None:
        return
    try:
        adapter_cfg = next(iter(rt.config.adapters.values()))
    except StopIteration:
        return
    try:
        pipeline.apply_blacklist(adapter_cfg.blacklist)
    except Exception:  # noqa: BLE001
        logger.exception("热应用黑名单失败")


# ============================================================
# Provider / 密钥
# ============================================================


@router.get("/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    """只读列出 provider 与 Agent 绑定。

    写操作在 ``/api/providers``（``webui/api/providers.py``）。这里保留是为了兼容
    旧前端调用，实现直接复用，避免两份逻辑各自漂移。
    """
    return await providers_api.list_all(request)


class ProviderPatch(BaseModel):
    """改 Agent 的模型绑定与采样参数。

    字段留 None = 不改。``provider`` 是换绑到另一个已存在的提供商——
    必须走这里而不是让前端直接改配置，否则很容易引用一个不存在的 ID。
    """

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    reasoning: bool | None = None
    reasoning_budget: Literal["low", "medium", "high"] | None = None
    first_token_timeout_seconds: float | None = None


@router.patch("/agents/{agent_name}")
async def patch_agent(
    request: Request,
    agent_name: str,
    payload: Annotated[ProviderPatch, Body()],
) -> dict[str, Any]:
    """改某个 Agent 的模型 / 参数。"""
    rt = require_runtime(request)
    host = request.app.state.host
    agent = getattr(rt.config.agents, agent_name, None)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"没有 {agent_name} 这个 Agent")

    if payload.provider is not None and payload.provider not in (rt.config.providers or {}):
        raise HTTPException(
            status_code=400,
            detail=f"没有 {payload.provider} 这个提供商，先在「添加提供商」里建好",
        )

    def mutate(draft: Any) -> None:
        target = getattr(draft.agents, agent_name)
        if payload.provider is not None:
            target.provider = payload.provider
        if payload.model is not None:
            target.model = payload.model
        if payload.temperature is not None:
            target.temperature = float(payload.temperature)
        if payload.top_p is not None:
            target.top_p = float(payload.top_p)
        if payload.max_tokens is not None:
            target.max_tokens = int(payload.max_tokens)
        if payload.first_token_timeout_seconds is not None:
            target.first_token_timeout_seconds = float(payload.first_token_timeout_seconds)
        if payload.reasoning is not None or payload.reasoning_budget is not None:
            if target.reasoning is None:
                from app_config.schema import ReasoningConfig

                target.reasoning = ReasoningConfig(enabled=bool(payload.reasoning))
            elif payload.reasoning is not None:
                target.reasoning.enabled = payload.reasoning
            if payload.reasoning_budget is not None:
                target.reasoning.budget = payload.reasoning_budget

    try:
        apply_validated(rt.config, mutate)
    except ValidationError as e:
        raise HTTPException(
            status_code=400, detail=f"设置值不合法：{user_error_text(e)}"
        ) from e

    await asyncio.to_thread(host.save_config)
    return {"ok": True, "needs_restart": True, "message": f"{agent_name} 已更新，重启后生效"}


class KeyPayload(BaseModel):
    key_id: str = Field(..., min_length=1)
    value: str = Field("", description="留空表示删除这个密钥")


@router.put("/secrets")
async def put_secret(
    request: Request, payload: Annotated[KeyPayload, Body()]
) -> dict[str, Any]:
    """写入或删除一个密钥（只进 secrets.enc，不落 config.yaml）。"""
    rt = require_runtime(request)
    key_id = payload.key_id.strip()
    if not key_id:
        raise HTTPException(status_code=400, detail="key_id 不能为空")

    def _write() -> None:
        if payload.value:
            rt.secrets.set(key_id, payload.value)
        else:
            rt.secrets.delete(key_id)

    try:
        await asyncio.to_thread(_write)
    except Exception as e:  # noqa: BLE001
        logger.exception("写入密钥失败")
        raise HTTPException(status_code=500, detail=f"写入密钥失败：{e}") from e
    return {
        "ok": True,
        "deleted": not payload.value,
        "message": "密钥已删除" if not payload.value else "密钥已保存",
    }


# ============================================================
# 黑名单（独立页面，热生效）
# ============================================================


class BlacklistPayload(BaseModel):
    enabled: bool = True
    qq_ids: list[int] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)


def _blacklist_cfg(rt: Any) -> Any:
    adapters = rt.config.adapters or {}
    if not adapters:
        raise HTTPException(status_code=404, detail="配置里没有任何渠道适配器")
    return next(iter(adapters.values())).blacklist


def _blacklist_json(cfg: Any) -> dict[str, Any]:
    return {
        "enabled": bool(cfg.enabled),
        "qq_ids": list(cfg.qq_ids),
        "group_ids": list(cfg.group_ids),
    }


@router.get("/blacklist")
async def get_blacklist(request: Request) -> dict[str, Any]:
    rt = require_runtime(request)
    data = _blacklist_json(_blacklist_cfg(rt))
    pipeline = getattr(rt, "pipeline", None)
    data["active_qq_ids"] = sorted(getattr(pipeline, "_blacklist_qq_ids", set()) or set())
    data["active_group_ids"] = sorted(getattr(pipeline, "_blacklist_group_ids", set()) or set())
    data["hot_applied"] = True
    return data


@router.put("/blacklist")
async def put_blacklist(
    request: Request,
    payload: Annotated[BlacklistPayload, Body()],
) -> dict[str, Any]:
    """整份替换黑名单，保存后**立即生效**（不用重启）。"""
    rt = require_runtime(request)
    host = request.app.state.host
    cfg = _blacklist_cfg(rt)
    cfg.enabled = payload.enabled
    cfg.qq_ids = sorted({int(x) for x in payload.qq_ids})
    cfg.group_ids = sorted({int(x) for x in payload.group_ids})

    await asyncio.to_thread(host.save_config)
    _apply_blacklist_hot(rt)
    logger.info(
        "黑名单已更新 enabled=%s qq=%s group=%s",
        cfg.enabled,
        cfg.qq_ids,
        cfg.group_ids,
    )
    return {
        "ok": True,
        "needs_restart": False,
        "message": "黑名单已保存并立即生效",
        **_blacklist_json(cfg),
    }


__all__ = ["router"]
