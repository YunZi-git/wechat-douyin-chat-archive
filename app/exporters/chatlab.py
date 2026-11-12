"""导出：ChatLab 0.0.2（JSON/JSONL）与 Markdown。

ChatLab 是聊天分析的通用交换格式，导出后可以直接喂给 ChatLab 或别的分析工具。
合并会话（同一个人同时有微信和抖音）默认导出成一份 ``platform: "mixed"``
的文件；需要严格单平台兼容时可以在界面上选平台分别导出。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

from .. import paths, settings, threads

CHATLAB_VERSION = "0.0.2"
GENERATOR = "chat-info-workbench"

# 统一 kind → ChatLab type
CHATLAB_TYPES = {
    "text": 0,
    "image": 1,
    "sticker": 5,
    "share": 24,
    "forward": 26,
    "voice": 0,
    "video": 0,
    "card": 24,
    "location": 24,
    "call": 99,
    "system": 99,
    "revoke": 99,
    "other": 99,
}

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(value: str | None, fallback: str = "conversation") -> str:
    name = _INVALID.sub("_", (value or "").strip())
    name = re.sub(r"\s+", "_", name).strip(" ._") or fallback
    if name.upper() in _RESERVED:
        name = f"_{name}"
    encoded = name.encode("utf-8")
    if len(encoded) > 180:
        name = encoded[:180].decode("utf-8", errors="ignore")
    return name.rstrip(" ._") or fallback


def _is_self_uid(platform: str) -> str:
    cfg = settings.load()
    return cfg.get("wechat_self_wxid" if platform == "wechat" else "douyin_self_uid") or ""


def _image_data_uri(abs_path: Path, max_bytes: int = 2_000_000) -> str | None:
    try:
        if abs_path.stat().st_size > max_bytes:
            return None
        mime = mimetypes.guess_type(abs_path.name)[0] or "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(abs_path.read_bytes()).decode()
    except OSError:
        return None


def _message_content(msg: dict[str, Any]) -> str:
    kind = msg.get("kind") or "text"
    text = (msg.get("text") or "").strip()
    if kind == "text":
        return text
    if kind == "image":
        if msg.get("media_url"):
            rel = msg["media_url"].removeprefix("/media/")
            data = _image_data_uri(paths.MEDIA_DIR / rel)
            if data:
                return data
        return text or "[图片]"
    if kind == "voice":
        duration = msg.get("duration")
        label = f"[语音 {duration:g}秒]" if duration else "[语音]"
        return text.strip() if text and not text.startswith("[语音") else label
    return text or ""


def _header(thread: dict[str, Any], platforms: list[str], owner_id: str) -> dict[str, Any]:
    kind = "contact" if thread.get("thread_kind") == "contact" else "conv"
    title = thread.get("title") or "未命名"
    is_group = thread.get("kind") == "group"
    meta = {
        "name": title if is_group else f"与{title}的对话",
        "platform": platforms[0] if len(platforms) == 1 else "mixed",
        "type": "group" if is_group else "private",
        "ownerId": owner_id,
    }
    if len(platforms) > 1:
        meta["platforms"] = platforms
    if kind == "conv" and is_group:
        meta["groupId"] = str(thread.get("thread_id"))
    return {
        "chatlab": {
            "version": CHATLAB_VERSION,
            "exportedAt": int(time.time()),
            "generator": GENERATOR,
        },
        "meta": meta,
    }


def build_export(thread_id: str, *, platform: str | None = None) -> tuple[dict, list[dict], list[dict]]:
    """返回 (header, members, messages)。"""
    info = threads.thread_info(thread_id)
    if not info:
        raise ValueError("会话不存在")

    title = ""
    if info["thread_kind"] == "contact":
        title = info["contact"]["display_name"]
        base = {
            "thread_id": thread_id,
            "thread_kind": "contact",
            "title": title,
            "kind": "private",
        }
    else:
        conv = info["conversation"]
        title = conv["title"] or "未命名"
        base = {
            "thread_id": thread_id,
            "thread_kind": "conv",
            "title": title,
            "kind": conv["kind"],
        }

    result_items = threads.all_messages(thread_id, platform=platform)
    platforms = sorted({m["platform"] for m in result_items}) or info["platforms"]
    owner_id = _is_self_uid(platforms[0]) if platforms else ""

    header = _header(base, platforms, owner_id)

    members: dict[str, dict[str, str]] = {}
    for m in result_items:
        uid = m.get("sender_uid") or ("self" if m.get("is_self") else "unknown")
        name = m.get("sender_name") or uid
        members.setdefault(uid, {"platformId": uid, "accountName": name})

    out_messages: list[dict[str, Any]] = []
    for m in result_items:
        uid = m.get("sender_uid") or ("self" if m.get("is_self") else "unknown")
        item = {
            "sender": uid,
            "accountName": (m.get("sender_name") or uid),
            "timestamp": int(m.get("ts") or 0),
            "type": CHATLAB_TYPES.get(m.get("kind") or "text", 99),
            "content": _message_content(m),
            "platformMessageId": m.get("platform_msg_id"),
        }
        if m.get("reply_to"):
            item["replyTo"] = {"content": m["reply_to"]}
        out_messages.append(item)

    return header, list(members.values()), out_messages


def export_thread(
    thread_id: str,
    *,
    fmt: str = "jsonl",
    platform: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    header, members, messages = build_export(thread_id, platform=platform)
    out_dir = out_dir or paths.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    title = header["meta"]["name"]
    stamp = time.strftime("%Y%m%d%H%M%S")
    suffix = "_" + platform if platform else ""
    ext = ".json" if fmt == "json" else ".jsonl"
    target = out_dir / f"{safe_filename(title)}{suffix}_{stamp}_export{ext}"

    if fmt == "json":
        payload = {**header, "members": members, "messages": messages}
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        with target.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"_type": "header", **header}, ensure_ascii=False) + "\n")
            for member in members:
                fh.write(json.dumps({"_type": "member", **member}, ensure_ascii=False) + "\n")
            for msg in messages:
                fh.write(json.dumps({"_type": "message", **msg}, ensure_ascii=False) + "\n")
    return target


def export_markdown(thread_id: str, *, platform: str | None = None,
                    out_dir: Path | None = None) -> Path:
    """导出成可读的 Markdown：适合归档和直接发给别人看。"""
    header, _members, messages = build_export(thread_id, platform=platform)
    out_dir = out_dir or paths.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    title = header["meta"]["name"]
    stamp = time.strftime("%Y%m%d%H%M%S")
    suffix = "_" + platform if platform else ""
    target = out_dir / f"{safe_filename(title)}{suffix}_{stamp}.md"

    platform_label = {"wechat": "微信", "douyin": "抖音", "mixed": "微信+抖音"}
    lines = [
        f"# {title}",
        "",
        f"> 平台：{platform_label.get(header['meta']['platform'], header['meta']['platform'])}"
        f" · 消息数：{len(messages)} · 导出时间：{time.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    current_day = None
    for msg in messages:
        day = time.strftime("%Y-%m-%d", time.localtime(msg["timestamp"]))
        if day != current_day:
            current_day = day
            lines += ["", f"## {day}", ""]
        who = msg["accountName"]
        stamp_hm = time.strftime("%H:%M", time.localtime(msg["timestamp"]))
        content = msg["content"]
        if content.startswith("data:image"):
            content = "[图片]"
        content = content.replace("\n", "\n  ")
        lines.append(f"- **{who}** `{stamp_hm}`：{content}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def list_exports(limit: int = 50) -> list[dict[str, Any]]:
    if not paths.EXPORT_DIR.exists():
        return []
    files = sorted(
        (p for p in paths.EXPORT_DIR.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": int(p.stat().st_mtime),
            "url": f"/api/exports/{p.name}",
        }
        for p in files
    ]
