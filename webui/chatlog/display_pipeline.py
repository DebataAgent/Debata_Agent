"""把原始聊天记录归一化成可显示的 DisplayItem 序列。

原 GUI 的这条链路在 ``ui/dashboard/chats_page.py`` 里，一半是纯逻辑、一半是
QTextBrowser 的 HTML 渲染。迁移时只搬了后半段（过滤/排序在 ``display_items.py``），
前半段（``normalize_history_records`` 那 400 行"生产"逻辑）漏掉了——结果是
``display_items.py`` 里的过滤函数全是死代码，对话页也就只能把记录平铺成文本。

本模块把那半段补上。原代码 **零 Qt、零 self 引用**，可以原样搬运，所以下面这些
函数基本是逐字复制（只去掉了返回 HTML 的 ``_render_display_item``）。这样做是有意的：

- 两端（桌面端历史行为 / WebUI 现状）对同一批记录得出同样的分组、配对、折叠判断，
  用户换界面不会看到不一样的对话；
- 这些判断规则是长期调出来的（发送回执去重、工具调用↔结果配对、legacy header 拆分），
  重写一遍必然丢细节。

新增的只有 ``display_item_to_json`` / ``display_items_json``：把 dataclass 翻成
HTTP 能返回的结构，供 ``webui/api/chats.py`` 使用。

导入方向：``records`` → ``display_items``，本模块 → 三者，没有环。
"""

from __future__ import annotations

from typing import Any

from .display_items import (
    _accepted_message_conversation_id,
    _filter_display_items,
    _record_send_id,
    _record_send_order,
    _remember_generated_outbound,
    _should_attach_tool_result_to_call,
    _should_skip_generated_outbound,
    _sort_display_items,
    _unique_item_id,
)
from .grouping import _LEGACY_HEADER_LINE_RE, _LEGACY_HEADER_RE, _record_role
from .models import DisplayItem, DisplaySeverity, SendDisplayContext
from .records import (
    _record_conversation_id_for_display,
    _record_display_base_id,
    _record_is_qq_visible_outbound,
    _record_message_id,
    _record_timestamp,
    _runtime_event_summary,
    _runtime_event_title,
    _runtime_record_severity,
    _runtime_tool_call_for_record,
    _tool_call_name,
)
from .text_format import (
    _compact_inline_tokens,
    _first_nonempty_line,
    _format_send_receipt_summary,
    _format_tool_call_for_display,
    _format_tool_result_summary,
    _parse_json_object,
    _send_receipt_sent_items,
    _send_status_info,
)
from .tool_display import format_tool_call

__all__ = [
    "DEFAULT_VISIBLE_RECORD_LIMIT",
    "build_display_items",
    "display_item_to_json",
    "display_items_json",
]

DEFAULT_VISIBLE_RECORD_LIMIT = 300
"""默认展示最近多少条。前端「加载更早」按这个档位递增，「显示全部」传 0。"""


# ============================================================
# 对外接口（新增）
# ============================================================


def build_display_items(
    records: list[dict],
    *,
    persona_name: str,
    search_text: str = "",
    show_chat: bool = True,
    show_system: bool = True,
    show_tools: bool = True,
    show_reasoning: bool = True,
    media_only: bool = False,
    bot_user_id: str | None = None,
) -> list[DisplayItem]:
    """归一化 + 过滤，一步到位。``limit`` 的截断由调用方做（它只管语义）。"""
    normalized = normalize_history_records(
        records, persona_name=persona_name, bot_user_id=bot_user_id
    )
    return _filter_display_items(
        normalized,
        search_text=search_text,
        show_chat=show_chat,
        show_system=show_system,
        show_tools=show_tools,
        show_reasoning=show_reasoning,
        media_only=media_only,
    )


def display_item_to_json(item: DisplayItem) -> dict[str, Any]:
    """翻成 HTTP 响应里的结构。``raw`` 不下发（体积大且含内部字段）。"""
    return {
        "id": item.item_id,
        "kind": item.kind,
        "timestamp": item.timestamp,
        "speaker_label": item.speaker_label,
        "speaker_id": item.speaker_id,
        "role_label": item.role_label,
        "text": item.text,
        "summary": item.summary,
        "severity": item.severity,
        "collapsed_by_default": item.collapsed_by_default,
        "related_tool_call_id": item.related_tool_call_id,
        "related_message_id": item.related_message_id,
        "tool_results": [display_item_to_json(x) for x in item.tool_results],
    }


def display_items_json(items: list[DisplayItem]) -> list[dict[str, Any]]:
    return [display_item_to_json(x) for x in items]


# ============================================================
# 以下为原 ui/dashboard/chats_page.py 的原样搬运（零 Qt 依赖）
# ============================================================


def normalize_history_records(
    records: list[dict],
    *,
    persona_name: str,
    bot_user_id: str | None = None,
) -> list[DisplayItem]:
    """把 raw history 记录归一化成 UI 可显示项，并保留必要的工具结果位置。"""
    items: list[DisplayItem] = []
    tool_calls: dict[str, DisplayItem] = {}
    seen_ids: set[str] = set()
    send_context = _build_send_display_context(records)
    for record_index, record in enumerate(records):
        record_items = normalize_history_record(
            record,
            persona_name=persona_name,
            bot_user_id=bot_user_id,
        )
        record_items.extend(
            _send_status_outbound_items(
                record,
                context=send_context,
                persona_name=persona_name,
                bot_user_id=bot_user_id,
            )
        )
        for item in record_items:
            if _should_skip_generated_outbound(item, send_context):
                continue
            item.item_id = _unique_item_id(item.item_id, record_index, seen_ids)
            if item.kind == "tool_call" and item.related_tool_call_id:
                tool_calls[item.related_tool_call_id] = item
                items.append(item)
                continue
            if _should_attach_tool_result_to_call(item):
                parent = tool_calls.get(item.related_tool_call_id)
                if parent is not None:
                    parent.tool_results.append(item)
                    continue
            items.append(item)
            _remember_generated_outbound(item, send_context)
    return _sort_display_items(items)


def _build_send_display_context(records: list[dict]) -> SendDisplayContext:
    context = SendDisplayContext()
    for rec in records:
        content = str(rec.get("content") or "")
        runtime = _runtime_event_summary(content)
        if runtime is None and not rec.get("_synthetic_source") and _record_is_qq_visible_outbound(rec):
            msg_id = _record_message_id(rec)
            if msg_id:
                context.real_msg_ids.add(msg_id)
            send_id = _record_send_id(rec)
            order = _record_send_order(rec)
            if send_id and order is not None:
                context.real_send_orders.add((send_id, order))
        cloned_accepted = rec.get("_accepted_messages_for_send_status")
        cloned_send_status = _send_status_info(content)
        if (
            isinstance(cloned_accepted, list)
            and cloned_send_status
            and cloned_send_status.get("send_id")
        ):
            context.accepted_by_send_id.setdefault(
                str(cloned_send_status["send_id"]),
                [dict(item) for item in cloned_accepted if isinstance(item, dict)],
            )
        payload = _parse_json_object(content)
        if not payload:
            continue
        send_id = str(payload.get("send_id") or "").strip()
        accepted = payload.get("accepted_messages")
        if send_id and isinstance(accepted, list):
            messages = [dict(item) for item in accepted if isinstance(item, dict)]
            if messages:
                context.accepted_by_send_id.setdefault(send_id, messages)
    return context


def _send_status_outbound_items(
    rec: dict[str, Any],
    *,
    context: SendDisplayContext,
    persona_name: str,
    bot_user_id: str | None,
) -> list[DisplayItem]:
    status = _send_status_info(str(rec.get("content") or ""))
    if not status:
        return []
    send_id = status.get("send_id") or ""
    accepted = context.accepted_by_send_id.get(send_id)
    msg_ids = status.get("msg_ids") or []
    if not send_id or not accepted or not msg_ids or not status.get("completed"):
        return []
    target_conversation_id = _record_conversation_id_for_display(rec)
    timestamp = _record_timestamp(rec)
    base_id = _record_display_base_id(rec, conversation_id=target_conversation_id, role=_record_role(rec))
    items: list[DisplayItem] = []
    for order, message in enumerate(accepted):
        conversation_id = _accepted_message_conversation_id(message, fallback=target_conversation_id)
        if conversation_id != target_conversation_id:
            continue
        content = str(message.get("content") or message.get("label") or "").strip()
        if not content:
            continue
        msg_id = msg_ids[order] if order < len(msg_ids) else ""
        raw = {
            **message,
            "send_id": send_id,
            "send_order": order,
            "msg_id": msg_id,
            "qq_visible": True,
            "_synthetic_source": "send_status",
        }
        items.append(
            DisplayItem(
                item_id=f"{base_id}:send_status_sent:{order}",
                conversation_id=conversation_id,
                timestamp=str(message.get("time") or timestamp or "") or None,
                kind="outbound_message",
                speaker_label=persona_name,
                speaker_id=bot_user_id,
                role_label="角色",
                text=content,
                summary=_sent_item_status(raw),
                raw=raw,
                related_message_id=msg_id or None,
                collapsed_by_default=False,
            )
        )
    return items


def normalize_history_record(
    rec: dict[str, Any],
    *,
    persona_name: str,
    bot_user_id: str | None = None,
) -> list[DisplayItem]:
    """把一条 history/archive 记录拆成一个或多个 DisplayItem。"""
    role = _record_role(rec)
    content = str(rec.get("content") or "")
    conversation_id = _record_conversation_id_for_display(rec)
    timestamp = _record_timestamp(rec)
    base_id = _record_display_base_id(rec, conversation_id=conversation_id, role=role)
    runtime = _runtime_event_summary(content)
    runtime_event_type = str(rec.get("_runtime_event_type") or "")

    if runtime_event_type == "tool_call_started":
        tool_call_id = str(rec.get("tool_call_id") or "").strip() or None
        tool_call = _runtime_tool_call_for_record(rec, base_id=base_id)
        return [
            DisplayItem(
                item_id=f"{base_id}:runtime_tool_call",
                conversation_id=conversation_id,
                timestamp=timestamp,
                kind="tool_call",
                speaker_label=persona_name,
                speaker_id=bot_user_id,
                role_label="工具动作",
                text=str(rec.get("_runtime_detail") or content),
                summary=str(rec.get("_runtime_summary") or _format_tool_call_for_display(tool_call)),
                raw={"record": rec, "tool_call": tool_call, "runtime_event": True},
                related_tool_call_id=tool_call_id,
                collapsed_by_default=True,
                severity=_runtime_record_severity(rec),
            )
        ]

    if runtime_event_type == "tool_result_received":
        tool_call_id = str(rec.get("tool_call_id") or "").strip() or None
        return [
            DisplayItem(
                item_id=f"{base_id}:runtime_tool_result",
                conversation_id=conversation_id,
                timestamp=timestamp,
                kind="tool_result",
                speaker_label="工具返回",
                speaker_id=None,
                role_label="工具返回",
                text=content,
                summary=str(rec.get("_runtime_summary") or _format_tool_result_summary(content)),
                raw=rec,
                related_tool_call_id=tool_call_id,
                collapsed_by_default=True,
                severity=_runtime_record_severity(rec),
            )
        ]

    if runtime_event_type:
        title = _runtime_event_title(runtime_event_type)
        summary = str(rec.get("_runtime_summary") or title)
        return [
            DisplayItem(
                item_id=f"{base_id}:runtime_event",
                conversation_id=conversation_id,
                timestamp=timestamp,
                kind="system_event",
                speaker_label="系统",
                speaker_id=None,
                role_label="系统",
                text=str(rec.get("_runtime_detail") or content),
                summary=f"{title} · {summary}" if summary and summary != title else title,
                raw=rec,
                collapsed_by_default=True,
                severity=_runtime_record_severity(rec),
            )
        ]

    if runtime is not None:
        title, summary = runtime
        items = [
            DisplayItem(
                item_id=f"{base_id}:runtime",
                conversation_id=conversation_id,
                timestamp=timestamp,
                kind="runtime_receipt" if "<send_receipt" in content else "system_event",
                speaker_label="系统",
                speaker_id=None,
                role_label="系统",
                text=content,
                summary=f"{title} · {summary}" if summary else title,
                raw=rec,
                collapsed_by_default=True,
                severity=_runtime_severity(content),
            )
        ]
        for index, sent in enumerate(_send_receipt_sent_items(content)):
            sent_conversation_id = _accepted_message_conversation_id(sent, fallback=conversation_id)
            if sent_conversation_id != conversation_id:
                continue
            msg_id = str(sent.get("msg_id") or "").strip() or None
            items.append(
                DisplayItem(
                    item_id=f"{base_id}:sent:{index}",
                    conversation_id=sent_conversation_id,
                    timestamp=str(sent.get("time") or timestamp or "") or None,
                    kind="outbound_message",
                    speaker_label=persona_name,
                    speaker_id=bot_user_id,
                    role_label="角色",
                    text=str(sent.get("content") or sent.get("label") or "").strip(),
                    summary=_sent_item_status(sent),
                    raw=dict(sent),
                    related_message_id=msg_id,
                    collapsed_by_default=False,
                )
            )
        return items

    if role == "tool":
        tool_call_id = str(rec.get("tool_call_id") or "").strip() or None
        return [
            DisplayItem(
                item_id=f"{base_id}:tool_result",
                conversation_id=conversation_id,
                timestamp=timestamp,
                kind="tool_result",
                speaker_label="工具结果",
                speaker_id=None,
                role_label="工具结果",
                text=content,
                summary=_format_tool_result_summary(content),
                raw=rec,
                related_tool_call_id=tool_call_id,
                collapsed_by_default=True,
                severity=_tool_result_severity(content),
            )
        ]

    if role == "user":
        legacy_messages = _split_legacy_header_messages(content)
        if legacy_messages:
            return [
                DisplayItem(
                    item_id=f"{base_id}:legacy:{index}:{message['message_id'] or index}",
                    conversation_id=message["conversation_id"],
                    timestamp=message["timestamp"] or timestamp,
                    kind="inbound_message",
                    speaker_label=message["speaker_label"],
                    speaker_id=message["speaker_id"],
                    role_label="成员",
                    text=message["text"],
                    summary=_compact_inline_tokens(_first_nonempty_line(message["text"])),
                    raw={**rec, "content": message["text"], "legacy_header": message["raw_header"]},
                    related_message_id=message["message_id"],
                )
                for index, message in enumerate(legacy_messages)
            ]
        stripped = _strip_legacy_header(content)
        return [
            DisplayItem(
                item_id=f"{base_id}:inbound",
                conversation_id=conversation_id,
                timestamp=timestamp,
                kind="inbound_message",
                speaker_label=_user_record_label(rec),
                speaker_id=_user_record_sender_id(rec),
                role_label="成员",
                text=stripped,
                summary=_compact_inline_tokens(_first_nonempty_line(stripped)),
                raw=rec,
                related_message_id=_record_message_id(rec),
            )
        ]

    if role == "assistant":
        items: list[DisplayItem] = []
        reasoning = str(rec.get("reasoning_content") or "")
        if reasoning:
            items.append(
                DisplayItem(
                    item_id=f"{base_id}:reasoning",
                    conversation_id=conversation_id,
                    timestamp=timestamp,
                    kind="reasoning",
                    speaker_label=persona_name,
                    speaker_id=bot_user_id,
                    role_label="思考",
                    text=reasoning,
                    summary="思考过程",
                    raw=rec,
                    collapsed_by_default=True,
                )
            )
        if content.strip():
            outbound = _record_is_qq_visible_outbound(rec)
            items.append(
                DisplayItem(
                    item_id=f"{base_id}:assistant_text",
                    conversation_id=conversation_id,
                    timestamp=timestamp,
                    kind="outbound_message" if outbound else "assistant_note",
                    speaker_label=persona_name,
                    speaker_id=bot_user_id,
                    role_label="角色" if outbound else "助手内部",
                    text=content,
                    summary=_sent_item_status(rec) if outbound else _assistant_note_summary(content),
                    raw=rec,
                    related_message_id=_record_message_id(rec),
                    collapsed_by_default=not outbound,
                )
            )
        for index, tool_call in enumerate(rec.get("tool_calls") or []):
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or f"{base_id}:tool:{index}").strip()
            tool_display = format_tool_call(tool_call)
            items.append(
                DisplayItem(
                    item_id=f"{base_id}:tool_call:{index}",
                    conversation_id=conversation_id,
                    timestamp=timestamp,
                    kind="tool_call",
                    speaker_label=persona_name,
                    speaker_id=bot_user_id,
                    role_label="工具动作",
                    text=tool_display.detail,
                    summary=tool_display.summary,
                    raw={"record": rec, "tool_call": tool_call},
                    related_tool_call_id=tool_call_id,
                    collapsed_by_default=True,
                    severity=_tool_call_severity(tool_call),
                )
            )
        return items

    return [
        DisplayItem(
            item_id=f"{base_id}:system",
            conversation_id=conversation_id,
            timestamp=timestamp,
            kind="system_event",
            speaker_label="系统",
            speaker_id=None,
            role_label="系统",
            text=content,
            summary=_compact_inline_tokens(_first_nonempty_line(content)) or "系统事件",
            raw=rec,
            collapsed_by_default=True,
        )
    ]


def _build_display_items(
    records: list[dict],
    *,
    persona_name: str,
    search_text: str,
    show_chat: bool,
    show_system: bool,
    show_tools: bool,
    show_reasoning: bool = True,
    media_only: bool = False,
) -> list[DisplayItem]:
    return _filter_display_items(
        normalize_history_records(records, persona_name=persona_name),
        search_text=search_text,
        show_chat=show_chat,
        show_system=show_system,
        show_tools=show_tools,
        show_reasoning=show_reasoning,
        media_only=media_only,
    )


def _user_record_sender_id(rec: dict[str, Any]) -> str | None:
    meta_messages = (rec.get("metadata") or {}).get("messages") or []
    first = meta_messages[0] if meta_messages else None
    if isinstance(first, dict):
        value = first.get("user_id") or first.get("sender_id") or first.get("target_id")
        return str(value) if value else None
    for key in ("sender_id", "user_id"):
        value = rec.get(key)
        if value:
            return str(value)
    content = str(rec.get("content") or "")
    match = _LEGACY_HEADER_RE.match(content)
    if match:
        return match.group("user_id")
    return None


def _sent_item_status(item: dict[str, Any]) -> str:
    parts = []
    status = item.get("status")
    if status:
        parts.append(_status_label(str(status)))
    elif item.get("qq_visible") is True or item.get("direction") == "outbound":
        parts.append("已发送")
    if item.get("delivery"):
        parts.append(f"投递 {item.get('delivery')}")
    if item.get("qq_visible") == "pending":
        parts.append("QQ 可见性待确认")
    elif item.get("qq_visible") is False:
        parts.append("未 QQ 可见")
    if item.get("msg_id"):
        parts.append(f"msg_id={item.get('msg_id')}")
    return " · ".join(parts) or "已发送"


def _status_label(status: str) -> str:
    labels = {
        "sent": "已发送",
        "accepted": "已入队",
        "queued": "排队中",
        "pending": "等待确认",
        "stale": "已过期",
        "failed": "失败",
        "needs_review": "等待复核",
        "needs_review_again": "等待再次复核",
    }
    return labels.get(status, status)


def _assistant_note_summary(content: str) -> str:
    first = _compact_inline_tokens(_first_nonempty_line(content))
    return first or "未发送文本"


def _runtime_severity(content: str) -> DisplaySeverity:
    summary = _format_send_receipt_summary(content) if "<send_receipt" in content else content
    lowered = summary.casefold()
    if any(token in lowered for token in ("failed", "失败", "stale", "过期")):
        return "warning"
    return "info"


def _tool_result_severity(content: str) -> DisplaySeverity:
    lowered = content.casefold()
    if any(token in lowered for token in ("failed", '"ok": false', "error", "失败")):
        return "warning"
    return "info"


def _tool_call_severity(tool_call: dict[str, Any]) -> DisplaySeverity:
    name = _tool_call_name(tool_call)
    if name in {"set_group_kick", "set_group_ban", "set_group_whole_ban", "set_group_leave"}:
        return "warning"
    return "info"


def _user_record_label(rec: dict) -> str:
    meta_messages = (rec.get("metadata") or {}).get("messages") or []
    first = meta_messages[0] if meta_messages else None
    if isinstance(first, dict):
        nickname = str(first.get("nickname") or "").strip()
        user_id = str(first.get("user_id") or first.get("target_id") or "").strip()
        if nickname and user_id:
            return f"{nickname}({user_id})"
        if nickname:
            return nickname
        if user_id:
            return f"用户 {user_id}"
    sender_name = str(rec.get("sender_name") or "").strip()
    sender_id = str(rec.get("sender_id") or "").strip()
    if sender_name and sender_id:
        return f"{sender_name}({sender_id})"
    if sender_name:
        return sender_name
    if sender_id:
        return f"用户 {sender_id}"
    meta = rec.get("metadata")
    if isinstance(meta, dict):
        sender_name = str(meta.get("sender_name") or "").strip()
        sender_id = str(meta.get("sender_id") or "").strip()
        if sender_name and sender_id:
            return f"{sender_name}({sender_id})"
        if sender_name:
            return sender_name
        if sender_id:
            return f"用户 {sender_id}"

    content = str(rec.get("content") or "")
    match = _LEGACY_HEADER_RE.match(content)
    if match:
        return f"{match.group('nickname')}({match.group('user_id')})"
    return "用户"


def _strip_legacy_header(content: str) -> str:
    match = _LEGACY_HEADER_RE.match(content)
    if not match:
        return content
    return content[match.end():].lstrip()


def _split_legacy_header_messages(content: str) -> list[dict[str, str]]:
    matches = list(_LEGACY_HEADER_LINE_RE.finditer(content))
    if not matches or matches[0].start() != 0:
        return []
    messages: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        text = content[match.end():next_start].strip()
        group_id = match.group("group_id") or ""
        user_id = match.group("user_id") or ""
        conversation_id = f"group:{group_id}" if group_id else f"private:{user_id}"
        messages.append(
            {
                "timestamp": match.group("timestamp") or "",
                "conversation_id": conversation_id,
                "speaker_label": f"{match.group('nickname')}({user_id})",
                "speaker_id": user_id,
                "message_id": match.group("message_id") or "",
                "text": text,
                "raw_header": match.group(0),
            }
        )
    return messages

