"""旧消息修复：把早期直接显示原始 XML / JSON 的消息归一化。

两类历史问题：

* 微信 ``kind=image``（或其它媒体）的 ``text`` 里存着 ``<msg>…``，气泡里会露出
  一大段 XML；
* 抖音分享/表情/系统卡片的 ``content`` 是 JSON，早期被整段当文本存下来。

修复只改展示用的 ``kind/text``，原始定位信息（md5/aeskey/媒体路径）保持不动。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .douyin_message import parse_payload
from .wechat_message import KIND_LABELS as WX_LABELS


def _looks_like_xml(text: str) -> bool:
    head = (text or "").lstrip()[:64].lower()
    return "<msg" in head or "<?xml" in head or "<sysmsg" in head


def repair_raw_messages(conn: sqlite3.Connection) -> dict[str, int]:
    stats = {"wechat": 0, "douyin": 0}
    # ① 微信：媒体消息的正文不该是 XML
    rows = conn.execute(
        """SELECT id, kind, text FROM messages
            WHERE platform='wechat'
              AND text IS NOT NULL
              AND (text LIKE '%<msg%' OR text LIKE '%<?xml%' OR text LIKE '%<sysmsg%')"""
    ).fetchall()
    for row in rows:
        if not _looks_like_xml(row["text"]):
            continue
        label = WX_LABELS.get(row["kind"]) or "[消息]"
        conn.execute("UPDATE messages SET text=? WHERE id=?", (label, row["id"]))
        stats["wechat"] += 1

    # ② 抖音：JSON 载荷 → 分享卡片 / 表情 / 系统提示
    rows = conn.execute(
        """SELECT id, kind, text, extra FROM messages
            WHERE platform='douyin' AND text LIKE '{%'"""
    ).fetchall()
    for row in rows:
        parsed = parse_payload(row["text"])
        if not parsed:
            continue
        try:
            old_meta: dict[str, Any] = json.loads(row["extra"] or "{}")
        except (TypeError, ValueError):
            old_meta = {}
        merged = {**old_meta, **(parsed.get("meta") or {})}
        conn.execute(
            "UPDATE messages SET kind=?, text=?, extra=? WHERE id=?",
            (
                parsed["kind"],
                parsed["text"],
                json.dumps(merged, ensure_ascii=False),
                row["id"],
            ),
        )
        stats["douyin"] += 1
    return stats
