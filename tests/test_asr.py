"""语音识别：配置、批量、重试、自动转写。"""
from __future__ import annotations

import time

from app import asr, db, secrets, settings


class FakeJob:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.progress: dict = {}
        self.cancelled_flag = False

    def write(self, line: str) -> None:
        self.lines.append(str(line))

    def update(self, payload: dict) -> None:
        self.progress = dict(payload)

    def cancelled(self) -> bool:
        return self.cancelled_flag


def _configure_tencent(client):
    res = client.put("/api/asr/config", json={
        "engine": "tencent",
        "tencent_secret_id": "example-secret-id",
        "tencent_secret_key": "example-secret-key",
        "concurrency": 2,
    })
    assert res.status_code == 200
    return res.json()


def _wait_job(client, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("ok", "failed", "cancelled"):
            return job
        time.sleep(0.15)
    raise AssertionError("任务超时")


def test_asr_status_shape(client, library):
    data = client.get("/api/asr/status").json()
    assert {"engines", "counts", "secrets"} <= set(data)
    assert {"tencent", "local"} <= set(data["engines"])
    assert data["counts"]["with_audio"] >= 1
    assert "secret_key" not in data["secrets"]["tencent"]


def test_asr_config_masks_secrets(client, library):
    data = _configure_tencent(client)
    tencent = data["secrets"]["tencent"]
    assert tencent["configured"] is True
    assert tencent["secret_id_masked"].startswith("exam")
    assert "example-secret-key" not in str(data)
    assert settings.load()["asr_engine"] == "tencent"


def test_asr_run_pending_with_fake_cloud(client, library, monkeypatch):
    _configure_tencent(client)
    calls: list[str] = []

    def fake(path, *, retries=3):
        calls.append(path.name)
        return f"云识别-{len(calls)}"

    monkeypatch.setattr(asr, "_tencent_recognize", fake)
    expected = asr.counts()["pending"]
    assert expected >= 1
    job = client.post("/api/asr/run", json={"scope": "pending"}).json()["job"]
    done = _wait_job(client, job["id"])
    assert done["status"] == "ok"
    assert done["result"]["succeeded"] == expected
    counts = client.get("/api/asr/status").json()["counts"]
    assert counts["success"] == expected and counts["pending"] == 0


def test_asr_scope_failed_retries(client, library, monkeypatch):
    _configure_tencent(client)
    monkeypatch.setattr(asr, "_tencent_recognize", lambda path, *, retries=3: "重试成功")
    with db.session() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM messages WHERE kind='voice' LIMIT 2"
        )]
        for message_id in ids:
            conn.execute(
                "INSERT OR REPLACE INTO voice_transcriptions"
                " (message_id, engine, status, text, error, updated_at)"
                " VALUES (?, 'tencent', 'failed', '', 'boom', 0)",
                (message_id,),
            )
    assert asr.counts()["failed"] == 2
    job = client.post("/api/asr/run", json={"scope": "failed"}).json()["job"]
    done = _wait_job(client, job["id"])
    assert done["status"] == "ok"
    assert done["result"]["succeeded"] == 2
    assert asr.counts()["failed"] == 0


def test_asr_single_message_rerun(client, library, monkeypatch):
    _configure_tencent(client)
    monkeypatch.setattr(asr, "_tencent_recognize", lambda path, *, retries=3: "单条重跑")
    message_id = asr.select_messages(scope="all")[0]["id"]
    job = client.post("/api/asr/run", json={
        "scope": "all", "message_ids": [message_id]
    }).json()["job"]
    done = _wait_job(client, job["id"])
    assert done["result"]["total"] == 1
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT text FROM voice_transcriptions WHERE message_id=?", (message_id,)
        ).fetchone()
    assert row["text"] == "单条重跑"


def test_asr_rejects_unknown_engine(client, library):
    # 不存在的引擎在 schema 层就被拦下（422），不会真的起任务
    assert client.post("/api/asr/run", json={"engine": "nope"}).status_code == 422
    assert client.post("/api/asr/run", json={"engine": "local", "scope": "nope"}).status_code == 422


def test_auto_transcribe_after_import(library, monkeypatch):
    """导入后自动识别：有新语音才触发，已识别的不重复跑。"""
    secrets.save({"tencent": {"secret_id": "AKIDX", "secret_key": "KEYX"}})
    settings.save({"asr_engine": "tencent", "asr_auto_after_import": True})
    monkeypatch.setattr(asr, "_tencent_recognize", lambda path, *, retries=3: "自动转写")

    job = FakeJob()
    expected = asr.counts()["pending"]
    stats = asr.auto_transcribe(job)
    assert stats is not None
    assert stats["succeeded"] == expected

    # 第二次：没有待转写了，返回 total=0
    job2 = FakeJob()
    stats2 = asr.auto_transcribe(job2)
    assert stats2["total"] == 0


def test_auto_transcribe_disabled(library):
    settings.save({"asr_engine": "tencent", "asr_auto_after_import": False})
    assert asr.auto_transcribe(FakeJob()) is None

def test_asr_config_accepts_patch(client, library):
    res = client.patch("/api/asr/config", json={
        "tencent_secret_id": "AKIDPATCH",
        "tencent_secret_key": "KEYPATCH",
    })
    assert res.status_code == 200, res.text
    data = client.get("/api/asr/status").json()
    assert data["secrets"]["tencent"]["configured"] is True

def test_asr_concurrency_max_100(client, library):
    res = client.put("/api/asr/config", json={"concurrency": 100})
    assert res.status_code == 200, res.text
    assert res.json()["engines"]["concurrency"] == 100
    assert client.put("/api/asr/config", json={"concurrency": 101}).status_code == 422
