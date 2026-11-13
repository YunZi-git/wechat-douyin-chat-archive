"""抖音分享/表情 JSON、旧载荷修复、首次会话列表。"""
from __future__ import annotations

import json
from pathlib import Path

from app import db, message_repair, paths, services
from app.douyin_message import parse_payload

SHARE = {
    "ai_ext": "{}",
    "aweType": 0,
    "awemeType": 68,
    "content_name": "肥鸭o",
    "content_title": "",
    "itemId": 7000000000000000001,
    "cover_url": {"url_list": ["https://p3.douyinpic.com/example-cover.jpeg"]},
}
STICKERS = {
    "stickers": [{
        "display_name": "Hi",
        "static_url": {"url_list": ["https://p3.douyinpic.com/sticker.png"]},
    }],
}


def test_parse_share_payload():
    parsed = parse_payload(json.dumps(SHARE))
    assert parsed["kind"] == "share"
    assert parsed["meta"]["share"]["url"].endswith("/video/7000000000000000001")
    assert parsed["meta"]["share"]["author"] == "肥鸭o"


def test_parse_sticker_payload():
    parsed = parse_payload(json.dumps(STICKERS))
    assert parsed["kind"] == "sticker"
    assert parsed["meta"]["sticker"]["remoteUrl"].endswith("sticker.png")


def test_parse_plain_text_returns_none():
    assert parse_payload("普通文本") is None
    assert parse_payload('{"other": 1}') is None


def test_repair_raw_payloads(library):
    with db.session() as conn:
        db.add_message(
            conn, 1, "wechat", "repair:wx:1",
            sender_uid="wxid_demo0000001", sender_name="林晓", is_self=False,
            ts=1_700_000_000, kind="image",
            text="chenkai1977:\n<?xml version=\"1.0\"?>\n<msg><img md5=\"x\" /></msg>",
            extra=json.dumps({"local_type": 3}),
        )
        db.add_message(
            conn, 1, "douyin", "repair:dy:1",
            sender_uid="dy", sender_name="阿杰", is_self=False,
            ts=1_700_000_001, kind="text", text=json.dumps(SHARE, ensure_ascii=False),
            extra=json.dumps({"msg_type": 1}),
        )
        stats = message_repair.repair_raw_messages(conn)
    assert stats["wechat"] >= 1 and stats["douyin"] >= 1
    with db.session(readonly=True) as conn:
        wx = conn.execute(
            "SELECT text FROM messages WHERE platform='wechat' AND platform_msg_id='repair:wx:1'"
        ).fetchone()
        dy = conn.execute(
            "SELECT kind, text, extra FROM messages WHERE platform='douyin'"
            " AND platform_msg_id='repair:dy:1'"
        ).fetchone()
    assert "<msg" not in wx["text"]
    assert dy["kind"] == "share"
    assert json.loads(dy["extra"])["share"]["url"]


def test_douyin_list_cmd(monkeypatch):
    monkeypatch.setattr(
        services, "browser_status",
        lambda: {"playwright": True, "channel": "msedge", "installed": True},
    )
    cmd = services._douyin_list_cmd()
    assert "--list-conversations" in cmd


def test_discovered_conversations_fallback(library, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOUYIN_DIR", tmp_path)
    (tmp_path / "conversations_list.json").write_text(
        json.dumps({
            "items": [
                {"nickname": "小林", "name": "小林"},
                {"nickname": "", "name": "阿杰"},
                {"nickname": "小林"},
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    data = services.douyin_conversations()
    assert data["source"] == "discovered"
    names = [item["name"] for item in data["items"]]
    assert names == ["小林", "阿杰"]

def test_discovered_list_wins_over_engine_db(library, tmp_path, monkeypatch):
    """引擎库里只有 1 个会话时，拉到的完整名单仍然要显示出来。"""
    import sqlite3

    engine = tmp_path / "engine.db"
    con = sqlite3.connect(engine)
    con.execute("CREATE TABLE conversations (conv_id TEXT, name TEXT,"
                " message_count INTEGER, last_message_time INTEGER, conv_type INTEGER)")
    con.execute("INSERT INTO conversations VALUES ('1', '小南梁喵', 0, 0, 0)")
    con.commit()
    con.close()
    monkeypatch.setattr(paths, "DOUYIN_DB", engine)
    monkeypatch.setattr(paths, "DOUYIN_DIR", tmp_path)
    (tmp_path / "conversations_list.json").write_text(
        json.dumps({"items": [{"nickname": "安心."}, {"nickname": "听风逝夜"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    data = services.douyin_conversations()
    assert data["source"] == "discovered"
    assert [item["name"] for item in data["items"]] == ["安心.", "听风逝夜"]
