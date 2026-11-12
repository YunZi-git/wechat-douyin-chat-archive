"""HTTP 接口：契约、错误处理、路径穿越防护。"""
from __future__ import annotations


def test_health(client):
    data = client.get("/api/health").json()
    assert data["ok"] is True
    assert data["first_run"] is False
    assert data["version"]


def test_stats(client):
    data = client.get("/api/stats").json()
    assert data["messages"] == 41
    assert data["messages_by_platform"] == {"wechat": 21, "douyin": 20}


def test_threads_list(client):
    data = client.get("/api/threads?limit=100").json()
    assert data["total"] == 10
    item = data["items"][0]
    assert {"thread_id", "title", "platforms", "msg_count", "last_ts"} <= set(item)


def test_threads_platform_filter(client):
    data = client.get("/api/threads?platform=douyin&limit=100").json()
    assert data["total"] > 0
    assert all("douyin" in item["platforms"] for item in data["items"])


def test_thread_detail_and_404(client):
    thread_id = client.get("/api/threads?limit=1").json()["items"][0]["thread_id"]
    assert client.get(f"/api/threads/{thread_id}").status_code == 200
    assert client.get("/api/threads/contact:99999").status_code == 404
    assert client.get("/api/threads/nonsense").status_code == 404


def test_thread_messages_pagination(client):
    thread_id = client.get("/api/threads?limit=100").json()["items"][0]["thread_id"]
    first = client.get(f"/api/threads/{thread_id}/messages?limit=3").json()
    assert len(first["items"]) == 3
    oldest = first["items"][0]["ts"]
    older = client.get(f"/api/threads/{thread_id}/messages?limit=3&before_ts={oldest}").json()
    assert all(m["ts"] < oldest for m in older["items"])


def test_search(client):
    data = client.get("/api/search?q=视频").json()
    assert data["total"] >= 1
    assert all("视频" in item["text"] for item in data["items"])
    assert client.get("/api/search?q=").status_code == 422


def test_merge_and_unmerge_via_api(client):
    items = client.get("/api/contacts").json()["items"]
    wx = next(c for c in items if c["display_name"] == "林晓" and c["platforms"] == ["wechat"])
    dy = next(c for c in items if c["display_name"] == "林晓" and c["platforms"] == ["douyin"])

    merged = client.post(
        "/api/contacts/merge",
        json={"primary_id": wx["id"], "secondary_id": dy["id"], "display_name": "林晓"},
    )
    assert merged.status_code == 200
    assert set(merged.json()["platforms"]) == {"wechat", "douyin"}

    again = client.post(
        "/api/contacts/merge", json={"primary_id": wx["id"], "secondary_id": wx["id"]}
    )
    assert again.status_code == 400

    detail = client.get(f"/api/threads/contact:{wx['id']}").json()
    assert detail["merged"] is True

    split = client.post(
        f"/api/contacts/{wx['id']}/unmerge", json={"platform": "douyin"}
    )
    assert split.status_code == 200
    assert split.json()["contact_id"] != wx["id"]


def test_rename_via_api(client):
    items = client.get("/api/contacts").json()["items"]
    target = next(c for c in items if c["display_name"] == "陈默")
    res = client.patch(f"/api/contacts/{target['id']}", json={"display_name": "陈默（同事）"})
    assert res.status_code == 200
    assert res.json()["display_name"] == "陈默（同事）"

    bad = client.patch(f"/api/contacts/{target['id']}", json={"display_name": ""})
    assert bad.status_code == 422


def test_settings_roundtrip(client):
    res = client.patch("/api/settings", json={"theme": "warm", "page_size": 100})
    assert res.status_code == 200
    assert res.json()["theme"] == "warm"
    assert client.get("/api/settings").json()["page_size"] == 100

    bad = client.patch("/api/settings", json={"theme": "neon"})
    assert bad.status_code == 422


def test_sources(client):
    data = client.get("/api/sources").json()
    assert "wechat_accounts" in data
    assert "douyin" in data
    assert "engines" in data
    assert data["engines"]["wechat"]["bundled"] is True


def test_browser_status_shape(client):
    douyin = client.get("/api/sources").json()["engines"]["douyin"]
    assert {"playwright", "edge", "chromium", "channel", "ready"} <= set(douyin)
    assert isinstance(douyin["playwright"], bool)
    assert isinstance(douyin["ready"], bool)
    # channel 只可能是三种：msedge / chromium / 空
    assert douyin["channel"] in ("msedge", "chromium", "")


def test_export_and_download(client):
    thread_id = client.get("/api/threads?limit=1").json()["items"][0]["thread_id"]
    res = client.post("/api/export", json={"thread_id": thread_id, "format": "jsonl"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["size"] > 0

    listing = client.get("/api/exports").json()["items"]
    assert any(item["name"] == payload["name"] for item in listing)

    download = client.get(payload["download"])
    assert download.status_code == 200
    assert b'"chatlab"' in download.content


def test_export_rejects_bad_thread(client):
    res = client.post("/api/export", json={"thread_id": "contact:99999", "format": "jsonl"})
    assert res.status_code == 400


def test_export_download_blocks_path_traversal(client):
    for name in ("..%2F..%2Fsettings.json", ".hidden", "..%2Funified.db"):
        res = client.get(f"/api/exports/{name}")
        assert res.status_code in (400, 404), name


def test_jobs_list(client):
    data = client.get("/api/jobs").json()
    assert "items" in data
    assert client.get("/api/jobs/deadbeef").status_code == 404
