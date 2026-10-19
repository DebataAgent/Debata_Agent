"""系统类接口：登录、运行状态、首次配置向导、重启/退出。"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, Response
from pydantic import BaseModel, Field

from webui.auth import COOKIE_NAME, SESSION_TTL_SECONDS
from webui.runtime_host import STATE_SETUP_REQUIRED

from .deps import get_auth, get_host

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

COOKIE_MAX_AGE = SESSION_TTL_SECONDS


class LoginPayload(BaseModel):
    token: str = Field(default="", description="启动横幅里打印的访问令牌")


@router.get("/api/auth/state")
async def auth_state(request: Request) -> dict[str, Any]:
    """前端启动时问一句：要不要登录、我现在登录了吗。"""
    auth = get_auth(request)
    return {
        "auth_enabled": auth.enabled,
        "authenticated": auth.request_authorized(request),
    }


@router.post("/api/auth/login")
async def login(
    request: Request,
    response: Response,
    payload: Annotated[LoginPayload, Body()],
) -> dict[str, Any]:
    auth = get_auth(request)
    if not auth.enabled:
        return {"ok": True, "message": "鉴权已关闭"}
    if not auth.token_matches(payload.token.strip()):
        raise HTTPException(status_code=401, detail="令牌不正确")
    sid = auth.new_session()
    response.set_cookie(
        COOKIE_NAME,
        sid,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "message": "登录成功"}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    auth = get_auth(request)
    auth.drop_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    host = get_host(request)
    info = host.status()
    info["setup_required"] = host.state == STATE_SETUP_REQUIRED
    return info


@router.post("/api/system/restart")
async def restart_runtime(request: Request) -> dict[str, Any]:
    """重启 Runtime（改完需要重启的配置后用）。"""
    host = get_host(request)
    hub = request.app.state.hub
    ok = await host.restart()
    hub.attach_timeline()
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=host.last_error or "Runtime 重启失败，请看日志",
        )
    return {"ok": True, "message": "Runtime 已重启"}


@router.post("/api/system/stop")
async def stop_runtime(request: Request) -> dict[str, Any]:
    host = get_host(request)
    await host.stop()
    return {"ok": True, "message": "Runtime 已停止"}


@router.post("/api/system/start")
async def start_runtime(request: Request) -> dict[str, Any]:
    host = get_host(request)
    hub = request.app.state.hub
    ok = await host.start()
    hub.attach_timeline()
    if not ok:
        raise HTTPException(status_code=500, detail=host.last_error or "Runtime 启动失败")
    return {"ok": True, "message": "Runtime 已启动"}


@router.post("/api/system/quit")
async def quit_app(request: Request) -> dict[str, Any]:
    """停掉 Runtime 并让整个 WebUI 进程退出。"""
    host = get_host(request)
    server = getattr(request.app.state, "server", None)
    host.request_stop()
    if server is not None:
        server.should_exit = True
    return {"ok": True, "message": "正在退出"}


# ============================================================
# 首次配置向导
# ============================================================


@router.get("/api/setup/options")
async def setup_options(request: Request) -> dict[str, Any]:
    """向导要用的下拉数据：provider 预设、人格列表、默认值。"""
    host = get_host(request)
    paths = host.build_paths()

    presets: list[dict[str, Any]] = []
    try:
        from providers.presets_loader import load_presets_for

        for pid, preset in sorted(load_presets_for(paths).items()):
            presets.append(
                {
                    "id": pid,
                    "display_name": preset.display_name,
                    "protocol": getattr(preset, "protocol", "openai_compat"),
                    "base_url": getattr(preset, "base_url", ""),
                    "registration_url": getattr(preset, "registration_url", ""),
                    "reasoning_style": getattr(preset, "reasoning_style", ""),
                    "models": [
                        {
                            "id": m.id,
                            "display_name": getattr(m, "display_name", m.id),
                            "capabilities": list(getattr(m, "capabilities", []) or []),
                            "context_length": getattr(m, "context_length", 0),
                        }
                        for m in (preset.models or [])
                    ],
                    "tutorial_dir": str(getattr(preset, "tutorial_dir", "") or ""),
                }
            )
    except Exception:  # noqa: BLE001
        logger.exception("加载 provider 预设失败")

    personas: list[dict[str, Any]] = []
    try:
        from agents import list_available_personas

        personas = [{"id": name, "name": name} for name in list_available_personas(paths)]
    except Exception:  # noqa: BLE001
        logger.exception("枚举人格失败")

    # 默认模型不再写死 deepseek：取第一个预设的首个模型，或能力矩阵的推荐值。
    # 内置预设排序后 anthropic 排最前，所以优先用能力矩阵的推荐，取不到再退回。
    default_provider, default_model = _default_provider_model(presets)

    return {
        "presets": presets,
        "personas": personas,
        "steps": _steps_meta(),
        "config_file": str(paths.CONFIG_FILE),
        "data_dir": str(paths.DATA_DIR),
        "defaults": {
            "provider_id": default_provider or "main_provider",
            "preset": default_provider,
            "model": default_model,
            "persona": "debata",
            "adapter": {
                "mode": "client",
                "host": "127.0.0.1",
                "port": 3000,
                "path": "/",
                "whitelist_mode": "verify",
            },
        },
    }


def _default_provider_model(presets: list[dict[str, Any]]) -> tuple[str, str]:
    """向导的默认 provider / 模型。

    优先问能力矩阵要一个"推荐"模型；查不到就取第一个有模型的预设。
    返回 ("", "") 表示一个预设都没有（此时前端让用户手填）。
    """
    try:
        from providers.model_capabilities import recommended_model

        best = recommended_model("deepseek")
        if best is not None:
            return "deepseek", best.id
    except Exception:  # noqa: BLE001
        logger.debug("能力矩阵推荐模型失败", exc_info=True)

    for preset in presets:
        models = preset.get("models") or []
        if models:
            return str(preset["id"]), str(models[0]["id"])
    return "", ""


def _steps_meta() -> list[dict[str, Any]]:
    """向导全部步骤的元数据，前端据此渲染进度条与标题。"""
    try:
        from webui.wizard.steps import STEPS

        return [
            {
                "id": step.id.value,
                "title": step.title,
                "subtitle": step.subtitle,
                "can_skip": bool(step.can_skip),
            }
            for step in STEPS.values()
        ]
    except Exception:  # noqa: BLE001
        logger.exception("加载向导步骤元数据失败")
        return []


@router.post("/api/setup/step")
async def setup_step(request: Request, body: Annotated[dict, Body()]) -> dict[str, Any]:
    """向导的步骤流转。

    流转规则全在 ``webui/wizard/flow.py``（原桌面端原样搬过来的），前端只负责渲染，
    不自己判断"下一步去哪"——两条路径 + PERSONA_CREATE 分支的判断写在前端一定会漂。
    """
    from webui.wizard.flow import (
        WIZARD_PATH_CUSTOM,
        WIZARD_PATH_RECOMMENDED,
        is_last_step,
        next_step,
        prev_step,
        progress,
    )
    from webui.wizard.steps import STEPS, StepId

    direction = str(body.get("direction") or "next")
    path = str(body.get("path") or WIZARD_PATH_RECOMMENDED)
    if path not in (WIZARD_PATH_RECOMMENDED, WIZARD_PATH_CUSTOM):
        raise HTTPException(status_code=400, detail=f"未知路径：{path}")

    try:
        current = StepId(str(body.get("current") or StepId.WELCOME.value))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"未知步骤：{body.get('current')}") from e

    context = body.get("context") or {}
    if not isinstance(context, dict):
        raise HTTPException(status_code=400, detail="context 必须是对象")

    target = prev_step(current, path, context) if direction == "prev" else next_step(
        current, path, context
    )
    index, total = progress(current, path, context)

    def _describe(step_id: Any) -> dict[str, Any] | None:
        if step_id is None:
            return None
        step = STEPS.get(step_id)
        return {
            "id": step_id.value,
            "title": step.title if step else step_id.value,
            "subtitle": step.subtitle if step else "",
            "can_skip": bool(step.can_skip) if step else False,
        }

    previous = prev_step(current, path, context)
    return {
        "current": current.value,
        "next": target.value if target else None,
        "next_meta": _describe(target),
        "prev": previous.value if previous else None,
        "is_last": is_last_step(current),
        "progress": [index, total],
    }


@router.get("/api/setup/copy")
async def setup_copy() -> dict[str, Any]:
    """向导 / 仪表盘的中文文案表。

    这两份文案是从原桌面端逐字复制过来的（``webui/copy.py``、``webui/wizard/copy.py``），
    之前一直没人引用。发到前端后，向导的措辞就与桌面端完全一致，
    改文案也只需动一处（两边共用一个文件）。
    """
    from webui import copy as dashboard_copy
    from webui.wizard import copy as wizard_copy

    def _dump(module: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in dir(module):
            if name.startswith("_"):
                continue
            value = getattr(module, name)
            if isinstance(value, (str, dict, list, tuple)):
                out[name] = value
        return out

    return {"wizard": _dump(wizard_copy), "dashboard": _dump(dashboard_copy)}


@router.post("/api/setup/apply")
async def setup_apply(
    request: Request, payload: Annotated[dict, Body()]
) -> dict[str, Any]:
    """写入首次配置并启动 Runtime。"""
    host = get_host(request)
    hub = request.app.state.hub
    try:
        result = await asyncio.to_thread(host.write_initial_config, payload)
    except Exception as e:  # noqa: BLE001
        logger.exception("写入首次配置失败")
        raise HTTPException(status_code=400, detail=str(e)) from e

    ok = await host.start()
    hub.attach_timeline()
    result["started"] = ok
    if not ok:
        result["message"] = host.last_error or "配置已写入，但 Runtime 启动失败"
    return result


__all__ = ["router"]
