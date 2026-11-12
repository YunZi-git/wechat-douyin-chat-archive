"""加固项：通配符转义、跳转边界、导出重名、Host 校验、取消、物化统计。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import contacts, db, paths, threads
from app.exporters import chatlab
from app.jobs import JobCancelled


def _lin_ids() -> tuple[int, int]:
    items = [c for c in contacts.list_contacts() if c["display_name"] == "林晓"]
    wx = next(c for c in items if c["platforms"] == ["wechat"])
    dy = next(c for c in items if c["platforms"] == ["douyin"])
    return wx["id"], dy["id"]


# ── 搜索：LIKE 通配符必须被转义 ────────────────────────────────────

def test_search_escapes_percent(library):
    assert threads.search_messages("%")["total"] == 0


def test_search_escapes_underscore(library):
    assert threads.search_messages("_")["total"] == 0


def test_search_normal_still_works(library):
    assert threads.search_messages("视频")["total"] >= 1


def test_thread_list_query_escapes_wildcards(library):
    # 不转义的话 "%" 会匹配全部 10 个会话
    assert threads.list_threads(q="%")["total"] == 0


# ── 跳转：around_ts 必须包含目标那条 ──────────────────────────────

def test_around_ts_includes_target(library):
    wx_id, dy_id = _lin_ids()
    contacts.merge_contacts(wx_id, dy_id)
    tid = f"contact:{wx_id}"

    target = threads.search_messages("转场", thread_id=tid)["items"][0]
    page = threads.thread_messages(tid, limit=20, around_ts=target["ts"])
    ids = [m["id"] for m in page["items"]]
    assert target["id"] in ids
    ts_list = [m["ts"] for m in page["items"]]
    assert ts_list == sorted(ts_list)


def test_around_ts_respects_platform_filter(library):
    wx_id, dy_id = _lin_ids()
    contacts.merge_contacts(wx_id, dy_id)
    tid = f"contact:{wx_id}"
    page = threads.thread_messages(tid, limit=20, around_ts=2_000_000_000, platform="douyin")
    assert all(m["platform"] == "douyin" for m in page["items"])


# ── 导出：同秒重名不覆盖 ──────────────────────────────────────────

def test_export_filename_not_overwritten(library, tmp_path):
    wx_id, dy_id = _lin_ids()
    contacts.merge_contacts(wx_id, dy_id)
    tid = f"contact:{wx_id}"
    first = chatlab.export_thread(tid, fmt="jsonl", out_dir=tmp_path)
    second = chatlab.export_thread(tid, fmt="jsonl", out_dir=tmp_path)
    assert first != second
    assert second.exists() and first.exists()


def test_export_json_is_streamed_and_valid(library, tmp_path):
    wx_id, dy_id = _lin_ids()
    contacts.merge_contacts(wx_id, dy_id)
    path = chatlab.export_thread(f"contact:{wx_id}", fmt="json", out_dir=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["messages"]) == 9
    assert payload["members"]


# ── 物化统计 ─────────────────────────────────────────────────────

def test_conversation_stats_populated(library):
    with db.session(readonly=True) as conn:
        total_conv = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        stats = conn.execute("SELECT COUNT(*) FROM conversation_stats").fetchone()[0]
    assert stats == total_conv


def test_conversation_stats_match_messages(library):
    with db.session(readonly=True) as conn:
        row = conn.execute(
            """SELECT s.conversation_id, s.msg_count, s.last_ts,
                      (SELECT COUNT(*) FROM messages m
                        WHERE m.conversation_id = s.conversation_id) AS real_count,
                      (SELECT MAX(ts) FROM messages m
                        WHERE m.conversation_id = s.conversation_id) AS real_last
                 FROM conversation_stats s LIMIT 1"""
        ).fetchone()
    assert row["msg_count"] == row["real_count"]
    assert row["last_ts"] == row["real_last"]


# ── 取消导入 ─────────────────────────────────────────────────────

def test_wechat_import_can_be_cancelled(demo_sources):
    from app.importers import wechat

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(paths.UNIFIED_DB) + suffix)
        if p.exists():
            p.unlink()
    db.init()
    with pytest.raises(JobCancelled):
        wechat.import_account(demo_sources["wechat"], should_cancel=lambda: True)

    with db.session(readonly=True) as conn:
        status = conn.execute(
            "SELECT status FROM import_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()["status"]
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert status == "cancelled"
    assert messages == 0


def test_douyin_import_can_be_cancelled(demo_sources):
    from app.importers import douyin

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(paths.UNIFIED_DB) + suffix)
        if p.exists():
            p.unlink()
    db.init()
    with pytest.raises(JobCancelled):
        douyin.import_chat_db(
            demo_sources["douyin"] / "chat.db",
            media_src=demo_sources["douyin"] / "media",
            copy_media=False,
            should_cancel=lambda: True,
        )
    with db.session(readonly=True) as conn:
        status = conn.execute(
            "SELECT status FROM import_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()["status"]
    assert status == "cancelled"


# ── Host 头校验（DNS rebinding 防护）─────────────────────────────

def test_host_guard_rejects_foreign_host(client):
    res = client.get("/api/stats", headers={"host": "evil.example.com"})
    assert res.status_code == 403


def test_host_guard_allows_loopback(client):
    for host in ("127.0.0.1", "127.0.0.1:8000", "localhost", "localhost:8765"):
        assert client.get("/api/health", headers={"host": host}).status_code == 200
