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
        "autosync_wechat_decrypt": False,
        "douyin_scope_mode": "include",
        "douyin_scope_names": ["安心.", "听风逝夜"],
    }).json()
    assert res["autosync_enabled"] is True
    assert res["autosync_interval_minutes"] == 15
    assert res["autosync_wechat_decrypt"] is False
    assert res["douyin_scope_mode"] == "include"
    assert res["douyin_scope_names"] == ["安心.", "听风逝夜"]
    assert client.patch("/api/settings", json={
        "autosync_interval_minutes": 0,
    }).status_code == 400
    assert client.patch("/api/settings", json={
        "douyin_scope_mode": "bogus",
    }).status_code == 400


def test_incremental_update_without_sources(library, monkeypatch, tmp_path):
    from app import settings

    settings.save({"autosync_wechat_decrypt": False})
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

def test_incremental_scope_passed(library, monkeypatch, tmp_path):
    from app import settings

    settings.save({
        "autosync_wechat_decrypt": False,
        "douyin_scope_mode": "include",
        "douyin_scope_names": ["安心."],
    })
    source = tmp_path / "chat.db"
    source.write_bytes(b"")
    monkeypatch.setattr(services.paths, "DOUYIN_DB", source)
    monkeypatch.setattr(services.wechat_importer, "detect_accounts", lambda _p: [])
    seen = {}

    def fake_import(*args, **kwargs):
        seen.update(kwargs)
        return {"platform": "douyin", "messages_added": 0, "messages_skipped": 0}

    monkeypatch.setattr(services.douyin_importer, "import_chat_db", fake_import)
    job = services.start_incremental_update(trigger="test")
    done = _wait(job)
    assert done.status == "ok"
    assert seen["only_names"] == ["安心."]
    assert seen["exclude_names"] is None
