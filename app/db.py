"""统一数据库：schema、连接与底层读写。

设计要点
--------
* **contacts** 是「一个人」，**identities** 是「这个人在某个平台的账号」。
  一个人可以同时有微信和抖音身份，这正是跨平台合并的基础。
* **conversations** 是平台原始会话（私聊 / 群聊），私聊通过 ``contact_id``
  挂到某个人身上；群聊没有 contact。
* 浏览时把同一个人的多个会话合起来，就是交错时间线。
* 消息以 ``(platform, platform_msg_id)`` 唯一，重复导入天然幂等。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from . import paths

SCHEMA_VERSION = 6

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    note         TEXT,
    avatar_url   TEXT,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS identities (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    platform     TEXT NOT NULL,              -- wechat | douyin
    platform_uid TEXT NOT NULL,              -- 微信 wxid / 抖音 uid
    platform_name TEXT,
    avatar_url   TEXT,
    unique_id    TEXT,                       -- 抖音号等对外 ID
    extra        TEXT,
    created_at   INTEGER NOT NULL,
    UNIQUE (platform, platform_uid)
);
CREATE INDEX IF NOT EXISTS ix_identities_contact ON identities(contact_id);

CREATE TABLE IF NOT EXISTS conversations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    platform       TEXT NOT NULL,
    platform_conv_id TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'private',  -- private | group
    title          TEXT,
    contact_id     INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    avatar_url     TEXT,
    member_count   INTEGER,
    self_uid       TEXT,
    extra          TEXT,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    UNIQUE (platform, platform_conv_id)
);
CREATE INDEX IF NOT EXISTS ix_conversations_contact ON conversations(contact_id);
CREATE INDEX IF NOT EXISTS ix_conversations_platform ON conversations(platform);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    platform_msg_id TEXT NOT NULL,
    sender_uid      TEXT,
    sender_name     TEXT,
    is_self         INTEGER NOT NULL DEFAULT 0,
    ts              INTEGER NOT NULL,        -- epoch 秒
    kind            TEXT NOT NULL DEFAULT 'text',
    text            TEXT,
    media_path      TEXT,                    -- 相对 data/media
    media_url       TEXT,
    duration        REAL,
    reply_to        TEXT,
    seq             INTEGER,
    extra           TEXT,
    UNIQUE (platform, platform_msg_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_conv_ts ON messages(conversation_id, ts);
CREATE INDEX IF NOT EXISTS ix_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS ix_messages_sender ON messages(sender_uid);
CREATE INDEX IF NOT EXISTS ix_messages_kind ON messages(kind);

-- 每个会话的物化统计：条数 / 最后一条。导入结束时刷新，
-- 列表查询因此只需要扫几百行，而不是几十万条消息。
CREATE TABLE IF NOT EXISTS conversation_stats (
    conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    msg_count       INTEGER NOT NULL DEFAULT 0,
    last_ts         INTEGER NOT NULL DEFAULT 0,
    last_text       TEXT,
    last_kind       TEXT
);

-- 语音转写结果。抖音的 native 识别、本地 whisper、腾讯云都写这张表；
-- 同一条消息只保留最后一次结果（重跑即覆盖）。
CREATE TABLE IF NOT EXISTS voice_transcriptions (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    engine     TEXT NOT NULL,              -- legacy | native_douyin | local_whisper | tencent
    status     TEXT NOT NULL DEFAULT 'success',  -- success | failed | skipped
    text       TEXT,
    error      TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS import_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT NOT NULL,
    source             TEXT,
    mode               TEXT NOT NULL DEFAULT 'full',
    status             TEXT NOT NULL DEFAULT 'running',  -- running|ok|failed
    started_at         INTEGER NOT NULL,
    finished_at        INTEGER,
    conversations_added INTEGER DEFAULT 0,
    messages_added     INTEGER DEFAULT 0,
    messages_skipped   INTEGER DEFAULT 0,
    detail             TEXT
);
"""


def connect(*, readonly: bool = False) -> sqlite3.Connection:
    paths.ensure_dirs()
    if readonly:
        conn = sqlite3.connect(f"file:{paths.UNIFIED_DB}?mode=ro", uri=True, timeout=10)
    else:
        conn = sqlite3.connect(paths.UNIFIED_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, transcript,
    message_id UNINDEXED,
    conversation_id UNINDEXED,
    platform UNINDEXED,
    ts UNINDEXED,
    kind UNINDEXED,
    sender_name UNINDEXED,
    sender_uid UNINDEXED,
    tokenize = 'trigram'
);
DROP TRIGGER IF EXISTS messages_fts_delete;
DROP TRIGGER IF EXISTS messages_fts_update;
CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;
CREATE TRIGGER messages_fts_update AFTER UPDATE OF text ON messages BEGIN
    UPDATE messages_fts SET text = new.text WHERE rowid = old.id;
END;
"""

_FTS_OK: bool | None = None


def has_fts(conn: sqlite3.Connection | None = None) -> bool:
    """当前库是否有 FTS5 索引（SQLite 编译时可能不带）。"""
    if _FTS_OK is False:
        return False
    try:
        if conn is None:
            with session(readonly=True) as own:
                return has_fts(own)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name='messages_fts'"
        ).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    if not has_fts(conn):
        return
    conn.execute("DELETE FROM messages_fts")
    # rowid 必须等于 messages.id：转写/修复按 rowid 更新是 O(1)，
    # 按 UNINDEXED 的 message_id 更新会退化成全表扫描（真实库上会卡死）
    conn.execute(
        """INSERT INTO messages_fts
             (rowid, text, transcript, message_id, conversation_id, platform, ts,
              kind, sender_name, sender_uid)
           SELECT m.id, COALESCE(m.text,''), COALESCE(vt.text,''), m.id,
                  m.conversation_id, m.platform, m.ts, m.kind,
                  COALESCE(m.sender_name,''), COALESCE(m.sender_uid,'')
             FROM messages m
             LEFT JOIN voice_transcriptions vt ON vt.message_id = m.id"""
    )


@contextmanager
def session(*, readonly: bool = False, write: bool = True) -> Iterator[sqlite3.Connection]:
    """短连接上下文：正常结束提交，异常回滚。"""
    conn = connect(readonly=readonly)
    try:
        yield conn
        if write and not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    paths.ensure_dirs()
    global _FTS_OK
    with session() as conn:
        conn.executescript(SCHEMA_SQL)
        try:
            conn.executescript(FTS_SQL)
            _FTS_OK = True
        except sqlite3.OperationalError as exc:  # pragma: no cover - 取决于 SQLite 编译
            _FTS_OK = False
            print(f"[warn] FTS5 不可用，搜索回退 LIKE：{exc}", file=sys.stderr)
        row = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        version = int(row["v"]) if row else 0
        if row is None:
            conn.execute(
                "INSERT INTO meta(k, v) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        stats_empty = conn.execute(
            "SELECT NOT EXISTS(SELECT 1 FROM conversation_stats)"
        ).fetchone()[0]
        has_messages = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM messages)"
        ).fetchone()[0]
        # 老库升级 / 统计表丢了：补一次物化
        if version < SCHEMA_VERSION:
            _migrate(conn, version)
        if has_messages and (version < SCHEMA_VERSION or stats_empty):
            refresh_conversation_stats(conn)
        if version and version < SCHEMA_VERSION:
            conn.execute(
                "UPDATE meta SET v=? WHERE k='schema_version'", (str(SCHEMA_VERSION),)
            )


# ── 领域模型辅助 ────────────────────────────────────────────────────

def now() -> int:
    return int(time.time())


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def ensure_contact(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    avatar_url: str | None = None,
) -> int:
    """新建一个联系人（同一个人只会在导入时创建一次）。"""
    ts = now()
    cur = conn.execute(
        "INSERT INTO contacts(display_name, avatar_url, created_at, updated_at)"
        " VALUES (?,?,?,?)",
        (display_name or "未命名", avatar_url, ts, ts),
    )
    return int(cur.lastrowid)


def ensure_identity(
    conn: sqlite3.Connection,
    contact_id: int,
    platform: str,
    platform_uid: str,
    *,
    platform_name: str | None = None,
    avatar_url: str | None = None,
    unique_id: str | None = None,
    extra: str | None = None,
) -> int:
    ts = now()
    conn.execute(
        """INSERT INTO identities
             (contact_id, platform, platform_uid, platform_name, avatar_url,
              unique_id, extra, created_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(platform, platform_uid) DO UPDATE SET
             platform_name = COALESCE(excluded.platform_name, platform_name),
             avatar_url    = COALESCE(excluded.avatar_url, avatar_url),
             unique_id     = COALESCE(excluded.unique_id, unique_id)""",
        (contact_id, platform, str(platform_uid), platform_name, avatar_url,
         unique_id, extra, ts),
    )
    row = conn.execute(
        "SELECT id FROM identities WHERE platform=? AND platform_uid=?",
        (platform, str(platform_uid)),
    ).fetchone()
    return int(row["id"])


def ensure_conversation(
    conn: sqlite3.Connection,
    platform: str,
    platform_conv_id: str,
    *,
    kind: str = "private",
    title: str | None = None,
    contact_id: int | None = None,
    avatar_url: str | None = None,
    member_count: int | None = None,
    self_uid: str | None = None,
    extra: str | None = None,
) -> int:
    ts = now()
    conn.execute(
        """INSERT INTO conversations
             (platform, platform_conv_id, kind, title, contact_id, avatar_url,
              member_count, self_uid, extra, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(platform, platform_conv_id) DO UPDATE SET
             kind         = excluded.kind,
             title        = COALESCE(excluded.title, title),
             contact_id   = COALESCE(excluded.contact_id, contact_id),
             avatar_url   = COALESCE(excluded.avatar_url, avatar_url),
             member_count = COALESCE(excluded.member_count, member_count),
             self_uid     = COALESCE(excluded.self_uid, self_uid),
             updated_at   = excluded.updated_at""",
        (platform, platform_conv_id, kind, title, contact_id, avatar_url,
         member_count, self_uid, extra, ts, ts),
    )
    row = conn.execute(
        "SELECT id FROM conversations WHERE platform=? AND platform_conv_id=?",
        (platform, platform_conv_id),
    ).fetchone()
    return int(row["id"])


def add_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    platform: str,
    platform_msg_id: str,
    *,
    sender_uid: str | None,
    sender_name: str | None,
    is_self: bool,
    ts: int,
    kind: str = "text",
    text: str | None = None,
    media_path: str | None = None,
    media_url: str | None = None,
    duration: float | None = None,
    reply_to: str | None = None,
    seq: int | None = None,
    extra: str | None = None,
) -> bool:
    """返回 True 表示确实写入了一条新消息。"""
    cur = conn.execute(
        """INSERT OR IGNORE INTO messages
             (conversation_id, platform, platform_msg_id, sender_uid, sender_name,
              is_self, ts, kind, text, media_path, media_url, duration, reply_to,
              seq, extra)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (conversation_id, platform, str(platform_msg_id), sender_uid, sender_name,
         1 if is_self else 0, int(ts), kind, text, media_path, media_url,
         duration, reply_to, seq, extra),
    )
    if cur.rowcount > 0 and _FTS_OK:
        conn.execute(
            """INSERT INTO messages_fts
                 (rowid, text, transcript, message_id, conversation_id, platform, ts,
                  kind, sender_name, sender_uid)
               VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?)""",
            (cur.lastrowid, text or "", cur.lastrowid, conversation_id, platform,
             int(ts), kind, sender_name or "", sender_uid or ""),
        )
    return cur.rowcount > 0


# 非文本消息的占位文案（早期版本把原始 XML 存进了 messages.text）
NON_TEXT_LABELS = {
    "image": "[图片]",
    "voice": "[语音]",
    "video": "[视频]",
    "sticker": "[表情]",
    "card": "[名片]",
    "location": "[位置]",
    "call": "[通话]",
    "revoke": "[撤回了一条消息]",
    "system": "[系统消息]",
    "other": "[消息]",
}


def _migrate(conn: sqlite3.Connection, version: int) -> None:
    """按版本号做数据修正（只跑一次）。"""
    if version < 6:
        # v6：rowid 对齐 + 修复早期直接展示原始 XML / JSON 的消息
        _rebuild_fts(conn)
        from .message_repair import repair_raw_messages

        stats = repair_raw_messages(conn)
        if any(stats.values()):
            print(f"[i] 修复原始载荷消息: 微信 {stats['wechat']} / 抖音 {stats['douyin']}",
                  file=sys.stderr)
    if version < 5:
        # v5：全文索引需要重建；v6 会统一做一次 rowid 对齐，这里不再重复
        pass
    if version < 4:
        # v3 之前的导入把 XML/JSON 原文写进了 text，界面上会露馅
        for kind, label in NON_TEXT_LABELS.items():
            conn.execute(
                "UPDATE messages SET text=? WHERE kind=? AND (text LIKE '<%' OR text LIKE '{%')",
                (label, kind),
            )


def refresh_conversation_stats(
    conn: sqlite3.Connection,
    conversation_ids: Sequence[int] | None = None,
) -> int:
    """重算 ``conversation_stats``（全量或指定会话）。

    导入结束后调用一次即可；合并/拆分不改消息，所以不需要重算。
    返回写入的行数。
    """
    if conversation_ids is None:
        conn.execute("DELETE FROM conversation_stats")
        conn.execute(
            """
            INSERT INTO conversation_stats
                (conversation_id, msg_count, last_ts, last_text, last_kind)
            SELECT a.conversation_id, a.msg_count, a.last_ts, m.text, m.kind
              FROM (SELECT conversation_id, COUNT(*) AS msg_count, MAX(ts) AS last_ts
                      FROM messages GROUP BY conversation_id) a
              JOIN messages m
                ON m.conversation_id = a.conversation_id
               AND m.id = (SELECT m2.id FROM messages m2
                            WHERE m2.conversation_id = a.conversation_id
                            ORDER BY m2.ts DESC, m2.id DESC LIMIT 1)
            """
        )
        return int(conn.execute("SELECT COUNT(*) FROM conversation_stats").fetchone()[0])

    ids = [int(i) for i in conversation_ids]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM conversation_stats WHERE conversation_id IN ({marks})", ids)
    conn.execute(
        f"""
        INSERT INTO conversation_stats
            (conversation_id, msg_count, last_ts, last_text, last_kind)
        SELECT a.conversation_id, a.msg_count, a.last_ts, m.text, m.kind
          FROM (SELECT conversation_id, COUNT(*) AS msg_count, MAX(ts) AS last_ts
                  FROM messages
                 WHERE conversation_id IN ({marks})
                 GROUP BY conversation_id) a
          JOIN messages m
            ON m.conversation_id = a.conversation_id
           AND m.id = (SELECT m2.id FROM messages m2
                        WHERE m2.conversation_id = a.conversation_id
                        ORDER BY m2.ts DESC, m2.id DESC LIMIT 1)
        """,
        ids,
    )
    return len(ids)


def iter_messages_for_contact(
    conn: sqlite3.Connection,
    contact_id: int,
) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """SELECT m.* FROM messages m
             JOIN conversations c ON c.id = m.conversation_id
            WHERE c.contact_id = ?
            ORDER BY m.ts, m.id""",
        (contact_id,),
    )


def stats() -> dict[str, Any]:
    with session(readonly=True) as conn:
        def one(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])

        platforms: dict[str, int] = {}
        for r in conn.execute(
            "SELECT platform, COUNT(*) n FROM messages GROUP BY platform"
        ):
            platforms[r["platform"]] = int(r["n"])

        merged = one(
            "SELECT COUNT(*) FROM (SELECT contact_id FROM identities"
            " GROUP BY contact_id HAVING COUNT(DISTINCT platform) > 1)"
        )
        voice_cache = {"files": 0, "bytes": 0}
        try:
            from .voice import cache_stats

            voice_cache = cache_stats()
        except Exception:  # noqa: BLE001 - 统计失败不该拖垮接口
            pass
        return {
            "voice_messages": one("SELECT COUNT(*) FROM messages WHERE kind='voice'"),
            "voice_with_audio": one(
                "SELECT COUNT(*) FROM messages WHERE kind='voice' AND media_path IS NOT NULL"
            ),
            "transcripts": one(
                "SELECT COUNT(*) FROM voice_transcriptions WHERE status='success'"
            ),
            "voice_cache": voice_cache,
            "contacts": one("SELECT COUNT(*) FROM contacts"),
            "merged_contacts": merged,
            "conversations": one("SELECT COUNT(*) FROM conversations"),
            "groups": one("SELECT COUNT(*) FROM conversations WHERE kind='group'"),
            "messages": one("SELECT COUNT(*) FROM messages"),
            "messages_by_platform": platforms,
            "unlinked_identities": one(
                "SELECT COUNT(*) FROM identities i WHERE NOT EXISTS"
                " (SELECT 1 FROM conversations c WHERE c.contact_id = i.contact_id)"
            ),
            "last_import": row_to_dict(
                conn.execute(
                    "SELECT * FROM import_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
            ),
        }
