"""表情包 HTTP 路由测试。

刻意**不用 TestClient**：那会走真实的 httpx，而本机沙箱的 NO_PROXY 里含 ``[::1]``
会让 httpx 解析崩溃（CI 无此问题，但本地跑不了）。直接调用路由函数既能覆盖
全部逻辑，又对本地和 CI 都稳定。路由函数是普通 async 函数，参数是 ``request``
和一个 ``file`` 列表，桩起来很直接。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from webui.api import emoji as emoji_api

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64


class _FakeHost:
    def __init__(self, emoji_dir: Path) -> None:
        self._paths = SimpleNamespace(EMOJI_DIR=emoji_dir)

    def build_paths(self):
        return self._paths


def _request(emoji_dir: Path):
    host = _FakeHost(emoji_dir)
    app = SimpleNamespace(state=SimpleNamespace(host=host))
    return SimpleNamespace(app=app)


def _upload(filename: str, data: bytes) -> UploadFile:
    import io

    return UploadFile(file=io.BytesIO(data), filename=filename)


@pytest.fixture
def emoji_dir(tmp_path: Path) -> Path:
    d = tmp_path / "emoji"
    d.mkdir()
    return d


# ============================================================
# 列表
# ============================================================


async def test_list_returns_items_and_dir(emoji_dir):
    (emoji_dir / "猫.png").write_bytes(PNG)
    data = await emoji_api.list_emoji(_request(emoji_dir))
    assert data["count"] == 1
    assert data["items"][0]["name"] == "猫"
    assert data["dir"] == str(emoji_dir)
    assert set(data["supported_exts"]) == {".jpg", ".jpeg", ".png", ".gif"}
    assert data["max_upload_bytes"] > 0


async def test_list_works_without_runtime(emoji_dir):
    """停掉 Runtime 也应该能整理表情包——路由不调 require_runtime。"""
    data = await emoji_api.list_emoji(_request(emoji_dir))
    assert data["count"] == 0


async def test_names_returns_name_to_file_map(emoji_dir):
    (emoji_dir / "猫.png").write_bytes(PNG)
    (emoji_dir / "dog.gif").write_bytes(GIF)
    data = await emoji_api.list_names(_request(emoji_dir))
    assert data["items"] == [
        {"name": "dog", "file": "dog.gif"},
        {"name": "猫", "file": "猫.png"},
    ]


# ============================================================
# 读文件
# ============================================================


async def test_read_file_returns_response(emoji_dir):
    (emoji_dir / "猫.png").write_bytes(PNG)
    response = await emoji_api.read_file(_request(emoji_dir), "猫.png")
    assert Path(response.path) == emoji_dir / "猫.png"
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "attack",
    ["../etc/passwd.png", "a/b.png", "..%2f..%2fetc.png", "evil.sh", "nope.png"],
)
async def test_read_file_rejects_bad_names(emoji_dir, attack):
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.read_file(_request(emoji_dir), attack)
    assert excinfo.value.status_code == 400


async def test_read_file_rejects_disguised_content(emoji_dir):
    (emoji_dir / "evil.png").write_bytes(b"#!/bin/sh\n")
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.read_file(_request(emoji_dir), "evil.png")
    assert excinfo.value.status_code == 400


async def test_read_file_404_via_400_message(emoji_dir):
    """不存在的文件给 400 + 人话，而不是 500 或裸 404。"""
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.read_file(_request(emoji_dir), "missing.png")
    assert "不存在" in str(excinfo.value.detail)


# ============================================================
# 上传
# ============================================================


async def test_upload_adds_files(emoji_dir):
    result = await emoji_api.upload(
        _request(emoji_dir),
        files=[_upload("猫.png", PNG), _upload("dog.gif", GIF)],
        rename="",
    )
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["skipped"] == []
    assert (emoji_dir / "猫.png").is_file()


async def test_upload_applies_rename_map(emoji_dir):
    """用户指定的名字被原样采用，且**不算**重名去重。"""
    result = await emoji_api.upload(
        _request(emoji_dir),
        files=[_upload("IMG_20260101_123456.png", PNG)],
        rename='{"IMG_20260101_123456.png": "无语"}',
    )
    assert result["added"][0]["name"] == "无语"
    assert result["deduped"] == []
    assert (emoji_dir / "无语.png").is_file()


async def test_upload_reports_deduped_names(emoji_dir):
    """与已有表情包重名时自动加 _1，并单独报出来让用户去核对。"""
    (emoji_dir / "猫.png").write_bytes(PNG)
    result = await emoji_api.upload(
        _request(emoji_dir), files=[_upload("猫.png", PNG)], rename=""
    )
    assert result["count"] == 1
    assert result["deduped"] == [
        {"name": "猫_1", "file": "猫_1.png", "deduped": True}
    ]


async def test_upload_reports_partial_skips(emoji_dir):
    result = await emoji_api.upload(
        _request(emoji_dir),
        files=[_upload("good.png", PNG), _upload("bad.txt", b"hello")],
        rename="",
    )
    assert result["count"] == 1
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["file"] == "bad.txt"


async def test_upload_all_failed_is_400(emoji_dir):
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.upload(
            _request(emoji_dir), files=[_upload("bad.txt", b"hello")], rename=""
        )
    assert excinfo.value.status_code == 400


async def test_upload_no_files_is_400(emoji_dir):
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.upload(_request(emoji_dir), files=[], rename="")
    assert excinfo.value.status_code == 400


async def test_upload_bad_rename_json_is_400(emoji_dir):
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.upload(
            _request(emoji_dir), files=[_upload("a.png", PNG)], rename="{not json"
        )
    assert excinfo.value.status_code == 400
    assert "JSON" in str(excinfo.value.detail)


async def test_upload_rename_must_be_object(emoji_dir):
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.upload(
            _request(emoji_dir), files=[_upload("a.png", PNG)], rename="[1,2]"
        )
    assert "对象" in str(excinfo.value.detail)


# ============================================================
# 改名 / 删除
# ============================================================


async def test_rename_endpoint(emoji_dir):
    (emoji_dir / "旧.png").write_bytes(PNG)
    result = await emoji_api.rename_emoji(
        _request(emoji_dir), "旧", {"new_name": "新"}
    )
    assert result == {"ok": True, "from": "旧", "to": "新"}
    assert (emoji_dir / "新.png").is_file()


async def test_rename_rejects_empty_new_name(emoji_dir):
    (emoji_dir / "旧.png").write_bytes(PNG)
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.rename_emoji(_request(emoji_dir), "旧", {"new_name": "  "})
    assert excinfo.value.status_code == 400


async def test_rename_rejects_traversal_new_name(emoji_dir):
    (emoji_dir / "旧.png").write_bytes(PNG)
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.rename_emoji(
            _request(emoji_dir), "旧", {"new_name": "../escaped"}
        )
    assert excinfo.value.status_code == 400


async def test_delete_endpoint(emoji_dir):
    (emoji_dir / "猫.png").write_bytes(PNG)
    result = await emoji_api.delete_emoji(_request(emoji_dir), "猫")
    assert result == {"ok": True, "deleted": "猫.png"}
    assert not (emoji_dir / "猫.png").exists()


async def test_delete_missing_is_400(emoji_dir):
    with pytest.raises(HTTPException) as excinfo:
        await emoji_api.delete_emoji(_request(emoji_dir), "nope")
    assert excinfo.value.status_code == 400
