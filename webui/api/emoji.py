"""表情包管理路由（原 GUI 的 ``ui/dashboard/emoji_section.py``）。

原面板直接操作文件系统；WebUI 换成 HTTP，所以文件读写全部经
``webui/emoji_store.py`` 的校验，且**不把 EMOJI_DIR 挂成静态目录**——
否则同目录下任何非图片文件都会被直接下载到。

鉴权：``/api/emoji/*`` 不在 ``webui/server.py`` 的 PUBLIC_PREFIXES 里，
因此全部需要令牌。浏览器 ``<img>`` 走同源 cookie 自动带凭据；
脚本场景可用 ``?token=`` 或 Bearer（见 ``webui/auth.py``）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from webui import emoji_store
from webui.emoji_store import EmojiError

from .deps import get_host

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/emoji", tags=["emoji"])

# 上传 / 改名 / 删除 串行化：这些操作会读目录再写，并发下容易互相踩。
# 放在模块级而不是 app.state，是因为它保护的是文件系统本身，与实例无关。
_write_lock = asyncio.Lock()


def _emoji_dir(request: Request) -> Any:
    """取表情包目录。不要求 Runtime 就绪——停掉 Runtime 也应该能整理表情包。"""
    paths = get_host(request).build_paths()
    return paths.EMOJI_DIR


def _guard(fn, *args, **kwargs):
    """把 EmojiError 统一转成 400（消息直接给用户看）。"""
    try:
        return fn(*args, **kwargs)
    except EmojiError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("")
async def list_emoji(request: Request) -> dict[str, Any]:
    """列出全部表情包与目录信息。"""
    emoji_dir = _emoji_dir(request)
    items = await asyncio.to_thread(emoji_store.list_items, emoji_dir)
    return {
        "dir": str(emoji_dir),
        "count": len(items),
        "supported_exts": sorted(emoji_store.SUPPORTED_IMAGE_EXTS),
        "max_upload_bytes": emoji_store.MAX_UPLOAD_BYTES,
        "items": items,
    }


@router.get("/names")
async def list_names(request: Request) -> dict[str, Any]:
    """名称 → 文件名 映射，供聊天页把 ``[表情包: 名字]`` 内联成缩略图。

    返回真实文件名（而不是让前端猜扩展名），这样图片 URL 完全由服务端数据拼成，
    消息正文里的名字只用来查表，不参与拼路径。
    """
    emoji_dir = _emoji_dir(request)
    items = await asyncio.to_thread(emoji_store.list_items, emoji_dir)
    return {"items": [{"name": x["name"], "file": x["file"]} for x in items]}


@router.get("/file/{filename}")
async def read_file(request: Request, filename: str) -> FileResponse:
    """读取单个表情包图片。

    filename 只允许是纯文件名，且必须通过 emoji_store 的四道校验
    （无路径成分 / 非符号链接 / realpath 仍在目录内 / 内容确是图片）。
    """
    emoji_dir = _emoji_dir(request)
    path = await asyncio.to_thread(_guard, emoji_store.resolve_file, emoji_dir, filename)
    return FileResponse(
        path,
        media_type=emoji_store.MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/upload")
async def upload(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    rename: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """上传一个或多个表情包。

    ``rename`` 是可选的 JSON 对象 ``{原始文件名: 新名称}``，用于让用户在上传前
    批量改名（对应原 GUI 的 EmojiAddDialog）。重名会自动追加 ``_1``、``_2``。
    """
    if not files:
        raise HTTPException(status_code=400, detail="没有选择文件")

    overrides: dict[str, str] = {}
    if rename:
        try:
            parsed = json.loads(rename)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail="rename 不是合法 JSON") from e
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="rename 必须是对象")
        overrides = {str(k): str(v) for k, v in parsed.items()}

    payload: list[tuple[str, bytes]] = []
    for upload_file in files:
        payload.append((upload_file.filename or "unnamed", await upload_file.read()))

    emoji_dir = _emoji_dir(request)
    async with _write_lock:
        result = await asyncio.to_thread(
            emoji_store.add_uploads, emoji_dir, payload, rename=overrides
        )

    added, skipped = result["added"], result["skipped"]
    if not added and skipped:
        # 全失败时给 400，前端能直接把第一个原因显示出来
        raise HTTPException(status_code=400, detail=f"都没能添加：{skipped[0]['reason']}")
    return {
        "ok": True,
        "added": added,
        # 因重名被系统加了 _1 后缀的那些——前端要提示用户去核对
        "deduped": [a for a in added if a["deduped"]],
        "skipped": skipped,
        "count": len(added),
    }


@router.patch("/{name}")
async def rename_emoji(request: Request, name: str, body: dict[str, Any]) -> dict[str, Any]:
    """改名。``name`` 是不带扩展名的旧名称，body 里是 ``new_name``。"""
    new_name = body.get("new_name")
    if not isinstance(new_name, str) or not new_name.strip():
        raise HTTPException(status_code=400, detail="new_name 不能为空")

    emoji_dir = _emoji_dir(request)
    async with _write_lock:
        result = await asyncio.to_thread(
            _guard, emoji_store.rename, emoji_dir, name, new_name
        )
    return {"ok": True, **result}


@router.delete("/{name}")
async def delete_emoji(request: Request, name: str) -> dict[str, Any]:
    """删除。不可撤销，前端须二次确认。"""
    emoji_dir = _emoji_dir(request)
    async with _write_lock:
        filename = await asyncio.to_thread(_guard, emoji_store.delete, emoji_dir, name)
    return {"ok": True, "deleted": filename}


__all__ = ["router"]
