"""「我的身份」设置。"""
from __future__ import annotations

from app import db


def _overview(client):
    return client.get("/api/self-identity").json()


def test_overview_lists_self_and_candidates(client, library):
    data = _overview(client)
    assert "platforms" in data
    dy = data["platforms"]["douyin"]
    assert dy["uid"]
    assert dy["candidates"]
    assert any(c["uid"] == dy["uid"] for c in dy["candidates"])


def test_change_self_updates_messages(client, library):
    data = _overview(client)
    dy = data["platforms"]["douyin"]
    other = next(c["uid"] for c in dy["candidates"] if c["uid"] != dy["uid"])
    res = client.post("/api/self-identity", json={"platform": "douyin", "uid": other})
    assert res.status_code == 200, res.text
    assert res.json()["self_messages"] > 0
    with db.session(readonly=True) as conn:
        mismatched = conn.execute(
            "SELECT COUNT(*) AS n FROM messages"
            " WHERE platform='douyin' AND is_self=1 AND sender_uid<>?",
            (other,),
        ).fetchone()["n"]
        self_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM messages"
            " WHERE platform='douyin' AND is_self=1 AND sender_uid=?",
            (other,),
        ).fetchone()["n"]
    assert mismatched == 0
    assert self_rows > 0
    after = _overview(client)
    assert after["platforms"]["douyin"]["uid"] == other


def test_change_self_rejects_unknown_uid(client, library):
    res = client.post("/api/self-identity", json={
        "platform": "douyin", "uid": "not-a-real-uid",
    })
    assert res.status_code == 400
