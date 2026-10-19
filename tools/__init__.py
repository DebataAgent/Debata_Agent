"""Debata_Agent 工具系统。

模块组成：
    base                —— ITool / ToolContext / ToolRegistry / @tool 装饰器
    schemas             —— 全部 Pydantic args 模型
    message_builder     —— 表情包/CQ 码/打字延迟等消息辅助
    messaging           —— send_private / send_group / recall / upload_file
    memory_tools        —— save / update / delete_important_memory
    platform_tools      —— list_contacts / get_user_info / get_forward_msg /
                           set_*_add_request / summarize_chat_history /
                           summarize_conversation / recall_history
    agent_task_tools    —— start_agent_task 后台子 Agent 资料处理
    control_tools       —— no_action / schedule_wakeup
    persona_tools       —— eat / sleep 人格生理状态工具
    feature_tools       —— describe_image / web_search / get_weather

公开 API：
    ToolContext         —— 给 AgentRunner 用的执行上下文
    ToolRegistry        —— 工具集合
    build_default_registry(config)
                        —— 构造稳定工具集合的工厂
    get_default_specs() —— 全部已注册的工具规格
"""

from __future__ import annotations

import logging
from dataclasses import replace

# 导入各模块的副作用：注册装饰器把工具加入全局列表
from . import (  # noqa: F401
    agent_task_tools,
    control_tools,
    feature_tools,
    group_admin_tools,
    memory_tools,
    messaging,
    persona_tools,
    platform_tools,
    qq_action_tools,
    tool_search_tools,
    workspace_tools,
)
from .base import (
    DEFAULT_NO_FEEDBACK_TOOLS,
    AgentTaskCallback,
    IVisionService,
    IWeatherService,
    IWebSearchService,
    ToolContext,
    ToolRegistry,
    ToolSchemaMode,
    ToolSpec,
    WakeupCallback,
    clear_default_registry,
    get_default_specs,
    tool,
)
from .message_builder import (
    FORBIDDEN_TAGS,
    build_emoji_hint,
    build_message_action,
    contains_forbidden,
    list_emoji_files,
    list_emoji_names,
    resolve_emoji_path,
    resolve_send_image_ref,
    typing_delay,
)

logger = logging.getLogger(__name__)


__all__ = [
    # base
    "ToolContext",
    "ToolRegistry",
    "ToolSpec",
    "ToolSchemaMode",
    "WakeupCallback",
    "IVisionService",
    "IWebSearchService",
    "IWeatherService",
    "DEFAULT_NO_FEEDBACK_TOOLS",
    "AgentTaskCallback",
    "tool",
    "get_default_specs",
    "clear_default_registry",
    # message_builder
    "FORBIDDEN_TAGS",
    "build_emoji_hint",
    "build_message_action",
    "contains_forbidden",
    "list_emoji_files",
    "list_emoji_names",
    "resolve_emoji_path",
    "resolve_send_image_ref",
    "typing_delay",
    # factory
    "build_default_registry",
    "MEMORY_FILE_TOOLS",
    "MEMORY_TOOLS",
    "PLATFORM_OPTIONAL_TOOLS",
    "FEATURE_TOOL_FEATURES",
    "FULL_SCHEMA_TOOLS",
    "STUB_SCHEMA_TOOLS",
]


# ============================================================
# 工厂：按配置决定启用工具集合
# ============================================================


MEMORY_TOOLS: set[str] = {
    "save_important_memory",
    "update_important_memory",
    "delete_important_memory",
}
"""长期重要记忆工具。RAG 只是历史召回，不再替代 important.json。"""

# 兼容旧测试/旧导入名。
MEMORY_FILE_TOOLS = MEMORY_TOOLS


PLATFORM_OPTIONAL_TOOLS: set[str] = {
    "set_friend_add_request",
    "set_group_add_request",
}
"""依赖白名单模式的工具——whitelist.mode == "verify" 时才有意义。
其它模式下保留注册，仅记录警告。"""


FEATURE_TOOL_FEATURES: dict[str, str] = {
    "describe_image": "vision",
    "web_search": "web_search",
    "get_weather": "weather",
    "send_voice_message": "tts",
}
"""feature 工具名 → features 字典里的字段名。"""


FULL_SCHEMA_TOOLS: set[str] = {
    "send_private_messages",
    "send_group_message",
    "commit_send_attempt",
    "no_action",
    "save_important_memory",
    "update_important_memory",
    "delete_important_memory",
    "get_recent_chat_messages",
    "get_forward_msg",
    "recall_message",
    "describe_image",
    "tool_search",
    "schedule_wakeup",
    "list_contacts",
    "get_user_info",
    "get_group_self_role",
    "get_msg",
    "send_poke",
    "set_msg_emoji_like",
    "set_friend_add_request",
    "set_group_add_request",
    "read_file",
    "write_file",
    "edit_file",
    "list_files",
    "delete_file",
    "run_python",
    "web_search",
    "get_weather",
    "eat",
    "sleep",
}
"""常驻完整 schema 工具（full profile 回滚用）。"""


COMPACT_SCHEMA_TOOLS: set[str] = {
    "send_private_messages",
    "send_group_message",
    "commit_send_attempt",
    "no_action",
    "save_important_memory",
    "update_important_memory",
    "delete_important_memory",
    "get_recent_chat_messages",
    "describe_image",
    "web_search",
    "tool_search",
}
"""compact profile 下直接暴露核心字段的工具。

其余工具全部降为 stub，模型需要时先 tool_search。依据真实调用频率选择：
这 11 个覆盖了绝大多数主流程调用，低频/高风险工具按需展开。
"""


_COMPACT_SHORT_DESCRIPTIONS: dict[str, str] = {
    "send_private_messages": "发送私聊消息；targets 按 order 排序，delay 控制间隔。",
    "send_group_message": "发送群消息；targets 按 order 排序，delay 控制间隔。",
    "commit_send_attempt": "提交 needs_review 的发送尝试；同一 attempt 只能提交一次。",
    "no_action": "本轮不操作，结束回复。",
    "save_important_memory": "保存一条长期重要记忆。",
    "update_important_memory": "更新已有重要记忆。",
    "delete_important_memory": "删除重要记忆。",
    "get_recent_chat_messages": "读取当前会话最近的真实消息窗口。",
    "describe_image": "理解图片内容或提取图中文字。",
    "web_search": "联网搜索实时/最新信息。",
    "tool_search": "查询低频工具的参数摘要、完整 schema 和风险约束。",
}


_COMPACT_HIDDEN_FIELDS: dict[str, list[str]] = {
    "send_private_messages": [
        "reviewed_until_seq",
        "review_policy",
        "delivery_interrupt_policy",
        "ignore_review_interrupts",
        "responding_to_message_ids",
        "reply_to_message_id",
        "reason",
    ],
    "send_group_message": [
        "reviewed_until_seq",
        "review_policy",
        "delivery_interrupt_policy",
        "ignore_review_interrupts",
        "responding_to_message_ids",
        "reply_to_message_id",
        "reason",
    ],
    "commit_send_attempt": [
        "reviewed_until_seq",
        "delivery_interrupt_policy",
        "reply_to_message_id",
        "ignore_review_interrupts",
        "reason",
    ],
    "save_important_memory": ["pinned"],
    "update_important_memory": ["pinned", "reason"],
    "delete_important_memory": ["keyword"],
    "get_recent_chat_messages": ["include_raw", "before_msg_id"],
    "describe_image": ["prompt"],
    "tool_search": ["intent"],
}


_PERSONA_CONDITIONAL_TOOLS: dict[str, tuple[str, str]] = {
    "sleep": ("energy", "tool"),
    "eat": ("satiety", "tool"),
}


STUB_SCHEMA_TOOLS: set[str] = {
    "start_agent_task",
    "summarize_chat_history",
    "summarize_conversation",
    "filter_archive_records",
    "recall_history",
    "upload_file",
    "send_voice_message",
    "set_group_kick",
    "set_group_ban",
    "set_group_whole_ban",
    "set_group_leave",
}
"""低频/高风险/大 schema 工具：常驻名称与简述，调用前通过 tool_search 查询参数摘要。"""


_STUB_SHORT_DESCRIPTIONS: dict[str, str] = {
    "start_agent_task": "低频资料整理工具。先用 tool_search 查询参数摘要；需要完整 schema 时 detail=full。",
    "summarize_chat_history": "低频群历史总结工具。先用 tool_search 查询参数摘要；需要完整 schema 时 detail=full。",
    "summarize_conversation": "低频本地会话总结工具。先用 tool_search 查询参数摘要；需要完整 schema 时 detail=full。",
    "filter_archive_records": "低频归档筛选工具。先用 tool_search 查询参数摘要；需要完整 schema 时 detail=full。",
    "recall_history": "低频归档检索工具。先用 tool_search 查询参数摘要；需要完整 schema 时 detail=full。",
    "upload_file": "文件发送工具。先用 tool_search 查询参数摘要和约束；需要完整 schema 时 detail=full。",
    "send_voice_message": "语音发送工具。先用 tool_search 查询参数摘要；需要完整 schema 时 detail=full。",
    "set_group_kick": "高风险群管理工具。先用 tool_search 查询参数摘要和约束；需要完整 schema 时 detail=full。",
    "set_group_ban": "高风险群管理工具。先用 tool_search 查询参数摘要和约束；需要完整 schema 时 detail=full。",
    "set_group_whole_ban": "高风险群管理工具。先用 tool_search 查询参数摘要和约束；需要完整 schema 时 detail=full。",
    "set_group_leave": "高风险群管理工具。先用 tool_search 查询参数摘要和约束；需要完整 schema 时 detail=full。",
    "schedule_wakeup": "定时唤醒/定时发送工具。先用 tool_search 查询参数摘要。",
    "read_file": "读取 workspace 文件（分页）。先用 tool_search 查询参数摘要。",
    "write_file": "覆盖写入 workspace 文件。先用 tool_search 查询参数摘要。",
    "edit_file": "按唯一字符串替换编辑 workspace 文件。先用 tool_search 查询参数摘要。",
    "list_files": "列出 workspace 文件。先用 tool_search 查询参数摘要。",
    "delete_file": "删除 workspace 文件。先用 tool_search 查询参数摘要。",
    "run_python": "在 workspace 执行 Python。先用 tool_search 查询参数摘要和约束。",
    "get_user_info": "查询 QQ 用户信息。先用 tool_search 查询参数摘要。",
    "get_forward_msg": "展开合并转发消息。先用 tool_search 查询参数摘要。",
    "get_group_self_role": "查询机器人在群里的权限。先用 tool_search 查询参数摘要。",
    "recall_message": "撤回 2 分钟内的消息。先用 tool_search 查询参数摘要。",
    "set_friend_add_request": "处理好友申请。先用 tool_search 查询参数摘要。",
    "set_group_add_request": "处理加群/邀请申请。先用 tool_search 查询参数摘要。",
    "list_contacts": "查询好友/群/群成员列表。先用 tool_search 查询参数摘要。",
    "send_poke": "戳一戳。先用 tool_search 查询参数摘要。",
    "set_msg_emoji_like": "给消息添加/取消表情回应。先用 tool_search 查询参数摘要。",
    "get_msg": "读取单条消息。先用 tool_search 查询参数摘要。",
    "get_weather": "查询天气。先用 tool_search 查询参数摘要。",
    "eat": "人格进食动作。先用 tool_search 查询参数摘要。",
    "sleep": "人格睡眠动作。先用 tool_search 查询参数摘要。",
}


_STUB_RISK_LEVELS: dict[str, str] = {
    "upload_file": "medium",
    "send_voice_message": "medium",
    "start_agent_task": "medium",
    "set_group_kick": "high",
    "set_group_ban": "high",
    "set_group_whole_ban": "high",
    "set_group_leave": "high",
    "write_file": "medium",
    "delete_file": "medium",
    "run_python": "medium",
    "schedule_wakeup": "medium",
}


def build_default_registry(config) -> ToolRegistry:
    """根据 RootConfig 构造稳定工具 Registry。

    规则：
        - messaging 工具（send_*/recall）：始终启用
        - upload_file：始终注册为 stub，执行受 ctx.workspace_dir / adapter 进一步限制
        - memory 工具：始终启用；RAG 是历史召回，不替代 important.json
        - platform 工具：始终启用（按需）
        - control 工具：始终启用
        - feature 工具：始终注册；执行时按 ctx 是否注入 service 返回成功或未启用
        - 低频/高风险/大 schema 工具：始终注册为 stub，通过 tool_search 查询参数摘要

    Args:
        config: RootConfig 实例

    Returns:
        ToolRegistry 实例
    """
    specs = list(get_default_specs())
    memory_mode = config.features.long_term_memory.mode
    features = config.features
    persona_management = getattr(config, "persona_management", None)
    persona_enabled = bool(getattr(persona_management, "enabled", False))
    physiology = getattr(persona_management, "physiology", None)
    energy_mode = _persona_physiology_mode(physiology, "energy")
    satiety_mode = _persona_physiology_mode(physiology, "satiety")
    persona_modes = {
        "energy": energy_mode,
        "satiety": satiety_mode,
    }

    schema_profile = str(
        getattr(getattr(config, "behavior", None), "tool_schema_profile", "compact")
        or "compact"
    )
    enabled: list[ToolSpec] = []
    for spec in specs:
        if spec.name in _PERSONA_CONDITIONAL_TOOLS:
            mode_name, required_mode = _PERSONA_CONDITIONAL_TOOLS[spec.name]
            if not persona_enabled or persona_modes[mode_name] != required_mode:
                continue

        configured = spec
        if schema_profile == "full":
            # 旧行为：保留给回滚与对照测试。
            if spec.name in STUB_SCHEMA_TOOLS:
                configured = replace(
                    spec,
                    schema_mode=ToolSchemaMode.STUB,
                    short_description=_STUB_SHORT_DESCRIPTIONS.get(spec.name),
                    risk_level=_STUB_RISK_LEVELS.get(spec.name, "low"),
                    search_tags=list(spec.search_tags) or [spec.category, spec.name],
                )
            elif spec.name in FULL_SCHEMA_TOOLS:
                configured = replace(spec, schema_mode=ToolSchemaMode.FULL)
        elif spec.name in COMPACT_SCHEMA_TOOLS:
            configured = replace(
                spec,
                schema_mode=ToolSchemaMode.COMPACT,
                short_description=(
                    _COMPACT_SHORT_DESCRIPTIONS.get(spec.name)
                    or spec.short_description
                ),
                compact_hidden_fields=list(
                    _COMPACT_HIDDEN_FIELDS.get(spec.name, [])
                ),
            )
        else:
            configured = replace(
                spec,
                schema_mode=ToolSchemaMode.STUB,
                short_description=(
                    _STUB_SHORT_DESCRIPTIONS.get(spec.name)
                    or spec.short_description
                ),
                risk_level=_STUB_RISK_LEVELS.get(spec.name, spec.risk_level),
                search_tags=list(spec.search_tags) or [spec.category, spec.name],
            )
        enabled.append(configured)

    registry = ToolRegistry(enabled)
    logger.info(
        f"build_default_registry: 启用 {len(registry)} 个工具 "
        f"(schema_profile={schema_profile}, memory_mode={memory_mode}, "
        f"vision={features.vision.enabled}, "
        f"web_search={features.web_search.enabled}, "
        f"weather={features.weather.enabled}, "
        f"persona_management={persona_enabled}, "
        f"energy_mode={energy_mode}, "
        f"satiety_mode={satiety_mode})"
    )
    return registry


def _persona_physiology_mode(physiology, name: str) -> str:
    item = physiology.get(name) if isinstance(physiology, dict) else getattr(physiology, name, None)
    if isinstance(item, dict):
        return str(item.get("mode") or "disabled")
    return str(getattr(item, "mode", "disabled") or "disabled")
