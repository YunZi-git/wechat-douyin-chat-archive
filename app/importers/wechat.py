"""微信导入器：把 SIWX 解密出来的明文库直接读进统一库。

不依赖任何中间 JSONL，也就不会在中途留下明文副本。

数据来源（``data/wechat/decrypted/<账号目录>/``）
--------------------------------------------------
* ``contact/contact.db`` —— 联系人、群、群成员
* ``message/message_N.db`` —— ``Name2Id`` 提供 rowid→wxid，``Msg_<md5>`` 是分表会话

关键映射（沿用微信 4.x 的既有实现）
------------------------------------
* 会话：表名 ``Msg_<md5(username)>`` 反查 ``Name2Id.user_name``
* 发送者：``real_sender_id`` → ``Name2Id.user_name``
* 文本：``message_content``，``WCDB_CT_message_content != 0`` 时是 zstd 压缩
* 本人 wxid：账号目录名去掉尾部 4 位十六进制后缀
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .. import db, paths
from .. import voice as voice_mod
from ..jobs import JobCancelled
from ..wechat_message import has_revoke_marker, parse_message

Progress = Callable[[dict[str, Any]], None] | None

try:  # zstd 是可选依赖：没有它就只能读未压缩文本
    import zstandard as _zstd

    _ZD = _zstd.ZstdDecompressor()
except Exception:  # pragma: no cover - 环境缺依赖时的降级
    _ZD = None

# 没有「人」的含义的系统会话
SYSTEM_TALKERS = {
    "filehelper", "floatbottle", "weixin", "medianote", "newsapp",
    "notifymessage", "qqsafe", "weixinreminder",
}

# 微信 local_type → 统一 kind
WECHAT_LOCAL_TYPES: dict[int, str] = {
    1: "text",
    3: "image",
    34: "voice",
    37: "system",      # 好友请求
    42: "card",        # 名片
    43: "video",
    47: "sticker",
    48: "location",
    49: "share",       # 链接/文件/小程序/引用
    50: "call",
    10000: "system",
    10002: "revoke",
}

KIND_LABELS = {
    "image": "[图片]",
    "voice": "[语音]",
    "video": "[视频]",
    "sticker": "[表情]",
    "share": "[链接/文件]",
    "card": "[名片]",
    "location": "[位置]",
    "call": "[通话]",
    "revoke": "[撤回了一条消息]",
    "system": "[系统消息]",
    "other": "[消息]",
}

_ACCOUNT_RE = re.compile(r"^(wxid_[0-9A-Za-z]+?)(_[0-9a-f]{4})?$")
_QUOTE_PREFIX = re.compile(r"^(wxid_[0-9A-Za-z]+|[0-9A-Za-z_\-]+@chatroom):\s*\n")
_TITLE_RE = re.compile(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", re.S)


def _reconfigure_stdio() -> None:
    """中文 Windows 控制台默认 GBK，子进程输出中文会炸。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def detect_accounts(root: Path | str) -> list[Path]:
    """在解密输出根目录下找出所有含 message/ 的账号目录。"""
    root = Path(root)
    if not root.exists():
        return []
    found: list[Path] = []
    if (root / "message").is_dir():
        found.append(root)
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "message").is_dir():
            found.append(child)
    return found


def self_wxid_from_dir(account_dir: Path) -> str:
    base = account_dir.name
    m = _ACCOUNT_RE.match(base)
    return m.group(1) if m else base


def _decompress(blob: Any, ct: Any) -> str:
    if not blob:
        return ""
    if isinstance(blob, str):
        return blob
    if ct and _ZD is not None:
        try:
            return _ZD.decompress(blob).decode("utf-8", "replace")
        except Exception:
            pass
    try:
        return blob.decode("utf-8", "replace")
    except Exception:
        return ""


def _clean_text(text: str) -> str:
    text = _QUOTE_PREFIX.sub("", text or "").strip()
    return text


def _share_title(text: str) -> str | None:
    m = _TITLE_RE.search(text or "")
    if not m:
        return None
    title = (m.group(1) or m.group(2) or "").strip()
    return title or None


def _load_contacts(contact_db: Path) -> tuple[dict[str, str], dict[str, int], dict[str, str]]:
    """返回 (username→显示名, 群 username→成员数, 群 owner)。"""
    names: dict[str, str] = {}
    member_counts: dict[str, int] = {}
    owners: dict[str, str] = {}
    if not contact_db.exists():
        return names, member_counts, owners

    con = sqlite3.connect(f"file:{contact_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT username, remark, nick_name FROM contact"):
            username = r["username"]
            if not username:
                continue
            display = (r["remark"] or r["nick_name"] or username).strip()
            names[username] = display or username
    except sqlite3.Error as exc:
        print(f"[warn] 读取 contact.db 失败: {exc}", file=sys.stderr)

    try:
        id_to_name = {
            r["id"]: r["username"] for r in con.execute("SELECT id, username FROM contact")
        }
        for r in con.execute(
            "SELECT room_id, COUNT(*) n FROM chatroom_member GROUP BY room_id"
        ):
            username = id_to_name.get(r["room_id"])
            if username:
                member_counts[username] = int(r["n"])
        for r in con.execute("SELECT id, username, owner FROM chat_room"):
            username = r["username"] or id_to_name.get(r["id"])
            if username and r["owner"]:
                owners[username] = r["owner"]
    except sqlite3.Error:
        pass
    con.close()
    return names, member_counts, owners


def _shard_sort_key(path: Path) -> int:
    m = re.fullmatch(r"message_(\d+)\.db", path.name)
    return int(m.group(1)) if m else 0


def _iter_message_rows(con: sqlite3.Connection, table: str,
                       after_ts: int = 0) -> sqlite3.Cursor:
    """流式返回消息游标：几万条的表也不一次性读进内存。

    不同微信小版本的 Msg_* 列不完全一致（`packed_info_data` 是 4.x 才有的），
    这里按实际列拼 SQL，缺列时用 NULL 占位。
    """
    columns = {row[1] for row in con.execute(f"PRAGMA table_info('{table}')")}
    packed = "packed_info_data" if "packed_info_data" in columns else "NULL AS packed_info_data"
    where = "WHERE create_time > ?" if after_ts else ""
    params = (after_ts,) if after_ts else ()
    return con.execute(
        f'SELECT local_id, server_id, local_type, real_sender_id, create_time,'
        f' message_content, compress_content, {packed},'
        f' WCDB_CT_message_content'
        f' FROM "{table}" {where} ORDER BY create_time, local_id',
        params,
    )


def _load_watermark(conn: sqlite3.Connection, account: str) -> int:
    row = conn.execute(
        "SELECT v FROM meta WHERE k=?", (f"wx_wm:{account}",)
    ).fetchone()
    try:
        return int(row["v"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _save_watermark(conn: sqlite3.Connection, account: str, ts: int) -> None:
    if ts <= 0:
        return
    conn.execute(
        "INSERT INTO meta(k, v) VALUES (?, ?)"
        " ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (f"wx_wm:{account}", str(int(ts))),
    )


def _load_emoticon_captions(account_dir: Path) -> dict[str, str]:
    """表情说明：微信的 emoji 图片是私有封装解不开，但表情库里有说明文字。"""
    path = account_dir / "emoticon" / "emoticon.db"
    if not path.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT md5, caption FROM kNonStoreEmoticonTable"
                " WHERE md5 IS NOT NULL AND caption IS NOT NULL AND caption != ''"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {str(r["md5"]).lower(): str(r["caption"]).strip() for r in rows}


def _extract_voice_files(
    account_dir: Path,
    *,
    progress: Progress = None,
) -> dict[tuple[str, int], str]:
    """把 media_*.db 里的语音原样落盘，只存 SILK，不转码。

    返回 ``(talker, local_id) -> 相对 media 目录的路径``。
    """
    # (talker, local_id) -> {svr_id: 相对路径}；同一 local_id 可能有多条历史记录
    mapping: dict[tuple[str, int], dict[int, str]] = {}
    written = 0
    msg_dir = account_dir / "message"
    for media_db in sorted(msg_dir.glob("media_*.db")):
        try:
            mcon = sqlite3.connect(f"file:{media_db}?mode=ro", uri=True)
            mcon.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            tables = {
                r["name"] for r in mcon.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "VoiceInfo" not in tables or "Name2Id" not in tables:
                continue
            names = {
                r["rowid"]: r["user_name"]
                for r in mcon.execute("SELECT rowid, user_name FROM Name2Id")
            }
            for row in mcon.execute(
                "SELECT chat_name_id, local_id, svr_id, voice_data FROM VoiceInfo"
            ):
                talker = names.get(row["chat_name_id"])
                blob = row["voice_data"]
                local_id = row["local_id"]
                if not talker or not blob or local_id is None:
                    continue
                ext = voice_mod.sniff_extension(bytes(blob))
                svr_id = int(row["svr_id"] or 0)
                rel = voice_mod.voice_relative_path(
                    account_dir.name, media_db.stem, talker,
                    f"{int(local_id)}_{svr_id}", ext,
                )
                target = paths.MEDIA_DIR / rel
                try:
                    if not target.is_file() or target.stat().st_size != len(blob):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(bytes(blob))
                        written += 1
                except OSError as exc:
                    print(f"[warn] 写语音失败 {rel}: {exc}", file=sys.stderr)
                    continue
                mapping.setdefault((talker, int(local_id)), {})[svr_id] = rel
                if progress and len(mapping) % 500 == 0:
                    progress({
                        "stage": "微信语音",
                        "voices": len(mapping),
                        "written": written,
                    })
        finally:
            mcon.close()
    print(f"[i] 语音落盘: {len(mapping)} 条（本次新写 {written} 个文件）")
    return mapping


_HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
_XML_MD5 = re.compile(r'md5\s*=\s*"([0-9a-f]{32})"')
_XML_AESKEY = re.compile(r'aeskey\s*=\s*"([0-9a-f]{32})"')


def _media_meta(kind: str, xml_text: str, packed: Any) -> dict[str, str]:
    """从消息 XML / packed_info 里抠出图片/视频/表情的定位信息。"""
    meta: dict[str, str] = {}
    if kind not in ("image", "video", "sticker"):
        return meta
    match = _XML_MD5.search(xml_text or "")
    if match:
        meta["wx_md5"] = match.group(1)
    aes = _XML_AESKEY.search(xml_text or "")
    if aes:
        meta["wx_aeskey"] = aes.group(1)
    if isinstance(packed, bytes):
        found = _HEX32.findall(packed.decode("latin-1", "ignore"))
    elif isinstance(packed, str):
        found = _HEX32.findall(packed)
    else:
        found = []
    if found:
        meta["wx_bubble_md5"] = found[0]
    return meta


def _ensure_talker_conversation(
    conn: sqlite3.Connection,
    talker: str,
    *,
    names: dict[str, str],
    member_counts: dict[str, int],
    self_wxid: str,
    account_name: str = "",
) -> tuple[int, bool]:
    """确保 talker 对应的会话（以及私聊联系人）存在。

    返回 ``(conversation_id, 是否新建)``。重复导入时复用已有身份，
    不会每次导入都造一批新联系人。
    """
    is_group = talker.endswith("@chatroom")
    is_system = talker in SYSTEM_TALKERS
    title = names.get(talker, talker)

    contact_id = None
    if not is_group and not is_system:
        ident = conn.execute(
            "SELECT contact_id FROM identities WHERE platform='wechat'"
            " AND platform_uid=?",
            (talker,),
        ).fetchone()
        if ident:
            contact_id = int(ident["contact_id"])
        else:
            contact_id = db.ensure_contact(conn, title)
            db.ensure_identity(conn, contact_id, "wechat", talker, platform_name=title)

    kind = "group" if is_group else ("system" if is_system else "private")
    existed = conn.execute(
        "SELECT 1 FROM conversations WHERE platform='wechat' AND platform_conv_id=?",
        (talker,),
    ).fetchone()
    cid = db.ensure_conversation(
        conn,
        "wechat",
        talker,
        kind=kind,
        title=title,
        contact_id=contact_id,
        member_count=member_counts.get(talker),
        self_uid=self_wxid,
        extra=json.dumps(
            {"talker": talker, "account": account_name}, ensure_ascii=False
        ),
    )
    return cid, not bool(existed)


def import_account(
    account_dir: Path | str,
    *,
    progress: Progress = None,
    limit_messages: int | None = None,
    incremental: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """把一个账号目录导入统一库，返回统计信息。

    ``incremental=True`` 时只读上次水位之后的（回退一天防漏），
    老数据的语音/媒体补全需要全量导入。
    """
    account_dir = Path(account_dir)
    msg_dir = account_dir / "message"
    if not msg_dir.is_dir():
        raise FileNotFoundError(f"{account_dir} 下没有 message/ 目录")

    self_wxid = self_wxid_from_dir(account_dir)
    names, member_counts, _owners = _load_contacts(account_dir / "contact" / "contact.db")

    shards = sorted(
        (p for p in msg_dir.iterdir() if re.fullmatch(r"message_\d+\.db", p.name)),
        key=_shard_sort_key,
    )
    if not shards:
        raise FileNotFoundError(f"{msg_dir} 下没有 message_*.db")

    started = time.time()
    report = {
        "platform": "wechat",
        "source": str(account_dir),
        "account": self_wxid,
        "conversations_added": 0,
        "messages_added": 0,
        "messages_skipped": 0,
        "shards": len(shards),
        "started_at": int(started),
    }

    with db.session() as conn:
        watermark = _load_watermark(conn, self_wxid) if incremental else 0
        after_ts = max(0, watermark - 86400) if watermark else 0
        report["incremental"] = bool(incremental and watermark)
        report["scan_after_ts"] = after_ts
        run_id = conn.execute(
            "INSERT INTO import_runs(platform, source, mode, status, started_at)"
            " VALUES ('wechat', ?, ?, 'running', ?)",
            (str(account_dir), "incremental" if incremental else "full", int(started)),
        ).lastrowid
        touched: set[int] = set()
        total_seen = 0
        max_ts = 0
        emoticons = _load_emoticon_captions(account_dir)
        voices = _extract_voice_files(account_dir, progress=progress)
        report["voices_found"] = len(voices)

        def cancelled() -> bool:
            return bool(should_cancel and should_cancel())

        def finish(status: str) -> None:
            report["messages_seen"] = total_seen
            report["elapsed"] = round(time.time() - started, 1)
            report["finished_at"] = int(time.time())
            db.refresh_conversation_stats(conn, touched)
            conn.execute(
                "UPDATE import_runs SET status=?, finished_at=?, conversations_added=?,"
                " messages_added=?, messages_skipped=?, detail=? WHERE id=?",
                (
                    status,
                    report["finished_at"],
                    report["conversations_added"],
                    report["messages_added"],
                    report["messages_skipped"],
                    json.dumps(report, ensure_ascii=False),
                    run_id,
                ),
            )
            conn.commit()

        try:
            if cancelled():
                raise JobCancelled()
            for shard_index, shard in enumerate(shards, 1):
                scon = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
                try:
                    scon.row_factory = sqlite3.Row
                    n2i = {
                        r["rowid"]: r["user_name"]
                        for r in scon.execute("SELECT rowid, user_name FROM Name2Id")
                    }
                    by_md5 = {
                        hashlib.md5((u or "").encode()).hexdigest(): u
                        for u in n2i.values() if u
                    }
                    tables = [
                        r["name"] for r in scon.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                            " AND name LIKE 'Msg_%'"
                        )
                    ]
                    if progress:
                        progress({
                            "stage": "微信导入",
                            "shard": shard.name,
                            "shard_index": shard_index,
                            "shards": len(shards),
                            "messages": report["messages_added"],
                        })

                    for table in tables:
                        talker = by_md5.get(table[4:].lower())
                        if not talker:
                            continue
                        cid, created = _ensure_talker_conversation(
                            conn,
                            talker,
                            names=names,
                            member_counts=member_counts,
                            self_wxid=self_wxid,
                            account_name=account_dir.name,
                        )
                        if created:
                            report["conversations_added"] += 1
                        touched.add(cid)
                        try:
                            rows = _iter_message_rows(scon, table, after_ts)
                        except sqlite3.Error as exc:
                            print(f"[warn] {shard.name}/{table}: {exc}", file=sys.stderr)
                            continue

                        for r in rows:
                            total_seen += 1
                            if total_seen % 3000 == 0 and cancelled():
                                raise JobCancelled()

                            max_ts = max(max_ts, int(r["create_time"] or 0))
                            local_type = int(r["local_type"] or 0) & 0xFFFFFFFF
                            kind = WECHAT_LOCAL_TYPES.get(local_type, "other")
                            text = _decompress(
                                r["message_content"], r["WCDB_CT_message_content"]
                            )
                            if not text and r["compress_content"]:
                                text = _decompress(r["compress_content"], 1)
                            text = _clean_text(text)

                            raw_xml = text
                            parsed = parse_message(local_type, raw_xml)
                            meta = parsed.get("meta") or {}
                            if parsed.get("kind") not in (None, "", "other"):
                                kind = parsed["kind"]
                                text = parsed.get("text") or KIND_LABELS.get(kind, "")
                            else:
                                kind = WECHAT_LOCAL_TYPES.get(local_type, "other")
                                if kind == "text" and not text:
                                    kind = "other"
                                if kind == "share" and text:
                                    title = _share_title(text)
                                    text = title or KIND_LABELS["share"]
                                # 图片/语音等类型的 message_content 是 XML 或空，别把原始标记露给用户
                                if kind not in ("text", "share") and text.lstrip().startswith(("<", "{")):
                                    text = ""
                            # 纯文本的撤回提示（没有 XML）也识别出来
                            if kind == "system" and has_revoke_marker(text):
                                kind = "revoke"
                            if kind == "revoke" and not text:
                                text = "撤回了一条消息"
                            if kind == "sticker" and emoticons:
                                sticker = meta.setdefault("sticker", {})
                                md5 = (sticker.get("md5")
                                       or _media_meta(kind, raw_xml, r["packed_info_data"]).get("wx_md5"))
                                caption = emoticons.get(str(md5 or "").lower())
                                if caption:
                                    sticker["caption"] = caption

                            sender = n2i.get(r["real_sender_id"], "") or ""
                            is_self = bool(self_wxid) and sender == self_wxid
                            body = text if kind == "text" else (text or KIND_LABELS.get(kind, ""))
                            media_rel = None
                            if kind == "voice":
                                candidates = voices.get((talker, int(r["local_id"]))) or {}
                                media_rel = (
                                    candidates.get(int(r["server_id"] or 0))
                                    or next(iter(candidates.values()), None)
                                )

                            inserted = db.add_message(
                                conn,
                                cid,
                                "wechat",
                                f"{shard.name}:{table}:{r['local_id']}",
                                sender_uid=sender or None,
                                sender_name="我" if is_self else names.get(sender, sender),
                                is_self=is_self,
                                ts=int(r["create_time"] or 0),
                                kind=kind,
                                text=body,
                                media_path=media_rel,
                                duration=parsed.get("duration"),
                                seq=int(r["server_id"] or 0) or None,
                                extra=json.dumps(
                                    {
                                        **meta,
                                        "local_type": local_type,
                                        "shard": shard.name,
                                        **_media_meta(kind, raw_xml, r["packed_info_data"]),
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                            if inserted:
                                report["messages_added"] += 1
                            else:
                                report["messages_skipped"] += 1
                                # 之前导入过：把语音路径修正/补上
                                if media_rel:
                                    conn.execute(
                                        "UPDATE messages SET media_path=?"
                                        " WHERE platform='wechat' AND platform_msg_id=?"
                                        " AND (media_path IS NULL OR media_path != ?)",
                                        (media_rel, f"{shard.name}:{table}:{r['local_id']}",
                                         media_rel),
                                    )

                            if limit_messages and report["messages_added"] >= limit_messages:
                                break

                            if report["messages_added"] % 20000 == 0 and report["messages_added"]:
                                conn.commit()
                                if progress:
                                    progress({
                                        "stage": "微信导入",
                                        "shard": shard.name,
                                        "messages": report["messages_added"],
                                        "seen": total_seen,
                                    })
                        if limit_messages and report["messages_added"] >= limit_messages:
                            break
                finally:
                    scon.close()
                conn.commit()
                if limit_messages and report["messages_added"] >= limit_messages:
                    break
        except JobCancelled:
            report["cancelled"] = True
            finish("cancelled")
            raise

        _save_watermark(conn, self_wxid, max_ts)
        report["watermark"] = max_ts
        finish("ok")

    return report
