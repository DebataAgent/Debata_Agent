"""设置页声明表（webui/settings_spec.py）与配置 schema 的一致性。

设置页是**声明式**的：``SETTINGS_GROUPS`` 里每一条声明都会被前端渲染成一个控件，
后端 ``PATCH /api/settings`` 也用同一张表校验。好处是不用写 8 个表单，
代价是**表和 schema 可能悄悄漂移**——表里有、schema 里没有的路径会在用户点保存时
才炸出 400；枚举选项写错了则更隐蔽：下拉能选，但一保存就失败。

这组用例就是盯住这两件事。历史上确实漂过一次（能量/饱食度模式的选项写成了
schema 里根本不存在的 off/auto）。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app_config.schema import RootConfig
from webui.api.deps import get_path_in, set_path_in
from webui.settings_spec import SETTINGS_GROUPS, field_map, iter_fields


def _default_config() -> RootConfig:
    return RootConfig.model_validate(
        {
            "persona": {"name": "test_bot"},
            "adapters": {"default": {"type": "napcat"}},
            "providers": {
                "deepseek_main": {"preset": "deepseek", "api_key_id": "k"},
            },
            "agents": {"chat": {"provider": "deepseek_main", "model": "deepseek-v4-flash"}},
        }
    )


def _paths() -> list[str]:
    return [field["path"] for _group, field in iter_fields()]


# ============================================================
# 路径存在性
# ============================================================


def test_field_paths_are_unique():
    paths = _paths()
    duplicates = sorted({p for p in paths if paths.count(p) > 1})
    assert not duplicates, f"声明表里有重复路径：{duplicates}"


MISSING = "missing"
UNKNOWN = "unknown"


def _resolve(config: RootConfig, path: str):
    """沿配置路径走一遍，返回 ``(容器, 最后一段字段名)``。

    不能用 get_path_in：它把 None 当作"取不到"，会把所有可空字段误报成缺失。
    遇到 dict（如 ``adapters`` 是 ``dict[str, NapCatAdapterConfig]``）时，
    路径里对应的那一段是**数据键**而不是模型字段，随便什么名字都算合法。

    返回 ``MISSING`` 表示 schema 里确实没这个字段；
    ``UNKNOWN`` 表示走到空 dict 无法继续判断（调用方应放行）。
    """
    parts = path.split(".")
    cur: object = config
    for part in parts[:-1]:
        fields = getattr(type(cur), "model_fields", None)
        if fields is not None:
            if part not in fields:
                return MISSING
            cur = getattr(cur, part)
        elif isinstance(cur, dict):
            if not cur:
                return UNKNOWN
            cur = next(iter(cur.values()))
        else:
            return MISSING
    fields = getattr(type(cur), "model_fields", None)
    if fields is None or parts[-1] not in fields:
        return MISSING
    return cur, parts[-1]


def _schema_has_path(config: RootConfig, path: str) -> bool:
    """路径在 schema 里是否**存在**（结构化判断，不看值是否为 None）。"""
    return _resolve(config, path) != MISSING


def test_every_declared_path_exists_in_schema():
    """表里的每条路径都必须真实存在于 schema，否则前端会渲染出一个死控件。"""
    cfg = _default_config()
    missing = [p for p in _paths() if not _schema_has_path(cfg, p)]
    assert not missing, f"声明表里有 schema 中不存在的路径：{missing}"


def test_every_declared_path_is_writable():
    """能读还不够——必须也能写回，否则保存时才报错。"""
    cfg = _default_config()
    unwritable = []
    for path in _paths():
        current = get_path_in(cfg, path, None)
        try:
            set_path_in(cfg, path, current)
        except Exception as e:  # noqa: BLE001
            unwritable.append(f"{path}: {e}")
    assert not unwritable, "这些路径写不回去：\n" + "\n".join(unwritable)


# ============================================================
# 枚举选项
# ============================================================


def _enum_options_of(config: RootConfig, path: str) -> set[str] | None:
    """从 schema 里取出某个 Literal 字段的允许取值；不是 Literal 则返回 None。"""
    import typing

    resolved = _resolve(config, path)
    if resolved in (MISSING, UNKNOWN):
        return None
    container, name = resolved
    field = type(container).model_fields[name]
    args = typing.get_args(field.annotation)
    if not args or not all(isinstance(a, str) for a in args):
        return None
    return set(args)


def test_enum_options_match_schema_literals():
    """enum 字段的候选值必须与 schema 的 Literal 完全一致。

    多一个 ⇒ 用户能选中一个存不进去的值；少一个 ⇒ 现有配置在界面上显示不出来。
    """
    cfg = _default_config()
    problems = []
    for _group, spec in iter_fields():
        if spec.get("type") != "enum":
            continue
        allowed = _enum_options_of(cfg, spec["path"])
        if allowed is None:
            continue  # 不是 Literal，跳过
        declared = {opt[0] for opt in spec.get("options", [])}
        extra = declared - allowed
        missing = allowed - declared
        if extra:
            problems.append(f"{spec['path']} 声明了 schema 不接受的值：{sorted(extra)}")
        if missing:
            problems.append(f"{spec['path']} 漏了 schema 支持的值：{sorted(missing)}")
    assert not problems, "\n".join(problems)


def test_enum_default_is_a_valid_option():
    for _group, spec in iter_fields():
        if spec.get("type") != "enum":
            continue
        declared = [opt[0] for opt in spec.get("options", [])]
        assert spec.get("default") in declared, (
            f"{spec['path']} 的默认值 {spec.get('default')!r} 不在候选里 {declared}"
        )


# ============================================================
# 写回环：把每条声明的默认值写进配置，整体校验必须通过
# ============================================================


def test_writing_defaults_keeps_config_valid():
    """逐条把默认值写回并整体校验——这是用户"点一次保存"的最坏情况。"""
    failures = []
    for path in _paths():
        trial = _default_config()
        spec = field_map()[path]
        try:
            set_path_in(trial, path, spec.get("default"))
            RootConfig.model_validate(trial.model_dump())
        except (ValidationError, ValueError, KeyError) as e:
            failures.append(f"{path} = {spec.get('default')!r} → {e}")
    assert not failures, "这些字段写入后配置不合法：\n" + "\n".join(failures)


def test_writing_declared_enum_options_keeps_config_valid():
    """枚举的每个候选值都要能存进去（用户会把下拉逐个试一遍）。"""
    failures = []
    for _group, spec in iter_fields():
        if spec.get("type") != "enum":
            continue
        for value, _label in spec.get("options", []):
            trial = _default_config()
            try:
                set_path_in(trial, spec["path"], value)
                RootConfig.model_validate(trial.model_dump())
            except (ValidationError, ValueError, KeyError) as e:
                failures.append(f"{spec['path']} = {value!r} → {e}")
    assert not failures, "这些枚举取值存不进去：\n" + "\n".join(failures)


# ============================================================
# 声明本身的形状
# ============================================================


def test_needs_restart_is_boolean():
    for _group, spec in iter_fields():
        assert isinstance(spec.get("needs_restart"), bool), (
            f"{spec['path']} 的 needs_restart 必须是布尔值"
        )


def test_every_field_has_label_and_type():
    known_types = {
        "bool", "int", "int_or_none", "float", "str", "enum",
        "secret_ref", "list_int", "list_str", "json",
    }
    for _group, spec in iter_fields():
        assert spec.get("label"), f"{spec['path']} 缺 label"
        assert spec.get("type") in known_types, (
            f"{spec['path']} 的类型 {spec.get('type')!r} 前端不认识"
        )


def test_groups_have_key_and_title():
    keys = [g["key"] for g in SETTINGS_GROUPS]
    assert len(keys) == len(set(keys)), f"分组 key 重复：{keys}"
    for group in SETTINGS_GROUPS:
        assert group.get("key"), "分组缺 key"
        assert group.get("title"), f"{group['key']} 缺 title"


def test_json_fields_default_is_serialisable():
    """json 类型字段的默认值要能序列化，否则前端 textarea 会显示 [object Object]。"""
    for _group, spec in iter_fields():
        if spec.get("type") != "json":
            continue
        json.dumps(spec.get("default"), ensure_ascii=False)


@pytest.mark.parametrize("group_key", [g["key"] for g in SETTINGS_GROUPS])
def test_group_fields_is_list(group_key):
    group = next(g for g in SETTINGS_GROUPS if g["key"] == group_key)
    assert isinstance(group["fields"], list)


# ============================================================
# 新字段类型的取值转换
# ============================================================


def test_coerce_list_str_splits_on_comma_and_newline():
    from webui.api.settings import _coerce

    spec = {"type": "list_str"}
    assert _coerce("a, b\nc", spec) == ["a", "b", "c"]
    assert _coerce("a，b", spec) == ["a", "b"]   # 全角逗号也要认
    assert _coerce("", spec) == []
    assert _coerce(None, spec) == []
    assert _coerce(["x", "y"], spec) == ["x", "y"]


def test_coerce_json_parses_string():
    from webui.api.settings import _coerce

    spec = {"type": "json"}
    assert _coerce('{"a": 1}', spec) == {"a": 1}
    assert _coerce('[1, 2]', spec) == [1, 2]
    assert _coerce("", spec) is None
    # 已经是结构（前端直接发对象）时原样透传
    assert _coerce({"a": 1}, spec) == {"a": 1}


def test_coerce_json_reports_line_number_on_bad_input():
    """JSON 写错时要指出行号，否则用户对着长文本找不着北。"""
    from fastapi import HTTPException

    from webui.api.settings import _coerce

    with pytest.raises(HTTPException) as excinfo:
        _coerce('{\n  "a": 1,\n  "b":\n}', {"type": "json"})
    assert excinfo.value.status_code == 400
    assert "JSON" in str(excinfo.value.detail)


def test_coerce_json_fields_accept_their_own_defaults():
    """声明表里 json 字段的默认值要能被自己的 _coerce 吃下去。"""
    from webui.api.settings import _coerce

    for _group, spec in iter_fields():
        if spec.get("type") != "json":
            continue
        value = _coerce(spec.get("default"), spec)
        assert value == spec.get("default"), f"{spec['path']} 的默认值过不了 _coerce"


# ============================================================
# 端到端：真的走一遍 PATCH /api/settings
# ============================================================


class _FakeSecrets:
    def list_ids(self) -> list[str]:
        return []

    def get(self, key_id: str):
        return None


class _FakeHost:
    def __init__(self, rt) -> None:
        self.rt = rt
        self.state = "running"
        self.last_error = None

    def save_config(self) -> None:
        pass


class _FakeRt:
    def __init__(self, config: RootConfig) -> None:
        self.config = config
        self.secrets = _FakeSecrets()
        self.paths = None


def _patch_request(rt):
    from types import SimpleNamespace

    host = _FakeHost(rt)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(host=host)))


@pytest.mark.parametrize("path", _paths())
async def test_patch_endpoint_accepts_each_declared_path(path):
    """每个声明路径都要能通过 PATCH /api/settings 真正存下去。

    这是用户操作的完整路径：前端渲染控件 → 用户改值 → PATCH → 落盘。
    上面几个用例测的是表本身，这条测的是"接线"也通。
    """
    from webui.api.settings import SettingPatch, patch_setting

    rt = _FakeRt(_default_config())
    spec = field_map()[path]
    result = await patch_setting(_patch_request(rt), SettingPatch(path=path, value=spec["default"]))
    assert result["ok"] is True
    assert result["path"] == path
    assert isinstance(result["needs_restart"], bool)


async def test_patch_endpoint_rejects_undeclared_path():
    """表里没有的路径必须拒绝——否则前端能改到任何配置字段。"""
    from fastapi import HTTPException

    from webui.api.settings import SettingPatch, patch_setting

    rt = _FakeRt(_default_config())
    with pytest.raises(HTTPException) as excinfo:
        await patch_setting(
            _patch_request(rt), SettingPatch(path="behavior.definitely_not_a_field", value=1)
        )
    assert excinfo.value.status_code == 400
    assert "不认识" in str(excinfo.value.detail)


def _config_with_vision() -> RootConfig:
    cfg = _default_config()
    # 直接赋值绕过校验：这里要的就是一份「vision 开着且指向真实 provider」的合法配置
    cfg.features.vision.enabled = True
    cfg.features.vision.provider = "deepseek_main"
    return cfg


async def test_rejected_patch_leaves_memory_untouched():
    """被拒绝的 PATCH 不能把内存配置改坏（真实事故的回归测试）。

    旧实现是「先改 rt.config 再校验」：清空 features.vision.provider 会被 schema
    拒绝、接口也回了 400，但内存里的值已经变成 ''。之后「模型」页换 Agent 的
    provider 时一次不校验的落盘，就把这份配置写进了 config.yaml，
    Runtime 下次启动直接起不来（features → vision 校验失败）。
    """
    from fastapi import HTTPException

    from webui.api.settings import SettingPatch, patch_setting

    rt = _FakeRt(_config_with_vision())
    with pytest.raises(HTTPException) as excinfo:
        await patch_setting(
            _patch_request(rt),
            SettingPatch(path="features.vision.provider", value=""),
        )
    assert excinfo.value.status_code == 400
    assert "vision" in str(excinfo.value.detail)
    # 关键：内存里的值必须一个都没变，否则下一次落盘就把坏配置写进磁盘
    assert rt.config.features.vision.provider == "deepseek_main"
    assert rt.config.features.vision.enabled is True


async def test_valid_patch_is_committed():
    """合法改动要真的落进 rt.config（关掉 vision 后再清 provider 是允许的顺序）。"""
    from webui.api.settings import SettingPatch, patch_setting

    rt = _FakeRt(_config_with_vision())
    request = _patch_request(rt)
    await patch_setting(request, SettingPatch(path="features.vision.enabled", value=False))
    await patch_setting(request, SettingPatch(path="features.vision.provider", value=""))
    assert rt.config.features.vision.enabled is False
    assert rt.config.features.vision.provider == ""
