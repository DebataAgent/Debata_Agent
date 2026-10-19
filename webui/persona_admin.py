"""角色人格的生成 / 改写 / 落盘（纯函数为主，便于单测）。

原桌面端把这条链路放在两个对话框里（``ui/dashboard/persona_dialogs.py`` 的
``_PersonaCreatorDialog`` / ``_PersonaRefineDialog``），它们各自持有一份"当前主模型"
的临时状态来构造 ``PersonaGenAgent``。搬到 WebUI 后改成从**已持久化的配置**取：

    agents.persona_gen（人格生成 Agent 的专用配置）> agents.chat（跟随主模型）

这样不必在前后端之间来回复制 provider/base_url/api_key，也不会出现"设置页改了模型、
生成人格却还用着旧模型"的错位。

生成器本身（``agents/persona_gen_agent.py``）一行没动——它是完整的，只是一直
没接上入口。``webui/wizard/persona_render.py`` 的 ``render_minimal_persona``
同理，此前零调用，现在在这里被用上。

落盘约定（与 ``agents/persona_loader.py`` 一致）：
    personas/<name>/persona_prompt.py
覆盖已有角色前**一定先备份**到 ``personas/<name>/.backups/persona_prompt_<时间戳>.py``
——原 GUI 有这个行为，改写是 AI 做的，没备份等于把用户的角色交出去了。
"""

from __future__ import annotations

import ast
import logging
import re
import time
from pathlib import Path
from typing import Any

from agents.persona_gen_agent import PersonaBrief

logger = logging.getLogger(__name__)

PERSONA_FILE = "persona_prompt.py"
BACKUP_DIR = ".backups"

# 目录名要能安全地出现在路径里，同时不能用别人的名字覆盖内置角色
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RESERVED_NAMES = frozenset({"__pycache__", "backups"})

BRIEF_FIELDS = (
    "name",
    "personality",
    "background",
    "voice_samples",
    "boundaries",
    "never_say",
    "relation_matrix",
    "sensitive_topics",
    "relation",
    "relation_detail",
    "admin_name",
    "admin_qq",
    "gender",
    "extra_notes",
)


class PersonaAdminError(Exception):
    """操作失败。消息直接展示给用户。"""


# ============================================================
# 校验与转换
# ============================================================


def validate_persona_name(name: str) -> str:
    """角色目录名。中文名不允许——它同时是 Python 包目录名。"""
    candidate = (name or "").strip()
    if not candidate:
        raise PersonaAdminError("角色名不能为空")
    if not _NAME_RE.match(candidate):
        raise PersonaAdminError(
            "角色名用字母开头，只能含字母、数字、下划线、点和短横线，最长 64 个字符"
            "（它会同时作为目录名）"
        )
    if candidate in _RESERVED_NAMES:
        raise PersonaAdminError(f"{candidate} 是保留名，换一个")
    return candidate


def brief_from_payload(payload: dict[str, Any]) -> PersonaBrief:
    """把请求体翻成 PersonaBrief。只取已知字段，多余的忽略。"""
    values: dict[str, Any] = {k: payload.get(k) or "" for k in BRIEF_FIELDS}
    age = payload.get("age")
    values["age"] = int(age) if age not in (None, "") else None
    admins = payload.get("admins")
    values["admins"] = [dict(x) for x in admins if isinstance(x, dict)] if isinstance(admins, list) else []
    if not str(values.get("name") or "").strip():
        raise PersonaAdminError("角色名不能为空")
    return PersonaBrief(**values)


def extract_prompt_source(text: str) -> str:
    """从 persona_prompt.py 里取出 PERSONA_PROMPT 的字面量。

    用 ``ast`` 而不是 exec：人格文件是用户/AI 写的代码，这里只需要那个字符串常量，
    没有理由把整个模块执行一遍。解析不出来时退回正则，再不行就原样返回。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _extract_by_regex(text)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PERSONA_PROMPT":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return _extract_by_regex(text)
                return value if isinstance(value, str) else _extract_by_regex(text)
    return _extract_by_regex(text)


def _extract_by_regex(text: str) -> str:
    match = re.search(r"PERSONA_PROMPT\s*=\s*(['\"]{3})(.*?)\1", text, re.DOTALL)
    if match:
        return match.group(2)
    return text


# ============================================================
# 生成 / 改写
# ============================================================


def resolve_gen_agent_config(rt: Any) -> Any:
    """人格生成用哪个 Agent 配置：专用配置优先，否则跟随主聊天模型。"""
    persona_gen = getattr(rt.config.agents, "persona_gen", None)
    if persona_gen is not None:
        return persona_gen
    return rt.config.agents.chat


def build_gen_agent(rt: Any):
    """构造 PersonaGenAgent。

    优先复用 Runtime 已经建好的 provider 实例（连接池、代理设置都是现成的）；
    Runtime 没起来时按配置临时构造一个，用完由调用方关闭。
    """
    from agents import PersonaGenAgent
    from providers import build_provider
    from providers.base import ProviderError

    cfg = resolve_gen_agent_config(rt)
    provider = getattr(rt, "providers", {}).get(cfg.provider)
    temporary = False
    if provider is None:
        presets = dict(getattr(rt.provider_registry, "presets", {}) or {})
        try:
            provider = build_provider(cfg.provider, rt.config.providers[cfg.provider], rt.secrets, presets)
        except (KeyError, ProviderError) as e:
            raise PersonaAdminError(
                f"人格生成用的提供商 {cfg.provider!r} 不可用：{e}。"
                "到「设置 → 模型」确认它已配置好密钥。"
            ) from e
        temporary = True

    agent = PersonaGenAgent(
        provider,
        cfg,
        usage_recorder=getattr(rt, "_record_model_usage", None),
        status_callback=getattr(rt, "_update_model_activity", None),
    )
    return agent, provider if temporary else None


async def generate_draft(rt: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """生成初版人格（不落盘）。"""
    brief = brief_from_payload(payload)
    agent, temporary = build_gen_agent(rt)
    try:
        result = await agent.generate(brief)
    except Exception as e:  # noqa: BLE001
        logger.exception("生成人格失败")
        raise PersonaAdminError(f"生成未能完成：{e}") from e
    finally:
        if temporary is not None:
            await _close_quietly(temporary)
    if not (result.persona_prompt or "").strip():
        raise PersonaAdminError("模型没有返回内容，换一个模型或稍后再试")
    return {
        "ok": True,
        "xml": result.persona_prompt,
        "name": result.display_name or brief.name,
        "reasoning": result.reasoning or "",
        "refined_count": result.refined_count,
    }


async def refine_draft(rt: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """按用户反馈出修订版（不落盘）。可反复调用，``refined_count`` 递增。"""
    prev = str(payload.get("xml") or "")
    feedback = str(payload.get("feedback") or "").strip()
    if not prev.strip():
        raise PersonaAdminError("没有可改写的底稿")
    if not feedback:
        raise PersonaAdminError("说说想改哪里")

    agent, temporary = build_gen_agent(rt)
    try:
        result = await agent.refine(
            prev,
            feedback,
            int(payload.get("refined_count") or 0),
            edit_history=payload.get("edit_history") or None,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("改写人格失败")
        raise PersonaAdminError(f"改写未能完成：{e}") from e
    finally:
        if temporary is not None:
            await _close_quietly(temporary)
    return {
        "ok": True,
        "xml": result.persona_prompt,
        "name": result.display_name,
        "reasoning": result.reasoning or "",
        "refined_count": result.refined_count,
    }


async def _close_quietly(provider: Any) -> None:
    try:
        await provider.aclose()
    except Exception:  # noqa: BLE001
        logger.debug("关闭临时 provider 失败", exc_info=True)


# ============================================================
# 落盘
# ============================================================


def persona_dir(paths: Any, name: str) -> Path:
    return Path(paths.PERSONAS_DIR) / name


def backup_persona(paths: Any, name: str) -> Path | None:
    """把现有 persona_prompt.py 备份到 .backups/。没有原文件则返回 None。"""
    source = persona_dir(paths, name) / PERSONA_FILE
    if not source.is_file():
        return None
    backup_dir = persona_dir(paths, name) / BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    # 时间戳到秒；同一秒内连续覆盖会加序号，避免互相盖掉
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"persona_prompt_{stamp}.py"
    index = 1
    while target.exists():
        target = backup_dir / f"persona_prompt_{stamp}_{index}.py"
        index += 1
    try:
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as e:
        raise PersonaAdminError(f"备份失败（未做任何修改）：{e}") from e
    logger.info("人格已备份: %s", target)
    return target


def save_persona(
    paths: Any,
    name: str,
    xml: str,
    *,
    brief: PersonaBrief | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """把生成的人设写成 ``personas/<name>/persona_prompt.py``。

    覆盖已有角色时**先备份**——改写是 AI 做的，没备份等于把用户的角色交出去了。
    """
    from webui.wizard.persona_render import admin_entries_from_brief, render_minimal_persona

    safe_name = validate_persona_name(name)
    content = (xml or "").strip()
    if not content:
        raise PersonaAdminError("内容为空，没什么可保存的")

    target_dir = persona_dir(paths, safe_name)
    target = target_dir / PERSONA_FILE
    existed = target.is_file()
    if existed and not overwrite:
        raise PersonaAdminError(f"{safe_name} 已存在。换个名字，或确认覆盖（会先自动备份）")

    backup: Path | None = None
    if existed:
        backup = backup_persona(paths, safe_name)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render_minimal_persona(
                safe_name,
                content,
                admins=admin_entries_from_brief(brief),
                gender=(brief.gender if brief else "") or "",
            ),
            encoding="utf-8",
        )
    except OSError as e:
        raise PersonaAdminError(f"写入失败：{e}") from e

    logger.info("人格已写入: %s（备份: %s）", target, backup or "无")
    return {
        "ok": True,
        "id": safe_name,
        "path": str(target),
        "created": not existed,
        "backup": str(backup) if backup else "",
        "message": f"{'已更新' if existed else '已创建'}角色 {safe_name}",
    }


def duplicate_persona(paths: Any, name: str, new_name: str) -> str:
    """复制一个角色（连同它的非备份资源）。"""
    source_dir = persona_dir(paths, name)
    if not (source_dir / PERSONA_FILE).is_file():
        raise PersonaAdminError(f"没有 {name} 这个角色")

    safe_new = validate_persona_name(new_name)
    target_dir = persona_dir(paths, safe_new)
    if target_dir.exists():
        raise PersonaAdminError(f"{safe_new} 已存在，换个名字")

    try:
        target_dir.mkdir(parents=True)
        for item in source_dir.iterdir():
            if item.name == BACKUP_DIR:
                continue  # 别人的历史备份不必跟着复制
            if item.is_dir():
                continue
            (target_dir / item.name).write_bytes(item.read_bytes())
    except OSError as e:
        raise PersonaAdminError(f"复制失败：{e}") from e

    logger.info("人格已复制: %s → %s", name, safe_new)
    return safe_new


__all__ = [
    "BRIEF_FIELDS",
    "PERSONA_FILE",
    "PersonaAdminError",
    "backup_persona",
    "brief_from_payload",
    "build_gen_agent",
    "duplicate_persona",
    "extract_prompt_source",
    "generate_draft",
    "persona_dir",
    "refine_draft",
    "resolve_gen_agent_config",
    "save_persona",
    "validate_persona_name",
]
