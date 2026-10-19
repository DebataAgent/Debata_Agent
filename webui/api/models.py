"""模型管理页接口 —— 本地插件扫描、状态、安装指引、依赖安装。

对应原 GUI 的 ``ui/dashboard/models_page.py``：把「拖文件夹」换成浏览器上传，
后台装依赖从 QProcess 换成 asyncio 子进程。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from webui import model_guide

from .deps import require_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])

STATUS_TEXT = {
    "NOT_INSTALLED": "未下载",
    "INSTALLED": "已下载",
    "ENABLED": "已启用",
    "ERROR": "出错了",
}

_running_installs: dict[str, asyncio.subprocess.Process] = {}


def _manager(rt: Any) -> Any:
    pm = getattr(rt, "plugin_manager", None)
    if pm is None:
        raise HTTPException(status_code=503, detail="插件管理器尚未就绪")
    return pm


def _record_json(record: Any) -> dict[str, Any]:
    meta = record.meta
    status = getattr(record.status, "name", str(record.status))
    try:
        target_dir = str(meta.resolve_model_dir())
    except Exception:  # noqa: BLE001
        target_dir = ""
    return {
        "name": meta.name,
        "display_name": getattr(meta, "display_name", meta.name),
        "kind": getattr(meta, "kind", ""),
        "description": getattr(meta, "description", ""),
        "status": status,
        "status_text": STATUS_TEXT.get(status, status),
        "error": record.error or "",
        "enabled": status == "ENABLED",
        "model_dir": target_dir,
        "download_url": getattr(meta, "download_url", "") or "",
        "python_deps": list(getattr(meta, "python_deps", []) or []),
        "missing_deps": model_guide.missing_python_deps(record),
    }


@router.get("")
async def list_models(request: Request, rescan: bool = False) -> dict[str, Any]:
    rt = require_runtime(request)
    pm = _manager(rt)
    try:
        if rescan:
            await asyncio.to_thread(pm.scan)
        records = pm.list_all()
    except Exception as e:  # noqa: BLE001
        logger.exception("扫描本地模型失败")
        raise HTTPException(status_code=500, detail=f"扫描失败：{e}") from e
    return {"models": [_record_json(r) for r in records]}


@router.post("/scan")
async def scan(request: Request) -> dict[str, Any]:
    rt = require_runtime(request)
    pm = _manager(rt)
    await asyncio.to_thread(pm.scan)
    return {"ok": True, "models": [_record_json(r) for r in pm.list_all()]}


def _find_record(rt: Any, name: str) -> Any:
    pm = _manager(rt)
    for record in pm.list_all():
        if record.meta.name == name:
            return record
    raise HTTPException(status_code=404, detail=f"没有名为 {name!r} 的模型插件")


@router.get("/{name}/guide")
async def guide(request: Request, name: str) -> dict[str, Any]:
    rt = require_runtime(request)
    record = _find_record(rt, name)
    markdown = model_guide.build_model_install_markdown(
        record, project_root=rt.paths.PROJECT_ROOT
    )
    return {
        "name": name,
        "markdown": markdown,
        "target_dir": str(record.meta.resolve_model_dir()),
        "download_url": getattr(record.meta, "download_url", "") or "",
        "required_paths": [str(p) for p in model_guide.required_model_paths(record)],
    }


class InstallDepsPayload(BaseModel):
    source: str = Field("tsinghua", description="pip 源：tsinghua / aliyun / official")


@router.post("/{name}/install-deps")
async def install_deps(
    request: Request,
    name: str,
    payload: Annotated[InstallDepsPayload, Body()] | None = None,
) -> dict[str, Any]:
    """在后台用当前解释器 pip 安装缺失依赖。

    这里只负责**启动**，结果通过 ``/api/logs`` 或轮询 ``/api/models`` 看
    （``missing_deps`` 变空即装好了）。
    """
    rt = require_runtime(request)
    record = _find_record(rt, name)
    payload = payload or InstallDepsPayload()

    running = _running_installs.get(name)
    if running is not None and running.returncode is None:
        return {"ok": True, "started": False, "message": "该模型依赖正在安装中"}

    missing = model_guide.missing_python_deps(record)
    if not missing:
        return {"ok": True, "started": False, "message": "依赖已齐全，无需安装"}

    args = model_guide.pip_install_args(missing, payload.source)
    env = model_guide.dependency_install_env(dict(os.environ))

    async def _run() -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except Exception:  # noqa: BLE001
            logger.exception("启动依赖安装进程失败：%s", missing)
            return
        _running_installs[name] = proc
        logger.info("开始安装 %s 的依赖：%s", name, ", ".join(missing))
        assert proc.stdout is not None
        async for raw in proc.stdout:
            logger.info("[pip:%s] %s", name, raw.decode("utf-8", "replace").rstrip())
        code = await proc.wait()
        _running_installs.pop(name, None)
        logger.info("依赖安装结束 name=%s exit_code=%s", name, code)

    asyncio.create_task(_run(), name=f"pip-install-{name}")
    return {
        "ok": True,
        "started": True,
        "message": f"已开始安装：{', '.join(missing)}",
        "installing": missing,
    }


@router.post("/{name}/upload")
async def upload_model(
    request: Request,
    name: str,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """上传模型文件夹的 zip，自动识别并复制到插件目标目录。"""
    rt = require_runtime(request)
    _find_record(rt, name)  # 不存在直接 404
    pm = _manager(rt)

    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请把模型文件夹打包成 .zip 再上传")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传内容为空")

    def _do() -> str:
        tmp_dir = Path(tempfile.mkdtemp(prefix="debata-model-"))
        try:
            import zipfile

            archive = tmp_dir / "model.zip"
            archive.write_bytes(data)
            extract_dir = tmp_dir / "extracted"
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_dir)

            matched = model_guide.find_matching_record_for_folder(
                extract_dir, pm.list_all()
            )
            if matched is None:
                raise ValueError(
                    "压缩包里没找到该模型需要的文件。请确认下载的是完整仓库，"
                    "或改用「安装指引」里的直链手动下载。"
                )
            target_record, source_root = matched
            model_guide.install_model_folder(source_root, target_record)
            return target_record.meta.name
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    try:
        installed = await asyncio.to_thread(_do)
    except Exception as e:  # noqa: BLE001
        logger.exception("导入模型压缩包失败")
        raise HTTPException(status_code=400, detail=f"导入失败：{e}") from e

    await asyncio.to_thread(pm.scan)
    return {"ok": True, "message": f"已导入 {installed}，点「重新扫描」确认状态", "id": installed}


__all__ = ["router"]
