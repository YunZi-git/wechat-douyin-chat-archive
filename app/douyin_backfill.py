"""从抖音引擎库的 ``raw_data.content_json`` 回填分享卡片。

上游引擎的 ``content`` 字段经常只有 ``[分享]``，真正的标题、itemId、封面在
``raw_data.content_json`` 里（导出器正是读这里）。早期导入只读了 ``content``，
导致大量分享没有链接/封面；这里按 ``msg_id`` 回填已导入的消息。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .douyin_message import parse_payload


def _load_json(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return None
    try:
        obj = json.loads(value)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def backfill_from_engine(conn: sqlite3.Connection, source: Path) -> dict[str, Any]:
    """返回 ``{"updated": n, "conversations": [...]}``。"""
    if not source.is_file():
        return {"updated": 0, "conversations": []}
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
    except sqlite3.Error:
        return {"updated": 0, "conversations": []}
    updated = 0
    touched: set[int] = set()
    try:
        try:
            rows = src.execute(
                "SELECT msg_id, content, raw_data FROM messages"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for row in rows:
            raw = _load_json(row["raw_data"]) or {}
            structured = _load_json(raw.get("content_json"))
            parsed = parse_payload(structured) or parse_payload(row["content"])
            if not parsed or parsed["kind"] != "share":
                continue
            target = conn.execute(
                "SELECT id, conversation_id, extra FROM messages"
                " WHERE platform='douyin' AND platform_msg_id=?",
                (row["msg_id"],),
            ).fetchone()
            if not target:
                continue
            try:
                extra = json.loads(target["extra"] or "{}")
            except (TypeError, ValueError):
                extra = {}
            if isinstance(extra, dict):
                extra.update(parsed.get("meta") or {})
            conn.execute(
                "UPDATE messages SET kind=?, text=?, extra=? WHERE id=?",
                (
                    parsed["kind"],
                    parsed["text"],
                    json.dumps(extra, ensure_ascii=False),
                    target["id"],
                ),
            )
            touched.add(int(target["conversation_id"]))
            updated += 1
    finally:
        src.close()
    return {"updated": updated, "conversations": sorted(touched)}
