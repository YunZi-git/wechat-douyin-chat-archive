"""抖音消息载荷解析：分享卡片、表情、系统提示。

抖音的 ``content`` 有时是一整段 JSON（分享视频/图文、表情包、系统卡片），
直接当文本显示就会看到一大坨 ``{"ai_ext":...}``。这里把它归一化成
``{kind, text, meta}``，导入和旧数据修复共用。
"""
from __future__ import annotations

import json
from typing import Any

SHARE_HOST = "www.douyin.com"


def _first_url(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    urls = obj.get("url_list") or []
    if isinstance(urls, list):
        for url in urls:
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
    return ""


def _cover(obj: dict[str, Any]) -> str:
    for key in ("cover_url", "cover_url_v2", "content_thumb"):
        url = _first_url(obj.get(key))
        if url:
            return url
    return ""


def parse_payload(content: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """content 是 JSON 字符串/对象时返回结构化消息，否则 None。"""
    if isinstance(content, dict):
        obj = content
    else:
        text = (content or "").strip()
        if not text.startswith("{"):
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None

    # 表情包/贴纸
    stickers = obj.get("stickers")
    if isinstance(stickers, list) and stickers:
        first = stickers[0] if isinstance(stickers[0], dict) else {}
        return {
            "kind": "sticker",
            "text": str(first.get("display_name") or "[表情]"),
            "meta": {
                "sticker": {
                    "remoteUrl": _first_url(first.get("static_url") or first.get("dynamic_url")),
                    "name": str(first.get("display_name") or ""),
                }
            },
        }

    related = obj.get("related_share_video")
    item_id = (obj.get("itemId") or obj.get("item_id") or obj.get("aweme_id")
               or (related.get("itemId") if isinstance(related, dict) else None))
    if not item_id:
        # 兜底：share_id 末段通常就是作品 ID（<uid>_<ts>_<itemId>）
        share_id = str(obj.get("share_id") or "")
        tail = share_id.rsplit("_", 1)[-1]
        if tail.isdigit() and len(tail) >= 10:
            item_id = tail
    author = str(obj.get("content_name") or "").strip()
    title = str(
        obj.get("content_title") or obj.get("aweme_title")
        or obj.get("push_detail") or ""
    ).strip()
    if not title and isinstance(obj.get("comment"), str):
        title = obj["comment"].strip()
    cover = _cover(obj)
    if item_id or author or cover:
        item = str(item_id).strip()
        url = f"https://{SHARE_HOST}/video/{item}" if item.isdigit() else ""
        label = title or (f"{author} 的抖音作品" if author else "[抖音分享]")
        return {
            "kind": "share",
            "text": label,
            "meta": {
                "share": {
                    "title": label,
                    "author": author,
                    "url": url,
                    "cover": cover,
                    "itemId": item,
                    "source": "抖音",
                }
            },
        }

    tips = obj.get("tips")
    if isinstance(tips, str) and tips.strip():
        return {"kind": "system", "text": tips.strip()[:200], "meta": {}}
    return None
