"""音频格式：抖音 mpeg → wav（腾讯云只收 wav）。"""
from __future__ import annotations

import pytest

from app import asr, db, paths, voice

pytest.importorskip("av")


def test_convert_mpeg_to_wav(library):
    src = paths.MEDIA_DIR / "douyin" / "voice" / "demo_0001_1.mpeg"
    assert src.is_file(), "演示数据里应当有抖音语音"
    wav, duration = voice.convert_to_wav(src, "test_audio_convert")
    assert wav.is_file() and wav.stat().st_size > 44
    assert duration and duration > 0
    # 第二次命中缓存
    again, _ = voice.convert_to_wav(src, "test_audio_convert")
    assert again == wav


def test_prepare_audio_returns_wav_for_douyin(library):
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT id, media_path FROM messages"
            " WHERE platform='douyin' AND kind='voice' AND media_path IS NOT NULL LIMIT 1"
        ).fetchone()
    assert row is not None
    wav = asr._prepare_audio({"id": row["id"], "media_path": row["media_path"]})
    assert wav.suffix.lower() == ".wav"
    assert wav.is_file()


def test_media_counts_keep_all_tabs(client, library):
    with db.session(readonly=True) as conn:
        conv = conn.execute(
            "SELECT id FROM conversations WHERE platform='wechat' LIMIT 1"
        ).fetchone()
    data = client.get(f"/api/threads/conv:{conv['id']}/media", params={"kind": "image"}).json()
    assert all(item["kind"] == "image" for item in data["items"])
    # 切到图片后，其它 tab 的计数仍然在
    assert data["counts"].get("sticker", 0) >= 1
    assert data["counts"].get("video", 0) >= 1
