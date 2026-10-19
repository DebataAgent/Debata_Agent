"""给模型看的非破坏性历史视图。

背景：
    运行时会把 ``task_context``、``send_receipt``、``send_done`` 等一次性状态
    写回 history。它们对当轮有用，但对后续轮次只是过期快照；永久 append-only
    会让每次请求重复发送上百个旧快照，成为最大的 token 黑洞。

这里的选择：
    - **只裁剪发给模型的视图**，历史/归档/事件库原样保留；
    - 真实聊天消息全部保留；
    - 同类运行时快照只保留最近 N 个；
    - ``no_action`` 按完整 assistant tool_call + tool result 成对保留最近 N 组；
    - 超长 user 记录做确定性头尾截断；
    - 发送类工具结果投影为定位/状态字段，不再回显消息正文。

视图必须是确定性的：同样的输入永远得到同样的字节，避免无意义地破坏 KV 前缀。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_TASK_CONTEXT_SNAPSHOT = "task_context_snapshot"
_SEND_RECEIPT = "send_receipt"
_SEND_DONE_SNAPSHOT = "send_done_snapshot"
_RUNTIME_KINDS = (
    _TASK_CONTEXT_SNAPSHOT,
    _SEND_RECEIPT,
    _SEND_DONE_SNAPSHOT,
)

_TRUNCATION_MARKER = (
    "\n...[model view truncated: original {chars} chars; "
    "full record retained in history/archive]...\n"
)
_TRUNCATION_MARKER_HINT = "full record retained in history/archive"


@dataclass(frozen=True, slots=True)
class ModelViewPolicy:
    """模型视图裁剪策略。所有“保留”都是指最近 N 个。"""

    enabled: bool = True
    task_context_keep: int = 2
    send_receipt_keep: int = 2
    send_status_keep: int = 2
    no_action_keep: int = 4
    max_record_chars: int = 8000
    project_tool_results: bool = True


@dataclass(slots=True)
class ModelViewResult:
    records: list[dict[str, Any]]
    raw_indices: list[int]
    dropped_runtime: int = 0
    dropped_no_action_pairs: int = 0
    dropped_no_action_records: int = 0
    capped_records: int = 0
    projected_tool_results: int = 0

    @property
    def dropped_records(self) -> int:
        return self.dropped_runtime + self.dropped_no_action_records


def runtime_kind(record: dict[str, Any]) -> str | None:
    """识别一次性运行时记录。

    优先看 metadata.kind（新记录），再退回内容开标签（旧记录）。
    只认 user 角色，避免把 assistant 的引用/复述误判成运行时块。
    """
    if not isinstance(record, dict) or record.get("role") != "user":
        return None
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        kind = str(metadata.get("kind") or "")
        if kind in _RUNTIME_KINDS:
            return kind
    content = str(record.get("content") or "").lstrip()
    if content.startswith("<task_context"):
        return _TASK_CONTEXT_SNAPSHOT
    if content.startswith("<send_receipt"):
        return _SEND_RECEIPT
    if content.startswith("<send_status"):
        return _SEND_DONE_SNAPSHOT
    return None


def assistant_tool_call_names(record: dict[str, Any]) -> dict[str, str]:
    if record.get("role") != "assistant":
        return {}
    result: dict[str, str] = {}
    for tool_call in record.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        call_id = str(tool_call.get("id") or "")
        function = tool_call.get("function")
        name = str(function.get("name") or "") if isinstance(function, dict) else ""
        if call_id and name:
            result[call_id] = name
    return result


def _tool_result_matches(record: dict[str, Any], call_id: str) -> bool:
    return (
        record.get("role") == "tool"
        and str(record.get("tool_call_id") or "") == call_id
    )


def no_action_pair_indices(
    records: list[dict[str, Any]],
    start: int,
) -> set[int]:
    """若 ``start`` 开始是一组完整 no_action 调用，返回整组下标。

    必须同时满足：assistant 无正文、所有 tool_calls 都叫 no_action、紧随其后的
    tool 结果与调用 ID 一一对应。任何一条不满足都返回空集合，绝不拆散配对。
    """
    if start < 0 or start >= len(records):
        return set()
    assistant_record = records[start]
    if assistant_record.get("role") != "assistant":
        return set()
    if str(assistant_record.get("content") or "").strip():
        return set()
    names = assistant_tool_call_names(assistant_record)
    if not names or any(name != "no_action" for name in names.values()):
        return set()
    call_ids = list(names)
    end = start + len(call_ids)
    if end >= len(records):
        return set()
    for offset, call_id in enumerate(call_ids, start=1):
        if not _tool_result_matches(records[start + offset], call_id):
            return set()
    return set(range(start, end + 1))


def _drop_old_runtime_records(
    records: list[dict[str, Any]],
    policy: ModelViewPolicy,
) -> tuple[set[int], int]:
    positions: dict[str, list[int]] = {kind: [] for kind in _RUNTIME_KINDS}
    for index, record in enumerate(records):
        kind = runtime_kind(record)
        if kind is not None:
            positions[kind].append(index)

    limits = {
        _TASK_CONTEXT_SNAPSHOT: policy.task_context_keep,
        _SEND_RECEIPT: policy.send_receipt_keep,
        _SEND_DONE_SNAPSHOT: policy.send_status_keep,
    }
    dropped: set[int] = set()
    for kind, indices in positions.items():
        keep = max(0, int(limits[kind]))
        if keep <= 0:
            dropped.update(indices)
            continue
        dropped.update(indices[:-keep])
    return dropped, len(dropped)


def _drop_old_no_action_pairs(
    records: list[dict[str, Any]],
    policy: ModelViewPolicy,
) -> tuple[set[int], int, int]:
    pairs: list[set[int]] = []
    index = 0
    while index < len(records):
        pair = no_action_pair_indices(records, index)
        if pair:
            pairs.append(pair)
            index = max(pair) + 1
            continue
        index += 1

    keep = max(0, int(policy.no_action_keep))
    if keep <= 0:
        dropped_pairs = pairs
    else:
        dropped_pairs = pairs[:-keep]
    dropped_records: set[int] = set()
    for pair in dropped_pairs:
        dropped_records.update(pair)
    return dropped_records, len(dropped_pairs), len(dropped_records)


def model_view_keep_indices(
    records: list[dict[str, Any]],
    *,
    policy: ModelViewPolicy,
) -> set[int]:
    keep = set(range(len(records)))
    if not policy.enabled:
        return keep
    runtime_dropped, _ = _drop_old_runtime_records(records, policy)
    no_action_dropped, _, _ = _drop_old_no_action_pairs(records, policy)
    keep.difference_update(runtime_dropped)
    keep.difference_update(no_action_dropped)
    return keep


def model_view_drop_indices(
    records: list[dict[str, Any]],
    *,
    policy: ModelViewPolicy,
) -> set[int]:
    keep = model_view_keep_indices(records, policy=policy)
    return set(range(len(records))) - keep


def _cap_content(content: str, limit: int) -> str:
    if limit <= 0 or len(content) <= limit:
        return content
    if _TRUNCATION_MARKER_HINT in content:
        return content
    marker = _TRUNCATION_MARKER.format(chars=len(content))
    room = max(0, limit - len(marker))
    if room <= 0:
        return marker.strip()
    head = max(0, int(room * 0.6))
    tail = max(0, room - head)
    prefix = content[:head].rstrip()
    suffix = content[-tail:].lstrip() if tail else ""
    return f"{prefix}{marker}{suffix}"


def _parse_tool_payload(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _project_tool_result(
    tool_name: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if tool_name == "no_action":
        return {"ok": True, "no_action": True}
    from tools.result_shrink import SEND_RESULT_TOOLS, project_send_result_payload

    if tool_name in SEND_RESULT_TOOLS:
        return project_send_result_payload(payload)
    return None


def build_model_view(
    records: list[dict[str, Any]],
    *,
    policy: ModelViewPolicy | None = None,
    conversation_id: str | None = None,
) -> ModelViewResult:
    """构造模型可见视图；不修改输入 records。"""
    policy = policy or ModelViewPolicy()
    _ = conversation_id  # 预留给未来按会话强制保留；当前真实消息全部保留。
    if not policy.enabled:
        return ModelViewResult(
            records=list(records),
            raw_indices=list(range(len(records))),
        )

    runtime_dropped, dropped_runtime = _drop_old_runtime_records(records, policy)
    no_action_dropped, dropped_pairs, dropped_pair_records = (
        _drop_old_no_action_pairs(records, policy)
    )
    dropped = runtime_dropped | no_action_dropped

    tool_names: dict[str, str] = {}
    for record in records:
        tool_names.update(assistant_tool_call_names(record))

    view: list[dict[str, Any]] = []
    raw_indices: list[int] = []
    capped_records = 0
    projected_tool_results = 0
    for index, record in enumerate(records):
        if index in dropped:
            continue
        kept = record
        if record.get("role") == "user":
            content = record.get("content")
            if isinstance(content, str) and len(content) > policy.max_record_chars:
                capped = dict(record)
                capped["content"] = _cap_content(content, policy.max_record_chars)
                kept = capped
                capped_records += 1
        elif record.get("role") == "tool" and policy.project_tool_results:
            content = record.get("content")
            payload = _parse_tool_payload(content) if isinstance(content, str) else None
            if payload is not None:
                tool_name = tool_names.get(
                    str(record.get("tool_call_id") or ""),
                    str(payload.get("tool") or ""),
                )
                projected = _project_tool_result(tool_name, payload)
                if projected is not None:
                    updated = dict(record)
                    updated["content"] = json.dumps(
                        projected,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    kept = updated
                    projected_tool_results += 1
        view.append(kept)
        raw_indices.append(index)

    return ModelViewResult(
        records=view,
        raw_indices=raw_indices,
        dropped_runtime=dropped_runtime,
        dropped_no_action_pairs=dropped_pairs,
        dropped_no_action_records=dropped_pair_records,
        capped_records=capped_records,
        projected_tool_results=projected_tool_results,
    )


def apply_model_view(
    records: list[dict[str, Any]],
    *,
    policy: ModelViewPolicy | None = None,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    return build_model_view(
        records,
        policy=policy,
        conversation_id=conversation_id,
    ).records


__all__ = [
    "ModelViewPolicy",
    "ModelViewResult",
    "apply_model_view",
    "build_model_view",
    "model_view_drop_indices",
    "model_view_keep_indices",
    "no_action_pair_indices",
    "runtime_kind",
]
