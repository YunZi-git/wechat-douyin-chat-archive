"""聚合查询：把「同一个人在不同平台的会话」合成一条时间线。

``thread_id`` 有两种：

* ``contact:<id>`` —— 一个联系人（可能同时有微信和抖音会话）
* ``conv:<id>``    —— 一个独立会话（群聊 / 系统会话）

前端只认 thread_id，不需要知道底下有几个平台的会话。

性能说明
--------
会话列表如果直接对 ``messages`` 做 ``COUNT/MAX/ROW_NUMBER``，几十万条消息时
每次冷查询要半秒。所以每个会话的「条数 / 最后一条 / 最后一条内容」被物化到
``conversation_stats``（导入结束时刷新），列表查询只扫几百行。
"""
from __future__ import annotations

import json

import sqlite3
from typing import Any, Iterator

from . import db, paths


def like_pattern(value: str) -> str:
    """把用户输入包成 LIKE 模式，并转义通配符。

    不转义的话，搜一个 ``%`` 会命中全库，既反直觉又慢。
    调用方必须搭配 ``LIKE ? ESCAPE '\\'`` 使用。
    """
    escaped = (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


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
    account: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """会话列表：联系人（含跨平台合并）在前，群聊在后，都按最近活跃排序。"""
    query = (q or "").strip()
    pattern = like_pattern(query) if query else None

    with db.session(readonly=True) as conn:
        contact_rows = conn.execute(
            """
            WITH conv AS (
                SELECT c.contact_id            AS contact_id,
                       c.id                    AS conv_id,
                       c.platform              AS platform,
                       COALESCE(s.msg_count, 0) AS msg_count,
                       COALESCE(s.last_ts, 0)   AS last_ts,
                       COALESCE(s.last_text, '') AS last_text,
                       COALESCE(s.last_kind, 'text') AS last_kind
                  FROM conversations c
                  LEFT JOIN conversation_stats s ON s.conversation_id = c.id
                 WHERE c.contact_id IS NOT NULL
            ),
            agg AS (
                SELECT contact_id,
                       SUM(msg_count)     AS msg_count,
                       MAX(last_ts)       AS last_ts,
                       GROUP_CONCAT(DISTINCT platform) AS platforms_raw
                  FROM conv
                 GROUP BY contact_id
            ),
            ranked AS (
                SELECT contact_id, last_text, last_kind,
                       ROW_NUMBER() OVER (
                           PARTITION BY contact_id
                           ORDER BY last_ts DESC, conv_id DESC
                       ) AS rn
                  FROM conv
            )
            SELECT 'contact:' || ct.id AS thread_id,
                   'contact'           AS thread_kind,
                   ct.id               AS contact_id,
                   NULL                AS conversation_id,
                   ct.display_name     AS title,
                   ct.avatar_url       AS avatar,
                   'private'           AS kind,
                   NULL                AS member_count,
                   agg.platforms_raw   AS platforms_raw,
                   agg.msg_count       AS msg_count,
                   agg.last_ts         AS last_ts,
                   r.last_text         AS preview,
                   r.last_kind         AS preview_kind
              FROM contacts ct
              JOIN agg ON agg.contact_id = ct.id
              LEFT JOIN ranked r ON r.contact_id = ct.id AND r.rn = 1
             WHERE (? IS NULL OR ct.display_name LIKE ? ESCAPE '\\')
               AND (? IS NULL OR EXISTS (
                     SELECT 1 FROM conversations c2
                      WHERE c2.contact_id = ct.id AND c2.self_uid = ?))
            """,
            (pattern, pattern, account, account),
        ).fetchall()

        group_rows = conn.execute(
            """
            SELECT 'conv:' || cv.id     AS thread_id,
                   'conv'               AS thread_kind,
                   cv.contact_id        AS contact_id,
                   cv.id                AS conversation_id,
                   cv.title             AS title,
                   cv.avatar_url        AS avatar,
                   cv.kind              AS kind,
                   cv.member_count      AS member_count,
                   cv.platform          AS platforms_raw,
                   COALESCE(s.msg_count, 0) AS msg_count,
                   COALESCE(s.last_ts, 0)   AS last_ts,
                   s.last_text          AS preview,
                   s.last_kind          AS preview_kind
              FROM conversations cv
              LEFT JOIN conversation_stats s ON s.conversation_id = cv.id
             WHERE cv.contact_id IS NULL
               AND cv.kind IN ('group', 'system')
               AND (? IS NULL OR cv.title LIKE ? ESCAPE '\\')
               AND (? IS NULL OR cv.self_uid = ?)
            """,
            (pattern, pattern, account, account),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in [*contact_rows, *group_rows]:
        item = dict(row)
        item["platforms"] = _platforms(item.pop("platforms_raw", None))
        if platform and platform not in item["platforms"]:
            continue
        item["preview"] = (item.get("preview") or "").strip()[:120]
        items.append(item)

    items.sort(key=lambda x: (x.get("last_ts") or 0, x.get("msg_count") or 0), reverse=True)
    total = len(items)
    return {"items": items[offset: offset + limit], "total": total}


def list_accounts() -> list[dict[str, Any]]:
    """导入过的微信账号（按最近活跃排序），用于会话列表按账号筛选。"""
    with db.session(readonly=True) as conn:
        rows = conn.execute(
            """SELECT c.self_uid AS uid,
                      COUNT(*) AS conversations,
                      COALESCE(SUM(COALESCE(s.msg_count, 0)), 0) AS messages,
                      MAX(COALESCE(s.last_ts, 0)) AS last_ts
                 FROM conversations c
                 LEFT JOIN conversation_stats s ON s.conversation_id = c.id
                WHERE c.platform = 'wechat' AND c.self_uid IS NOT NULL
                  AND c.self_uid != ''
                GROUP BY c.self_uid
                ORDER BY last_ts DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


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
                          COALESCE(s.msg_count, 0) AS msg_count,
                          COALESCE(s.last_ts, 0)   AS last_ts
                     FROM conversations cv
                     LEFT JOIN conversation_stats s ON s.conversation_id = cv.id
                    WHERE cv.contact_id = ?
                    ORDER BY last_ts DESC, cv.id DESC""",
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
                """SELECT cv.*,
                          COALESCE(s.msg_count, 0) AS msg_count,
                          COALESCE(s.last_ts, 0)   AS last_ts
                     FROM conversations cv
                     LEFT JOIN conversation_stats s ON s.conversation_id = cv.id
                    WHERE cv.id = ?""",
                (int(raw_id),),
            ).fetchone()
            if not conv:
                return None
            members = conn.execute(
                """SELECT m.sender_uid AS uid, MAX(m.sender_name) AS name,
                          m.platform AS platform, COUNT(*) AS n,
                          MIN(m.ts) AS first_ts, MAX(m.ts) AS last_ts
                     FROM messages m
                    WHERE m.conversation_id = ? AND m.is_self = 0
                      AND m.sender_uid IS NOT NULL
                    GROUP BY m.sender_uid, m.platform
                    ORDER BY n DESC LIMIT 200""",
                (int(raw_id),),
            ).fetchall()
            member_list = []
            for row in members:
                item = dict(row)
                # 群成员 → 和 TA 的私聊（有就跳过去，没有就置灰）
                if item["platform"] == "wechat":
                    pv = conn.execute(
                        """SELECT id, contact_id FROM conversations
                            WHERE platform='wechat' AND kind='private'
                              AND platform_conv_id=? LIMIT 1""",
                        (item["uid"],),
                    ).fetchone()
                else:
                    pv = None
                item["thread_id"] = (
                    (f"contact:{pv['contact_id']}" if pv["contact_id"] else f"conv:{pv['id']}")
                    if pv else None
                )
                member_list.append(item)
            return {
                "thread_id": thread_id,
                "thread_kind": "conv",
                "conversation": dict(conv),
                "members": member_list,
                "platforms": [conv["platform"]],
                "merged": False,
            }
    return None


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["media_url"] = paths.media_url(item.get("media_path"))
    item.pop("media_path", None)
    # extra 里是引用块/文件信息/位置等结构化字段，前端直接读 meta
    raw = item.get("extra")
    try:
        item["meta"] = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        item["meta"] = {}
    return item


def _where(thread_id: str, platform: str | None) -> tuple[str, list[Any]] | None:
    kind, _, raw_id = thread_id.partition(":")
    if not raw_id.isdigit():
        return None
    if kind == "contact":
        cond, args = "c.contact_id = ?", [int(raw_id)]
    elif kind == "conv":
        cond, args = "c.id = ?", [int(raw_id)]
    else:
        return None
    if platform:
        cond += " AND m.platform = ?"
        args.append(platform)
    return cond, args


_MESSAGE_COLUMNS = """m.*, c.title AS conv_title, c.kind AS conv_kind,
                      c.platform AS conv_platform,
                      (SELECT vt.text FROM voice_transcriptions vt
                        WHERE vt.message_id = m.id) AS transcript"""


def thread_messages(
    thread_id: str,
    *,
    limit: int = 60,
    before_ts: int | None = None,
    after_ts: int | None = None,
    around_ts: int | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """取一页消息。

    ``around_ts`` 用于「搜索命中后跳过去」：以该时间点为中心取前后各一半，
    并且 **包含** 那个时间点本身（before/after 单独用是开区间，容易漏掉目标）。
    """
    built = _where(thread_id, platform)
    if built is None:
        return {"items": [], "has_more_before": False, "has_more_after": False}
    cond, args = built
    limit = max(1, min(limit, 500))

    with db.session(readonly=True) as conn:
        if around_ts is not None:
            half = max(1, limit // 2)
            before = conn.execute(
                f"""SELECT {_MESSAGE_COLUMNS}
                      FROM messages m JOIN conversations c ON c.id = m.conversation_id
                     WHERE {cond} AND m.ts <= ?
                     ORDER BY m.ts DESC, m.id DESC LIMIT ?""",
                [*args, int(around_ts), half],
            ).fetchall()
            after = conn.execute(
                f"""SELECT {_MESSAGE_COLUMNS}
                      FROM messages m JOIN conversations c ON c.id = m.conversation_id
                     WHERE {cond} AND m.ts > ?
                     ORDER BY m.ts, m.id LIMIT ?""",
                [*args, int(around_ts), limit - half],
            ).fetchall()
            rows = list(reversed(before)) + list(after)
        elif after_ts is not None:
            rows = conn.execute(
                f"""SELECT {_MESSAGE_COLUMNS}
                      FROM messages m JOIN conversations c ON c.id = m.conversation_id
                     WHERE {cond} AND m.ts > ?
                     ORDER BY m.ts, m.id LIMIT ?""",
                [*args, int(after_ts), limit],
            ).fetchall()
        elif before_ts is not None:
            rows = conn.execute(
                f"""SELECT * FROM (
                      SELECT {_MESSAGE_COLUMNS}
                        FROM messages m JOIN conversations c ON c.id = m.conversation_id
                       WHERE {cond} AND m.ts < ?
                       ORDER BY m.ts DESC, m.id DESC LIMIT ?)
                    ORDER BY ts, id""",
                [*args, int(before_ts), limit],
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT * FROM (
                      SELECT {_MESSAGE_COLUMNS}
                        FROM messages m JOIN conversations c ON c.id = m.conversation_id
                       WHERE {cond}
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
                     WHERE {cond} AND m.ts < ? LIMIT 1""",
                [*args, oldest],
            ).fetchone() is not None
        if newest is not None:
            has_more_after = conn.execute(
                f"""SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id
                     WHERE {cond} AND m.ts > ? LIMIT 1""",
                [*args, newest],
            ).fetchone() is not None

    return {
        "items": [_message_row(r) for r in rows],
        "has_more_before": has_more_before,
        "has_more_after": has_more_after,
    }


def thread_platforms(thread_id: str, *, platform: str | None = None) -> list[str]:
    """这个 thread 实际出现过消息的平台（按字母序）。"""
    built = _where(thread_id, platform)
    if built is None:
        return []
    cond, args = built
    with db.session(readonly=True) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT m.platform AS platform
                  FROM messages m JOIN conversations c ON c.id = m.conversation_id
                 WHERE {cond}
                 ORDER BY m.platform""",
            args,
        ).fetchall()
    return [r["platform"] for r in rows]


def iter_messages(
    thread_id: str, *, platform: str | None = None
) -> Iterator[dict[str, Any]]:
    """流式遍历整个会话的消息（导出用，避免把几十万条一次性读进内存）。"""
    built = _where(thread_id, platform)
    if built is None:
        return
    cond, args = built
    conn = db.connect(readonly=True)
    try:
        cursor = conn.execute(
            f"""SELECT {_MESSAGE_COLUMNS}
                  FROM messages m JOIN conversations c ON c.id = m.conversation_id
                 WHERE {cond}
                 ORDER BY m.ts, m.id""",
            args,
        )
        for row in cursor:
            yield _message_row(row)
    finally:
        conn.close()


def iter_senders(
    thread_id: str, *, platform: str | None = None
) -> Iterator[dict[str, Any]]:
    """会话里出现过的发送者（导出的 members 段用）。"""
    built = _where(thread_id, platform)
    if built is None:
        return
    cond, args = built
    conn = db.connect(readonly=True)
    try:
        cursor = conn.execute(
            f"""SELECT m.sender_uid AS uid,
                       MAX(m.sender_name) AS name,
                       MAX(m.is_self) AS is_self
                  FROM messages m JOIN conversations c ON c.id = m.conversation_id
                 WHERE {cond}
                 GROUP BY m.sender_uid
                 ORDER BY MIN(m.ts)""",
            args,
        )
        for row in cursor:
            yield dict(row)
    finally:
        conn.close()


def all_messages(thread_id: str, *, platform: str | None = None) -> list[dict[str, Any]]:
    """取全量消息（小会话 / 测试用；大导出请走 iter_messages）。"""
    return list(iter_messages(thread_id, platform=platform))


def search_messages(
    q: str,
    *,
    platform: str | None = None,
    thread_id: str | None = None,
    sender: str | None = None,
    kinds: list[str] | None = None,
    since_ts: int | None = None,
    until_ts: int | None = None,
    limit: int = 50,
    offset: int = 0,
    group: bool = False,
) -> dict[str, Any]:
    """全文/模糊搜索，支持筛选、分页与按会话分组。

    * 查询词 >= 3 个字符且 FTS 可用时走 trigram 索引（中文子串也能命中）
    * 更短的词退化为 LIKE（trigram 要求至少 3 个字符）
    * 语音转写文本参与匹配
    """
    q = (q or "").strip()
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    if not q:
        return {"items": [], "total": 0, "offset": offset, "has_more": False,
                "groups": [], "engine": "none"}

    where: list[str] = []
    args: list[Any] = []
    engine = "like"
    with db.session(readonly=True) as conn:
        if len(q) >= 3 and db.has_fts(conn):
            phrase = '"' + q.replace('"', '""') + '"'
            where.append(
                "m.id IN (SELECT message_id FROM messages_fts"
                " WHERE messages_fts MATCH ?)"
            )
            args.append(phrase)
            engine = "fts"
        else:
            where.append(
                "(m.text LIKE ? ESCAPE '\\' OR EXISTS ("
                "SELECT 1 FROM voice_transcriptions vt2"
                " WHERE vt2.message_id = m.id AND vt2.text LIKE ? ESCAPE '\\'))"
            )
            args.extend([like_pattern(q), like_pattern(q)])

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
        if sender:
            where.append("(m.sender_name = ? OR m.sender_uid = ?)")
            args.extend([sender, sender])
        if kinds:
            clean = [k for k in dict.fromkeys(kinds) if k]
            if clean:
                where.append(f"m.kind IN ({','.join('?' for _ in clean)})")
                args.extend(clean)
        if since_ts:
            where.append("m.ts >= ?")
            args.append(int(since_ts))
        if until_ts:
            where.append("m.ts < ?")
            args.append(int(until_ts))

        base_from = "FROM messages m JOIN conversations c ON c.id = m.conversation_id"
        cond = " AND ".join(where)
        total = int(conn.execute(
            f"SELECT COUNT(*) {base_from} WHERE {cond}", args
        ).fetchone()[0])
        rows = conn.execute(
            f"""SELECT m.*, c.title AS conv_title, c.kind AS conv_kind,
                       c.platform AS conv_platform,
                       c.contact_id AS conv_contact_id,
                       (SELECT vt.text FROM voice_transcriptions vt
                         WHERE vt.message_id = m.id) AS transcript
                  {base_from}
                 WHERE {cond}
                 ORDER BY m.ts DESC, m.id DESC
                 LIMIT ? OFFSET ?""",
            [*args, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            item = _message_row(row)
            contact_id = item.pop("conv_contact_id", None)
            item["thread_id"] = (
                f"contact:{contact_id}" if contact_id else f"conv:{item['conversation_id']}"
            )
            items.append(item)

        groups: list[dict[str, Any]] = []
        if group:
            grows = conn.execute(
                f"""SELECT c.id AS conv_id, c.contact_id, c.title, c.kind, c.platform,
                           COUNT(*) AS n, MAX(m.ts) AS last_ts
                      {base_from}
                     WHERE {cond}
                     GROUP BY c.id
                     ORDER BY n DESC, last_ts DESC
                     LIMIT 30""",
                args,
            ).fetchall()
            groups = [
                {
                    "thread_id": (
                        f"contact:{g['contact_id']}" if g["contact_id"] else f"conv:{g['conv_id']}"
                    ),
                    "title": g["title"] or "未命名",
                    "kind": g["kind"],
                    "platform": g["platform"],
                    "n": int(g["n"]),
                    "last_ts": int(g["last_ts"] or 0),
                }
                for g in grows
            ]

    return {
        "items": items,
        "total": total,
        "offset": offset,
        "has_more": offset + len(items) < total,
        "groups": groups,
        "engine": engine,
    }


MEDIA_KINDS = ("image", "sticker", "video", "file")


def thread_media(
    thread_id: str,
    *,
    kind: str | None = None,
    platform: str | None = None,
    limit: int = 60,
    offset: int = 0,
) -> dict[str, Any]:
    """会话内媒体页：图片/表情/视频/文件，按时间倒序分页。"""
    built = _where(thread_id, platform)
    if built is None:
        return {"items": [], "total": 0, "counts": {}}
    cond, args = built
    kinds = [k for k in ([kind] if kind else MEDIA_KINDS) if k in MEDIA_KINDS]
    if not kinds:
        kinds = list(MEDIA_KINDS)
    marks = ",".join("?" for _ in kinds)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with db.session(readonly=True) as conn:
        where = f"{cond} AND m.kind IN ({marks})"
        conn_args = [*args, *kinds]
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM messages m JOIN conversations c"
            f" ON c.id = m.conversation_id WHERE {where}",
            conn_args,
        ).fetchone()[0])
        counts = {
            r["kind"]: int(r["n"])
            for r in conn.execute(
                f"""SELECT m.kind, COUNT(*) AS n FROM messages m
                      JOIN conversations c ON c.id = m.conversation_id
                     WHERE {where} GROUP BY m.kind""",
                conn_args,
            )
        }
        rows = conn.execute(
            f"""SELECT {_MESSAGE_COLUMNS}
                  FROM messages m JOIN conversations c ON c.id = m.conversation_id
                 WHERE {where}
                 ORDER BY m.ts DESC, m.id DESC
                 LIMIT ? OFFSET ?""",
            [*conn_args, limit, offset],
        ).fetchall()
    return {
        "items": [_message_row(r) for r in rows],
        "total": total,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "counts": counts,
    }


def day_counts(thread_id: str, *, platform: str | None = None) -> list[dict[str, Any]]:
    """按天聚合消息量，给日历/热力条用。"""
    built = _where(thread_id, platform)
    if built is None:
        return []
    cond, args = built
    sql = f"""SELECT date(m.ts, 'unixepoch', '+8 hours') AS day, COUNT(*) AS n
                FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE {cond}
               GROUP BY day ORDER BY day"""
    with db.session(readonly=True) as conn:
        return [dict(r) for r in conn.execute(sql, args)]
