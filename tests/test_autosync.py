"""自动增量更新与合并页头像。"""
from __future__ import annotations

import time

from app import autosync, jobs, paths, services


def _wait(job: jobs.Job, timeout: float = 20.0) -> jobs.Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in ("ok", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError("任务超时")


def test_autosync_status(client, library):
    data = client.get("/api/autosync/status").json()
    assert {"enabled", "interval_minutes", "last_run", "running",
            "sources_available"} <= set(data)
    assert data["interval_minutes"] >= 1


def test_autosync_settings_roundtrip(client, library):
    res = client.patch("/api/settings", json={
        "autosync_enabled": True,
        "autosync_interval_minutes": 15,
    }).json()
    assert res["autosync_enabled"] is True
    assert res["autosync_interval_minutes"] == 15
    assert client.patch("/api/settings", json={
        "autosync_interval_minutes": 0,
    }).status_code == 400


def test_incremental_update_without_sources(library, monkeypatch, tmp_path):
    monkeypatch.setattr(services.wechat_importer, "detect_accounts", lambda _p: [])
    monkeypatch.setattr(paths, "DOUYIN_DB", tmp_path / "missing.db")
    monkeypatch.setattr(services, "asr", services.asr)  # keep module
    job = services.start_incremental_update(trigger="test")
    done = _wait(job)
    assert done.status == "ok"
    assert done.result["reports"] == []


def test_contacts_have_avatar_field(client, library):
    data = client.get("/api/contacts").json()
    assert data["items"]
    assert any(item.get("display_avatar") or item.get("avatar_url")
               for item in data["items"])
