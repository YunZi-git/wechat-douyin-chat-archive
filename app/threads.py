"""聚合查询：把「同一个人在不同平台的会话」合成一条时间线。

``thread_id`` 有两种：

* ``contact:<id>`` —— 一个联系人（可能同时有微信和抖音会话）
* ``conv:<id>``    —— 一个独立会话（群聊 / 系统会话）

前端只认 thread_id，不需要知道底下有几个平台的会话。
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from . import db, paths

# 会话列表是一条聚合查询（几十万条消息），给个短缓存让界面切换无感。
# 导入 / 合并 / 拆分之后调用 invalidate_cache() 立刻失效。
_CACHE_TTL = 25.0
_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}


def invalidate_cache() -> None:
    _cache.clear()


def _platforms(raw: str | None) -> list[str]:
    if not raw:
        return []
    seen: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def list_threads(
    *,
    platform: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """会话列表：联系人（含跨平台合并）在前，群聊在后，都按最近活跃排序。"""
    key = (platform, (q or "").strip(), limit, offset)
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    result = _list_threads_uncached(platform=platform, q=q, limit=limit, offset=offset)
    _cache[key] = (time.time(), result)
    return result


def _list_threads_uncached(
    *,
    platform: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    like = f"%{q.strip()}%" if q and q.strip() else None
    with db.session(readonly=True) as conn:
        contact_rows = conn.execute(
            """
            WITH msgs AS (
                SELECT c.contact_id AS cid,
                       COUNT(*) AS msg_count,
                       MAX(m.ts) AS last_ts
                  FROM messages m
                  JOIN conversations c ON c.id = m.conversation_id
                 WHERE c.contact_id IS NOT NULL
                 GROUP BY c.contact_id
            ),
            last AS (
                SELECT cid, text, kind FROM (
                    SELECT c.contact_id AS cid, m.text AS text, m.kind AS kind,
                           ROW_NUMBER() OVER (
                               PARTITION BY c.contact_id
                               ORDER BY m.ts DESC, m.id DESC
                           ) AS rn
                      FROM messages m
                      JOIN conversations c ON c.id = m.conversation_id
                     WHERE c.contact_id IS NOT NULL
                ) WHERE rn = 1
            )
            SELECT 'contact:' || ct.id            AS thread_id,
                   'contact'                      AS thread_kind,
                   ct.id                          AS contact_id,
                   NULL                           AS conversation_id,
                   ct.display_name                AS title,
                   ct.avatar_url                  AS avatar,
                   'private'                      AS kind,
                   NULL                           AS member_count,
                   (SELECT GROUP_CONCAT(DISTINCT c2.platform)
                      FROM conversations c2 WHERE c2.contact_id = ct.id)
                                                  AS platforms_raw,
                   COALESCE(msgs.msg_count, 0)    AS msg_count,
                   COALESCE(msgs.last_ts, 0)      AS last_ts,
                   last.text                      AS preview,
                   last.kind                      AS preview_kind,
                   (SELECT GROUP_CONCAT(DISTINCT c3.platform || ':' || c3.platform_conv_id)
                      FROM conversations c3 WHERE c3.contact_id = ct.id)
                                                  AS sources
              FROM contacts ct
              LEFT JOIN msgs ON msgs.cid = ct.id
              LEFT JOIN last ON last.cid = ct.id
             WHERE EXISTS (SELECT 1 FROM conversations c4 WHERE c4.contact_id = ct.id)
               AND (? IS NULL OR ct.display_name LIKE ?)
            """,
            (like, like),
        ).fetchall()

        group_rows = conn.execute(
            """
            SELECT 'conv:' || cv.id              AS thread_id,
                   'conv'                        AS thread_kind,
                   cv.contact_id                 AS contact_id,
                   cv.id                         AS conversation_id,
                   cv.title                      AS title,
                   cv.avatar_url                 AS avatar,
                   cv.kind                       AS kind,
                   cv.member_count               AS member_count,
                   cv.platform                   AS platforms_raw,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = cv.id)
                                                 AS msg_count,
                   (SELECT MAX(m.ts) FROM messages m WHERE m.conversation_id = cv.id)
                                                 AS last_ts,
                   (SELECT m.text FROM messages m WHERE m.conversation_id = cv.id
                     ORDER BY m.ts DESC, m.id DESC LIMIT 1) AS preview,
                   (SELECT m.kind FROM messages m WHERE m.conversation_id = cv.id
                     ORDER BY m.ts DESC, m.id DESC LIMIT 1) AS preview_kind,
                   cv.platform || ':' || cv.platform_conv_id AS sources
              FROM conversations cv
             WHERE cv.contact_id IS NULL
               AND cv.kind IN ('group', 'system')
               AND (? IS NULL OR cv.title LIKE ?)
            """,
            (like, like),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in [*contact_rows, *group_rows]:
        item = dict(row)
        item["platforms"] = _platforms(item.pop("platforms_raw", None))
        item["sources"] = item.get("sources") or ""
        if platform and platform not in item["platforms"]:
            continue
        item["preview"] = (item.get("preview") or "").strip()[:120]
        items.append(item)

    items.sort(key=lambda x: (x.get("last_ts") or 0, x.get("msg_count") or 0), reverse=True)
    total = len(items)
    return {"items": items[offset: offset + limit], "total": total}


def thread_info(thread_id: str) -> dict[str, Any] | None:
    kind, _, raw_id = thread_id.partition(":")
    if not raw_id.isdigit():
        return None
    with db.session(readonly=True) as conn:
        if kind == "contact":
            contact = conn.execute(
                "SELECT * FROM contacts WHERE id=?", (int(raw_id),)
            ).fetchone()
            if not contact:
                return None
            identities = conn.execute(
                "SELECT * FROM identities WHERE contact_id=? ORDER BY platform",
                (int(raw_id),),
            ).fetchall()
            convs = conn.execute(
                """SELECT cv.*,
                          (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=cv.id) AS msg_count,
                          (SELECT MAX(m.ts) FROM messages m WHERE m.conversation_id=cv.id) AS last_ts
                     FROM conversations cv WHERE cv.contact_id=? ORDER BY last_ts DESC""",
                (int(raw_id),),
            ).fetchall()
            platforms = sorted({c["platform"] for c in convs})
            return {
                "thread_id": thread_id,
                "thread_kind": "contact",
                "contact": dict(contact),
                "identities": [dict(r) for r in identities],
                "conversations": [dict(r) for r in convs],
                "platforms": platforms,
                "merged": len(platforms) > 1,
            }
        if kind == "conv":
            conv = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (int(raw_id),)
            ).fetchone()
            if not conv:
                return None
            members = conn.execute(
                """SELECT sender_uid AS uid, MAX(sender_name) AS name, COUNT(*) AS n
                     FROM messages WHERE conversation_id=? AND is_self=0 AND sender_uid IS NOT NULL
                    GROUP BY sender_uid ORDER BY n DESC LIMIT 50""",
                (int(raw_id),),
            ).fetchall()
            return {
                "thread_id": thread_id,
                "thread_kind": "conv",
                "conversation": dict(conv),
                "members": [dict(r) for r in members],
                "platforms": [conv["platform"]],
                "merged": False,
            }
    return None


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["media_url"] = paths.media_url(item.get("media_path"))
    item.pop("media_path", None)
    return item


def thread_messages(
    thread_id: str,
    *,
    limit: int = 60,
    before_ts: int | None = None,
    after_ts: int | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    kind, _, raw_id = thread_id.partition(":")
    if not raw_id.isdigit():
        return {"items": [], "has_more_before": False, "has_more_after": False}

    where = ["1=1"]
    args: list[Any] = []
    if kind == "contact":
        where.append("c.contact_id = ?")
        args.append(int(raw_id))
    elif kind == "conv":
        where.append("c.id = ?")
        args.append(int(raw_id))
    else:
        return {"items": [], "has_more_before": False, "has_more_after": False}
    if platform:
        where.append("m.platform = ?")
        args.append(platform)

    limit = max(1, min(limit, 500))
    with db.session(readonly=True) as conn:
        if after_ts is not None:
            rows = conn.execute(
                f"""SELECT m.*, c.title AS conv_title, c.kind AS conv_kind, c.platform AS conv_platform
                      FROM messages m JOIN conversations c ON c.id = m.conversation_id
                     WHERE {' AND '.join(where)} AND m.ts > ?
                     ORDER BY m.ts, m.id LIMIT ?""",
                [*args, int(after_ts), limit],
            ).fetchall()
        elif before_ts is not None:
            rows = conn.execute(
                f"""SELECT * FROM (
                      SELECT m.*, c.title AS conv_title, c.kind AS conv_kind,
                             c.platform AS conv_platform
                        FROM messages m JOIN conversations c ON c.id = m.conversation_id
                       WHERE {' AND '.join(where)} AND m.ts < ?
                       ORDER BY m.ts DESC, m.id DESC LIMIT ?)
                    ORDER BY ts, id""",
                [*args, int(before_ts), limit],
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT * FROM (
                      SELECT m.*, c.title AS conv_title, c.kind AS conv_kind,
                             c.platform AS conv_platform
                        FROM messages m JOIN conversations c ON c.id = m.conversation_id
                       WHERE {' AND '.join(where)}
                       ORDER BY m.ts DESC, m.id DESC LIMIT ?)
                    ORDER BY ts, id""",
                [*args, limit],
            ).fetchall()

        oldest = rows[0]["ts"] if rows else None
        newest = rows[-1]["ts"] if rows else None
        has_more_before = False
        has_more_after = False
        if oldest is not None:
            has_more_before = conn.execute(
                f"""SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id
                     WHERE {' AND '.join(where)} AND m.ts < ? LIMIT 1""",
                [*args, oldest],
            ).fetchone() is not None
        if newest is not None:
            has_more_after = conn.execute(
                f"""SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id
                     WHERE {' AND '.join(where)} AND m.ts > ? LIMIT 1""",
                [*args, newest],
            ).fetchone() is not None

    return {
        "items": [_message_row(r) for r in rows],
        "has_more_before": has_more_before,
        "has_more_after": has_more_after,
    }


def search_messages(
    q: str,
    *,
    platform: str | None = None,
    thread_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"items": [], "total": 0}
    where = ["m.text LIKE ?"]
    args: list[Any] = [f"%{q}%"]
    if platform:
        where.append("m.platform = ?")
        args.append(platform)
    if thread_id:
        kind, _, raw_id = thread_id.partition(":")
        if kind == "contact" and raw_id.isdigit():
            where.append("c.contact_id = ?")
            args.append(int(raw_id))
        elif kind == "conv" and raw_id.isdigit():
            where.append("c.id = ?")
            args.append(int(raw_id))
    with db.session(readonly=True) as conn:
        total = conn.execute(
            f"""SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id
                 WHERE {' AND '.join(where)}""",
            args,
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT m.id, m.ts, m.platform, m.kind, m.text, m.sender_name, m.is_self,
                       m.conversation_id, c.title AS conv_title, c.kind AS conv_kind,
                       c.contact_id AS contact_id
                  FROM messages m JOIN conversations c ON c.id=m.conversation_id
                 WHERE {' AND '.join(where)}
                 ORDER BY m.ts DESC LIMIT ?""",
            [*args, max(1, min(limit, 200))],
        ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["thread_id"] = (
            f"contact:{item['contact_id']}" if item["contact_id"] else f"conv:{item['conversation_id']}"
        )
        items.append(item)
    return {"items": items, "total": int(total)}


def all_messages(thread_id: str, *, platform: str | None = None) -> list[dict[str, Any]]:
    """取整个会话的全部消息（导出处用），按时间升序。"""
    kind, _, raw_id = thread_id.partition(":")
    if not raw_id.isdigit():
        return []
    if kind == "contact":
        cond, arg = "c.contact_id = ?", int(raw_id)
    elif kind == "conv":
        cond, arg = "c.id = ?", int(raw_id)
    else:
        return []
    sql = f"""SELECT m.*, c.title AS conv_title, c.kind AS conv_kind,
                     c.platform AS conv_platform
                FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE {cond}"""
    args: list[Any] = [arg]
    if platform:
        sql += " AND m.platform = ?"
        args.append(platform)
    sql += " ORDER BY m.ts, m.id"
    with db.session(readonly=True) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_message_row(r) for r in rows]


def day_counts(thread_id: str, *, platform: str | None = None) -> list[dict[str, Any]]:
    """按天聚合消息量，给日历/热力条用。"""
    kind, _, raw_id = thread_id.partition(":")
    if not raw_id.isdigit():
        return []
    if kind == "contact":
        cond, arg = "c.contact_id = ?", int(raw_id)
    elif kind == "conv":
        cond, arg = "c.id = ?", int(raw_id)
    else:
        return []
    sql = f"""SELECT date(m.ts, 'unixepoch', '+8 hours') AS day, COUNT(*) AS n
                FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE {cond}"""
    args: list[Any] = [arg]
    if platform:
        sql += " AND m.platform = ?"
        args.append(platform)
    sql += " GROUP BY day ORDER BY day"
    with db.session(readonly=True) as conn:
        return [dict(r) for r in conn.execute(sql, args)]
