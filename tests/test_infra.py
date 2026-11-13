"""日志脱敏、版本检查、系统主题与设置。"""
from __future__ import annotations

import json
from pathlib import Path

from app import jobs, redact, updates


def test_redact_windows_user_path():
    text = r"C:\Users\alice\AppData\Local\Temp\x.log"
    out = redact.redact(text)
    assert "<user>" in out
    assert "alice" not in out


def test_redact_unix_home():
    out = redact.redact("/home/bob/chat/data.db")
    assert "bob" not in out and "<user>" in out


def test_redact_app_root():
    out = redact.redact(str(redact.ROOT / "data" / "x.json"))
    assert "<app>" in out


def test_job_log_is_redacted():
    job = jobs.Job("test", "测试")
    job.write(r"读取 C:\Users\alice\secret\file.db")
    assert "alice" not in job.log[-1]
    assert "<user>" in job.log[-1]


def test_parse_version():
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.parse_version("2.0") == (2, 0, 0)
    assert updates.parse_version("1.10.0") > updates.parse_version("1.9.9")


def test_check_reads_manifest(tmp_path):
    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps({
        "version": "9.9.9",
        "notes": "新功能",
        "url": "https://example.com/download",
    }), encoding="utf-8")
    result = updates.check(manifest.as_uri(), current="1.0.0")
    assert result["checked"] is True
    assert result["available"] is True
    assert result["latest"] == "9.9.9"
    assert result["notes"] == "新功能"


def test_check_same_version(tmp_path):
    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps({"tag_name": "v1.1.0"}), encoding="utf-8")
    result = updates.check(manifest.as_uri(), current="1.1.0")
    assert result["available"] is False


def test_check_without_url():
    result = updates.check("")
    assert result["checked"] is False
    assert "没有配置" in result["reason"]


def test_version_endpoint(client, library):
    data = client.get("/api/version").json()
    assert data["version"] == updates.APP_VERSION


def test_update_check_endpoint(client, library, tmp_path):
    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps({"version": "99.0.0"}), encoding="utf-8")
    data = client.get("/api/update/check", params={"url": manifest.as_uri()}).json()
    assert data["available"] is True


def test_settings_language(client, library):
    assert client.patch("/api/settings", json={"language": "en"}).json()["language"] == "en"
    assert client.patch("/api/settings", json={"language": "fr"}).status_code == 400


def test_settings_accepts_system_theme(client, library):
    res = client.patch("/api/settings", json={"theme": "system"})
    assert res.status_code == 200
    assert res.json()["theme"] == "system"


def test_themes_builtin_contains_system(client, library):
    data = client.get("/api/themes").json()
    assert "system" in data["builtin"]
