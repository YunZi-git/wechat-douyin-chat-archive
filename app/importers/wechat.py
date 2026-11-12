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

from .. import db, paths, threads

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


def _message_rows(con: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return con.execute(
        f'SELECT local_id, server_id, local_type, real_sender_id, create_time,'
        f' message_content, compress_content, WCDB_CT_message_content'
        f' FROM "{table}" ORDER BY create_time, local_id'
    ).fetchall()


def import_account(
    account_dir: Path | str,
    *,
    progress: Progress = None,
    limit_messages: int | None = None,
) -> dict[str, Any]:
    """把一个账号目录导入统一库，返回统计信息。"""
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
        run_id = conn.execute(
            "INSERT INTO import_runs(platform, source, mode, status, started_at)"
            " VALUES ('wechat', ?, 'full', 'running', ?)",
            (str(account_dir), int(started)),
        ).lastrowid
        conv_cache: dict[str, int] = {}
        total_seen = 0

        def conv_for(talker: str) -> int:
            nonlocal report
            if talker in conv_cache:
                return conv_cache[talker]
            is_group = talker.endswith("@chatroom")
            is_system = talker in SYSTEM_TALKERS
            title = names.get(talker, talker)
            contact_id = None
            avatar = None
            if not is_group and not is_system:
                # 重复导入时复用已有身份，避免每次导入都造一个新的联系人
                ident = conn.execute(
                    "SELECT contact_id FROM identities WHERE platform='wechat'"
                    " AND platform_uid=?",
                    (talker,),
                ).fetchone()
                if ident:
                    contact_id = int(ident["contact_id"])
                else:
                    contact_id = db.ensure_contact(conn, title, avatar_url=avatar)
                    db.ensure_identity(
                        conn, contact_id, "wechat", talker, platform_name=title
                    )
            kind = "group" if is_group else ("system" if is_system else "private")
            existed = conn.execute(
                "SELECT 1 FROM conversations WHERE platform='wechat'"
                " AND platform_conv_id=?",
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
                extra=json.dumps({"talker": talker}, ensure_ascii=False),
            )
            if not existed:
                report["conversations_added"] += 1
            conv_cache[talker] = cid
            return cid

        for shard_index, shard in enumerate(shards, 1):
            scon = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
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
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
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
                cid = conv_for(talker)
                try:
                    rows = _message_rows(scon, table)
                except sqlite3.Error as exc:
                    print(f"[warn] {shard.name}/{table}: {exc}", file=sys.stderr)
                    continue

                for r in rows:
                    total_seen += 1
                    local_type = int(r["local_type"] or 0) & 0xFFFFFFFF
                    kind = WECHAT_LOCAL_TYPES.get(local_type, "other")
                    text = _decompress(
                        r["message_content"], r["WCDB_CT_message_content"]
                    )
                    if not text and r["compress_content"]:
                        text = _decompress(r["compress_content"], 1)
                    text = _clean_text(text)

                    if kind == "text" and not text:
                        kind = "other"
                    if kind == "share" and text:
                        title = _share_title(text)
                        text = title or KIND_LABELS["share"]
                    # 图片/语音等类型的 message_content 是 XML 或空，别把原始标记露给用户
                    if kind not in ("text", "share") and text.lstrip().startswith(("<", "{")):
                        text = ""

                    sender = n2i.get(r["real_sender_id"], "") or ""
                    is_self = bool(self_wxid) and sender == self_wxid
                    if kind == "text":
                        body = text
                    else:
                        body = text or KIND_LABELS.get(kind, "")

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
                        seq=int(r["server_id"] or 0) or None,
                        extra=json.dumps(
                            {"local_type": local_type, "shard": shard.name},
                            ensure_ascii=False,
                        ),
                    )
                    if inserted:
                        report["messages_added"] += 1
                    else:
                        report["messages_skipped"] += 1

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
            scon.close()
            conn.commit()
            if limit_messages and report["messages_added"] >= limit_messages:
                break

        report["messages_seen"] = total_seen
        report["elapsed"] = round(time.time() - started, 1)
        report["finished_at"] = int(time.time())
        conn.execute(
            "UPDATE import_runs SET status='ok', finished_at=?, conversations_added=?,"
            " messages_added=?, messages_skipped=?, detail=? WHERE id=?",
            (
                report["finished_at"],
                report["conversations_added"],
                report["messages_added"],
                report["messages_skipped"],
                json.dumps(report, ensure_ascii=False),
                run_id,
            ),
        )
        conn.commit()

    threads.invalidate_cache()

    # 记下本人 wxid，方便以后增量判断
    if self_wxid:
        from .. import settings as settings_mod

        if not settings_mod.load().get("wechat_self_wxid"):
            settings_mod.save({"wechat_self_wxid": self_wxid})
    return report
