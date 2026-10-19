"""对话页接口 —— 会话列表、会话详情、实时时间线、注入测试消息。

数据组装直接复用从原 GUI 搬迁过来的 ``webui.chatlog``（本来就是 Qt-free 的），
所以 Web 端看到的记录合并 / 分组结果与桌面端完全一致。
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field

from webui.chatlog.display_pipeline import (
    DEFAULT_VISIBLE_RECORD_LIMIT,
    build_display_items,
    display_items_json,
)
from webui.chatlog.grouping import _group_records_by_conversation
from webui.chatlog.records import _load_chat_page_records

from .deps import require_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _conversation_summary(conv: dict[str, Any]) -> dict[str, Any]:
    records = conv.get("records") or []
    last_ts = ""
    for rec in reversed(records):
        ts = rec.get("timestamp") or rec.get("_ts") or ""
        if ts:
            last_ts = str(ts)
            break
    return {
        "key": conv.get("key", ""),
        "label": conv.get("label", ""),
        "preview": (conv.get("preview") or "")[:120],
        "record_count": len(records),
        "last_timestamp": last_ts,
    }


@router.get("/conversations")
async def conversations(request: Request) -> dict[str, Any]:
    """侧栏会话列表（最近的排前面）。"""
    rt = require_runtime(request)
    try:
        records = await _load_chat_page_records(rt)
    except Exception as e:  # noqa: BLE001
        logger.exception("读取会话记录失败")
        raise HTTPException(status_code=500, detail=f"读取会话记录失败：{e}") from e
    grouped = _group_records_by_conversation(records)
    return {
        "conversations": [_conversation_summary(c) for c in grouped],
        "total_records": len(records),
    }


@router.get("/conversation")
async def conversation(
    request: Request,
    conversation_id: str = Query(..., description="如 private:123456 / group:987654"),
    limit: int = Query(300, ge=1, le=5000),
) -> dict[str, Any]:
    """单个会话的记录（默认只回最近 limit 条）。"""
    rt = require_runtime(request)
    try:
        records = await _load_chat_page_records(rt)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取会话记录失败：{e}") from e

    grouped = _group_records_by_conversation(records)
    target = next((c for c in grouped if c.get("key") == conversation_id), None)
    if target is None:
        return {"conversation": None, "records": [], "truncated": False}

    items = target.get("records") or []
    truncated = len(items) > limit
    if truncated:
        items = items[-limit:]
    return {
        "conversation": _conversation_summary(target),
        "records": [_record_to_json(r) for r in items],
        "truncated": truncated,
        "total": len(target.get("records") or []),
    }


@router.get("/display")
async def display(
    request: Request,
    conversation_id: str = Query(..., description="如 private:123456 / group:987654"),
    limit: int = Query(
        DEFAULT_VISIBLE_RECORD_LIMIT,
        ge=0,
        le=5000,
        description="取过滤后最后 N 条；0 表示不截断（对应「显示全部」）",
    ),
    show_chat: bool = Query(True),
    show_system: bool = Query(True),
    show_tools: bool = Query(True),
    show_reasoning: bool = Query(True),
    media_only: bool = Query(False, description="只看图片/文件"),
    q: str = Query("", description="在记录正文里搜索"),
) -> dict[str, Any]:
    """结构化显示项（对应原 GUI 的 DisplayItem 管线）。

    与原 ``/conversation`` 的区别：这里回的是**已归一化并配对**的显示项——
    工具调用与它的返回合并成一项、发送回执去重、思考单独成项，
    前端只需按 kind 渲染，不必自己猜记录之间的关系。

    ``limit`` 的语义照搬原 GUI：截断的是**过滤之后**的末尾 N 条
    （``chats_page.py`` 的 DEFAULT_VISIBLE_RECORD_LIMIT / VISIBLE_RECORD_STEP）。
    """
    rt = require_runtime(request)
    try:
        records = await _load_chat_page_records(rt)
    except Exception as e:  # noqa: BLE001
        logger.exception("读取会话记录失败")
        raise HTTPException(status_code=500, detail=f"读取会话记录失败：{e}") from e

    grouped = _group_records_by_conversation(records)
    target = next((c for c in grouped if c.get("key") == conversation_id), None)
    if target is None:
        return {
            "conversation": None, "items": [], "total": 0,
            "filtered_total": 0, "truncated": False,
        }

    all_records = target.get("records") or []
    total = len(all_records)
    raw_records = all_records
    # 默认只展示最近 300 条，但原实现会先归一化整段会话（大群可能 8k+ 条，
    # 在 ARM 上要 5 秒）。这里先按 limit 的 4 倍截一个尾部窗口，再做展示项
    # 归一化；搜索和“只看媒体”需要全量，保持不截断。
    if limit and not q and not media_only and total > limit * 4:
        raw_records = all_records[-(limit * 4):]

    persona_name = getattr(getattr(rt, "persona", None), "name", "") or ""
    items = build_display_items(
        raw_records,
        persona_name=persona_name,
        search_text=q,
        show_chat=show_chat,
        show_system=show_system,
        show_tools=show_tools,
        show_reasoning=show_reasoning,
        media_only=media_only,
    )

    filtered_total = len(items)
    truncated = bool(limit) and (
        filtered_total > limit or len(raw_records) < total
    )
    if truncated:
        items = items[-limit:]

    return {
        "conversation": _conversation_summary(target),
        "items": display_items_json(items),
        "total": total,
        "filtered_total": filtered_total,
        "truncated": truncated,
    }


@router.get("/timeline")
async def timeline(
    request: Request,
    conversation_id: str = Query("", description="留空则回所有会话最近的消息"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """``chat_timeline`` 里的消息（QQ 实际收发的那一份，含媒体段）。"""
    rt = require_runtime(request)
    timeline_store = getattr(getattr(rt, "pipeline", None), "chat_timeline", None)
    if timeline_store is None:
        return {"messages": []}

    if conversation_id:
        messages = timeline_store.recent(conversation_id, limit)
    else:
        messages = []
        for cid in timeline_store.conversation_ids():
            messages.extend(timeline_store.recent(cid, limit))
        messages.sort(key=lambda m: getattr(m, "timestamp", 0) or 0)
        messages = messages[-limit:]

    from webui.events import _timeline_to_dict

    return {"messages": [_timeline_to_dict(m) for m in messages]}


def _record_to_json(rec: dict[str, Any]) -> dict[str, Any]:
    """把 history record 压成前端够用的形状。"""
    content = rec.get("content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    out = {
        "role": rec.get("role") or "",
        "content": content,
        "timestamp": rec.get("timestamp") or "",
        "conversation_id": rec.get("conversation_id") or "",
    }
    if rec.get("tool_calls"):
        out["tool_calls"] = [
            {
                "id": (tc.get("id") or ""),
                "name": ((tc.get("function") or {}).get("name") or ""),
                "arguments": ((tc.get("function") or {}).get("arguments") or ""),
            }
            for tc in rec["tool_calls"]
            if isinstance(tc, dict)
        ]
    if rec.get("name"):
        out["name"] = rec["name"]
    if rec.get("reasoning_content"):
        out["reasoning"] = rec["reasoning_content"]
    return out


# ============================================================
# 注入 / 发送
# ============================================================


class InjectPayload(BaseModel):
    """模拟一条入站消息，用来在不接 QQ 的情况下试跑人格。"""

    text: str = Field(..., min_length=1, description="消息正文")
    user_id: str = Field("10001", description="发送者 QQ 号")
    nickname: str = Field("测试用户")
    scope: str = Field("private", description="private 或 group")
    group_id: str | None = None


@router.post("/inject")
async def inject(
    request: Request, payload: Annotated[InjectPayload, Body()]
) -> dict[str, Any]:
    """把一条伪造的入站消息丢进 pipeline，跟真收到 QQ 消息走同一条链路。"""
    from adapters.types import EventType, IncomingMessage

    rt = require_runtime(request)
    pipeline = getattr(rt, "pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="消息管道尚未就绪")

    scope = "group" if payload.scope == "group" else "private"
    if scope == "group" and not payload.group_id:
        raise HTTPException(status_code=400, detail="群聊消息必须提供 group_id")

    adapter = getattr(rt, "adapter", None)
    adapter_name = getattr(adapter, "name", "webui") or "webui"
    now = time.time()
    event = IncomingMessage(
        adapter=adapter_name,
        event_type=EventType.MESSAGE,
        timestamp=now,
        self_id="0",
        message_id=f"webui-{int(now * 1000)}",
        scope=scope,
        user_id=str(payload.user_id),
        nickname=payload.nickname,
        group_id=str(payload.group_id) if scope == "group" and payload.group_id else None,
        text=payload.text,
        raw_message=payload.text,
        raw={"source": "webui"},
    )

    # 黑名单 / 白名单会在这里按配置拦截；命中时 enqueue 直接返回。
    blacklisted = _is_blacklisted(pipeline, event)
    await pipeline.enqueue(event)
    if blacklisted:
        return {
            "ok": False,
            "blacklisted": True,
            "message": "该对象在黑名单中，消息已被丢弃（这正是黑名单生效的表现）",
        }
    return {
        "ok": True,
        "blacklisted": False,
        "message": "消息已注入，回复会异步出现在会话里",
        "conversation_id": f"{scope}:{payload.group_id if scope == 'group' else payload.user_id}",
    }


def _is_blacklisted(pipeline: Any, event: Any) -> bool:
    qq_ids = getattr(pipeline, "_blacklist_qq_ids", set()) or set()
    group_ids = getattr(pipeline, "_blacklist_group_ids", set()) or set()
    try:
        if event.is_group() and int(event.group_id or 0) in group_ids:
            return True
        return int(event.user_id or 0) in qq_ids
    except (TypeError, ValueError):
        return False


class SendPayload(BaseModel):
    """直接以机器人身份发一条文本（不过模型，用于连通性验证）。"""

    target_id: str = Field(..., description="私聊 QQ 号或群号")
    text: str = Field(..., min_length=1)
    scope: str = Field("private", description="private 或 group")


@router.post("/send")
async def send(request: Request, payload: Annotated[SendPayload, Body()]) -> dict[str, Any]:
    from adapters.types import Target

    rt = require_runtime(request)
    adapter = getattr(rt, "adapter", None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="渠道适配器尚未就绪")
    if not getattr(adapter, "is_connected", False):
        raise HTTPException(status_code=409, detail="NapCat 未连接，无法发送")

    scope = "group" if payload.scope == "group" else "private"
    target = Target(
        adapter=getattr(adapter, "name", "napcat"),
        scope=scope,
        target_id=str(payload.target_id),
    )
    try:
        message_id = await adapter.send_text(target, payload.text)
    except Exception as e:  # noqa: BLE001
        logger.exception("发送消息失败")
        raise HTTPException(status_code=500, detail=f"发送失败：{e}") from e
    return {"ok": True, "message_id": message_id or ""}


__all__ = ["router"]
