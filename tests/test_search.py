"""搜索：FTS5 全文、筛选、分页、分组、转写命中。"""
from __future__ import annotations

import time

from app import db, voice

BASE = 1_700_000_000


def _conversations(n: int = 2):
    with db.session(readonly=True) as conn:
        return conn.execute(
            "SELECT id, platform FROM conversations ORDER BY id LIMIT ?", (n,)
        ).fetchall()


def _add(conv_id: int, platform: str, key: str, text: str, *, kind="text",
         sender="小林", is_self=False, ts=BASE, extra=None):
    with db.session() as conn:
        db.add_message(
            conn, conv_id, platform, key,
            sender_uid="wxid_demo0000001" if not is_self else "self",
            sender_name=sender, is_self=is_self, ts=ts, kind=kind, text=text,
            extra=extra,
        )
        return conn.execute(
            "SELECT id FROM messages WHERE platform_msg_id=?", (key,)
        ).fetchone()["id"]


def _seed():
    convs = _conversations(2)
    c1, c2 = convs[0]["id"], convs[1]["id"]
    _add(c1, convs[0]["platform"], "s:1", "今天晚餐吃什么", ts=BASE)
    vid = _add(c1, convs[0]["platform"], "s:2", "明天见", kind="voice", ts=BASE + 10)
    voice.set_transcript(vid, engine="tencent", text="明天见，老地方")
    _add(c1, convs[0]["platform"], "s:3", "图片", kind="image", ts=BASE + 20)
    _add(c1, convs[0]["platform"], "s:4", "测试分页 第一条", ts=BASE + 30)
    _add(c1, convs[0]["platform"], "s:5", "测试分页 第二条", ts=BASE + 31)
    _add(c2, convs[1]["platform"], "s:6", "测试分页 第三条", sender="阿杰", ts=BASE + 32)
    _add(c1, convs[0]["platform"], "s:7", "共同关键词在这里", ts=BASE + 40)
    _add(c2, convs[1]["platform"], "s:8", "共同关键词也在", sender="阿杰", ts=BASE + 41)
    return c1, c2


def test_fts_engine_used_for_long_query(client, library):
    _seed()
    data = client.get("/api/search", params={"q": "晚餐吃什么"}).json()
    assert data["engine"] == "fts"
    assert data["total"] >= 1
    assert any("晚餐" in (i["text"] or "") for i in data["items"])


def test_short_query_falls_back_to_like(client, library):
    _seed()
    data = client.get("/api/search", params={"q": "晚餐"}).json()
    assert data["engine"] == "like"
    assert data["total"] >= 1


def test_transcript_is_searchable(client, library):
    _seed()
    data = client.get("/api/search", params={"q": "老地方"}).json()
    assert data["total"] >= 1
    items = [i for i in data["items"] if i["kind"] == "voice"]
    assert items, "应当能按转写文本找到语音"
    assert "老地方" in (items[0]["transcript"] or "")


def test_kind_filter(client, library):
    _seed()
    data = client.get("/api/search", params={"q": "明天见", "kinds": "voice"}).json()
    assert data["total"] >= 1
    assert all(i["kind"] == "voice" for i in data["items"])

    data2 = client.get("/api/search", params={"q": "明天见", "kinds": "text"}).json()
    assert data2["total"] == 0


def test_sender_filter(client, library):
    _seed()
    data = client.get("/api/search", params={"q": "晚餐", "sender": "小林"}).json()
    assert data["total"] >= 1
    data2 = client.get("/api/search", params={"q": "晚餐", "sender": "不存在的人"}).json()
    assert data2["total"] == 0


def test_time_range_filter(client, library):
    _seed()
    data = client.get("/api/search", params={"q": "晚餐", "since_ts": BASE + 5}).json()
    assert data["total"] == 0
    data2 = client.get("/api/search", params={"q": "晚餐", "since_ts": BASE - 10}).json()
    assert data2["total"] >= 1


def test_pagination(client, library):
    _seed()
    first = client.get("/api/search", params={"q": "测试分页", "limit": 1}).json()
    assert first["total"] == 3
    assert len(first["items"]) == 1
    assert first["has_more"] is True

    last = client.get("/api/search", params={"q": "测试分页", "limit": 1, "offset": 2}).json()
    assert len(last["items"]) == 1
    assert last["has_more"] is False
    assert first["items"][0]["id"] != last["items"][0]["id"]


def test_group_by_conversation(client, library):
    _seed()
    data = client.get("/api/search", params={"q": "共同关键词", "group": "true"}).json()
    assert data["total"] == 2
    assert len(data["groups"]) >= 2
    assert all(g["n"] >= 1 for g in data["groups"])


def test_search_requires_query(client, library):
    assert client.get("/api/search").status_code == 422
