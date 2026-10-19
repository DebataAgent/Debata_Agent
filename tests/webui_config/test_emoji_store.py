"""表情包文件管理测试。

这个模块直接面对 HTTP 传来的文件名，所以路径穿越 / 符号链接 / 伪装扩展名
是重点用例。全部测纯函数，不经过 FastAPI。
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from webui import emoji_store
from webui.emoji_store import EmojiError

# 最小合法图片字节，够过 magic 校验
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32


@pytest.fixture
def emoji_dir(tmp_path: Path) -> Path:
    d = tmp_path / "emoji"
    d.mkdir()
    return d


def _write(directory: Path, name: str, data: bytes = PNG) -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


# ============================================================
# 扩展名口径：与发送链路同源
# ============================================================


def test_supported_exts_is_same_object_as_send_chain():
    """管理端白名单必须与 AI 实际能发送的集合恒等。

    两边一旦漂移，用户就会遇到"上传成功但角色从不使用它"。
    """
    from tools.message_builder import SUPPORTED_IMAGE_EXTS

    assert emoji_store.SUPPORTED_IMAGE_EXTS is SUPPORTED_IMAGE_EXTS
    assert emoji_store.SUPPORTED_IMAGE_EXTS == frozenset(
        {".jpg", ".jpeg", ".png", ".gif"}
    )


# ============================================================
# 名称校验
# ============================================================


@pytest.mark.parametrize(
    "bad",
    ["", "   ", ".", "..", "a/b", "a\\b", "..\\etc", "x\x00y", ".hidden", "a" * 65,
     "CON", "con", "NUL", "COM1", "lpt9"],
)
def test_validate_stem_rejects(bad):
    with pytest.raises(EmojiError):
        emoji_store.validate_stem(bad)


@pytest.mark.parametrize("good", ["无语", "cat", "猫猫_01", "a b", "表情-包", "x" * 64])
def test_validate_stem_accepts(good):
    assert emoji_store.validate_stem(good) == good


def test_validate_stem_normalizes_nfc():
    """macOS 上传的文件名常是 NFD，不归一化会与已有文件"看起来同名却不同"。

    用带变音符号的名字做样本——纯汉字没有可分解形式，NFD 与 NFC 相同，
    拿它当样本证明不了任何事。
    """
    nfd = unicodedata.normalize("NFD", "café猫")
    nfc = unicodedata.normalize("NFC", "café猫")
    assert nfd != nfc, "样本必须真的会分解，否则这条测试是空的"
    assert emoji_store.validate_stem(nfd) == nfc
    assert len(nfd) > len(nfc)


def test_validate_stem_strips_whitespace():
    assert emoji_store.validate_stem("  猫  ") == "猫"


# ============================================================
# 路径穿越：resolve_file
# ============================================================


@pytest.mark.parametrize(
    "attack",
    [
        "../etc/passwd.png",
        "../../secret.png",
        "a/b.png",
        "..\\..\\windows\\system32\\x.png",
        "/etc/passwd.png",
        "sub/../../out.png",
        ".",
        "..",
        "",
    ],
)
def test_resolve_file_rejects_traversal(emoji_dir, attack):
    with pytest.raises(EmojiError):
        emoji_store.resolve_file(emoji_dir, attack)


def test_resolve_file_rejects_absolute_path_outside(emoji_dir, tmp_path):
    outside = _write(tmp_path, "outside.png")
    with pytest.raises(EmojiError):
        emoji_store.resolve_file(emoji_dir, str(outside))


def test_resolve_file_rejects_symlink(emoji_dir, tmp_path):
    """指向目录外的软链接必须被挡住，否则能读任意文件。"""
    secret = _write(tmp_path, "secret.png")
    link = emoji_dir / "link.png"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")
    with pytest.raises(EmojiError, match="符号链接"):
        emoji_store.resolve_file(emoji_dir, "link.png")


def test_resolve_file_rejects_non_image_extension(emoji_dir):
    _write(emoji_dir, "evil.sh", b"#!/bin/sh\nrm -rf /\n")
    with pytest.raises(EmojiError, match="不支持的图片格式"):
        emoji_store.resolve_file(emoji_dir, "evil.sh")


def test_resolve_file_rejects_disguised_extension(emoji_dir):
    """把脚本改名成 .png 混进来 —— 后缀合法但 magic 不对。"""
    _write(emoji_dir, "evil.png", b"#!/bin/sh\nrm -rf /\n")
    with pytest.raises(EmojiError, match="不是图片"):
        emoji_store.resolve_file(emoji_dir, "evil.png")


def test_resolve_file_rejects_mismatched_magic(emoji_dir):
    """内容是 GIF 但后缀写 .png，也要挡住（某些解析器会按后缀走）。"""
    _write(emoji_dir, "fake.png", GIF)
    with pytest.raises(EmojiError, match="不符"):
        emoji_store.resolve_file(emoji_dir, "fake.png")


def test_resolve_file_accepts_real_image(emoji_dir):
    _write(emoji_dir, "无语.png")
    assert emoji_store.resolve_file(emoji_dir, "无语.png") == emoji_dir / "无语.png"


def test_resolve_file_missing_file(emoji_dir):
    with pytest.raises(EmojiError, match="文件不存在"):
        emoji_store.resolve_file(emoji_dir, "nope.png")


# ============================================================
# 内容嗅探
# ============================================================


@pytest.mark.parametrize(
    "data,mime",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif")],
)
def test_sniff_image(data, mime):
    assert emoji_store.sniff_image(data) == mime


def test_sniff_image_unknown():
    assert emoji_store.sniff_image(b"not an image at all") is None
    assert emoji_store.sniff_image(b"") is None


# ============================================================
# 列表
# ============================================================


def test_list_items_empty_dir(emoji_dir):
    assert emoji_store.list_items(emoji_dir) == []


def test_list_items_missing_dir(tmp_path):
    """首次启动时目录还没建，不能报错。"""
    assert emoji_store.list_items(tmp_path / "nope") == []


def test_list_items_filters_and_sorts(emoji_dir):
    _write(emoji_dir, "b.png")
    _write(emoji_dir, "a.gif", GIF)
    _write(emoji_dir, "notes.txt", b"hello")
    _write(emoji_dir, "noext", b"x")
    items = emoji_store.list_items(emoji_dir)
    assert [x["name"] for x in items] == ["a", "b"]
    assert items[0]["ext"] == ".gif"
    assert items[0]["size"] > 0


def test_list_items_skips_symlinks(emoji_dir, tmp_path):
    outside = _write(tmp_path, "outside.png")
    link = emoji_dir / "link.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")
    assert emoji_store.list_items(emoji_dir) == []


def test_list_names_dedupes_case_insensitively(emoji_dir):
    _write(emoji_dir, "Cat.png")
    _write(emoji_dir, "cat.gif", GIF)
    assert emoji_store.list_names(emoji_dir) == ["Cat"]


# ============================================================
# 重名去重
# ============================================================


def test_unique_target_no_conflict(emoji_dir):
    assert emoji_store.unique_target(emoji_dir, "新图", ".png") == emoji_dir / "新图.png"


def test_unique_target_appends_suffix(emoji_dir):
    _write(emoji_dir, "图.png")
    assert emoji_store.unique_target(emoji_dir, "图", ".png") == emoji_dir / "图_1.png"
    _write(emoji_dir, "图_1.png")
    assert emoji_store.unique_target(emoji_dir, "图", ".png") == emoji_dir / "图_2.png"


def test_unique_target_is_case_insensitive(emoji_dir):
    """Cat.png 已存在时，再传 cat.png 也要让路（Windows/macOS 上它们是同一个文件）。"""
    _write(emoji_dir, "Cat.png")
    assert emoji_store.unique_target(emoji_dir, "cat", ".png") == emoji_dir / "cat_1.png"


# ============================================================
# 保存
# ============================================================


def test_save_one_writes_file(emoji_dir):
    result = emoji_store.save_one(emoji_dir, "cat.png", PNG)
    assert result["name"] == "cat"
    assert result["deduped"] is False
    assert (emoji_dir / "cat.png").read_bytes() == PNG


def test_save_one_renames_on_conflict(emoji_dir):
    _write(emoji_dir, "cat.png")
    result = emoji_store.save_one(emoji_dir, "cat.png", PNG)
    assert result["name"] == "cat_1"
    assert result["deduped"] is True


def test_save_one_honours_user_supplied_name(emoji_dir):
    result = emoji_store.save_one(emoji_dir, "IMG_20260101.png", PNG, stem="无语")
    assert result["name"] == "无语"
    assert (emoji_dir / "无语.png").is_file()


def test_save_one_rejects_oversized(emoji_dir, monkeypatch):
    monkeypatch.setattr(emoji_store, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(EmojiError, match="上限"):
        emoji_store.save_one(emoji_dir, "big.png", PNG)


def test_save_one_rejects_empty(emoji_dir):
    with pytest.raises(EmojiError, match="为空"):
        emoji_store.save_one(emoji_dir, "empty.png", b"")


def test_save_one_rejects_disguised_content(emoji_dir):
    with pytest.raises(EmojiError, match="不是图片"):
        emoji_store.save_one(emoji_dir, "evil.png", b"#!/bin/sh\n")


def test_save_one_creates_dir_if_missing(tmp_path):
    target = tmp_path / "fresh"
    emoji_store.save_one(target, "cat.png", PNG)
    assert (target / "cat.png").is_file()


# ============================================================
# 批量上传
# ============================================================


def test_add_uploads_partial_failure(emoji_dir):
    """单个文件不合格只记 skipped，不影响同批其它文件。"""
    result = emoji_store.add_uploads(
        emoji_dir,
        [("ok.png", PNG), ("bad.txt", b"hello"), ("ok2.gif", GIF)],
    )
    assert {x["name"] for x in result["added"]} == {"ok", "ok2"}
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["file"] == "bad.txt"


def test_add_uploads_respects_rename_map(emoji_dir):
    result = emoji_store.add_uploads(
        emoji_dir,
        [("IMG_001.png", PNG)],
        rename={"IMG_001.png": "猫猫"},
    )
    assert result["added"][0]["name"] == "猫猫"


def test_add_uploads_enforces_file_count_cap(emoji_dir, monkeypatch):
    monkeypatch.setattr(emoji_store, "MAX_FILES_PER_REQUEST", 2)
    result = emoji_store.add_uploads(
        emoji_dir, [(f"f{i}.png", PNG) for i in range(5)]
    )
    assert len(result["added"]) == 2
    assert len(result["skipped"]) == 3
    assert "最多" in result["skipped"][0]["reason"]


# ============================================================
# 改名 / 删除
# ============================================================


def test_rename_keeps_extension(emoji_dir):
    _write(emoji_dir, "旧名.gif", GIF)
    result = emoji_store.rename(emoji_dir, "旧名", "新名")
    assert result == {"from": "旧名", "to": "新名"}
    assert (emoji_dir / "新名.gif").is_file()
    assert not (emoji_dir / "旧名.gif").exists()


def test_rename_rejects_existing_target(emoji_dir):
    _write(emoji_dir, "a.png")
    _write(emoji_dir, "b.png")
    with pytest.raises(EmojiError, match="已存在"):
        emoji_store.rename(emoji_dir, "a", "b")


def test_rename_same_name_is_noop(emoji_dir):
    _write(emoji_dir, "a.png")
    assert emoji_store.rename(emoji_dir, "a", "a") == {"from": "a", "to": "a"}


def test_rename_validates_new_name(emoji_dir):
    _write(emoji_dir, "a.png")
    with pytest.raises(EmojiError):
        emoji_store.rename(emoji_dir, "a", "../evil")


def test_rename_missing_source(emoji_dir):
    with pytest.raises(EmojiError, match="不存在"):
        emoji_store.rename(emoji_dir, "nope", "x")


def test_delete_removes_file(emoji_dir):
    _write(emoji_dir, "猫.png")
    assert emoji_store.delete(emoji_dir, "猫") == "猫.png"
    assert not (emoji_dir / "猫.png").exists()


def test_delete_missing(emoji_dir):
    with pytest.raises(EmojiError, match="不存在"):
        emoji_store.delete(emoji_dir, "nope")


def test_find_by_stem_rejects_ambiguous(emoji_dir):
    """同一名称有 png 和 gif 两个文件时，发送链路也会报错，管理端要给出同样的提示。"""
    _write(emoji_dir, "图.png")
    _write(emoji_dir, "图.gif", GIF)
    with pytest.raises(EmojiError, match="多个同名文件"):
        emoji_store.find_by_stem(emoji_dir, "图")


def test_find_by_stem_rejects_traversal(emoji_dir):
    _write(emoji_dir, "图.png")
    with pytest.raises(EmojiError):
        emoji_store.find_by_stem(emoji_dir, "../../etc/passwd")
