"""抖音导入器：把上游引擎产出的 ``chat.db`` 读进统一库。

上游引擎（``engines/douyin``）负责抓取并落盘到自己的 schema，我们不复制它
的网络代码，只做「读库 → 统一模型」这一层，所以上游升级时这一层几乎不用动。

媒体约定
--------
上游把文件存在 ``data/media/{images,emoji,voice,videos,avatars}/`` 下，
``messages.media_local_path`` 是相对路径。导入后我们存成 ``douyin/<相对路径>``，
统一库的媒体根目录就是 ``data/media/``，两边天然对齐。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .. import db, paths
from ..jobs import JobCancelled

Progress = Callable[[dict[str, Any]], None] | None

KIND_LABELS = {
    "image": "[图片]",
    "voice": "[语音]",
    "video": "[视频]",
    "sticker": "[表情]",
    "share": "[分享]",
    "system": "[系统消息]",
    "forward": "[合并转发]",
    "other": "[消息]",
}

VOICE_EXT = {".mp3", ".mpeg", ".m4a", ".aac", ".silk", ".amr", ".wav", ".ogg"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}



def _json_obj(raw: Any) -> dict | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def guess_self_uid(source: sqlite3.Connection) -> str | None:
    """本人 uid：在几乎所有私聊 conv_id 里都出现的那一个。

    私聊 conv_id 形如 ``0:1:uidA:uidB``；群聊是纯数字，不参与统计。
    """
    counts: dict[str, int] = {}
    convs = [
        r["conv_id"]
        for r in source.execute("SELECT conv_id FROM conversations").fetchall()
        if ":" in (r["conv_id"] or "")
    ]
    for conv_id in convs:
        parts = [p for p in conv_id.split(":")[2:] if p.isdigit() and len(p) > 8]
        for uid in set(parts):
            counts[uid] = counts.get(uid, 0) + 1
    if not counts:
        return None
    total = len(convs)
    best, hits = max(counts.items(), key=lambda kv: kv[1])
    # 正常情况下本人出现在每个私聊会话里；出现率过低就不要瞎猜
    return best if hits >= max(1, total * 0.6) else None


def private_partner(conv_id: str, self_uid: str | None) -> str | None:
    """从 ``0:1:uidA:uidB`` 里取出「对方」的 uid。"""
    parts = [p for p in (conv_id or "").split(":")[2:] if p.isdigit() and len(p) > 8]
    for part in parts:
        if part != self_uid:
            return part
    return parts[0] if parts else None



def classify(
    msg_type: int,
    content: str | None,
    media_local_path: str | None,
    duration: float | None = None,
) -> tuple[str, str, float | None]:
    """返回 (kind, 展示文本, 时长)。"""
    path = (media_local_path or "").replace("\\", "/")
    ext = os.path.splitext(path)[1].lower()
    text = (content or "").strip()
    obj = _json_obj(content)

    # ① 本地媒体文件是最强信号
    if path.startswith("emoji/"):
        return "sticker", text or KIND_LABELS["sticker"], duration
    if path.startswith("voice/") or ext in VOICE_EXT:
        return "voice", text or KIND_LABELS["voice"], duration
    if path.startswith("video/") or ext in VIDEO_EXT:
        return "video", text or KIND_LABELS["video"], duration
    if path.startswith(("images/", "image/")) or ext in IMAGE_EXT:
        return "image", text or KIND_LABELS["image"], duration

    # ② 再看内容里的 JSON 字段
    if obj:
        if "tips" in obj and "aweType" in obj:
            return "system", text or KIND_LABELS["system"], duration
        if obj.get("static_image") or obj.get("emoji"):
            return "sticker", text or KIND_LABELS["sticker"], duration
        if obj.get("video"):
            return "video", text or KIND_LABELS["video"], duration
        if obj.get("check_pics") or obj.get("inline_pic") is not None:
            return "image", text or KIND_LABELS["image"], duration
        if obj.get("content_title") or obj.get("item_id") or obj.get("aweme_id"):
            title = obj.get("content_title") or text
            return "share", title or KIND_LABELS["share"], duration

    # ③ 最后按 msg_type 兜底
    if msg_type == 0:
        return ("system", text or KIND_LABELS["system"], duration) if text else (
            "other", KIND_LABELS["other"], duration
        )
    if msg_type in (2, 7):
        return "sticker", text or KIND_LABELS["sticker"], duration
    if msg_type == 3:
        return "image", text or KIND_LABELS["image"], duration
    if msg_type == 4:
        return "share", text or KIND_LABELS["share"], duration
    if msg_type == 5:
        return "video", text or KIND_LABELS["video"], duration
    return "text" if text else "other", text or KIND_LABELS["other"], duration


def _parse_duration(content: str | None, raw: dict | None) -> float | None:
    if raw:
        for key in ("duration", "voice_duration", "duration_ms"):
            value = raw.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return round(value / 1000, 1) if key == "duration_ms" else float(value)
    m = re.search(r"(\d+(?:\.\d+)?)\s*[秒s]", content or "")
    return float(m.group(1)) if m else None


def _reply_preview(ref_msg: Any) -> str | None:
    if not ref_msg:
        return None
    obj = _json_obj(ref_msg)
    if not obj:
        return None
    for key in ("content", "text", "preview"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return None


def _copy_media_tree(src: Path, dst: Path, progress: Progress) -> int:
    """把上游媒体目录合并进 data/media/douyin。返回复制的文件数。"""
    if not src.is_dir():
        return 0
    try:
        if dst.exists() and src.resolve() == dst.resolve():
            return 0
    except OSError:
        pass
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            target = dst / rel / name
            if target.exists():
                continue
            try:
                shutil.copy2(Path(root) / name, target)
                copied += 1
            except OSError:
                continue
        if progress and copied and copied % 500 == 0:
            progress({"stage": "复制抖音媒体", "copied": copied})
    return copied


def _participant_uids(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def import_chat_db(
    chat_db: Path | str,
    *,
    media_src: Path | str | None = None,
    copy_media: bool = True,
    progress: Progress = None,
    limit_messages: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    chat_db = Path(chat_db)
    if not chat_db.exists():
        raise FileNotFoundError(f"找不到抖音数据库: {chat_db}")

    src = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    started = time.time()
    self_uid = guess_self_uid(src)
    report: dict[str, Any] = {
        "platform": "douyin",
        "source": str(chat_db),
        "account": self_uid or "",
        "conversations_added": 0,
        "messages_added": 0,
        "messages_skipped": 0,
        "started_at": int(started),
    }

    users = {
        r["uid"]: dict(r) for r in src.execute("SELECT uid, nickname, avatar_url, unique_id FROM users")
    }
    transcriptions = {
        r["msg_id"]: r["text_result"]
        for r in src.execute(
            "SELECT msg_id, text_result FROM voice_transcriptions WHERE status='success'"
        )
        if r["text_result"]
    }

    if media_src and copy_media:
        media_src_path = Path(media_src)
        copied = _copy_media_tree(media_src_path, paths.DOUYIN_MEDIA, progress)
        report["media_copied"] = copied

    with db.session() as conn:
        run_id = conn.execute(
            "INSERT INTO import_runs(platform, source, mode, status, started_at)"
            " VALUES ('douyin', ?, 'full', 'running', ?)",
            (str(chat_db), int(started)),
        ).lastrowid

        conv_map: dict[str, int] = {}
        touched: set[int] = set()
        for row in src.execute("SELECT * FROM conversations"):
            conv_id = row["conv_id"]
            conv_type = int(row["conv_type"] or 1)
            title = (row["name"] or "").strip()
            avatar_rel = row["avatar_url"]
            # 上游约定：群聊 conv_id 是纯数字，私聊是 0:1:uidA:uidB
            is_group = str(conv_id).isdigit()
            kind = "group" if is_group or conv_type == 2 else "private"

            other_uid = None if kind == "group" else private_partner(conv_id, self_uid)
            other = users.get(other_uid or "", {})
            if not title:
                title = (other.get("nickname") or "").strip() or ("群聊" if kind == "group" else (other_uid or conv_id))

            avatar = None
            if avatar_rel:
                avatar = "douyin/" + avatar_rel.replace("\\", "/").lstrip("/")

            contact_id = None
            if kind == "private" and other_uid:
                ident = conn.execute(
                    "SELECT contact_id FROM identities WHERE platform='douyin'"
                    " AND platform_uid=?",
                    (other_uid,),
                ).fetchone()
                if ident:
                    contact_id = int(ident["contact_id"])
                else:
                    contact_id = db.ensure_contact(conn, title, avatar_url=avatar)
                    db.ensure_identity(
                        conn, contact_id, "douyin", other_uid,
                        platform_name=title, avatar_url=avatar,
                        unique_id=other.get("unique_id"),
                    )

            existed = conn.execute(
                "SELECT 1 FROM conversations WHERE platform='douyin' AND platform_conv_id=?",
                (conv_id,),
            ).fetchone()
            cid = db.ensure_conversation(
                conn, "douyin", conv_id,
                kind=kind, title=title, contact_id=contact_id, avatar_url=avatar,
                self_uid=self_uid,
                extra=json.dumps(
                    {
                        "conv_type": conv_type,
                        "participant_uids": row["participant_uids"],
                        "members": [
                            {
                                "uid": uid,
                                "nickname": (users.get(uid) or {}).get("nickname"),
                                "unique_id": (users.get(uid) or {}).get("unique_id"),
                                "avatar_url": (users.get(uid) or {}).get("avatar_url"),
                            }
                            for uid in _participant_uids(row["participant_uids"])[:300]
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
            if not existed:
                report["conversations_added"] += 1
            conv_map[conv_id] = cid
            touched.add(cid)

        total = src.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if progress:
            progress({"stage": "抖音导入", "total": total, "messages": 0})

        def finish(status: str) -> None:
            report["elapsed"] = round(time.time() - started, 1)
            report["finished_at"] = int(time.time())
            db.refresh_conversation_stats(conn, touched)
            conn.execute(
                "UPDATE import_runs SET status=?, finished_at=?, conversations_added=?,"
                " messages_added=?, messages_skipped=?, detail=? WHERE id=?",
                (
                    status,
                    report["finished_at"],
                    report["conversations_added"],
                    report["messages_added"],
                    report["messages_skipped"],
                    json.dumps(report, ensure_ascii=False),
                    run_id,
                ),
            )
            conn.commit()

        processed = 0
        try:
            if should_cancel and should_cancel():
                raise JobCancelled()
            for row in src.execute(
                "SELECT msg_id, conv_id, sender_uid, sender_name, content, msg_type,"
                " media_url, media_local_path, timestamp, seq, raw_data, ref_msg"
                " FROM messages ORDER BY conv_id, seq, timestamp"
            ):
                cid = conv_map.get(row["conv_id"])
                if cid is None:
                    continue
                processed += 1
                if should_cancel and processed % 2000 == 0 and should_cancel():
                    raise JobCancelled()

                raw = _json_obj(row["raw_data"]) or {}
                sender_uid = row["sender_uid"]
                is_self = bool(self_uid) and sender_uid == self_uid
                sender_name = (row["sender_name"] or "").strip() or (
                    users.get(sender_uid or "", {}).get("nickname") or ""
                )

                kind, text, duration = classify(
                    int(row["msg_type"] or 0),
                    row["content"],
                    row["media_local_path"],
                    _parse_duration(row["content"], raw),
                )
                if kind == "voice":
                    transcript = transcriptions.get(row["msg_id"])
                    if transcript:
                        text = transcript

                media_rel = None
                if row["media_local_path"]:
                    media_rel = "douyin/" + row["media_local_path"].replace("\\", "/").lstrip("/")

                inserted = db.add_message(
                    conn, cid, "douyin", row["msg_id"],
                    sender_uid=sender_uid,
                    sender_name="我" if is_self else sender_name,
                    is_self=is_self,
                    ts=int(row["timestamp"] or 0),
                    kind=kind,
                    text=text,
                    media_path=media_rel,
                    media_url=None,
                    duration=duration,
                    reply_to=_reply_preview(row["ref_msg"]),
                    seq=int(row["seq"] or 0) or None,
                    extra=json.dumps({"msg_type": int(row["msg_type"] or 0)},
                                     ensure_ascii=False),
                )
                if inserted:
                    report["messages_added"] += 1
                else:
                    report["messages_skipped"] += 1

                if limit_messages and report["messages_added"] >= limit_messages:
                    break

                if report["messages_added"] and report["messages_added"] % 20000 == 0:
                    conn.commit()
                    if progress:
                        progress({"stage": "抖音导入", "total": total,
                                  "messages": report["messages_added"]})
        except JobCancelled:
            report["cancelled"] = True
            finish("cancelled")
            raise

        finish("ok")

    src.close()

    if self_uid:
        from .. import settings as settings_mod

        if not settings_mod.load().get("douyin_self_uid"):
            settings_mod.save({"douyin_self_uid": self_uid})
    return report
