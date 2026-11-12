"""HTTP API。前端只跟这里打交道，不直接碰数据库。"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import contacts as contacts_mod
from . import voice as voice_mod
from . import db, jobs, paths, services, settings, threads
from .exporters import chatlab

VERSION = "0.1.0"

router = APIRouter(prefix="/api")


# ── 请求体 ──────────────────────────────────────────────────────────

class MergeBody(BaseModel):
    primary_id: int
    secondary_id: int
    display_name: str | None = None


class UnmergeBody(BaseModel):
    platform: str


class RenameBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    note: str | None = None


class WeChatImportBody(BaseModel):
    account_dir: str | None = None


class DouyinImportBody(BaseModel):
    chat_db: str | None = None
    media_dir: str | None = None
    copy_media: bool = True


class CollectBody(BaseModel):
    filters: str | None = None
    incremental: bool = True


class ExportBody(BaseModel):
    thread_id: str
    format: Literal["json", "jsonl", "markdown"] = "jsonl"
    platform: str | None = None


class VoiceImportBody(BaseModel):
    path: str


class SettingsBody(BaseModel):
    theme: Literal["dark", "light", "warm", "purple"] | None = None
    density: Literal["comfortable", "compact"] | None = None
    page_size: int | None = Field(default=None, ge=20, le=300)
    timezone_offset: float | None = Field(default=None, ge=-12, le=14)


# ── 基础 ────────────────────────────────────────────────────────────

@router.get("/health")
def health() -> dict[str, Any]:
    stats = db.stats()
    return {
        "ok": True,
        "version": VERSION,
        "first_run": stats["messages"] == 0,
        "data_dir": str(paths.DATA_DIR),
    }


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    return db.stats()


# ── 会话与消息 ──────────────────────────────────────────────────────

@router.get("/threads")
def list_threads(
    platform: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return threads.list_threads(platform=platform, q=q, limit=limit, offset=offset)


@router.get("/threads/{thread_id}")
def thread_detail(thread_id: str) -> dict[str, Any]:
    info = threads.thread_info(thread_id)
    if not info:
        raise HTTPException(404, "会话不存在")
    return info


@router.get("/threads/{thread_id}/messages")
def thread_messages(
    thread_id: str,
    limit: int = Query(60, ge=1, le=500),
    before_ts: int | None = Query(None),
    after_ts: int | None = Query(None),
    around_ts: int | None = Query(None),
    platform: str | None = Query(None),
) -> dict[str, Any]:
    return threads.thread_messages(
        thread_id,
        limit=limit,
        before_ts=before_ts,
        after_ts=after_ts,
        around_ts=around_ts,
        platform=platform,
    )


@router.get("/threads/{thread_id}/days")
def thread_days(thread_id: str, platform: str | None = Query(None)) -> dict[str, Any]:
    return {"items": threads.day_counts(thread_id, platform=platform)}


@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    platform: str | None = Query(None),
    thread_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    return threads.search_messages(q, platform=platform, thread_id=thread_id, limit=limit)


# ── 联系人合并 ──────────────────────────────────────────────────────

@router.get("/contacts")
def list_contacts(
    q: str | None = Query(None),
    merged: bool | None = Query(None),
) -> dict[str, Any]:
    return {"items": contacts_mod.list_contacts(q=q, merged=merged)}


@router.post("/contacts/merge")
def merge_contacts(body: MergeBody) -> dict[str, Any]:
    try:
        return contacts_mod.merge_contacts(
            body.primary_id, body.secondary_id, display_name=body.display_name
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/contacts/{contact_id}/unmerge")
def unmerge_contact(contact_id: int, body: UnmergeBody) -> dict[str, Any]:
    try:
        return contacts_mod.unmerge_contact(contact_id, body.platform)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/contacts/{contact_id}")
def rename_contact(contact_id: int, body: RenameBody) -> dict[str, Any]:
    try:
        return contacts_mod.rename_contact(contact_id, body.display_name, note=body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ── 设置 ────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings() -> dict[str, Any]:
    return settings.load()


@router.patch("/settings")
def patch_settings(body: SettingsBody) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return settings.save(patch)


# ── 数据源与导入 ────────────────────────────────────────────────────

@router.get("/sources")
def sources() -> dict[str, Any]:
    return {
        "wechat_accounts": services.wechat_accounts(),
        "douyin": services.douyin_source(),
        "engines": services.engine_status(),
        "data_dir": str(paths.DATA_DIR),
    }


@router.post("/wechat/decrypt")
def wechat_decrypt() -> dict[str, Any]:
    try:
        job = services.start_wechat_decrypt()
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job": job.to_dict(with_log=False)}


@router.post("/wechat/refresh")
def wechat_refresh() -> dict[str, Any]:
    try:
        job = services.start_wechat_refresh()
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job": job.to_dict(with_log=False)}


@router.post("/import/wechat")
def import_wechat(body: WeChatImportBody) -> dict[str, Any]:
    try:
        job = services.start_wechat_import(body.account_dir)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job": job.to_dict(with_log=False)}


@router.post("/import/douyin")
def import_douyin(body: DouyinImportBody) -> dict[str, Any]:
    try:
        job = services.start_douyin_import(
            body.chat_db, media_dir=body.media_dir, copy_media=body.copy_media
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job": job.to_dict(with_log=False)}


@router.post("/douyin/collect")
def douyin_collect(body: CollectBody) -> dict[str, Any]:
    job = services.start_douyin_collect(filters=body.filters, incremental=body.incremental)
    return {"job": job.to_dict(with_log=False)}


@router.post("/douyin/refresh")
def douyin_refresh(body: CollectBody) -> dict[str, Any]:
    job = services.start_douyin_refresh(filters=body.filters, incremental=body.incremental)
    return {"job": job.to_dict(with_log=False)}


# ── 任务 ────────────────────────────────────────────────────────────

@router.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return {"items": jobs.list_jobs(limit)}


@router.get("/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    return {"ok": jobs.cancel(job_id)}


# ── 语音 ────────────────────────────────────────────────────────────

@router.get("/messages/{msg_id}/audio")
def message_audio(msg_id: int) -> FileResponse:
    """播放语音。SILK 会按需转成 16k WAV 并缓存，只转一次。"""
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT id, platform, media_path, duration FROM messages WHERE id=?",
            (msg_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "消息不存在")
    if not row["media_path"]:
        raise HTTPException(404, "这条消息没有语音文件")
    abs_path = voice_mod.resolve_media_path(row["media_path"])
    if not abs_path:
        raise HTTPException(404, "语音文件不存在")

    suffix = abs_path.suffix.lower()
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    # 已经是浏览器能直接播的格式（抖音的 mpeg、演示数据的 wav 等）
    passthrough = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".mpeg": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
    }
    if suffix in passthrough:
        if suffix == ".wav" and not row["duration"]:
            duration = voice_mod.wav_duration(abs_path)
            if duration:
                try:
                    voice_mod.set_duration(msg_id, duration)
                except Exception:  # noqa: BLE001
                    pass
        return FileResponse(abs_path, media_type=passthrough[suffix], headers=headers)
    if suffix == ".silk":
        try:
            wav, duration = voice_mod.transcode_silk(abs_path, str(msg_id))
        except Exception as exc:  # noqa: BLE001 - 回复具体的失败原因
            raise HTTPException(500, f"语音转码失败：{exc}") from exc
        if duration and not row["duration"]:
            try:
                voice_mod.set_duration(msg_id, duration)
            except Exception:  # noqa: BLE001 - 补时长失败不影响播放
                pass
        return FileResponse(wav, media_type="audio/wav", headers=headers)
    raise HTTPException(415, f"不支持的音频格式：{suffix}")


@router.post("/voice/import-jsonl")
def import_voice_jsonl(body: VoiceImportBody) -> dict[str, Any]:
    """回填旧项目 voice_api.jsonl 里的微信语音转写。"""
    try:
        return voice_mod.import_legacy_transcripts(body.path)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc


# ── 导出 ────────────────────────────────────────────────────────────

@router.post("/export")
def export(body: ExportBody) -> dict[str, Any]:
    try:
        if body.format == "markdown":
            target = chatlab.export_markdown(body.thread_id, platform=body.platform)
        else:
            target = chatlab.export_thread(
                body.thread_id, fmt=body.format, platform=body.platform
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "name": target.name,
        "path": str(target),
        "size": target.stat().st_size,
        "download": f"/api/exports/{target.name}",
    }


@router.get("/exports")
def list_exports() -> dict[str, Any]:
    return {"items": chatlab.list_exports()}


@router.get("/exports/{name}")
def download_export(name: str) -> FileResponse:
    # 只允许导出目录下的文件名，杜绝路径穿越
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "非法文件名")
    target = (paths.EXPORT_DIR / name).resolve()
    if target.parent != paths.EXPORT_DIR.resolve() or not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(target, filename=name)
