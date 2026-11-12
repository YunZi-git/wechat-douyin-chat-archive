"""媒体页与批量缓存。"""
from __future__ import annotations

import time

from app import db, wechat_media


def _conv(kind: str | None = None):
    with db.session(readonly=True) as conn:
        if kind:
            row = conn.execute(
                "SELECT id, platform FROM conversations WHERE kind=? LIMIT 1", (kind,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, platform FROM conversations WHERE platform='wechat' LIMIT 1"
            ).fetchone()
    return row


def _wait_job(client, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("ok", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError("任务超时")


def test_thread_media_lists_kinds(client, library):
    row = _conv()
    tid = f"conv:{row['id']}"
    data = client.get(f"/api/threads/{tid}/media").json()
    assert data["total"] >= 1
    assert data["counts"]
    assert data["items"]

    images = client.get(f"/api/threads/{tid}/media", params={"kind": "image"}).json()
    assert all(i["kind"] == "image" for i in images["items"])
    assert images["counts"].get("image", 0) >= 1


def test_thread_media_pagination(client, library):
    row = _conv()
    tid = f"conv:{row['id']}"
    first = client.get(f"/api/threads/{tid}/media", params={"limit": 1}).json()
    assert first["total"] >= 2
    assert len(first["items"]) == 1 and first["has_more"] is True
    second = client.get(f"/api/threads/{tid}/media", params={"limit": 1, "offset": 1}).json()
    assert second["items"][0]["id"] != first["items"][0]["id"]


def test_members_have_platform(client, library):
    row = _conv("group")
    if row is None:
        return
    info = client.get(f"/api/threads/conv:{row['id']}").json()
    members = info.get("members") or []
    assert members
    for member in members:
        assert "platform" in member
        assert "thread_id" in member


def test_media_prewarm_job(client, library, monkeypatch):
    calls = []

    def fake_image(message_id):
        calls.append(("image", message_id))
        return None

    def fake_video(message_id):
        calls.append(("video", message_id))
        return None

    monkeypatch.setattr(wechat_media, "fetch_image", fake_image)
    monkeypatch.setattr(wechat_media, "fetch_video", fake_video)

    job = client.post("/api/media/prewarm", json={"limit": 5}).json()["job"]
    done = _wait_job(client, job["id"])
    assert done["status"] == "ok"
    assert done["result"]["total"] >= 1
    assert done["result"]["ok"] == done["result"]["total"]
    assert calls
