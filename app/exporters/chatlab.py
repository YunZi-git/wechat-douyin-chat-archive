"""导出：ChatLab 0.0.2（JSON/JSONL）与 Markdown。

ChatLab 是聊天分析的通用交换格式，导出后可以直接喂给 ChatLab 或别的分析工具。
合并会话（同一个人同时有微信和抖音）默认导出成一份 ``platform: "mixed"``
的文件；需要严格单平台兼容时可以在界面上选平台分别导出。

导出走流式写入：几十万条消息的会话也不会把整库读进内存。
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

# 一张图最多内嵌 2 MB；整个文件最多内嵌 120 MB，超了退化成 "[图片]"
MAX_IMAGE_BYTES = 2_000_000
MAX_TOTAL_EMBED_BYTES = 120_000_000

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

PLATFORM_NAMES = {"wechat": "微信", "douyin": "抖音", "mixed": "微信+抖音"}

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


def _unique_path(out_dir: Path, stem: str, ext: str) -> Path:
    """同一秒里重复导出同一个会话时，别互相覆盖。"""
    target = out_dir / f"{stem}{ext}"
    if not target.exists():
        return target
    for i in range(2, 1000):
        candidate = out_dir / f"{stem}_{i}{ext}"
        if not candidate.exists():
            return candidate
    return out_dir / f"{stem}_{int(time.time() * 1000)}{ext}"


def _is_self_uid(platform: str) -> str:
    cfg = settings.load()
    return cfg.get("wechat_self_wxid" if platform == "wechat" else "douyin_self_uid") or ""


class _ImageBudget:
    """限制单次导出内嵌图片的总量，避免导出一个几 GB 的文件。"""

    def __init__(self, total: int = MAX_TOTAL_EMBED_BYTES) -> None:
        self.remaining = total

    def take(self, size: int) -> bool:
        if size > self.remaining:
            return False
        self.remaining -= size
        return True


def _image_data_uri(abs_path: Path, budget: _ImageBudget) -> str | None:
    try:
        size = abs_path.stat().st_size
        if size > MAX_IMAGE_BYTES or not budget.take(size):
            return None
        mime = mimetypes.guess_type(abs_path.name)[0] or "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(abs_path.read_bytes()).decode()
    except OSError:
        return None


def _message_content(msg: dict[str, Any], budget: _ImageBudget) -> str:
    kind = msg.get("kind") or "text"
    text = (msg.get("text") or "").strip()
    if kind == "text":
        quote = (msg.get("meta") or {}).get("quote") or {}
        if quote:
            sender = quote.get("sender") or "对方"
            return f"{text}\n\n> 引用 {sender}：{quote.get('content') or ''}"
        return text
    if kind == "image":
        if msg.get("media_url"):
            rel = msg["media_url"].removeprefix("/media/")
            data = _image_data_uri(paths.MEDIA_DIR / rel, budget)
            if data:
                return data
        return text or "[图片]"
    if kind == "voice":
        # 有转写就用转写，没有才退回时长标签
        transcript = (msg.get("transcript") or "").strip()
        if transcript:
            return transcript
        duration = msg.get("duration")
        return f"[语音 {duration:g}秒]" if duration else "[语音]"
    return text or ""


def _base_thread(thread_id: str) -> dict[str, Any]:
    info = threads.thread_info(thread_id)
    if not info:
        raise ValueError("会话不存在")
    if info["thread_kind"] == "contact":
        return {
            "thread_id": thread_id,
            "thread_kind": "contact",
            "title": info["contact"]["display_name"],
            "kind": "private",
            "info": info,
        }
    conv = info["conversation"]
    return {
        "thread_id": thread_id,
        "thread_kind": "conv",
        "title": conv["title"] or "未命名",
        "kind": conv["kind"],
        "info": info,
    }


def _header(base: dict[str, Any], platforms: list[str], owner_id: str,
            exported_at: int | None = None) -> dict[str, Any]:
    is_group = base.get("kind") == "group"
    meta = {
        "name": base["title"] if is_group else f"与{base['title']}的对话",
        "platform": platforms[0] if len(platforms) == 1 else "mixed",
        "type": "group" if is_group else "private",
        "ownerId": owner_id,
    }
    if len(platforms) > 1:
        meta["platforms"] = platforms
    if is_group:
        meta["groupId"] = str(base["thread_id"])
    return {
        "chatlab": {
            "version": CHATLAB_VERSION,
            "exportedAt": int(time.time()) if exported_at is None else exported_at,
            "generator": GENERATOR,
        },
        "meta": meta,
    }


def _members(thread_id: str, platform: str | None) -> list[dict[str, str]]:
    members: dict[str, dict[str, str]] = {}
    for row in threads.iter_senders(thread_id, platform=platform):
        uid = row.get("uid") or ("self" if row.get("is_self") else "unknown")
        members.setdefault(uid, {"platformId": uid, "accountName": row.get("name") or uid})
    return list(members.values())


def _chatlab_message(msg: dict[str, Any], budget: _ImageBudget) -> dict[str, Any]:
    uid = msg.get("sender_uid") or ("self" if msg.get("is_self") else "unknown")
    item = {
        "sender": uid,
        "accountName": (msg.get("sender_name") or uid),
        "timestamp": int(msg.get("ts") or 0),
        "type": CHATLAB_TYPES.get(msg.get("kind") or "text", 99),
        "content": _message_content(msg, budget),
        "platformMessageId": msg.get("platform_msg_id"),
    }
    if msg.get("reply_to"):
        item["replyTo"] = {"content": msg["reply_to"]}
    return item


def build_export(
    thread_id: str, *, platform: str | None = None
) -> tuple[dict, list[dict], list[dict]]:
    """返回 (header, members, messages)。小会话/测试用；导出请走 export_thread。"""
    base = _base_thread(thread_id)
    platforms = threads.thread_platforms(thread_id, platform=platform)
    owner_id = _is_self_uid(platforms[0]) if platforms else ""
    header = _header(base, platforms, owner_id)
    budget = _ImageBudget()
    messages = [_chatlab_message(m, budget) for m in
                threads.iter_messages(thread_id, platform=platform)]
    return header, _members(thread_id, platform), messages


def export_thread(
    thread_id: str,
    *,
    fmt: str = "jsonl",
    platform: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    base = _base_thread(thread_id)
    platforms = threads.thread_platforms(thread_id, platform=platform)
    owner_id = _is_self_uid(platforms[0]) if platforms else ""
    header = _header(base, platforms, owner_id)
    members = _members(thread_id, platform)
    budget = _ImageBudget()

    out_dir = out_dir or paths.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    suffix = "_" + platform if platform else ""
    ext = ".json" if fmt == "json" else ".jsonl"
    target = _unique_path(out_dir, f"{safe_filename(header['meta']['name'])}{suffix}_{stamp}", ext)

    if fmt == "json":
        with target.open("w", encoding="utf-8") as fh:
            fh.write("{\n")
            fh.write(f'  "chatlab": {json.dumps(header["chatlab"], ensure_ascii=False)},\n')
            fh.write(f'  "meta": {json.dumps(header["meta"], ensure_ascii=False)},\n')
            fh.write(f'  "members": {json.dumps(members, ensure_ascii=False)},\n')
            fh.write('  "messages": [\n')
            first = True
            for msg in threads.iter_messages(thread_id, platform=platform):
                if not first:
                    fh.write(",\n")
                fh.write("    " + json.dumps(_chatlab_message(msg, budget), ensure_ascii=False))
                first = False
            fh.write("\n  ]\n}\n")
    else:
        with target.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"_type": "header", **header}, ensure_ascii=False) + "\n")
            for member in members:
                fh.write(json.dumps({"_type": "member", **member}, ensure_ascii=False) + "\n")
            for msg in threads.iter_messages(thread_id, platform=platform):
                fh.write(json.dumps(
                    {"_type": "message", **_chatlab_message(msg, budget)},
                    ensure_ascii=False,
                ) + "\n")
    return target


def export_markdown(
    thread_id: str, *, platform: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    """导出成可读的 Markdown：适合归档和直接发给别人看。"""
    base = _base_thread(thread_id)
    platforms = threads.thread_platforms(thread_id, platform=platform)
    header = _header(base, platforms, "")
    budget = _ImageBudget()

    out_dir = out_dir or paths.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    suffix = "_" + platform if platform else ""
    target = _unique_path(out_dir, f"{safe_filename(header['meta']['name'])}{suffix}_{stamp}", ".md")

    platform_label = PLATFORM_NAMES.get(header["meta"]["platform"], header["meta"]["platform"])
    total = sum(1 for _ in threads.iter_messages(thread_id, platform=platform))

    with target.open("w", encoding="utf-8") as fh:
        fh.write(f"# {header['meta']['name']}\n\n")
        fh.write(
            f"> 平台：{platform_label} · 消息数：{total} · "
            f"导出时间：{time.strftime('%Y-%m-%d %H:%M')}\n"
        )
        current_day = None
        for msg in threads.iter_messages(thread_id, platform=platform):
            day = time.strftime("%Y-%m-%d", time.localtime(msg["ts"]))
            if day != current_day:
                current_day = day
                fh.write(f"\n## {day}\n\n")
            who = msg.get("sender_name") or "未知"
            hm = time.strftime("%H:%M", time.localtime(msg["ts"]))
            content = _message_content(msg, budget)
            if content.startswith("data:image"):
                content = "[图片]"
            fh.write(f"- **{who}** `{hm}`：{content.replace(chr(10), chr(10) + '  ')}\n")
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
