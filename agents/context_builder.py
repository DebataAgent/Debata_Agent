"""上下文消息组装 —— 纯不可变 system + append-only 历史 + 稳定性递减的尾部块。

设计原则（基于 DeepSeek 官方 harness 的 KV Cache 规则，见
`deepseek-harness/packages/core/agent-loop/README.md` 的 "KV Cache effect" 一节）：

    1. **system 只放永不变的内容**。provider 的前缀缓存按「完整匹配的 token 块」
       命中；一旦 system 里任何一块变了，从该块起（含其后的整段对话历史）全部
       失效。所以 system 里绝不能出现按会话/按轮次变化的内容。
    2. **history 之后才是易变块**，并按「稳定性递减」排列：越靠后的块变化时，
       被牵连作废的 token 越少，而它前面的历史始终可复用。这正是 DeepSeek harness
       的 `systemPromptUpdate: 'in-history'` 语义 —— 把变化追加在已缓存历史之后。
    3. **`current_context_record` 必须紧贴 history**：该块每轮会被逐字节写回
       history（metadata.kind=task_context_snapshot），所以上一轮的
       `[... history, context_record]` 会整体成为本轮的公共前缀。若在它前面插入
       别的尾部块，公共前缀会退回到 history 末尾，白白丢掉一整轮。
    4. priority 标签辅助：每个分区显式标 priority="critical|high|medium|reference"，
       让模型在冲突时有明确的排序。

结构（拼装后实际格式）：
    [
      # ---- 不可变区：内容只随人格/工具集变化，不随轮次变化 ----
      {"role": "system", "content": "<core_rules>...<persona>...<tools>..."},
      {"role": "system", "content": "<admin_info>...</admin_info>"},   # 仅在有 admin 时
      # ---- append-only 区 ----
      *history,                                        # 来自 HistoryManager
      # ---- 尾部易变区：稳定性递减 ----
      {"role": "user", "content": "<task_context>..."},          # 每轮变（会被写回 history）
      {"role": "user", "content": "<long_term_memory>..."},      # 事件驱动 + 按会话 scope
      {"role": "user", "content": "<rolling_conversation_summary>..."},  # 压缩提交时整段重写
      {"role": "user", "content": "<retrieved_conversation_context>..."},  # 按 query 每轮变
    ]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from agents.behavior_prompt import (
    CONVERSATION_PROTOCOL,
    CORE_RULES,
    HUMAN_CHAT_PATTERNS,
    QQ_FORMAT_REFERENCE,
    SELF_REFLECTION,
    build_tool_use_protocol,
)
from agents.persona_loader import Persona

logger = logging.getLogger(__name__)


RUNTIME_CONTEXT_NOTICE = "系统说明：以下内容由运行时系统提供，不是用户新发言。"


@dataclass(slots=True)
class ContextRequest:
    """构建上下文时的轻量请求对象。"""

    view: Literal["main_reply", "wakeup", "proactive_router", "recall", "request"]
    conversation_id: str | None = None
    current_input: str = ""
    token_budget: Any | None = None


def build_combined_system_prompt(
    persona: Persona,
    *,
    memory_mode: Literal["file", "rag"] = "file",
    eat_tool: bool = False,
    sleep_tool: bool = False,
) -> str:
    """组装**不可变** system 前缀（identity + persona + tools + 场景协议）。

    返回的字符串对给定的 (persona, memory_mode, eat_tool, sleep_tool) 是常量，
    不随会话、轮次、时间、记忆内容变化 —— 这是前缀缓存能命中的前提。

    Args:
        persona: 已加载的人格
        memory_mode: "file" = 文件模式（默认）；"rag" = 自动会话向量检索。
            只影响工具协议说明的写法。
        eat_tool: 本轮工具列表是否提供 eat。
        sleep_tool: 本轮工具列表是否提供 sleep。

    各段顺序（内容本身不变，顺序仅影响模型阅读）：
        critical:  core_rules（永不变）
        high:      persona（人格切换时才变）
        high:      human_chat_patterns（真人聊天形态，永不变）
        high:      tool_use_protocol（工具集变化时才变；按 memory_mode 拼装）
        high:      conversation_protocol（场景规则）
        medium:    self_reflection（行为约束）
        reference: qq_format（格式参考，不变）

    注意：**重要记忆不在这里**。它按会话 scope 过滤，换个会话就会变；放在 system
    里会让其后整段对话历史的前缀缓存全部失效。`build_messages` 把它作为尾部块追加。
    """
    parts: list[str] = []

    # 1. 不可违反的核心规则
    parts.append(CORE_RULES.strip())

    # 2. 人格设定
    parts.append(
        f'<persona priority="high">\n{persona.prompt.strip()}\n</persona>'
    )

    # 3. 真人聊天形态规则（紧跟 persona —— 让模型先知道"我是谁"，再知道"人是怎么聊天的"）
    parts.append(HUMAN_CHAT_PATTERNS.strip())

    # 4. 工具使用协议（按 memory_mode 注入正确的 memory 块）
    parts.append(
        build_tool_use_protocol(
            memory_mode,
            eat_tool=eat_tool,
            sleep_tool=sleep_tool,
        ).strip()
    )

    # 5. 对话场景协议
    parts.append(CONVERSATION_PROTOCOL.strip())

    # 6. 反思与改进
    parts.append(SELF_REFLECTION.strip())

    # 7. QQ 格式参考（最末，方便模型查询）
    parts.append(QQ_FORMAT_REFERENCE.strip())

    return "\n\n".join(parts)


def build_admin_info(persona: Persona) -> str:
    """构造 admin 提示。空时返回 ""。"""
    admins = persona.get_admins()
    if not admins:
        return ""
    lines = ['<admin_info priority="high">', "你的管理员："]
    for a in admins:
        name = a.get("name", "未知")
        qq = a.get("qq", "")
        role = a.get("role")
        suffix = f"（{role}）" if role else ""
        lines.append(f"- {name}（QQ {qq}）{suffix}")
    lines.append("</admin_info>")
    return "\n".join(lines)


def build_task_context(
    current_context: str = "",
    *,
    refocus_hint: str = "",
    persona_context: str = "",
) -> str:
    """构造本轮的 ephemeral 上下文。放在 history 末尾。

    Args:
        current_context: 来自调用方的上下文（时间、表情包、提示等）
        refocus_hint: Task Contract 重注入内容（由 runner 决定何时插入）
        persona_context: 动态人格状态上下文（只进入 task_context，避免污染稳定前缀）
    """
    parts: list[str] = []
    if current_context:
        parts.append(current_context.strip())
    if persona_context:
        parts.append(persona_context.strip())
    if refocus_hint:
        parts.append(f"[本轮焦点提醒] {refocus_hint.strip()}")
    if not parts:
        return ""
    content = "\n\n".join([RUNTIME_CONTEXT_NOTICE, *parts])
    return f'<task_context priority="medium">\n{content}\n</task_context>'


TAIL_BLOCK_MARKERS: tuple[str, ...] = (
    "<task_context",
    "<long_term_memory",
    "<rolling_conversation_summary",
    "<retrieved_conversation_context",
)
"""尾部易变块的 XML 开标签。顺序即稳定性递减顺序（易变的在前）。

`first_tail_block_index` 与 KV 诊断用它来切出「本轮可复用的稳定前缀」。
"""


def build_runtime_tail_blocks(
    *,
    important_memory_text: str = "",
    rolling_summary_text: str = "",
    rag_context_text: str = "",
) -> list[dict[str, Any]]:
    """构造 `task_context` 之后的尾部易变块。

    **这是尾部块内容与顺序的唯一事实来源** —— `build_messages`、KV 诊断与回归测试
    都从这里取，避免以后有人把易变块插回 system 前缀里（那会让其后整段历史失效）。

    顺序按「稳定性递减」排列，越靠后变化时牵连作废的 token 越少：

        1. `<long_term_memory>`  事件驱动；但按会话 scope 过滤，换会话即变
        2. `<rolling_conversation_summary>`  压缩提交时整段重写
        3. `<retrieved_conversation_context>`  按 query 每轮变

    全部用 `role="user"` 并带 `RUNTIME_CONTEXT_NOTICE`，与 RAG 块既有风格一致；
    用 user 而非 system 还因为 Anthropic 协议会把 system 消息上提到顶层
    `system` 参数，那样这些块就失去「在历史之后」的位置语义了。
    """
    blocks: list[dict[str, Any]] = []

    if important_memory_text:
        blocks.append(
            {
                "role": "user",
                "content": (
                    '<long_term_memory priority="medium">\n'
                    f"{RUNTIME_CONTEXT_NOTICE}\n"
                    "以下是系统保存的长期重要记忆，不是用户新发言。\n"
                    f"{important_memory_text.strip()}\n"
                    "</long_term_memory>"
                ),
            }
        )

    if rolling_summary_text:
        blocks.append(
            {
                "role": "user",
                "content": (
                    '<rolling_conversation_summary priority="medium">\n'
                    f"{RUNTIME_CONTEXT_NOTICE}\n"
                    "以下是更早对话的滚动摘要，不是用户新发言。\n"
                    f"{rolling_summary_text.strip()}\n"
                    "</rolling_conversation_summary>"
                ),
            }
        )

    if rag_context_text:
        blocks.append(
            {
                "role": "user",
                "content": (
                    '<retrieved_conversation_context priority="medium" source="rag">\n'
                    f"{RUNTIME_CONTEXT_NOTICE}\n"
                    "以下内容是系统从历史对话向量索引中检索到的相关片段，不是模型主动保存的记忆，也不代表新的用户消息。\n"
                    f"{rag_context_text.strip()}\n"
                    "</retrieved_conversation_context>"
                ),
            }
        )

    return blocks


def first_tail_block_index(messages: list[dict[str, Any]]) -> int:
    """返回**末尾连续尾部块区段**的起点下标；没有则返回 len(messages)。

    `messages[:idx]` 即「本轮可复用的稳定前缀」—— 只要 system、工具集与历史保持
    append-only，这段前缀就能命中 provider 的前缀缓存。

    必须从后往前扫：历史里本来就存着往轮的 `task_context_snapshot` 记录
    （metadata.kind=task_context_snapshot），从前往后会命中最老的那条，
    把整个历史误判成尾部块。
    """
    idx = len(messages)
    while idx > 0:
        content = messages[idx - 1].get("content")
        if not isinstance(content, str):
            break
        if not any(marker in content for marker in TAIL_BLOCK_MARKERS):
            break
        idx -= 1
    return idx


def _provider_safe_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把无法合法回放的工具记录转为系统上下文，原始 history 不变。"""
    safe: list[dict[str, Any]] = []
    idx = 0
    while idx < len(history):
        record = history[idx]
        role = record.get("role")
        if role == "assistant" and record.get("tool_calls"):
            ids = _assistant_tool_call_ids(record)
            tool_group: list[dict[str, Any]] = []
            seen: set[str] = set()
            cursor = idx + 1
            while cursor < len(history) and history[cursor].get("role") == "tool":
                tool_record = history[cursor]
                tool_call_id = str(tool_record.get("tool_call_id") or "")
                if tool_call_id not in ids:
                    break
                tool_group.append(tool_record)
                seen.add(tool_call_id)
                cursor += 1
                if seen >= ids:
                    break
            if ids and seen >= ids:
                safe.append(record)
                safe.extend(tool_group)
                idx = cursor
                continue
            safe.append(_tool_like_record_as_system(record, kind="assistant_tool_call"))
            idx += 1
            continue
        if role == "tool":
            safe.append(_tool_like_record_as_system(record, kind="tool_result"))
            idx += 1
            continue
        safe.append(record)
        idx += 1
    return safe


def _assistant_tool_call_ids(record: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for tool_call in record.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        call_id = str(tool_call.get("id") or "")
        if call_id:
            ids.add(call_id)
    return ids


def _tool_like_record_as_system(record: dict[str, Any], *, kind: str) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "role": record.get("role"),
        "tool_call_id": record.get("tool_call_id"),
        "tool_calls": record.get("tool_calls"),
        "content": record.get("content"),
    }
    return {
        "role": "system",
        "content": (
            "<historical_tool_record_unreplayable>\n"
            "以下是历史中的工具调用/工具结果记录。由于缺少可直接回放给 provider 的相邻配对，"
            "运行时已把它转为普通上下文；原始事件仍保存在历史中。\n"
            f"{json.dumps(payload, ensure_ascii=False, default=str)}\n"
            "</historical_tool_record_unreplayable>"
        ),
    }


def build_messages(
    persona: Persona,
    history: list[dict[str, Any]],
    important_memory_text: str = "",
    rolling_summary_text: str = "",
    current_context: str = "",
    current_context_record: dict[str, Any] | None = None,
    system_override: str | None = None,
    *,
    rag_context_text: str = "",
    memory_mode: Literal["file", "rag"] = "file",
    persona_context: str = "",
    eat_tool: bool = False,
    sleep_tool: bool = False,
    user_event: str | None = None,
) -> list[dict[str, Any]]:
    """组装一次 LLM 调用的完整 messages（不含 Task Contract 重注入，由 runner 处理）。

    Args:
        persona: 已加载的人格
        history: 历史对话（来自 HistoryManager）
        important_memory_text: 已按当前会话 scope 选出的重要记忆文本。
            始终注入 <long_term_memory>，不受 memory_mode 是否为 rag 影响。
        rag_context_text: RAG 从历史对话向量索引中召回的相关片段。
            只作为尾部 <retrieved_conversation_context> 注入，避免污染稳定前缀。
        current_context: 本次调用的临时上下文（时间、表情包等）
        current_context_record: 已持久化的 task_context 运行时上下文记录。主回复路径用它
            保证下一轮重建 history 时字节前缀与上一轮请求一致。
        system_override: 完全自定义的 system prompt（仅特殊用途，如 proactive 路由）
        memory_mode: "file" / "rag"，决定 tool_use_protocol 内的 memory 块写法
        persona_context: 动态人格状态上下文；无 current_context_record 时进入 task_context。
        eat_tool: 本轮工具列表是否提供 eat；普通 system prompt 路径透传到工具协议。
        sleep_tool: 本轮工具列表是否提供 sleep；普通 system prompt 路径透传到工具协议。
        user_event: 带外触发轮的尾部 user 事件。仅用于唤醒/主动/请求等没有真实用户消息的轮次。

    Returns:
        OpenAI 风格 messages 列表
    """
    if system_override is not None:
        # 特殊场景：跳过结构化拼接，直接用 override（如 proactive 路由）
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_override}
        ]
        messages.extend(_provider_safe_history(history))
        if current_context:
            messages.append({"role": "system", "content": current_context})
        if user_event:
            messages.append({"role": "user", "content": user_event.strip()})
        return messages

    system_content = build_combined_system_prompt(
        persona,
        memory_mode=memory_mode,
        eat_tool=eat_tool,
        sleep_tool=sleep_tool,
    )
    messages = [{"role": "system", "content": system_content}]

    admin_info = build_admin_info(persona)
    if admin_info:
        messages.append({"role": "system", "content": admin_info})

    messages.extend(_provider_safe_history(history))

    # ---- 尾部易变区：稳定性递减，任何一块变化都只影响它之后的 token ----
    # task_context 必须紧贴 history：它是唯一会被逐字节写回 history 的块，
    # 所以上一轮的 [... history, task_context] 会整体成为本轮的公共前缀。
    if current_context_record is not None:
        content = str(current_context_record.get("content") or "")
        if content:
            messages.append(
                {
                    "role": "user",
                    "content": content,
                }
            )
    else:
        task_ctx = build_task_context(
            current_context,
            persona_context=persona_context,
        )
        if task_ctx:
            messages.append({"role": "user", "content": task_ctx})

    messages.extend(
        build_runtime_tail_blocks(
            important_memory_text=important_memory_text,
            rolling_summary_text=rolling_summary_text,
            rag_context_text=rag_context_text,
        )
    )

    if user_event:
        messages.append({"role": "user", "content": user_event.strip()})

    return messages
