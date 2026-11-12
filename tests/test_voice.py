"""微信语音：落盘、按需转码播放、历史转写回填。"""
from __future__ import annotations

import json

from app import db, paths, voice


def _voice_messages(limit: int = 10):
    with db.session(readonly=True) as conn:
        return conn.execute(
            "SELECT id, platform_msg_id, media_path, duration FROM messages"
            " WHERE platform='wechat' AND kind='voice' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()


def test_voice_blobs_are_landed(library):
    rows = _voice_messages()
    assert rows, "演示数据里的语音没有被导入"
    for row in rows:
        assert row["media_path"], "语音消息缺少 media_path"
        abs_path = paths.MEDIA_DIR / row["media_path"]
        assert abs_path.is_file(), abs_path
        assert abs_path.suffix in (".wav", ".silk")


def test_voice_audio_endpoint_serves_wav(client, library):
    row = _voice_messages(1)[0]
    res = client.get(f"/api/messages/{row['id']}/audio")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/")
    assert res.content[:4] == b"RIFF"


def test_voice_audio_missing_file(client, library):
    res = client.get("/api/messages/999999/audio")
    assert res.status_code == 404


def test_voice_audio_requires_media(client, library):
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT id FROM messages WHERE platform='wechat' AND media_path IS NULL"
            " AND kind='voice' LIMIT 1"
        ).fetchone()
    if row is None:
        return  # 演示数据里都有音频，跳过
    res = client.get(f"/api/messages/{row['id']}/audio")
    assert res.status_code == 404


def test_voice_sniff_extension():
    assert voice.sniff_extension(b"RIFF....WAVE") == ".wav"
    assert voice.sniff_extension(b"\x02#!SILK_V3rest") == ".silk"
    assert voice.sniff_extension(b"#!SILK_V3rest") == ".silk"
    assert voice.sniff_extension(b"") == ".bin"


def test_voice_strip_silk_prefix():
    assert voice.strip_silk_prefix(b"\x02#!SILK_V3abc") == b"#!SILK_V3abc"
    assert voice.strip_silk_prefix(b"#!SILK_V3abc") == b"#!SILK_V3abc"
    assert voice.strip_silk_prefix(b"plain") == b"plain"


def test_import_legacy_transcripts(client, library, tmp_path):
    row = _voice_messages(1)[0]
    payload = {
        "id": row["platform_msg_id"],
        "talker": "x",
        "sender": "y",
        "ts": 0,
        "text": "这是回填的转写。",
        "dur": 1.2,
    }
    jsonl = tmp_path / "voice_api.jsonl"
    jsonl.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n"
        + json.dumps({"id": "message_0.db:Msg_none:1", "text": "对不上"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    res = client.post("/api/voice/import-jsonl", json={"path": str(jsonl)})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["matched"] == 1
    assert data["unmatched"] == 1

    with db.session(readonly=True) as conn:
        tr = conn.execute(
            "SELECT engine, status, text FROM voice_transcriptions WHERE message_id=?",
            (row["id"],),
        ).fetchone()
    assert tr["engine"] == "legacy"
    assert tr["status"] == "success"
    assert tr["text"] == "这是回填的转写。"


def test_transcript_is_searchable_and_returned(client, library, tmp_path):
    row = _voice_messages(1)[0]
    jsonl = tmp_path / "v.jsonl"
    jsonl.write_text(
        json.dumps({"id": row["platform_msg_id"], "text": "独一无二的转写内容"},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert client.post("/api/voice/import-jsonl", json={"path": str(jsonl)}).status_code == 200

    found = client.get("/api/search?q=独一无二").json()
    assert found["total"] >= 1
    hit = found["items"][0]
    assert hit["id"] == row["id"]

    detail = client.get(f"/api/threads/{hit['thread_id']}/messages?limit=100").json()
    matched = [m for m in detail["items"] if m["id"] == row["id"]]
    assert matched and matched[0]["transcript"] == "独一无二的转写内容"


def test_stats_include_voice_metrics(client, library):
    stats = client.get("/api/stats").json()
    assert stats["voice_messages"] >= 1
    assert stats["voice_with_audio"] >= 1
    assert "transcripts" in stats
    assert {"files", "bytes"} <= set(stats["voice_cache"])


def test_clear_voice_cache(client, library):
    # 演示语音是 WAV 直出、不进缓存，这里手工造一个缓存文件来验证清理
    voice.VOICE_CACHE.mkdir(parents=True, exist_ok=True)
    (voice.VOICE_CACHE / "dummy-test.wav").write_bytes(b"RIFF0000")
    assert voice.cache_stats()["files"] >= 1

    res = client.post("/api/voice/cache/clear", json={}).json()
    assert res["removed_files"] >= 1
    assert res["freed_bytes"] >= 8
    assert voice.cache_stats()["files"] == 0


def test_import_voice_jsonl_rejects_non_json(client):
    res = client.post("/api/voice/import-jsonl", json={"path": "C:/tmp/a.txt"})
    assert res.status_code == 400


def test_import_voice_jsonl_reports_empty(client, library, tmp_path):
    row = _voice_messages(1)[0]
    jsonl = tmp_path / "v.jsonl"
    jsonl.write_text(
        json.dumps({"id": row["platform_msg_id"], "text": "有内容"}, ensure_ascii=False) + "\n"
        + json.dumps({"id": "message_0.db:Msg_x:1", "text": ""}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    res = client.post("/api/voice/import-jsonl", json={"path": str(jsonl)}).json()
    assert res["lines"] == 2
    assert res["total"] == 1
    assert res["empty"] == 1
    assert res["matched"] == 1


def test_import_voice_jsonl_missing_file(client):
    res = client.post("/api/voice/import-jsonl", json={"path": "C:/nope/voice.jsonl"})
    assert res.status_code == 400
