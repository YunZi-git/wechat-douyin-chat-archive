"""语音识别：腾讯云一句话识别 + 本地 faster-whisper。

统一入口是 :func:`run_batch`（由后台任务调用）：

1. 按 scope 选出候选语音消息（待转写 / 失败重试 / 全部重跑 / 指定消息）
2. 把 SILK 转成 WAV（复用 `voice.transcode_silk` 的缓存）
3. 交给对应引擎识别
4. 结果写 `voice_transcriptions`

云端走腾讯云 `SentenceRecognition`（`16k_zh`，按次计费，约 1 元/千次）；
本地走 faster-whisper（CPU int8，首次使用会下载模型，之后离线可用）。

密钥只从 `app.secrets` 读，本模块不接触磁盘上的明文配置。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence, Any, Callable, Iterable

from . import db, paths, secrets, settings
from . import voice as voice_mod
from .jobs import JobCancelled

TENCENT_HOST = "asr.tencentcloudapi.com"
TENCENT_SERVICE = "asr"
TENCENT_ACTION = "SentenceRecognition"
TENCENT_VERSION = "2019-06-14"

ENGINES = ("tencent", "local")
SCOPES = ("pending", "failed", "all")

LOCAL_MODEL_CHOICES = ("base", "small", "medium")
AUDIO_FORMATS = {".wav": "wav", ".mp3": "mp3", ".mpeg": "mp3", ".m4a": "m4a"}

_local_lock = threading.Lock()
_local_models: dict[str, Any] = {}


class AsrError(RuntimeError):
    """识别失败（可展示给用户的中文原因）。"""


# ── 引擎状态 ────────────────────────────────────────────────────────

def _local_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def local_ready() -> bool:
    return _local_available()


def engine_info() -> dict[str, Any]:
    """给界面看的引擎可用性。"""
    sid, skey, region = secrets.tencent_credentials()
    cfg = settings.load()
    return {
        "tencent": {
            "available": bool(sid and skey),
            "region": region,
            "configured": bool(sid and skey),
            "billing": "约 1 元 / 千次（按调用次数计费）",
        },
        "local": {
            "available": _local_available(),
            "model": cfg.get("whisper_model", "small"),
            "models": list(LOCAL_MODEL_CHOICES),
            "device": "CPU (int8)",
            "endpoint": cfg.get("whisper_endpoint", ""),
        },
        "current": cfg.get("asr_engine", "off"),
        "auto": bool(cfg.get("asr_auto_after_import")),
        "douyin_fallback": bool(cfg.get("asr_douyin_fallback", True)),
        "concurrency": int(cfg.get("asr_concurrency", 2)),
    }


def counts() -> dict[str, int]:
    """按状态统计语音消息，供任务面板显示。"""
    base = ("FROM messages m LEFT JOIN voice_transcriptions vt"
            " ON vt.message_id = m.id WHERE m.kind='voice'")

    with db.session(readonly=True) as conn:
        def one(extra: str = "") -> int:
            return int(conn.execute(f"SELECT COUNT(*) {base} {extra}").fetchone()[0])

        return {
            "total": one(),
            "with_audio": one(" AND m.media_path IS NOT NULL"),
            "pending": one(" AND m.media_path IS NOT NULL AND vt.message_id IS NULL"),
            "failed": one(
                " AND m.media_path IS NOT NULL AND (vt.status IN ('failed','skipped')"
                " OR (vt.status='success' AND (vt.text IS NULL OR vt.text='')))"
            ),
            "success": one(
                " AND vt.status='success' AND vt.text IS NOT NULL AND vt.text != ''"
            ),
        }


# ── 腾讯云 ──────────────────────────────────────────────────────────

def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _tencent_call(secret_id: str, secret_key: str, region: str,
                  params: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    """TC3-HMAC-SHA256 签名并调用，返回 Response 对象。"""
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))

    content_type = "application/json; charset=utf-8"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{TENCENT_HOST}\n"
        f"x-tc-action:{TENCENT_ACTION.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join([
        "POST", "/", "", canonical_headers, signed_headers, hashed_payload,
    ])
    scope = f"{date}/{TENCENT_SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256", str(ts), scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    key = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    key = _hmac(key, TENCENT_SERVICE)
    key = _hmac(key, "tc3_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "Authorization": (f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
                          f"SignedHeaders={signed_headers}, Signature={signature}"),
        "Content-Type": content_type,
        "Host": TENCENT_HOST,
        "X-TC-Action": TENCENT_ACTION,
        "X-TC-Version": TENCENT_VERSION,
        "X-TC-Timestamp": str(ts),
        "X-TC-Region": region,
    }
    request = urllib.request.Request(
        "https://" + TENCENT_HOST, data=payload.encode("utf-8"),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response).get("Response", {})


def _tencent_recognize(wav_path: Path, *, retries: int = 3) -> str:
    """返回识别文本；空字符串表示这段音频没有语音。"""
    secret_id, secret_key, region = secrets.tencent_credentials()
    if not secret_id or not secret_key:
        raise AsrError("还没有配置腾讯云 SecretId / SecretKey（设置 → 语音识别）")
    suffix = wav_path.suffix.lower()
    voice_format = AUDIO_FORMATS.get(suffix)
    if not voice_format:
        raise AsrError(f"腾讯云不支持这种音频格式：{suffix}")

    data = wav_path.read_bytes()
    params = {
        "ProjectId": 0,
        "SubServiceType": 2,
        "EngSerViceType": "16k_zh",
        "SourceType": 1,
        "VoiceFormat": voice_format,
        "Data": base64.b64encode(data).decode("ascii"),
        "DataLen": len(data),
    }

    last_error = "请求失败"
    for attempt in range(retries):
        try:
            response = _tencent_call(secret_id, secret_key, region, params)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = f"网络错误：{exc}"
            time.sleep(1 + attempt * 2)
            continue
        if "Result" in response:
            return str((response.get("Result") or {}).get("Text") or "").strip()
        error = response.get("Error") or {}
        code = str(error.get("Code") or "")
        message = str(error.get("Message") or "")
        if code in ("RequestLimitExceeded", "InternalError", "FailedOperation.ServiceIsolate"):
            last_error = f"{code}：{message}"
            time.sleep(1.5 + attempt * 2)
            continue
        if code == "AuthFailure.SignatureFailure":
            raise AsrError(
                "签名校验失败：腾讯云认为 SecretId 与 SecretKey 不匹配，"
                "或 SecretKey 已被重置/复制不完整。请到腾讯云访问管理重新复制"
                f"同一对密钥（{message}）"
            )
        if code == "AuthFailure.SignatureExpire":
            raise AsrError(
                "签名已过期：本机时间与标准时间相差超过 5 分钟，请同步系统时间"
                f"（{message}）"
            )
        if code in ("AuthFailure.SecretIdNotFound", "AuthFailure.InvalidSecretId"):
            raise AsrError(
                "SecretId 不存在或已禁用，请检查腾讯云 API 密钥管理"
                f"（{message}）"
            )
        if code == "UnauthorizedOperation":
            raise AsrError(
                "这组密钥没有语音识别权限，请给对应子账号授予 ASR 权限"
                f"（{message}）"
            )
        raise AsrError(f"{code}：{message}" if code else "腾讯云返回了未知错误")
    raise AsrError(f"重试 {retries} 次仍失败（{last_error}）")


def test_tencent() -> dict[str, Any]:
    """用一段静音 WAV 验证密钥是否可用（会消耗一次调用）。"""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(16000)
        fh.writeframes(b"\x00\x00" * 8000)  # 0.5 秒静音
    tmp = paths.DATA_DIR / "logs" / "asr_test.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(buffer.getvalue())
    try:
        text = _tencent_recognize(tmp, retries=1)
        return {"ok": True, "text": text}
    except AsrError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ── 本地 whisper ────────────────────────────────────────────────────

def _local_model():
    if not _local_available():
        raise AsrError("没有安装 faster-whisper（本地识别不可用）")

    cfg = settings.load()
    # huggingface_hub 在 import 时读取 HF_ENDPOINT，所以必须先设再导入
    endpoint = str(cfg.get("whisper_endpoint") or "").strip()
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint

    from faster_whisper import WhisperModel

    name = str(cfg.get("whisper_model") or "small")
    model_dir = str(cfg.get("whisper_model_dir") or "").strip()
    key = f"{name}|{model_dir}"
    with _local_lock:
        model = _local_models.get(key)
        if model is None:
            kwargs: dict[str, Any] = {"device": "cpu", "compute_type": "int8"}
            if model_dir:
                kwargs["download_root"] = model_dir
            model = WhisperModel(name, **kwargs)
            _local_models[key] = model
    return model


def _local_recognize(audio_path: Path) -> str:
    model = _local_model()
    with _local_lock:  # WhisperModel 不是为多线程共享设计的，串行更稳
        # vad_filter=False：VAD 需要 onnxruntime，而它只用于切静音段；
        # 关掉能少带 40MB 依赖，对短语音识别质量影响很小。
        segments, _info = model.transcribe(
            str(audio_path), language="zh", vad_filter=False, beam_size=5
        )
        return "".join(segment.text for segment in segments).strip()


# ── 消息选择与音频准备 ──────────────────────────────────────────────

def _audio_path(msg: dict[str, Any]) -> Path | None:
    abs_path = voice_mod.resolve_media_path(msg.get("media_path") or "")
    return abs_path


def _prepare_audio(msg: dict[str, Any]) -> Path:
    """把语音准备成引擎能吃的格式；SILK 会走转码缓存。"""
    path = _audio_path(msg)
    if path is None:
        raise AsrError("语音文件缺失")
    if path.suffix.lower() == ".silk":
        wav, _duration = voice_mod.transcode_silk(path, str(msg["id"]))
        return wav
    if path.suffix.lower() != ".wav":
        # 抖音语音是 mpeg；腾讯云指定了 VoiceFormat=wav，必须先转
        wav, _duration = voice_mod.convert_to_wav(path, f"asr_{msg['id']}")
        return wav
    return path


def select_messages(
    *,
    scope: str = "pending",
    conversation_id: int | None = None,
    message_ids: Iterable[int] | None = None,
    platforms: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    where = ["m.kind='voice'", "m.media_path IS NOT NULL"]
    args: list[Any] = []
    if platforms:
        where.append(f"m.platform IN ({','.join('?' for _ in platforms)})")
        args.extend(platforms)
    if message_ids is not None:
        ids = [int(i) for i in message_ids]
        if not ids:
            return []
        where.append(f"m.id IN ({','.join('?' for _ in ids)})")
        args.extend(ids)
    else:
        if scope == "pending":
            where.append("vt.message_id IS NULL")
        elif scope == "failed":
            where.append(
                "(vt.status IN ('failed','skipped')"
                " OR (vt.status='success' AND (vt.text IS NULL OR vt.text='')))"
            )
        elif scope != "all":
            raise ValueError(f"未知范围：{scope}")
    if conversation_id is not None:
        where.append("m.conversation_id = ?")
        args.append(int(conversation_id))
    sql = (
        "SELECT m.id, m.media_path, m.duration, m.conversation_id"
        " FROM messages m LEFT JOIN voice_transcriptions vt ON vt.message_id = m.id"
        f" WHERE {' AND '.join(where)} ORDER BY m.ts, m.id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    with db.session(readonly=True) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ── 批量执行 ────────────────────────────────────────────────────────

def _recognize_one(msg: dict[str, Any], engine: str) -> str:
    if engine == "tencent":
        return _tencent_recognize(_prepare_audio(msg))
    if engine == "local":
        return _local_recognize(_prepare_audio(msg))
    raise AsrError(f"未知引擎：{engine}")


def run_batch(
    job: Any,
    *,
    engine: str,
    scope: str = "pending",
    conversation_id: int | None = None,
    message_ids: Iterable[int] | None = None,
    platforms: Sequence[str] | None = None,
    limit: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """批量识别。`job` 需要提供 write/update/cancelled（见 app.jobs.Job）。"""
    if engine not in ENGINES:
        raise AsrError(f"未知引擎：{engine}")
    messages = select_messages(
        scope=scope, conversation_id=conversation_id,
        message_ids=message_ids, platforms=platforms, limit=limit,
    )
    total = len(messages)
    stats = {"engine": engine, "scope": scope, "total": total,
             "succeeded": 0, "failed": 0, "empty": 0, "skipped": 0}
    if not total:
        job.write("[i] 没有需要识别的语音")
        return stats

    job.write(f"[i] 引擎 {engine}，范围 {scope}，共 {total} 条")
    cfg = settings.load()
    concurrency = max(1, min(int(cfg.get("asr_concurrency", 2)), 100))
    if engine == "local":
        concurrency = 1  # 本地模型串行，避免抢占 CPU / 线程安全问题

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    def one(msg: dict[str, Any]) -> tuple[int, str, str | None]:
        """返回 (message_id, text, error)。"""
        try:
            return int(msg["id"]), _recognize_one(msg, engine), None
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
            return int(msg["id"]), "", f"{type(exc).__name__}: {exc}"

    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, total, concurrency):
            if cancelled():
                raise JobCancelled()
            batch = messages[start:start + concurrency]
            for message_id, text, error in pool.map(one, batch):
                done += 1
                if error:
                    stats["failed"] += 1
                    voice_mod.set_transcript(
                        message_id, engine=engine, text="", status="failed", error=error
                    )
                elif text:
                    stats["succeeded"] += 1
                    voice_mod.set_transcript(
                        message_id, engine=engine, text=text, status="success"
                    )
                else:
                    stats["empty"] += 1
                    voice_mod.set_transcript(
                        message_id, engine=engine, text="", status="success"
                    )
                job.update({
                    "done": done, "total": total,
                    "succeeded": stats["succeeded"], "failed": stats["failed"],
                    "engine": engine,
                })
                if done % 10 == 0 or done == total:
                    job.write(
                        f"[+] {done}/{total}  成功 {stats['succeeded']}  "
                        f"失败 {stats['failed']}  空 {stats['empty']}"
                    )
    stats["elapsed"] = round(time.time() - started, 1)
    job.write(
        f"[✓] 完成：成功 {stats['succeeded']}，失败 {stats['failed']}，"
        f"空文本 {stats['empty']}，用时 {stats['elapsed']}s"
    )
    return stats


def auto_transcribe(job: Any, *, limit: int = 200) -> dict[str, Any] | None:
    """导入完成后自动识别新语音（设置里开关）。"""
    cfg = settings.load()
    engine = cfg.get("asr_engine", "off")
    if not cfg.get("asr_auto_after_import") or engine not in ENGINES:
        return None
    info = engine_info()
    if not info.get(engine, {}).get("available"):
        job.write(f"[!] 自动转写跳过：{engine} 不可用（检查设置）")
        return None
    # 抖音语音优先用采集时的 native 转写；失败的（没有记录）由这里兜底
    platforms = None if cfg.get("asr_douyin_fallback", True) else ["wechat"]
    job.write(f"[i] 自动转写新语音（{engine}，最多 {limit} 条）")
    try:
        return run_batch(job, engine=engine, scope="pending", limit=limit,
                         platforms=platforms, should_cancel=job.cancelled)
    except JobCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - 自动任务失败不应让导入显示失败
        job.write(f"[!] 自动转写失败：{exc}")
        return None
