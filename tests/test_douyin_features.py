"""抖音：结构化进度、失败重试、媒体开关、ASR 兜底。"""
from __future__ import annotations

from app import asr, db, secrets, services, settings


class FakeJob:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.progress: dict = {}

    def write(self, line: str) -> None:
        self.lines.append(str(line))

    def update(self, payload: dict) -> None:
        self.progress = dict(payload)

    def cancelled(self) -> bool:
        return False


def test_douyin_progress_parses_lines():
    job = FakeJob()
    progress = services.DouyinProgress(job)
    progress("[+] 共发现 12 个会话")
    progress("[1/12] 小林 (最后活跃: 刚刚)")
    assert progress.total == 12 and progress.done == 1
    progress("  [!] 错误: 页面超时")
    assert progress.failed == ["小林"]
    progress("[2/12] 阿杰 (最后活跃: 昨天)")
    assert progress.current == "阿杰"
    assert job.progress["done"] == 2


def test_douyin_collect_cmd_has_media_flag(monkeypatch):
    monkeypatch.setattr(
        services, "browser_status",
        lambda: {"playwright": True, "channel": "msedge", "installed": True},
    )
    cmd = services._douyin_collect_cmd("A,B", True, True)
    assert "--download-images" in cmd
    assert "--incremental" in cmd
    assert "--filter" in cmd

    cmd2 = services._douyin_collect_cmd(None, False, False)
    assert "--download-images" not in cmd2
    assert "--incremental" not in cmd2


def test_douyin_failures_endpoint(client, library):
    data = client.get("/api/douyin/failures").json()
    assert isinstance(data["items"], list)


def test_auto_transcribe_wechat_only_when_douyin_fallback_off(library, monkeypatch):
    secrets.save({"tencent": {"secret_id": "sid", "secret_key": "skey"}})
    settings.save({
        "asr_engine": "tencent",
        "asr_auto_after_import": True,
        "asr_douyin_fallback": False,
    })
    calls = []
    monkeypatch.setattr(asr, "_tencent_recognize",
                        lambda path, *, retries=3: calls.append(1) or "ok")

    stats = asr.auto_transcribe(FakeJob())
    assert stats is not None
    # 演示库微信语音 5 条，抖音 4 条；关掉兜底只应处理微信
    assert stats["total"] == 5
    assert len(calls) == 5


def test_auto_transcribe_includes_douyin_by_default(library, monkeypatch):
    secrets.save({"tencent": {"secret_id": "sid", "secret_key": "skey"}})
    settings.save({
        "asr_engine": "tencent",
        "asr_auto_after_import": True,
        "asr_douyin_fallback": True,
    })
    monkeypatch.setattr(asr, "_tencent_recognize",
                        lambda path, *, retries=3: "ok")
    stats = asr.auto_transcribe(FakeJob())
    assert stats is not None
    assert stats["total"] > 5


def test_douyin_group_members_have_profiles(client, library):
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE platform='douyin' AND kind='group' LIMIT 1"
        ).fetchone()
    assert row is not None
    info = client.get(f"/api/threads/conv:{row['id']}").json()
    members = info.get("members") or []
    assert members
    assert any(m.get("unique_id") for m in members)
