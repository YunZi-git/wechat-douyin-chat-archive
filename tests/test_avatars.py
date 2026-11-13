"""头像：微信 head_image 导入、合并兜底、重新解密后的目录后缀。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app import db, paths, wechat_media
from app.importers import wechat


def _png(color: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, "PNG")
    return buf.getvalue()


def test_wechat_avatars_imported(library):
    with db.session(readonly=True) as conn:
        rows = conn.execute(
            """SELECT c.avatar_url FROM contacts c
                 JOIN identities i ON i.contact_id = c.id
                WHERE i.platform='wechat'
                  AND c.avatar_url LIKE 'wechat/avatars/%'"""
        ).fetchall()
    assert rows, "应该从头像库导入联系人头像"
    for row in rows:
        assert (paths.MEDIA_DIR / row["avatar_url"]).is_file()


def test_avatar_updates_on_reimport(library, tmp_path):
    source = Path(library["wechat"]["source"])
    account = tmp_path / "account" / source.name
    import shutil

    shutil.copytree(source, account)

    head_db = account / "head_image" / "head_image.db"
    con = sqlite3.connect(head_db)
    try:
        username = con.execute("SELECT username FROM head_image LIMIT 1").fetchone()[0]
        con.execute(
            "UPDATE head_image SET image_buffer=? WHERE username=?",
            (_png((250, 10, 10)), username),
        )
        con.commit()
    finally:
        con.close()

    with db.session(readonly=True) as conn:
        old = conn.execute(
            """SELECT c.avatar_url FROM contacts c
                 JOIN identities i ON i.contact_id = c.id
                WHERE i.platform='wechat' AND i.platform_uid=?""",
            (username,),
        ).fetchone()["avatar_url"]

    report = wechat.import_account(account)
    assert report["avatars"] >= 1

    with db.session(readonly=True) as conn:
        new = conn.execute(
            """SELECT c.avatar_url FROM contacts c
                 JOIN identities i ON i.contact_id = c.id
                WHERE i.platform='wechat' AND i.platform_uid=?""",
            (username,),
        ).fetchone()["avatar_url"]

    assert new != old, "头像内容变化后 URL 应更新（避免浏览器一直用旧缓存）"
    assert (paths.MEDIA_DIR / new).is_file()


def test_merged_contact_uses_identity_avatar(client, library):
    with db.session() as conn:
        contact_id = db.ensure_contact(conn, "头像兜底")
        db.ensure_identity(
            conn, contact_id, "douyin", "dy_avatar_demo",
            platform_name="头像兜底", avatar_url="douyin/avatars/demo.png",
        )
        db.ensure_conversation(
            conn, "douyin", "dy:avatar:conv", title="头像兜底",
            contact_id=contact_id, kind="private",
        )
    data = client.get("/api/threads").json()
    item = next(
        i for i in data["items"] if i["thread_id"] == f"contact:{contact_id}"
    )
    assert item["avatar"] == "douyin/avatars/demo.png"


def test_resolve_account_dir_after_redecrypt(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "WECHAT_DECRYPTED", tmp_path)
    (tmp_path / "wxid_demo_abcd").mkdir()
    (tmp_path / "wxid_demo_1234").mkdir()
    # 记录的是旧后缀，但目录已经换成新后缀
    resolved = wechat_media._resolve_account_dir("wxid_demo_9999")
    assert resolved is not None
    assert resolved.name.startswith("wxid_demo_")
