"""记忆页接口 —— 重要记忆 CRUD + RAG 索引浏览。

对应原 GUI 的 ``ui/dashboard/memory_page.py``。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .deps import require_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
async def list_memory(request: Request) -> dict[str, Any]:
    rt = require_runtime(request)
    important = getattr(rt, "important", None)
    items = important.items() if important is not None else []

    cfg = getattr(rt, "config", None)
    mode = "file"
    if cfg is not None:
        mode = getattr(cfg.features.long_term_memory, "mode", "file")

    # 按时间倒序，最近记的排前面；pinned 的始终置顶。
    def sort_key(item: dict[str, Any]):
        return (1 if item.get("pinned") else 0, str(item.get("timestamp") or ""))

    return {
        "mode": mode,
        "items": sorted(items, key=sort_key, reverse=True),
        "count": len(items),
    }


class MemoryCreatePayload(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    scope: str | None = None
    pinned: bool = False


@router.post("")
async def add_memory(
    request: Request, payload: Annotated[MemoryCreatePayload, Body()]
) -> dict[str, Any]:
    rt = require_runtime(request)
    important = getattr(rt, "important", None)
    if important is None:
        raise HTTPException(status_code=503, detail="记忆管理器尚未就绪")
    result = await important.save(
        payload.content.strip(), scope=payload.scope, pinned=payload.pinned
    )
    if result.get("duplicate"):
        return {"ok": False, "message": "内容重复，已存在的记忆未重复写入", "id": result.get("id", "")}
    return {"ok": True, "message": "已记下", "id": result.get("id", "")}


class MemoryUpdatePayload(BaseModel):
    scope: str | None = None
    pinned: bool | None = None


@router.patch("/{item_id}")
async def update_memory(
    request: Request,
    item_id: str,
    payload: Annotated[MemoryUpdatePayload, Body()],
) -> dict[str, Any]:
    rt = require_runtime(request)
    important = getattr(rt, "important", None)
    if important is None:
        raise HTTPException(status_code=503, detail="记忆管理器尚未就绪")
    ok = await important.update_metadata(
        item_id, scope=payload.scope, pinned=payload.pinned
    )
    if not ok:
        raise HTTPException(status_code=404, detail="没找到这条记忆，或没有字段需要更新")
    return {"ok": True, "message": "已更新"}


@router.delete("/{item_id}")
async def delete_memory(request: Request, item_id: str) -> dict[str, Any]:
    rt = require_runtime(request)
    important = getattr(rt, "important", None)
    if important is None:
        raise HTTPException(status_code=503, detail="记忆管理器尚未就绪")
    ok = await important.delete_by_id(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="没找到这条记忆")
    return {"ok": True, "message": "已删除"}


# ============================================================
# RAG 索引
# ============================================================


@router.get("/rag")
async def rag_entries(
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """RAG 模式下已索引的长期记忆条目。"""
    rt = require_runtime(request)
    store = getattr(rt, "rag_store", None)
    if store is None:
        return {"available": False, "entries": [], "count": 0}
    try:
        entries = store.all_entries()
    except Exception as e:  # noqa: BLE001
        logger.exception("读取 RAG 索引失败")
        return {"available": False, "entries": [], "count": 0, "error": str(e)}

    out = []
    for entry in entries[-limit:]:
        out.append(
            {
                "id": getattr(entry, "id", ""),
                "content": getattr(entry, "content", ""),
                "conversation_id": getattr(entry, "conversation_id", ""),
                "timestamp": getattr(entry, "timestamp", ""),
                "scope": getattr(entry, "scope", ""),
            }
        )
    return {
        "available": True,
        "entries": out,
        "count": len(entries),
        "embedding_ready": getattr(rt, "embedding_service", None) is not None,
    }


__all__ = ["router"]
