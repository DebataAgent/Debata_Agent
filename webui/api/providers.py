"""Provider 管理路由（原 GUI 设置页「模型」分页的增删改部分）。

``webui/api/settings.py`` 里的 ``/api/settings/providers`` 保留为只读兼容入口，
写操作全在这里。逻辑都在 ``webui/provider_admin.py``（纯函数，好单测）。

所有写接口都会落盘并返回 ``needs_restart: true``：provider 实例在
``Runtime.start()`` 里构造，改了配置必须重启才生效。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request

from webui.provider_admin import (
    ProviderAdminError,
    ProviderInUseError,
    ProviderPayload,
    create_provider,
    delete_provider,
    list_providers,
    resolve_probe_target,
    update_provider,
    validate_provider_id,
)

from .deps import require_runtime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/providers", tags=["providers"])


def _presets(rt: Any) -> dict[str, Any]:
    """当前已加载的预设（内置 + 数据目录里的用户预设）。"""
    return dict(getattr(rt.provider_registry, "presets", {}) or {})


def _fail(error: Exception) -> HTTPException:
    """把 admin 层的异常翻成 HTTP 状态码。"""
    if isinstance(error, ProviderInUseError):
        return HTTPException(
            status_code=409,
            detail={"message": str(error), "used_by": error.used_by},
        )
    return HTTPException(status_code=400, detail=str(error))


async def _save(request: Request) -> None:
    host = request.app.state.host
    await asyncio.to_thread(host.save_config)


# ============================================================
# 读
# ============================================================


@router.get("")
async def list_all(request: Request) -> dict[str, Any]:
    """列出全部 provider 与各 Agent 的当前绑定。"""
    rt = require_runtime(request)
    providers = list_providers(
        rt.config, rt.secrets, health=getattr(rt, "provider_health", None)
    )
    agents: dict[str, Any] = {}
    for name in ("chat", "proactive", "summary", "persona_gen"):
        agent = getattr(rt.config.agents, name, None)
        if agent is None:
            agents[name] = None
            continue
        agents[name] = {
            "provider": agent.provider,
            "model": agent.model,
            "temperature": agent.temperature,
            "top_p": getattr(agent, "top_p", 1.0),
            "max_tokens": agent.max_tokens,
            "reasoning": bool(getattr(agent.reasoning, "enabled", False))
            if agent.reasoning
            else False,
        }
    return {"providers": providers, "agents": agents, "presets": sorted(_presets(rt))}


@router.get("/presets")
async def list_presets(request: Request) -> dict[str, Any]:
    """预设清单，供「添加提供商」的下拉用。"""
    rt = require_runtime(request)
    presets = []
    for pid, preset in sorted(_presets(rt).items()):
        presets.append(
            {
                "id": pid,
                "display_name": preset.display_name,
                "protocol": preset.protocol,
                "base_url": preset.base_url,
                "registration_url": preset.registration_url,
                "reasoning_style": getattr(preset, "reasoning_style", ""),
                "models": [
                    {
                        "id": m.id,
                        "display_name": m.display_name,
                        "capabilities": list(m.capabilities),
                        "context_length": m.context_length,
                    }
                    for m in preset.models
                ],
                "has_tutorial": preset.tutorial_dir is not None,
            }
        )
    return {"presets": presets}


# ============================================================
# 写
# ============================================================


@router.post("")
async def create(request: Request, payload: Annotated[ProviderPayload, Body()]) -> dict[str, Any]:
    """新增一个提供商（预设或自定义中转站）。"""
    rt = require_runtime(request)
    try:
        provider_id = create_provider(
            rt.config, rt.secrets, payload, presets=_presets(rt)
        )
    except ProviderAdminError as e:
        raise _fail(e) from e
    await _save(request)
    logger.info("WebUI 新增 provider: %s", provider_id)
    return {
        "ok": True,
        "id": provider_id,
        "needs_restart": True,
        "message": f"已添加 {provider_id}，重启 Runtime 后生效",
    }


@router.patch("/{provider_id}")
async def update(
    request: Request,
    provider_id: str,
    payload: Annotated[ProviderPayload, Body()],
) -> dict[str, Any]:
    """编辑提供商。只改请求体里出现过的字段。"""
    rt = require_runtime(request)
    try:
        validate_provider_id(provider_id)
        changed = update_provider(
            rt.config, rt.secrets, provider_id, payload, presets=_presets(rt)
        )
    except ProviderAdminError as e:
        raise _fail(e) from e
    await _save(request)
    return {
        "ok": True,
        "id": provider_id,
        "changed": changed,
        "needs_restart": True,
        "message": f"已更新 {provider_id}，重启 Runtime 后生效" if changed else "没有改动",
    }


@router.delete("/{provider_id}")
async def remove(
    request: Request,
    provider_id: str,
    drop_key: bool = False,
) -> dict[str, Any]:
    """删除提供商。默认保留已存密钥（``drop_key=true`` 才一并删除）。"""
    rt = require_runtime(request)
    try:
        validate_provider_id(provider_id)
        delete_provider(rt.config, rt.secrets, provider_id, drop_key=drop_key)
    except ProviderAdminError as e:
        raise _fail(e) from e
    await _save(request)
    logger.info("WebUI 删除 provider: %s（drop_key=%s）", provider_id, drop_key)
    return {
        "ok": True,
        "id": provider_id,
        "needs_restart": True,
        "message": f"已删除 {provider_id}，重启 Runtime 后生效",
    }


# ============================================================
# 远端探测：测试连接 / 拉取模型列表
# ============================================================


def _probe_args(rt: Any, payload: ProviderPayload, provider_id: str | None):
    try:
        return resolve_probe_target(
            rt.config, rt.secrets, _presets(rt), payload, provider_id=provider_id
        )
    except ProviderAdminError as e:
        raise _fail(e) from e


@router.post("/test")
async def test_unsaved(request: Request, payload: Annotated[ProviderPayload, Body()]) -> dict[str, Any]:
    """测试一组**尚未保存**的配置能否连通。"""
    rt = require_runtime(request)
    target = _probe_args(rt, payload, None)
    return await _do_test(target)


@router.post("/{provider_id}/test")
async def test_saved(request: Request, provider_id: str) -> dict[str, Any]:
    """测试一个已保存的提供商（走它真实的 SDK 路径与代理设置）。"""
    rt = require_runtime(request)
    try:
        validate_provider_id(provider_id)
    except ProviderAdminError as e:
        raise _fail(e) from e
    if provider_id not in (rt.config.providers or {}):
        raise HTTPException(status_code=404, detail=f"没有 {provider_id} 这个提供商")

    provider = getattr(rt, "providers", {}).get(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=409,
            detail="这个提供商还没在运行中的 Runtime 里装配，先重启一次再测",
        )
    from providers.health import probe_provider_instance

    model = _model_for(rt, provider_id)
    if not model:
        raise HTTPException(status_code=400, detail="这个提供商还没绑定模型，无法测试")

    try:
        probe = await probe_provider_instance(provider, model=model)
    except Exception as e:  # noqa: BLE001
        logger.exception("测试 provider 失败: %s", provider_id)
        raise HTTPException(status_code=502, detail=f"测试未能完成：{e}") from e
    return {
        "ok": probe.status == "ok",
        "status": probe.status,
        "message": probe.message,
        "latency_ms": probe.latency_ms,
        "model": model,
    }


async def _do_test(target: Any) -> dict[str, Any]:
    """用**未保存**的配置发一次最小真实请求。

    走 probe_provider_endpoint（裸 HTTP）而不是构造 provider 实例——后者会建连接池，
    为一个"测一下"付出停机重建的代价不值当。
    """
    from providers.health import probe_provider_endpoint

    try:
        probe = await probe_provider_endpoint(
            protocol=target.protocol,
            base_url=target.base_url,
            api_key=target.api_key,
            model=target.model,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("测试未保存的 provider 配置失败")
        raise HTTPException(status_code=502, detail=f"测试未能完成：{e}") from e
    return {
        "ok": probe.status == "ok",
        "status": probe.status,
        "message": probe.message,
        "latency_ms": probe.latency_ms,
        "model": target.model,
    }


def _model_for(rt: Any, provider_id: str) -> str:
    """找一个绑定到该 provider 的模型名，用来发探测请求。"""
    for name in ("chat", "proactive", "summary", "persona_gen"):
        agent = getattr(rt.config.agents, name, None)
        if agent is not None and agent.provider == provider_id and agent.model:
            return agent.model
    return ""


async def _fetch_models(target: Any, provider_id: str) -> dict[str, Any]:
    from providers.model_fetcher import fetch_model_infos

    try:
        infos = await fetch_model_infos(
            target.base_url, target.api_key, target.protocol, provider_id=provider_id
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "ok": True,
        "models": [
            {
                "id": m.id,
                "display_name": m.display_name or m.id,
                "capabilities": sorted(m.capabilities),
                "context_length": m.context_length,
                "status": m.status,
            }
            for m in infos
        ],
    }


@router.post("/fetch-models")
async def fetch_unsaved(request: Request, payload: Annotated[ProviderPayload, Body()]) -> dict[str, Any]:
    """拉取一组**尚未保存**配置的可用模型列表。"""
    rt = require_runtime(request)
    target = _probe_args(rt, payload, None)
    return await _fetch_models(target, payload.preset or "")


@router.post("/{provider_id}/fetch-models")
async def fetch_saved(request: Request, provider_id: str) -> dict[str, Any]:
    """拉取已保存提供商的模型列表。"""
    rt = require_runtime(request)
    try:
        validate_provider_id(provider_id)
    except ProviderAdminError as e:
        raise _fail(e) from e
    if provider_id not in (rt.config.providers or {}):
        raise HTTPException(status_code=404, detail=f"没有 {provider_id} 这个提供商")
    target = _probe_args(rt, ProviderPayload(), provider_id)
    preset = getattr(rt.config.providers[provider_id], "preset", None) or ""
    return await _fetch_models(target, preset)


__all__ = ["router"]
