"""语音：落盘、按需转码、历史转写导入。

微信 4.x 的语音以 SILK 存在 ``media_*.db`` 的 ``VoiceInfo`` 表里，导入时
把原始 blob 原样落地（不做转码，省空间）；只有用户真的点播放，才用 pilk
转成 16 kHz WAV 并缓存。转写（本地 whisper / 腾讯云）后续也挂在这一层。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Iterable

from . import db, paths

SILK_MAGIC = b"#!SILK_V3"
RIFF_MAGIC = b"RIFF"
VOICE_CACHE = paths.MEDIA_DIR / "voice_cache"


def sniff_extension(blob: bytes) -> str:
    """按文件头判断真实格式。

    微信的语音有时带 1 个控制字节前缀（``\\x02#!SILK_V3``），所以用 find
    而不是 startswith；演示数据里也可能直接是 WAV。
    """
    if not blob:
        return ".bin"
    if blob[:4] == RIFF_MAGIC:
        return ".wav"
    if SILK_MAGIC in blob[:16]:
        return ".silk"
    head = blob[:4]
    if head[:3] == b"ID3" or head[:2] == b"\xff\xfb":
        return ".mp3"
    return ".bin"


def strip_silk_prefix(blob: bytes) -> bytes:
    """pilk 要求文件以 ``#!SILK_V3`` 开头，剥掉前面的控制字节。"""
    if blob.startswith(SILK_MAGIC):
        return blob
    idx = blob.find(SILK_MAGIC, 0, 16)
    return blob[idx:] if idx > 0 else blob


def _safe_filename(name: str) -> str:
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def voice_relative_path(account: str, media_shard: str, talker: str,
                        name: str, ext: str) -> str:
    """统一库里的语音相对路径。按 talker 的哈希分目录，避免奇怪的 wxid 字符。"""
    account_dir = _safe_filename(account) if account else "account"
    return f"wechat/{account_dir}/{media_shard}/{_safe_filename(talker)}/{name}{ext}"


def wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as fh:
            frames = fh.getnframes()
            rate = fh.getframerate() or 1
            return round(frames / rate, 1)
    except (wave.Error, OSError, EOFError):
        return None


def transcode_silk(silk_path: Path, cache_key: str) -> tuple[Path, float | None]:
    """SILK → 16k WAV，结果放缓存目录；返回 (wav 路径, 时长)。"""
    VOICE_CACHE.mkdir(parents=True, exist_ok=True)
    target = VOICE_CACHE / f"{cache_key}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target, wav_duration(target)

    try:
        import pilk
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的明确报错
        raise RuntimeError("缺少 pilk，无法解码微信语音（pip install pilk）") from exc

    raw = silk_path.read_bytes()
    payload = strip_silk_prefix(raw)

    # 并发请求同一条语音时各写各的临时文件，最后 rename 覆盖，避免互相踩踏
    nonce = uuid.uuid4().hex[:8]
    tmp_silk = VOICE_CACHE / f"{cache_key}.{nonce}.silk"
    tmp_wav = VOICE_CACHE / f"{cache_key}.{nonce}.wav"
    try:
        tmp_silk.write_bytes(payload)
        pilk.silk_to_wav(str(tmp_silk), str(tmp_wav), rate=16000)
        # 先写临时文件再改名，避免并发请求读到半个文件
        os.replace(tmp_wav, target)
    finally:
        for p in (tmp_silk, tmp_wav):
            try:
                p.unlink()
            except OSError:
                pass
    if not target.is_file() or target.stat().st_size <= 44:
        raise RuntimeError("语音解码结果为空（文件可能不完整）")
    return target, wav_duration(target)


def convert_to_wav(src: Path, cache_key: str) -> tuple[Path, float | None]:
    """任意音频 → 16k 单声道 WAV（抖音语音是 mpeg，云端识别只收 wav）。

    用 PyAV（faster-whisper 的依赖）解码，不额外依赖 ffmpeg 可执行文件。
    """
    VOICE_CACHE.mkdir(parents=True, exist_ok=True)
    target = VOICE_CACHE / f"{cache_key}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target, wav_duration(target)

    try:
        import av
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 PyAV，无法转换语音格式（pip install av）") from exc

    nonce = uuid.uuid4().hex[:8]
    tmp = VOICE_CACHE / f"{cache_key}.{nonce}.wav"
    frames = 0
    try:
        with av.open(str(src)) as container:
            if not container.streams.audio:
                raise RuntimeError("文件里没有音频流")
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            with wave.open(str(tmp), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                for frame in container.decode(stream):
                    for chunk in resampler.resample(frame):
                        out.writeframes(bytes(chunk.planes[0]))
                        frames += chunk.samples
                # 冲刷重采样器里剩下的样本
                for chunk in resampler.resample(None):
                    out.writeframes(bytes(chunk.planes[0]))
                    frames += chunk.samples
        if frames <= 0:
            raise RuntimeError("转换结果为空")
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return target, round(frames / 16000, 1)


def cache_stats() -> dict[str, int]:
    """语音缓存占了多少空间（前端设置页展示 / 清理用）。"""
    if not VOICE_CACHE.is_dir():
        return {"files": 0, "bytes": 0}
    files = 0
    size = 0
    for p in VOICE_CACHE.iterdir():
        try:
            if p.is_file():
                files += 1
                size += p.stat().st_size
        except OSError:
            continue
    return {"files": files, "bytes": size}


def clear_cache() -> dict[str, int]:
    """删掉转码缓存（不影响原始 SILK，下一次播放会重新生成）。"""
    before = cache_stats()
    if VOICE_CACHE.is_dir():
        for p in VOICE_CACHE.iterdir():
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                continue
    return {"removed_files": before["files"], "freed_bytes": before["bytes"]}


def resolve_media_path(rel_path: str) -> Path | None:
    """把库里的相对路径解析成绝对路径，并确保没有跑出 media 目录。"""
    if not rel_path:
        return None
    try:
        target = (paths.MEDIA_DIR / rel_path).resolve()
        target.relative_to(paths.MEDIA_DIR.resolve())
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def set_duration(message_id: int, duration: float) -> None:
    """首次转码后把真实时长补回消息，前端就能显示「3″」。"""
    with db.session() as conn:
        conn.execute(
            "UPDATE messages SET duration=? WHERE id=? AND duration IS NULL",
            (float(duration), int(message_id)),
        )


def _sync_fts_transcript(conn, message_id: int, text: str) -> None:
    """转写变了就更新全文索引；FTS 不可用时静默跳过。"""
    try:
        conn.execute(
            "UPDATE messages_fts SET transcript=? WHERE rowid=?",
            (text or "", int(message_id)),
        )
    except Exception:  # noqa: BLE001
        pass


def set_transcript(message_id: int, *, engine: str, text: str,
                   status: str = "success", error: str | None = None) -> None:
    with db.session() as conn:
        conn.execute(
            """INSERT INTO voice_transcriptions
                   (message_id, engine, status, text, error, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(message_id) DO UPDATE SET
                   engine=excluded.engine,
                   status=excluded.status,
                   text=excluded.text,
                   error=excluded.error,
                   updated_at=excluded.updated_at""",
            (int(message_id), engine, status, text or "", error, int(time.time())),
        )
        _sync_fts_transcript(conn, message_id, text or "")


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def import_legacy_transcripts(jsonl_path: Path | str) -> dict[str, Any]:
    """把旧项目 ``voice_api.jsonl`` 的转写回填到统一库。

    该文件的 ``id`` 与我们的 ``messages.platform_msg_id`` 格式一致
    （``message_0.db:Msg_xxx:26``），所以能直接对上。
    """
    path = Path(jsonl_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到转写文件：{path}")

    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = str(item.get("id") or "").strip()
            text = (item.get("text") or "").strip()
            if msg_id and text:
                records[msg_id] = item

    if not records:
        return {"total": 0, "matched": 0, "unmatched": 0, "updated": 0}

    total_lines = 0
    empty_text = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                total_lines += 1
    empty_text = total_lines - len(records)

    matched = 0
    updated = 0
    unmatched: list[str] = []
    with db.session() as conn:
        for chunk in _chunks(list(records.keys()), 500):
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT id, platform_msg_id FROM messages"
                f" WHERE platform='wechat' AND platform_msg_id IN ({marks})",
                chunk,
            ).fetchall()
            found = {r["platform_msg_id"]: int(r["id"]) for r in rows}
            now = int(time.time())
            for platform_id in chunk:
                message_id = found.get(platform_id)
                if message_id is None:
                    unmatched.append(platform_id)
                    continue
                matched += 1
                record = records[platform_id]
                duration = record.get("dur")
                conn.execute(
                    """INSERT INTO voice_transcriptions
                           (message_id, engine, status, text, error, updated_at)
                       VALUES (?, 'legacy', 'success', ?, NULL, ?)
                       ON CONFLICT(message_id) DO UPDATE SET
                           engine='legacy', status='success',
                           text=excluded.text, error=NULL, updated_at=excluded.updated_at""",
                    (message_id, record["text"], now),
                )
                updated += 1
                _sync_fts_transcript(conn, message_id, record["text"])
                if duration is not None:
                    conn.execute(
                        "UPDATE messages SET duration=? WHERE id=? AND duration IS NULL",
                        (float(duration), message_id),
                    )
    return {
        "lines": total_lines,
        "total": len(records),
        "empty": max(0, empty_text),
        "matched": matched,
        "unmatched": len(unmatched),
        "updated": updated,
        "unmatched_sample": unmatched[:5],
    }
