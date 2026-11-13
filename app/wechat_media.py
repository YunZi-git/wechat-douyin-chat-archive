"""微信图片 / 表情 / 视频的按需取出。

导入时只把定位信息（md5、bubble md5）记进 `messages.extra`，不搬文件；
真正显示时才：

* 图片/表情：调 SIWX 的 `media.get_image()` 解密，落盘到 `data/media/wechat/media/`
* 视频：按 md5 在账号目录里找缓存文件（微信 4.x 视频通常是明文 mp4）

依赖：微信自己的数据目录（缓存/附件）必须还在。旧账号的数据被清理后，
解密会失败——这时接口返回 404 和具体原因，界面显示占位而不是报错。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import db, paths

MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}

# 表情的 cdnurl 是腾讯 CDN 上的明文 GIF/PNG（SIWX 已验证）；只允许腾讯域名
_CDN_SUFFIXES = (
    ".qpic.cn", ".qq.com", ".tencent-cloud.net",
    ".weixin.qq.com", ".wechat.com", ".weixin.com",
)
_MAX_CDN_BYTES = 10 * 1024 * 1024

_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
}

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v")

_siwx_ready = False


class MediaUnavailable(RuntimeError):
    """拿不到媒体文件（带中文原因，直接给界面显示）。"""


def _ensure_siwx() -> None:
    global _siwx_ready
    if _siwx_ready:
        return
    engine = str(paths.SIWX_DIR)
    if engine not in sys.path:
        sys.path.insert(0, engine)
    _siwx_ready = True


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _context(message_id: int) -> dict[str, Any]:
    with db.session(readonly=True) as conn:
        row = conn.execute(
            """SELECT m.id, m.kind, m.ts, m.extra AS msg_extra,
                      m.platform, m.platform_msg_id, m.media_path,
                      c.extra AS conv_extra
                 FROM messages m JOIN conversations c ON c.id = m.conversation_id
                WHERE m.id = ?""",
            (int(message_id),),
        ).fetchone()
    if not row:
        raise MediaUnavailable("消息不存在")
    ctx = dict(row)
    ctx["msg_meta"] = _json(ctx.pop("msg_extra"))
    conv_meta = _json(ctx.pop("conv_extra") or None)
    ctx["talker"] = conv_meta.get("talker")
    ctx["account"] = conv_meta.get("account")
    parts = str(ctx.get("platform_msg_id") or "").split(":")
    ctx["local_id"] = int(parts[-1]) if parts and parts[-1].isdigit() else None
    return ctx


def _resolve_account_dir(name: str) -> Path | None:
    """按账号名找解密目录；重新解密后后缀会变（wxid_x_ab12 → wxid_x_cd34）。"""
    exact = paths.WECHAT_DECRYPTED / name
    if exact.is_dir():
        return exact
    base = re.sub(r"_[0-9a-fA-F]{4}$", "", name)
    for pattern in (f"{base}_*", f"{base}*", f"*{base}*"):
        candidates = [p for p in paths.WECHAT_DECRYPTED.glob(pattern) if p.is_dir()]
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


def _account_dir(ctx: dict[str, Any]) -> Path:
    name = ctx.get("account")
    if not name:
        raise MediaUnavailable("这条消息没有记录微信账号目录")
    account = _resolve_account_dir(str(name))
    if account is None:
        raise MediaUnavailable(
            f"微信账号目录不存在：{name}。请先解密并导入微信数据"
        )
    return account


def _persist(rel: str, body: bytes) -> Path:
    target = paths.MEDIA_DIR / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(target)
    return target


def _siwx_get_image(account: str, md5: str, account_dir: Path, *,
                    chat: str | None, local_id: int | None, ts: int | None,
                    bubble_md5: str | None):
    """单独抽出来，测试里可以直接替换，不必真的去解微信库。"""
    _ensure_siwx()
    try:
        from siwx import media  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise MediaUnavailable(f"内置解密组件不可用：{exc}") from exc
    return media.get_image(
        account, md5, account_dir,
        chat=chat, local_id=local_id, ts=ts, bubble_md5=bubble_md5,
    )


def _cdn_allowed(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    # 优先复用 SIWX 的腾讯 CDN 白名单（域名会变，它维护得更勤）
    try:
        _ensure_siwx()
        from siwx import sns_cdn  # type: ignore
        return bool(sns_cdn.is_wechat_cdn(url))
    except Exception:  # noqa: BLE001 - 没有 SIWX 时退回本地后缀表
        host = (parsed.hostname or "").lower()
        return any(host == suffix.lstrip(".") or host.endswith(suffix)
                   for suffix in _CDN_SUFFIXES)


def _image_sig(body: bytes) -> tuple[str, str] | None:
    if body.startswith(b"\x89PNG"):
        return ".png", "image/png"
    if body.startswith(b"GIF8"):
        return ".gif", "image/gif"
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def _fetch_cdn(url: str) -> bytes:
    """下载表情 CDN 资源；失败抛 MediaUnavailable。"""
    if not _cdn_allowed(url):
        raise MediaUnavailable("不是微信 CDN 地址，已拒绝")
    body: bytes | None = None
    try:
        _ensure_siwx()
        from siwx import sns_cdn  # type: ignore
        body, _headers = sns_cdn.fetch(url, timeout=15.0)
    except Exception:  # noqa: BLE001 - 退回直接下载，域名已经校验过
        request = urllib.request.Request(
            url, headers={"User-Agent": "chatwork/1.1"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(_MAX_CDN_BYTES + 1)
    if not body:
        raise MediaUnavailable("表情 CDN 返回空数据")
    if len(body) > _MAX_CDN_BYTES:
        raise MediaUnavailable("表情文件过大，已跳过")
    return body


def _sticker_from_cdn(meta: dict[str, Any]) -> tuple[bytes, str] | None:
    sticker = meta.get("sticker") or {}
    url = str(sticker.get("cdnurl") or "").strip()
    if not url:
        return None
    body = _fetch_cdn(url)
    sig = _image_sig(body)
    if not sig:
        raise MediaUnavailable("表情 CDN 数据不是图片")
    return body, sig[1]


def fetch_image(message_id: int) -> tuple[Path, str]:
    """返回 (本地文件, MIME)。已落盘过就直接用缓存。"""
    ctx = _context(message_id)
    existing = _existing(ctx)
    if existing:
        return existing

    meta = ctx["msg_meta"]
    sticker = meta.get("sticker") or {}
    md5 = (meta.get("wx_md5") or meta.get("wx_bubble_md5")
           or sticker.get("md5"))
    if not md5:
        raise MediaUnavailable("这条消息里没有图片 md5，无法定位")
    body = None
    content_type = ""
    local_error = ""
    try:
        account = _account_dir(ctx)
        body, content_type = _siwx_get_image(
            str(ctx["account"]), md5, account,
            chat=ctx.get("talker"), local_id=ctx.get("local_id"),
            ts=ctx.get("ts"), bubble_md5=meta.get("wx_bubble_md5"),
        )
        if not body:
            local_error = str(content_type or "本地没有这张图的原图/缩略图缓存")
    except MediaUnavailable as exc:
        local_error = str(exc)

    if not body and ctx["kind"] == "sticker":
        # 动画表情本地是私有容器解不开；XML 里的 cdnurl 是明文 GIF/PNG
        try:
            cdn = _sticker_from_cdn(meta)
        except MediaUnavailable as exc:
            cdn = None
            local_error = f"{local_error}；CDN：{exc}"
        if cdn:
            body, content_type = cdn

    if not body:
        raise MediaUnavailable(local_error or "本地没有这张图的缓存")

    ext = _CONTENT_TYPE_EXT.get(str(content_type).lower(), ".jpg")
    rel = f"wechat/media/{md5}{ext}"
    _persist(rel, body)
    with db.session() as conn:
        conn.execute(
            "UPDATE messages SET media_path=? WHERE id=? AND (media_path IS NULL OR media_path='')",
            (rel, int(message_id)),
        )
    return paths.MEDIA_DIR / rel, str(content_type)


def fetch_video(message_id: int) -> tuple[Path, str]:
    ctx = _context(message_id)
    existing = _existing(ctx)
    if existing:
        return existing

    meta = ctx["msg_meta"]
    md5 = meta.get("wx_md5") or meta.get("wx_bubble_md5")
    if not md5:
        raise MediaUnavailable("这条消息里没有视频 md5，无法定位")
    account = _account_dir(ctx)

    candidates: list[Path] = []
    for pattern in (
        f"msg/video/**/{md5}*.mp4",
        f"msg/video/**/{md5}*",
        f"msg/attach/**/{md5}*",
        f"msg/file/**/{md5}*",
    ):
        try:
            candidates.extend(p for p in account.glob(pattern) if p.is_file())
        except OSError:
            continue
    if not candidates:
        raise MediaUnavailable("微信本地缓存里找不到这个视频（可能已被清理）")

    source = max(candidates, key=lambda p: p.stat().st_size)
    ext = source.suffix.lower() if source.suffix else ".mp4"
    rel = f"wechat/media/{md5}{ext}"
    target = _persist(rel, source.read_bytes())
    with db.session() as conn:
        conn.execute("UPDATE messages SET media_path=? WHERE id=?", (rel, int(message_id)))
    return target, MIME_BY_EXT.get(ext, "video/mp4")


def _existing(ctx: dict[str, Any]) -> tuple[Path, str] | None:
    rel = ctx.get("media_path")
    if not rel:
        return None
    path = paths.MEDIA_DIR / rel
    if not path.is_file():
        return None
    suffix = path.suffix.lower()
    return path, MIME_BY_EXT.get(suffix, "application/octet-stream")
