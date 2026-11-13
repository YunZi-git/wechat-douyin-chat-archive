"""微信媒体：元数据解析、按需解密、缓存复用、失败原因。"""
from __future__ import annotations

import json

from app import db, paths, wechat_media
from app.importers.wechat import _media_meta

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _first_message(kind: str):
    with db.session(readonly=True) as conn:
        return conn.execute(
            "SELECT id, extra FROM messages WHERE platform='wechat' AND kind=?"
            " ORDER BY id LIMIT 1",
            (kind,),
        ).fetchone()


def test_media_meta_parses_xml_and_packed():
    xml = (
        '<msg><img md5="0123456789abcdef0123456789abcdef" '
        'aeskey="fedcba9876543210fedcba9876543210" /></msg>'
    )
    packed = b"\x08\x04\x1a\x22" + b'"" eaa5e7aeb70c97ef34bb3604ab8f74c2'
    meta = _media_meta("image", xml, packed)
    assert meta["wx_md5"] == "0123456789abcdef0123456789abcdef"
    assert meta["wx_aeskey"] == "fedcba9876543210fedcba9876543210"
    assert meta["wx_bubble_md5"] == "eaa5e7aeb70c97ef34bb3604ab8f74c2"
    # 非媒体类型不产生元数据
    assert _media_meta("text", xml, packed) == {}


def test_image_without_md5_returns_404_with_reason(client, library):
    row = _first_message("image")
    assert row is not None
    with db.session() as conn:  # 抹掉 md5，模拟老数据
        conn.execute("UPDATE messages SET extra='{}' WHERE id=?", (row["id"],))
    res = client.get(f"/api/messages/{row['id']}/image")
    assert res.status_code == 404
    assert "md5" in res.json()["detail"]


def test_video_without_md5_returns_404(client, library):
    row = _first_message("video")
    if row is None:
        return
    res = client.get(f"/api/messages/{row['id']}/video")
    assert res.status_code == 404


def test_image_decrypt_path_and_cache(client, library, monkeypatch):
    row = _first_message("image")
    md5 = "0123456789abcdef0123456789abcdef"
    with db.session() as conn:
        conn.execute(
            "UPDATE messages SET extra=? WHERE id=?",
            (json.dumps({"local_type": 3, "wx_md5": md5}), row["id"]),
        )
    # 账号目录（演示数据的来源目录不在统一 data 下，这里补一个）
    account = paths.WECHAT_DECRYPTED / "wxid_demo0000_0001"
    account.mkdir(parents=True, exist_ok=True)

    calls: list[tuple] = []

    def fake_get_image(account_id, md5_arg, account_dir, *, chat, local_id, ts, bubble_md5):
        calls.append((account_id, md5_arg))
        return PNG, "image/png"

    monkeypatch.setattr(wechat_media, "_siwx_get_image", fake_get_image)

    res = client.get(f"/api/messages/{row['id']}/image")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/png")
    assert res.content == PNG
    assert calls and calls[0][1] == md5

    # 落盘 + 写回 media_path
    with db.session(readonly=True) as conn:
        media_path = conn.execute(
            "SELECT media_path FROM messages WHERE id=?", (row["id"],)
        ).fetchone()["media_path"]
    assert media_path and (paths.MEDIA_DIR / media_path).is_file()

    # 第二次直接读缓存，不再调解密
    before = len(calls)
    res2 = client.get(f"/api/messages/{row['id']}/image")
    assert res2.status_code == 200 and res2.content == PNG
    assert len(calls) == before


def test_image_decrypt_failure_is_reported(client, library, monkeypatch):
    row = _first_message("image")
    with db.session() as conn:
        conn.execute(
            "UPDATE messages SET extra=? WHERE id=?",
            (json.dumps({"wx_md5": "ffffffffffffffffffffffffffffffff"}), row["id"]),
        )
    (paths.WECHAT_DECRYPTED / "wxid_demo0000_0001").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        wechat_media, "_siwx_get_image",
        lambda *a, **k: (None, "本地无原图/气泡/缩略图"),
    )
    res = client.get(f"/api/messages/{row['id']}/image")
    assert res.status_code == 404
    assert "本地无原图" in res.json()["detail"]


def test_cdn_host_whitelist():
    assert wechat_media._cdn_allowed("http://vweixinf.tc.qq.com/a?b=1")
    assert wechat_media._cdn_allowed("https://wxapp.tc.qq.com/x")
    assert not wechat_media._cdn_allowed("http://evil.example.com/x")
    assert not wechat_media._cdn_allowed("file:///C:/x.png")


def test_sticker_cdn_fallback(client, library, monkeypatch):
    row = _first_message("sticker")
    if row is None:
        return
    md5 = "abcabcabcabcabcabcabcabcabcabcab"
    with db.session() as conn:
        conn.execute(
            "UPDATE messages SET extra=? WHERE id=?",
            (json.dumps({
                "sticker": {"md5": md5, "cdnurl": "http://vweixinf.tc.qq.com/x?m=1"},
            }), row["id"]),
        )
    monkeypatch.setattr(wechat_media, "_fetch_cdn", lambda url: PNG)
    res = client.get(f"/api/messages/{row['id']}/image")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/png")
    with db.session(readonly=True) as conn:
        media_path = conn.execute(
            "SELECT media_path FROM messages WHERE id=?", (row["id"],)
        ).fetchone()["media_path"]
    assert media_path and media_path.startswith("wechat/media/")
    assert (paths.MEDIA_DIR / media_path).is_file()
