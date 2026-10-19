"""角色生成 / 改写 / 落盘（webui/persona_admin.py）测试。

这套能力在原桌面端由两个对话框承担，迁移到 WebUI 时整个入口丢了
（生成器 ``PersonaGenAgent`` 一直是完整的，只是没人调）。这里锁住三条：

    1. **覆盖前一定备份。** 改写是 AI 做的，没备份等于把用户的角色交出去了。
    2. **草稿不落盘。** 用户可以反复重生成，不该在磁盘上留一堆半成品。
    3. **角色名不能带路径。** 它同时是目录名，``../`` 这类必须挡住。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from webui import persona_admin as pa

FULL_XML = """<persona>
  <identity><name>林晚</name><gender>female</gender></identity>
  <past>旧书店二楼长大</past>
  <personality>慢热，话少</personality>
  <voice>句尾常带「嗯」</voice>
  <boundaries>不主动打听隐私</boundaries>
  <relation_with_user>认识很久的朋友</relation_with_user>
  <consistency_anchors>不撒娇</consistency_anchors>
</persona>"""

FRAGMENT = "<personality>更冷，几乎不主动开口</personality>"


class FakeProvider:
    """按调用次数返回不同内容：第一次是完整档案，之后是顶层标签片段。

    片段复用是真实模型常见的行为——``refine()`` 会把它合并进当前文档。
    """

    name = "fake"

    def __init__(self, *, full: str = FULL_XML, fragment: str = FRAGMENT) -> None:
        self.full = full
        self.fragment = fragment
        self.calls: list[str] = []
        self.closed = 0

    async def chat_completion(self, messages, **kwargs):
        self.calls.append(messages[0]["content"])
        content = self.full if len(self.calls) == 1 else self.fragment
        return SimpleNamespace(content=content, reasoning_content="", usage=None)

    async def aclose(self) -> None:
        self.closed += 1


def _agent_cfg(**overrides):
    base = {
        "provider": "fake",
        "model": "m",
        "temperature": 0.8,
        "top_p": 1.0,
        "max_tokens": 8192,
        "reasoning": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _rt(persona_root, *, persona_gen=None, provider=None):
    provider = provider or FakeProvider()
    return SimpleNamespace(
        config=SimpleNamespace(
            agents=SimpleNamespace(persona_gen=persona_gen, chat=_agent_cfg()),
            providers={"fake": SimpleNamespace()},
        ),
        providers={"fake": provider},
        secrets=None,
        provider_registry=SimpleNamespace(presets={}),
        paths=persona_root,
    )


@pytest.fixture
def persona_root(tmp_path):
    """返回一个 AppPaths 替身——被测函数要的是带 PERSONAS_DIR / DATA_DIR 的对象。"""
    (tmp_path / "personas").mkdir()
    return SimpleNamespace(PERSONAS_DIR=tmp_path / "personas", DATA_DIR=tmp_path)


# ============================================================
# 名称校验
# ============================================================


@pytest.mark.parametrize("good", ["linwan", "LinWan", "a", "lin_wan-2", "x.y", "a" * 64])
def test_validate_name_accepts(good):
    assert pa.validate_persona_name(good) == good


@pytest.mark.parametrize(
    "bad",
    ["", "  ", "../escape", "a/b", "a\\b", ".hidden", "-lead", "_lead",
     "中文名", "a b", "a" * 65, "__pycache__"],
)
def test_validate_name_rejects(bad):
    """角色名同时是目录名——路径成分与非 ASCII 一律拒绝。"""
    with pytest.raises(pa.PersonaAdminError):
        pa.validate_persona_name(bad)


# ============================================================
# brief 转换
# ============================================================


def test_brief_from_payload_keeps_known_fields():
    brief = pa.brief_from_payload({
        "name": "林晚",
        "personality": "安静",
        "age": "24",
        "unknown_field": "忽略我",
    })
    assert brief.name == "林晚"
    assert brief.personality == "安静"
    assert brief.age == 24
    assert not hasattr(brief, "unknown_field")


def test_brief_from_payload_requires_name():
    with pytest.raises(pa.PersonaAdminError, match="角色名"):
        pa.brief_from_payload({"personality": "没有名字"})


def test_brief_from_payload_blank_age_is_none():
    assert pa.brief_from_payload({"name": "x", "age": ""}).age is None


# ============================================================
# 提取 PERSONA_PROMPT
# ============================================================


def test_extract_prompt_source_round_trips():
    """render_minimal_persona 写出来的文件必须能原样读回来——改写要以它为底稿。"""
    from webui.wizard.persona_render import render_minimal_persona

    source = render_minimal_persona("linwan", FULL_XML, admins=[{"qq": 123}])
    assert pa.extract_prompt_source(source).strip() == FULL_XML.strip()


def test_extract_prompt_source_handles_escaped_quotes():
    """提示词里若含三引号，渲染时会转义；提取不能因此断掉。"""
    from webui.wizard.persona_render import render_minimal_persona

    tricky = "说话爱用 ''' 引号"
    source = render_minimal_persona("x", tricky)
    assert "引号" in pa.extract_prompt_source(source)


def test_extract_prompt_source_falls_back_on_unparsable():
    """语法坏掉的文件不能抛异常——「修改角色」至少要能看到原文。"""
    broken = "PERSONA_PROMPT = '''内容'''\nthis is not python("
    assert "内容" in pa.extract_prompt_source(broken)


def test_extract_prompt_source_no_assignment():
    assert pa.extract_prompt_source("nothing here") == "nothing here"


# ============================================================
# 生成 / 改写（不落盘）
# ============================================================


async def test_generate_draft_does_not_touch_disk(persona_root):
    rt = _rt(persona_root)
    result = await pa.generate_draft(rt, {"name": "林晚", "personality": "安静"})
    assert result["ok"] is True
    assert "identity" in result["xml"]
    assert list((persona_root.PERSONAS_DIR).iterdir()) == [], "生成阶段不该写任何文件"


async def test_generate_draft_reuses_runtime_provider(persona_root):
    """Runtime 已经建好的 provider 优先复用（连接池/代理都是现成的），不该临时新建。"""
    provider = FakeProvider()
    rt = _rt(persona_root, provider=provider)
    await pa.generate_draft(rt, {"name": "x"})
    assert provider.closed == 0, "复用来的 provider 不该被关掉"


async def test_refine_merges_fragment_into_full_document(persona_root):
    """模型只回一个标签片段时，要合并进当前文档而不是丢掉别的字段。"""
    rt = _rt(persona_root)
    draft = await pa.generate_draft(rt, {"name": "林晚"})
    refined = await pa.refine_draft(rt, {"xml": draft["xml"], "feedback": "再冷淡一点"})
    assert "更冷" in refined["xml"]
    assert "旧书店" in refined["xml"], "没被改到的字段必须保留"
    assert refined["refined_count"] == 1


async def test_refine_increments_count_across_rounds(persona_root):
    rt = _rt(persona_root)
    draft = await pa.generate_draft(rt, {"name": "林晚"})
    first = await pa.refine_draft(rt, {"xml": draft["xml"], "feedback": "a"})
    second = await pa.refine_draft(rt, {
        "xml": first["xml"], "feedback": "b", "refined_count": first["refined_count"],
    })
    assert second["refined_count"] == 2


async def test_refine_requires_feedback(persona_root):
    rt = _rt(persona_root)
    with pytest.raises(pa.PersonaAdminError, match="想改哪里"):
        await pa.refine_draft(rt, {"xml": FULL_XML, "feedback": "  "})


async def test_refine_requires_draft(persona_root):
    rt = _rt(persona_root)
    with pytest.raises(pa.PersonaAdminError, match="底稿"):
        await pa.refine_draft(rt, {"xml": "", "feedback": "改"})


async def test_generate_reports_model_failure(persona_root):
    class Boom(FakeProvider):
        async def chat_completion(self, messages, **kwargs):
            raise RuntimeError("上游 502")

    rt = _rt(persona_root, provider=Boom())
    with pytest.raises(pa.PersonaAdminError, match="生成未能完成"):
        await pa.generate_draft(rt, {"name": "x"})


async def test_generate_rejects_empty_model_output(persona_root):
    class Silent(FakeProvider):
        async def chat_completion(self, messages, **kwargs):
            return SimpleNamespace(content="   ", reasoning_content="", usage=None)

    rt = _rt(persona_root, provider=Silent())
    with pytest.raises(pa.PersonaAdminError, match="没有返回内容"):
        await pa.generate_draft(rt, {"name": "x"})


def test_resolve_gen_agent_config_prefers_persona_gen():
    """配了 persona_gen 就用它，没配才跟随主聊天模型。"""
    persona_gen = _agent_cfg(provider="special")
    rt = SimpleNamespace(config=SimpleNamespace(
        agents=SimpleNamespace(persona_gen=persona_gen, chat=_agent_cfg()),
    ))
    assert pa.resolve_gen_agent_config(rt) is persona_gen

    rt.config.agents.persona_gen = None
    assert pa.resolve_gen_agent_config(rt) is rt.config.agents.chat


# ============================================================
# 落盘
# ============================================================


def test_save_creates_persona_file(persona_root):
    result = pa.save_persona(persona_root, "linwan", FULL_XML)
    assert result["created"] is True
    assert result["backup"] == ""
    written = (persona_root.PERSONAS_DIR / "linwan" / "persona_prompt.py").read_text("utf-8")
    assert "PERSONA_PROMPT" in written
    assert pa.extract_prompt_source(written).strip() == FULL_XML.strip()


def test_save_refuses_overwrite_without_flag(persona_root):
    pa.save_persona(persona_root, "linwan", FULL_XML)
    with pytest.raises(pa.PersonaAdminError, match="已存在"):
        pa.save_persona(persona_root, "linwan", FULL_XML)


def test_save_overwrite_backs_up_first(persona_root):
    """覆盖前必须留下旧版——这是用户唯一的后悔药。"""
    pa.save_persona(persona_root, "linwan", FULL_XML)
    result = pa.save_persona(persona_root, "linwan", FULL_XML.replace("慢热", "更冷"),
                             overwrite=True)
    assert result["backup"], "覆盖时必须产生备份"
    backup = persona_root.PERSONAS_DIR / "linwan" / ".backups"
    files = list(backup.iterdir())
    assert len(files) == 1
    assert "慢热" in files[0].read_text("utf-8"), "备份应是覆盖前的内容"


def test_backup_names_do_not_collide_within_same_second(persona_root):
    """同一秒内连续覆盖两次不能互相盖掉备份。"""
    pa.save_persona(persona_root, "linwan", FULL_XML)
    pa.save_persona(persona_root, "linwan", FULL_XML.replace("慢热", "版本二"), overwrite=True)
    pa.save_persona(persona_root, "linwan", FULL_XML.replace("慢热", "版本三"), overwrite=True)
    backups = list((persona_root.PERSONAS_DIR / "linwan" / ".backups").iterdir())
    assert len(backups) == 2
    assert len({b.name for b in backups}) == 2


def test_save_rejects_empty_content(persona_root):
    with pytest.raises(pa.PersonaAdminError, match="为空"):
        pa.save_persona(persona_root, "linwan", "   ")


def test_save_rejects_bad_name(persona_root):
    with pytest.raises(pa.PersonaAdminError):
        pa.save_persona(persona_root, "../escape", FULL_XML)
    assert not (persona_root.DATA_DIR.parent / "escape").exists()


def test_save_writes_admin_entries_from_brief(persona_root):
    brief = pa.brief_from_payload({"name": "林晚", "admin_qq": "12345", "admin_name": "老王"})
    pa.save_persona(persona_root, "linwan", FULL_XML, brief=brief)
    written = (persona_root.PERSONAS_DIR / "linwan" / "persona_prompt.py").read_text("utf-8")
    assert "12345" in written
    assert "老王" in written


def test_backup_persona_returns_none_when_absent(persona_root):
    assert pa.backup_persona(persona_root, "nobody") is None


# ============================================================
# 复制
# ============================================================


def test_duplicate_copies_files(persona_root):
    pa.save_persona(persona_root, "linwan", FULL_XML)
    created = pa.duplicate_persona(persona_root, "linwan", "linwan_copy")
    assert created == "linwan_copy"
    copied = (persona_root.PERSONAS_DIR / "linwan_copy" / "persona_prompt.py")
    assert copied.is_file()
    assert pa.extract_prompt_source(copied.read_text("utf-8")).strip() == FULL_XML.strip()


def test_duplicate_skips_backups(persona_root):
    """别人的历史备份不该跟着复制——那是它自己的旧版本，与新角色无关。"""
    pa.save_persona(persona_root, "linwan", FULL_XML)
    pa.save_persona(persona_root, "linwan", FULL_XML, overwrite=True)
    pa.duplicate_persona(persona_root, "linwan", "linwan_copy")
    assert not (persona_root.PERSONAS_DIR / "linwan_copy" / ".backups").exists()


def test_duplicate_rejects_existing_target(persona_root):
    pa.save_persona(persona_root, "linwan", FULL_XML)
    with pytest.raises(pa.PersonaAdminError, match="已存在"):
        pa.duplicate_persona(persona_root, "linwan", "linwan")


def test_duplicate_missing_source(persona_root):
    with pytest.raises(pa.PersonaAdminError, match="没有"):
        pa.duplicate_persona(persona_root, "nobody", "x")
