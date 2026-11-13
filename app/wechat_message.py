"""微信消息 XML 的语义解析。

微信 4.x 的 ``message_content`` 对不同类型是一段 XML：

* ``local_type=49``  → ``<appmsg type=N>``：链接 / 文件 / 合并转发 / 引用 / 视频号 / 小程序
* ``local_type=10000`` → ``<sysmsg type="revokemsg">`` 或纯文本系统提示
* ``local_type=48``  → ``<location ...>``
* ``local_type=42``  → ``<msg ... nickname=...>`` 名片
* ``local_type=43``  → ``<videomsg playlength length>``（时长/大小）
* ``local_type=47``  → ``<emoji md5 len>`` 表情

这里只做「解析成结构化字段」，不碰数据库；导入器负责落库。
"""
from __future__ import annotations

import html
import re
from typing import Any

# appmsg 子类型 → 统一 kind（只列常见的，其它归到 share）
APPMSG_KINDS = {
    3: "share",       # 音乐
    4: "share",       # 视频链接
    5: "share",       # 链接
    6: "file",        # 文件
    8: "sticker",     # 表情
    19: "forward",    # 合并转发
    33: "miniprogram",  # 小程序
    36: "miniprogram",
    51: "channel",    # 视频号
    57: "quote",      # 引用消息
    62: "share",      # 拍一拍? 兼容
    2000: "share",
    2001: "share",
}

# kind 的中文占位
KIND_LABELS = {
    "image": "[图片]",
    "voice": "[语音]",
    "video": "[视频]",
    "sticker": "[表情]",
    "share": "[链接]",
    "file": "[文件]",
    "forward": "[合并转发]",
    "quote": "[引用]",
    "miniprogram": "[小程序]",
    "channel": "[视频号]",
    "card": "[名片]",
    "location": "[位置]",
    "call": "[通话]",
    "system": "[系统消息]",
    "revoke": "[撤回了一条消息]",
    "other": "[消息]",
}

_TAG = re.compile(r"<([a-zA-Z_][\w:.-]*)([^>]*)>(.*?)</\1>", re.S)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')


def _tag_text(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", xml, re.S)
    if not m:
        return ""
    raw = m.group(1)
    raw = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", raw, flags=re.S)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(raw).strip()


def _tag_attrs(xml: str, tag: str) -> dict[str, str]:
    m = re.search(rf"<{tag}\b([^>]*)>", xml)
    if not m:
        return {}
    return {k: html.unescape(v) for k, v in _ATTR.findall(m.group(1))}


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_refermsg(xml: str) -> dict[str, Any] | None:
    m = re.search(r"<refermsg\b[^>]*>(.*?)</refermsg>", xml, re.S)
    if not m:
        return None
    block = m.group(1)
    content = _tag_text(block, "content")
    # 引用里的 content 可能还是 XML（嵌套引用），提取可读部分
    if "<" in content:
        nested = _tag_text(content, "title") or _tag_text(content, "des")
        content = nested or re.sub(r"<[^>]+>", "", content).strip()
    return {
        "sender": _tag_text(block, "displayname") or _tag_text(block, "chatusr"),
        "content": content[:300],
        "type": _int(_tag_text(block, "type")),
    }


def _parse_forward(xml: str) -> dict[str, Any]:
    """合并转发：数一数 dataitem，取摘要。"""
    record = re.search(r"<recorditem\b[^>]*>(.*?)</recorditem>", xml, re.S)
    body = record.group(1) if record else xml
    count = len(re.findall(r"<dataitem\b", body))
    desc = _tag_text(body, "datadesc") or _tag_text(body, "desc")
    title = _tag_text(xml, "title")
    return {
        "count": max(count, _int(_tag_text(xml, "count")) or 0),
        "desc": desc[:200],
        "title": title[:120],
    }


def _parse_appmsg(xml: str) -> dict[str, Any]:
    m = re.search(r"<appmsg\b[^>]*>(.*?)</appmsg>", xml, re.S)
    body = m.group(1) if m else xml
    app_type = _int(_tag_text(body, "type"))
    title = _tag_text(body, "title")
    des = _tag_text(body, "des")
    url = _tag_text(body, "url")
    kind = APPMSG_KINDS.get(app_type, "share")

    meta: dict[str, Any] = {"appmsgType": app_type}
    if url:
        meta["url"] = url[:500]

    if kind == "file":
        attach = _tag_attrs(body, "appattach")
        meta["file"] = {
            "name": title or attach.get("filename") or "文件",
            "size": _int(_tag_text(body, "totallen") or attach.get("totallen")),
            "ext": _tag_text(body, "fileext") or attach.get("fileext") or "",
        }
        return {"kind": kind, "text": meta["file"]["name"], "meta": meta}

    if kind == "forward":
        forward = _parse_forward(body)
        meta["forward"] = forward
        text = f"[合并转发] {forward['title'] or forward['desc'] or ''}".strip()
        if forward["count"]:
            text = f"[合并转发 {forward['count']} 条] {forward['title'] or ''}".strip()
        return {"kind": kind, "text": text, "meta": meta}

    if kind == "quote":
        quote = _parse_refermsg(body)
        if quote:
            meta["quote"] = quote
        return {"kind": "text", "text": title or des, "meta": meta}

    if kind == "channel":
        meta["channelTitle"] = title
        return {"kind": kind, "text": title or "[视频号]", "meta": meta}

    if kind == "miniprogram":
        meta["miniprogram"] = {"title": title, "source": _tag_text(body, "sourcedisplayname")}
        return {"kind": kind, "text": title or "[小程序]", "meta": meta}

    # 链接 / 音乐 / 其它分享
    meta["share"] = {"title": title, "des": des[:160]}
    return {"kind": "share", "text": title or des or "[链接]", "meta": meta}


def _parse_revoke(xml: str) -> str:
    text = _tag_text(xml, "content") if "<" in xml else xml
    return text or "撤回了一条消息"


def parse_message(local_type: int, xml_text: str) -> dict[str, Any]:
    """把一段消息 XML 解析成 ``{kind, text, meta, duration}``。"""
    xml_text = (xml_text or "").strip()
    result: dict[str, Any] = {"kind": "other", "text": "", "meta": {}}

    if local_type in (49, 10000, 42, 48, 43, 47) and xml_text:
        if local_type == 49:
            return _parse_appmsg(xml_text)
        if local_type == 10000:
            if "revokemsg" in xml_text:
                return {"kind": "revoke", "text": _parse_revoke(xml_text), "meta": {}}
            text = _tag_text(xml_text, "content") or re.sub(r"<[^>]+>", "", xml_text).strip()
            return {"kind": "system", "text": text[:300], "meta": {}}
        if local_type == 42:
            attrs = _tag_attrs(xml_text, "msg")
            return {
                "kind": "card",
                "text": attrs.get("nickname") or attrs.get("displayname") or "[名片]",
                "meta": {"card": {"nickname": attrs.get("nickname", ""),
                                  "alias": attrs.get("alias", "")}},
            }
        if local_type == 48:
            attrs = _tag_attrs(xml_text, "location")
            label = attrs.get("poiname") or attrs.get("label") or ""
            return {
                "kind": "location",
                "text": label or "[位置]",
                "meta": {"location": {"lat": attrs.get("x"), "lng": attrs.get("y"),
                                      "label": label}},
            }
        if local_type == 3:
            # 图片的 CDN 字段先留着（当前没有可靠的 CDN 解密方案，不做下载）
            attrs = _tag_attrs(xml_text, "img")
            return {
                "kind": "image",
                "text": "[图片]",
                "meta": {"image": {
                    "cdnThumb": attrs.get("cdnthumburl") or "",
                    "cdnMid": attrs.get("cdnmidimgurl") or "",
                    "cdnBig": attrs.get("cdnbigimgurl") or "",
                }},
            }
        if local_type == 43:
            attrs = _tag_attrs(xml_text, "videomsg")
            seconds = _int(attrs.get("playlength"))
            return {
                "kind": "video",
                "text": "[视频]",
                "meta": {"video": {"size": _int(attrs.get("length")),
                                   "seconds": seconds}},
                "duration": float(seconds) if seconds else None,
            }
        if local_type == 47:
            attrs = _tag_attrs(xml_text, "emoji")
            sticker = {
                "md5": attrs.get("md5") or attrs.get("androidmd5"),
                "width": attrs.get("width"),
                "height": attrs.get("height"),
                "cdnurl": attrs.get("cdnurl") or "",
            }
            return {
                "kind": "sticker",
                "text": "[表情]",
                "meta": {"sticker": sticker},
            }

    # 文本 / 语音 / 图片：保持原样，由导入器兜底
    result["text"] = xml_text
    return result


def has_revoke_marker(text: str) -> bool:
    return "撤回了一条消息" in (text or "")
