"""导入器：数量、幂等、媒体落地。"""
from __future__ import annotations

from app import db, paths, threads
from app.importers import douyin, wechat


def test_wechat_import_counts(library):
    report = library["wechat"]
    assert report["platform"] == "wechat"
    assert report["conversations_added"] == 5      # 4 个私聊 + 1 个群
    assert report["messages_added"] == 21
    assert report["messages_skipped"] == 0


def test_douyin_import_counts(library):
    report = library["douyin"]
    assert report["platform"] == "douyin"
    assert report["conversations_added"] == 5      # 4 个私聊 + 1 个群
    assert report["messages_added"] == 20


def test_global_stats(library):
    stats = db.stats()
    assert stats["messages"] == 41
    assert stats["messages_by_platform"] == {"wechat": 21, "douyin": 20}
    assert stats["contacts"] == 8
    assert stats["groups"] == 2
    assert stats["merged_contacts"] == 0


def test_import_is_idempotent(library, demo_sources):
    before = db.stats()
    wechat.import_account(demo_sources["wechat"])
    douyin.import_chat_db(
        demo_sources["douyin"] / "chat.db",
        media_src=demo_sources["douyin"] / "media",
        copy_media=False,
    )
    after = db.stats()
    assert after["messages"] == before["messages"]
    assert after["conversations"] == before["conversations"]
    assert after["contacts"] == before["contacts"]


def test_message_kinds_are_normalised(library):
    with db.session(readonly=True) as conn:
        kinds = {
            r["kind"] for r in conn.execute("SELECT DISTINCT kind FROM messages")
        }
    assert {"text", "image", "voice", "sticker"} <= kinds


def test_douyin_media_landed_in_unified_media_dir(library):
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT media_path FROM messages WHERE kind='image' AND media_path IS NOT NULL LIMIT 1"
        ).fetchone()
    assert row is not None
    rel = row["media_path"]
    assert rel.startswith("douyin/")
    assert (paths.MEDIA_DIR / rel).is_file()


def test_group_conversations_have_no_contact(library):
    with db.session(readonly=True) as conn:
        rows = conn.execute(
            "SELECT contact_id, kind FROM conversations WHERE kind='group'"
        ).fetchall()
    assert len(rows) == 2
    assert all(r["contact_id"] is None for r in rows)


def test_thread_list_contains_both_platforms(library):
    result = threads.list_threads(limit=100)
    platforms = {p for item in result["items"] for p in item["platforms"]}
    assert platforms == {"wechat", "douyin"}
    assert result["total"] == 10
