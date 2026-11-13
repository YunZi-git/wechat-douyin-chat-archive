"""HTTP API。前端只跟这里打交道，不直接碰数据库。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import contacts as contacts_mod
from . import asr
from . import updates
from . import themes as themes_mod
from . import secrets as secrets_mod
from . import voice as voice_mod
from . import wechat_media
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
    incremental: bool = False


class DouyinImportBody(BaseModel):
    chat_db: str | None = None
    media_dir: str | None = None
    copy_media: bool = True


class CollectBody(BaseModel):
    filters: str | None = None
    incremental: bool = True
    mode: Literal["all", "include", "exclude"] = "all"
    conversations: list[str] | None = None
    download_images: bool = False


class ExportBody(BaseModel):
    thread_id: str
    format: Literal["json", "jsonl", "markdown"] = "jsonl"
    platform: str | None = None


class VoiceImportBody(BaseModel):
    path: str


class AsrConfigBody(BaseModel):
    engine: Literal["off", "tencent", "local"] | None = None
    auto_after_import: bool | None = None
    concurrency: int | None = Field(default=None, ge=1, le=4)
    whisper_model: Literal["base", "small", "medium"] | None = None
    whisper_model_dir: str | None = None
    tencent_secret_id: str | None = None
    tencent_secret_key: str | None = None
    tencent_region: str | None = None


class AsrTestBody(BaseModel):
    engine: Literal["tencent", "local"]


class AsrRunBody(BaseModel):
    engine: Literal["tencent", "local"] | None = None
    scope: Literal["pending", "failed", "all"] = "pending"
    conversation_id: int | None = None
    message_ids: list[int] | None = None
    limit: int | None = Field(default=None, ge=1, le=5000)


class SettingsBody(BaseModel):
    theme: str | None = None
    update_url: str | None = None
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
    account: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return threads.list_threads(
        platform=platform, account=account, q=q, limit=limit, offset=offset
    )


@router.get("/accounts")
def list_accounts() -> dict[str, Any]:
    return {"items": threads.list_accounts()}


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


@router.get("/threads/{thread_id}/media")
def thread_media(
    thread_id: str,
    kind: str | None = Query(None),
    platform: str | None = Query(None),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return threads.thread_media(
        thread_id, kind=kind, platform=platform, limit=limit, offset=offset
    )


class MediaPrewarmBody(BaseModel):
    thread_id: str | None = None
    limit: int = 300


@router.post("/media/prewarm")
def media_prewarm(body: MediaPrewarmBody) -> dict[str, Any]:
    job = services.start_media_prewarm(thread_id=body.thread_id, limit=body.limit)
    return {"job": job.to_dict(with_log=False)}


@router.get("/threads/{thread_id}/days")
def thread_days(thread_id: str, platform: str | None = Query(None)) -> dict[str, Any]:
    return {"items": threads.day_counts(thread_id, platform=platform)}


@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    platform: str | None = Query(None),
    thread_id: str | None = Query(None),
    sender: str | None = Query(None),
    kinds: str | None = Query(None, description="逗号分隔的消息类型"),
    since_ts: int | None = Query(None, ge=0),
    until_ts: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    group: bool = Query(False),
) -> dict[str, Any]:
    kind_list = [k.strip() for k in (kinds or "").split(",") if k.strip()]
    return threads.search_messages(
        q,
        platform=platform,
        thread_id=thread_id,
        sender=sender,
        kinds=kind_list or None,
        since_ts=since_ts,
        until_ts=until_ts,
        limit=limit,
        offset=offset,
        group=group,
    )


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

@router.get("/version")
def app_version() -> dict[str, Any]:
    return {"version": updates.APP_VERSION}


@router.get("/update/check")
def update_check(url: str | None = Query(None)) -> dict[str, Any]:
    target = url or settings.load().get("update_url") or ""
    return updates.check(target)


@router.get("/themes")
def list_themes() -> dict[str, Any]:
    """151 个 StyleKit 外观 + 4 个内置主题。"""
    return themes_mod.list_themes()


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    return settings.load()


@router.patch("/settings")
def patch_settings(body: SettingsBody) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "theme" in patch and not themes_mod.is_valid(patch["theme"]):
        raise HTTPException(400, f"未知外观：{patch['theme']}")
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
def wechat_refresh(body: WeChatImportBody | None = None) -> dict[str, Any]:
    try:
        job = services.start_wechat_refresh(
            incremental=bool(body.incremental) if body else False
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job": job.to_dict(with_log=False)}


@router.post("/import/wechat")
def import_wechat(body: WeChatImportBody) -> dict[str, Any]:
    try:
        job = services.start_wechat_import(body.account_dir, incremental=body.incremental)
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


@router.get("/douyin/conversations")
def douyin_conversations() -> dict[str, Any]:
    """本地抖音库里的会话，供「采集范围」勾选。"""
    return services.douyin_conversations()


@router.get("/douyin/failures")
def douyin_failures() -> dict[str, Any]:
    return {"items": services.douyin_last_failures()}


@router.post("/douyin/collect")
def douyin_collect(body: CollectBody) -> dict[str, Any]:
    try:
        job = services.start_douyin_collect(
            filters=body.filters, incremental=body.incremental,
            mode=body.mode, conversations=body.conversations,
            download_images=body.download_images,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job": job.to_dict(with_log=False)}


@router.post("/douyin/refresh")
def douyin_refresh(body: CollectBody) -> dict[str, Any]:
    try:
        job = services.start_douyin_refresh(
            filters=body.filters, incremental=body.incremental,
            mode=body.mode, conversations=body.conversations,
            download_images=body.download_images,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
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


@router.get("/messages/{msg_id}/image")
def message_image(msg_id: int) -> FileResponse:
    """微信图片/表情按需解密；失败返回 404 + 原因。"""
    try:
        path, content_type = wechat_media.fetch_image(msg_id)
    except wechat_media.MediaUnavailable as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        path, media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/messages/{msg_id}/video")
def message_video(msg_id: int) -> FileResponse:
    try:
        path, content_type = wechat_media.fetch_video(msg_id)
    except wechat_media.MediaUnavailable as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        path, media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/voice/import-jsonl")
def import_voice_jsonl(body: VoiceImportBody) -> dict[str, Any]:
    """回填旧项目 voice_api.jsonl 里的微信语音转写。"""
    path = Path(body.path)
    if path.suffix.lower() not in (".jsonl", ".json"):
        raise HTTPException(400, "只接受 .jsonl / .json 文件")
    try:
        return voice_mod.import_legacy_transcripts(path)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/asr/status")
def asr_status() -> dict[str, Any]:
    return {
        "engines": asr.engine_info(),
        "counts": asr.counts(),
        "secrets": secrets_mod.public_view(),
    }


@router.put("/asr/config")
def asr_config(body: AsrConfigBody) -> dict[str, Any]:
    settings_patch: dict[str, Any] = {}
    if body.engine is not None:
        settings_patch["asr_engine"] = body.engine
    if body.auto_after_import is not None:
        settings_patch["asr_auto_after_import"] = body.auto_after_import
    if body.concurrency is not None:
        settings_patch["asr_concurrency"] = body.concurrency
    if body.whisper_model is not None:
        settings_patch["whisper_model"] = body.whisper_model
    if body.whisper_model_dir is not None:
        settings_patch["whisper_model_dir"] = body.whisper_model_dir
    if settings_patch:
        settings.save(settings_patch)

    secret_patch: dict[str, str] = {}
    if body.tencent_secret_id is not None:
        secret_patch["secret_id"] = body.tencent_secret_id
    if body.tencent_secret_key is not None:
        secret_patch["secret_key"] = body.tencent_secret_key
    if body.tencent_region is not None:
        secret_patch["region"] = body.tencent_region
    if secret_patch:
        secrets_mod.save({"tencent": secret_patch})

    return {
        "engines": asr.engine_info(),
        "counts": asr.counts(),
        "secrets": secrets_mod.public_view(),
    }


@router.post("/asr/test")
def asr_test(body: AsrTestBody) -> dict[str, Any]:
    if body.engine == "tencent":
        return asr.test_tencent()
    return {
        "ok": asr.local_ready(),
        "note": "本地模型会在第一次识别时下载（base≈150MB / small≈500MB / medium≈1.5GB）",
    }


@router.post("/asr/run")
def asr_run(body: AsrRunBody) -> dict[str, Any]:
    try:
        job = services.start_transcription(
            engine=body.engine,
            scope=body.scope,
            conversation_id=body.conversation_id,
            message_ids=body.message_ids,
            limit=body.limit,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job": job.to_dict(with_log=False)}


@router.post("/voice/cache/clear")
def clear_voice_cache() -> dict[str, Any]:
    """清掉 SILK 转码缓存；原始语音不受影响，下次播放会重新生成。"""
    return voice_mod.clear_cache()


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
