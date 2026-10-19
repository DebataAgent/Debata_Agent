"""表情包文件管理（纯函数，不依赖 FastAPI）。

原 GUI 的表情包管理面板（``ui/dashboard/emoji_section.py``）直接在 UI 进程里对
``EMOJI_DIR`` 做 ``iterdir`` / ``shutil.copy2`` / ``rename`` / ``unlink``。WebUI 是
HTTP 模式，文件名来自网络，所以同样的操作必须先把好关：

    1. 只接受文件名，不接受路径 —— ``Path(name).name == name``
    2. 拒绝符号链接（否则一个指向 /etc 的软链接就能读出任意文件）
    3. ``resolve(strict=True)`` 后必须仍在 EMOJI_DIR 内
    4. 后缀在白名单内，且文件头 magic 与后缀一致（防止把 .sh 改名成 .png）

扩展名以 ``tools/message_builder.py`` 的 ``SUPPORTED_IMAGE_EXTS`` 为**唯一真源**：
那里是 AI 实际能发送的集合。管理端若允许上传 AI 发不出的格式，用户会看到
"上传成功但角色不会用它"的矛盾。原 GUI 白名单多出 webp/bmp，属于 GUI 侧的不一致。

所有写操作由调用方（``webui/api/emoji.py``）用锁串行化，这里只保证单次操作原子。
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from tools.message_builder import SUPPORTED_IMAGE_EXTS

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
"""单文件大小上限。表情包是聊天里发的小图，5MB 已经很宽松。"""

MAX_FILES_PER_REQUEST = 50
"""单次上传文件数上限，防止一个请求塞满磁盘。"""

MAX_STEM_LENGTH = 64
"""名称长度上限。太长会被 QQ 客户端截断，也会让缩略图标题换行。"""

# Windows 保留设备名。这些名字在 Windows 上无法创建为普通文件，
# 而 CI 矩阵里有 windows-latest，必须在这里挡住而不是等 OSError。
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

# 文件头 magic → MIME。GIF 有两个版本标识，都要认。
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# 后缀 → 可接受的 MIME。.jpg 与 .jpeg 都对应 JPEG。
_EXT_MIME: dict[str, frozenset[str]] = {
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".gif": frozenset({"image/gif"}),
}

MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}


class EmojiError(Exception):
    """表情包管理操作失败。消息直接展示给用户，所以要写人话。"""


# ============================================================
# 校验
# ============================================================


def validate_stem(stem: str) -> str:
    """校验并归一化一个表情包名称（不含扩展名）。失败抛 EmojiError。"""
    if stem is None:
        raise EmojiError("名称不能为空")

    # 中文名要用 NFC：macOS 上传的文件名常是 NFD，不归一化会与已有文件"看起来同名却不同"
    name = unicodedata.normalize("NFC", str(stem)).strip()

    if not name:
        raise EmojiError("名称不能为空")
    if len(name) > MAX_STEM_LENGTH:
        raise EmojiError(f"名称太长（最多 {MAX_STEM_LENGTH} 个字符）")
    if any(ch in name for ch in ("/", "\\", "\0")):
        raise EmojiError("名称不能包含路径分隔符")
    if name in (".", "..") or ".." in name:
        raise EmojiError("名称不能包含 ..")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise EmojiError("名称不能包含控制字符")
    if name.startswith("."):
        raise EmojiError("名称不能以点开头")
    if name.upper() in _WINDOWS_RESERVED:
        raise EmojiError(f"{name} 是系统保留名，换一个")
    return name


def sniff_image(head: bytes) -> str | None:
    """按文件头判断图片类型，认不出返回 None。"""
    for signature, mime in _MAGIC_SIGNATURES:
        if head.startswith(signature):
            return mime
    return None


def _check_content(ext: str, head: bytes) -> None:
    """后缀与文件头必须一致，避免把可执行脚本改名成 .png 混进表情包目录。"""
    mime = sniff_image(head)
    if mime is None:
        raise EmojiError("这不是图片文件（或格式不受支持）")
    allowed = _EXT_MIME.get(ext, frozenset())
    if mime not in allowed:
        raise EmojiError(f"文件内容与后缀 {ext} 不符")


# ============================================================
# 路径解析
# ============================================================


def resolve_file(emoji_dir: Path, filename: str) -> Path:
    """把 HTTP 传来的文件名解析成表情包目录下的真实文件。

    四道防线，任一不过就抛 EmojiError。
    """
    if not filename:
        raise EmojiError("文件名不能为空")
    if filename in (".", ".."):
        raise EmojiError("文件名不合法")
    # ① 只接受纯文件名：任何目录成分都在这里被拒
    if Path(filename).name != filename:
        raise EmojiError("文件名不能包含路径")
    if "/" in filename or "\\" in filename or "\0" in filename:
        raise EmojiError("文件名不能包含路径分隔符")

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_IMAGE_EXTS:
        raise EmojiError(f"不支持的图片格式：{ext or '（无后缀）'}")

    candidate = emoji_dir / filename
    # ② 符号链接一律拒绝：一个指向目录外的软链接就能绕过下面的 resolve 检查
    if candidate.is_symlink():
        raise EmojiError("不处理符号链接")
    if not candidate.is_file():
        raise EmojiError("文件不存在")

    # ③ realpath 后必须仍在表情包目录内
    try:
        resolved = candidate.resolve(strict=True)
        root = emoji_dir.resolve(strict=True)
    except OSError as e:
        raise EmojiError(f"读取失败：{e}") from e
    if not resolved.is_relative_to(root):
        raise EmojiError("文件不在表情包目录内")

    # ④ 内容确实是图片
    try:
        with open(resolved, "rb") as f:
            head = f.read(16)
    except OSError as e:
        raise EmojiError(f"读取失败：{e}") from e
    _check_content(ext, head)
    return resolved


def find_by_stem(emoji_dir: Path, stem: str) -> Path:
    """按不含后缀的名称找文件（与 AI 发送时 `resolve_emoji_path` 的口径一致）。"""
    name = validate_stem(stem)
    if not emoji_dir.exists():
        raise EmojiError("表情包目录不存在")
    matches = sorted(
        p
        for p in emoji_dir.iterdir()
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in SUPPORTED_IMAGE_EXTS
        and unicodedata.normalize("NFC", p.stem) == name
    )
    if not matches:
        raise EmojiError(f"表情包不存在：{name}")
    if len(matches) > 1:
        raise EmojiError(
            f"有多个同名文件（{ '、'.join(p.name for p in matches) }），请先删掉多余的"
        )
    return matches[0]


# ============================================================
# 读取
# ============================================================


def list_items(emoji_dir: Path) -> list[dict[str, Any]]:
    """列出目录下的表情包。目录不存在时返回空列表（首次启动的正常状态）。"""
    if not emoji_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in emoji_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "name": path.stem,
                "file": path.name,
                "ext": path.suffix.lower(),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    items.sort(key=lambda x: x["name"])
    return items


def list_names(emoji_dir: Path) -> list[str]:
    """只要名称列表，给聊天页内联渲染用（轻量）。"""
    seen: set[str] = set()
    names: list[str] = []
    for item in list_items(emoji_dir):
        key = item["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(item["name"])
    return names


# ============================================================
# 写入
# ============================================================


def unique_target(emoji_dir: Path, stem: str, ext: str) -> Path:
    """重名时按原 GUI 规则追加 _1、_2……（casefold 比较，避免只在大小写上不同）。"""
    existing = {
        p.stem.casefold()
        for p in emoji_dir.iterdir()
        if p.is_file()
    } if emoji_dir.exists() else set()

    if stem.casefold() not in existing:
        return emoji_dir / f"{stem}{ext}"
    for index in range(1, 1000):
        candidate = f"{stem}_{index}"
        if candidate.casefold() not in existing:
            return emoji_dir / f"{candidate}{ext}"
    raise EmojiError(f"{stem} 的副本太多了，请先整理一下")


def save_one(emoji_dir: Path, filename: str, data: bytes, *, stem: str | None = None) -> dict[str, Any]:
    """写入单个文件。返回 ``{name, file, deduped}``；失败抛 EmojiError。

    ``stem`` 是用户在前端改过的名称；为空时取原文件名的 stem。

    ``deduped`` 表示**系统因重名自动加了 _1 后缀**，不是"用户改过名"——
    前端据此提示"有 N 个因为重名被自动改名"，用户才知道该去核对哪个。
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise EmojiError(
            f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限"
        )
    if not data:
        raise EmojiError("文件内容为空")

    raw_ext = Path(filename or "").suffix.lower()
    if raw_ext not in SUPPORTED_IMAGE_EXTS:
        raise EmojiError(f"不支持的图片格式：{raw_ext or '（无后缀）'}")
    _check_content(raw_ext, data[:16])

    requested = stem if stem is not None else Path(filename).stem
    name = validate_stem(requested)
    emoji_dir.mkdir(parents=True, exist_ok=True)
    target = unique_target(emoji_dir, name, raw_ext)

    try:
        target.write_bytes(data)
    except OSError as e:
        raise EmojiError(f"写入失败：{e}") from e

    logger.info("表情包已添加: %s", target.name)
    return {"name": target.stem, "file": target.name, "deduped": target.stem != name}


def add_uploads(
    emoji_dir: Path,
    files: Iterable[tuple[str, bytes]],
    *,
    rename: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """批量写入。单个文件失败只记进 ``skipped``，不影响其它文件。

    ``rename`` 的键是上传时的原始文件名，值是用户改过的名称。
    """
    rename = rename or {}
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    count = 0
    for filename, data in files:
        count += 1
        if count > MAX_FILES_PER_REQUEST:
            skipped.append(
                {"file": filename, "reason": f"一次最多上传 {MAX_FILES_PER_REQUEST} 个文件"}
            )
            continue
        try:
            added.append(save_one(emoji_dir, filename, data, stem=rename.get(filename)))
        except EmojiError as e:
            skipped.append({"file": filename, "reason": str(e)})
        except Exception as e:  # noqa: BLE001
            logger.exception("添加表情包失败: %s", filename)
            skipped.append({"file": filename, "reason": f"未预期的错误：{e}"})
    return {"added": added, "skipped": skipped}


def rename(emoji_dir: Path, name: str, new_name: str) -> dict[str, str]:
    """改名（保持原扩展名）。同名已存在时拒绝，与原 GUI 行为一致。"""
    source = find_by_stem(emoji_dir, name)
    target_stem = validate_stem(new_name)
    if target_stem == unicodedata.normalize("NFC", source.stem):
        return {"from": source.stem, "to": source.stem}
    target = emoji_dir / f"{target_stem}{source.suffix.lower()}"
    if target.exists():
        raise EmojiError(f"{target_stem} 已存在")
    try:
        source.rename(target)
    except OSError as e:
        raise EmojiError(f"改名失败：{e}") from e
    logger.info("表情包已改名: %s → %s", source.name, target.name)
    return {"from": source.stem, "to": target.stem}


def delete(emoji_dir: Path, name: str) -> str:
    """删除一个表情包，返回被删的文件名。"""
    target = find_by_stem(emoji_dir, name)
    filename = target.name
    try:
        target.unlink()
    except OSError as e:
        raise EmojiError(f"删除失败：{e}") from e
    logger.info("表情包已删除: %s", filename)
    return filename


__all__ = [
    "MAX_FILES_PER_REQUEST",
    "MAX_UPLOAD_BYTES",
    "MIME_BY_EXT",
    "SUPPORTED_IMAGE_EXTS",
    "EmojiError",
    "add_uploads",
    "delete",
    "find_by_stem",
    "list_items",
    "list_names",
    "rename",
    "resolve_file",
    "save_one",
    "sniff_image",
    "unique_target",
    "validate_stem",
]
