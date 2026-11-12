#!/usr/bin/env python3
"""生成一套完全合成的演示数据。

开源仓库里不放任何真实聊天记录。这个脚本负责造一份假数据，格式与真实
来源完全一致（微信解密目录 + 抖音 chat.db），所以它同时也是一个
「导入链路能不能跑通」的回归测试。

用法::

    python scripts/make_demo_data.py --out demo            # 只生成源数据
    python scripts/make_demo_data.py --out demo --import   # 再导入到 data/

生成的人物、内容、ID 全是编的，不含任何个人信息。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEED = 20260101
random.seed(SEED)

# ── 虚构人物 ────────────────────────────────────────────────────────
SELF_WXID = "wxid_demo0000self0001"
SELF_WX_NAME = "我"

PEOPLE = [
    # name,       wechat?, douyin?, douyin_uid
    ("林晓", True, True, "7000000000000000001"),
    ("陈默", True, False, None),
    ("赵一鸣", True, False, None),
    ("苏打水", False, True, "7000000000000000002"),
    ("阿橘", False, True, "7000000000000000003"),
    ("周与安", True, True, "7000000000000000004"),
]

GROUPS = [
    {
        "platform": "wechat",
        "name": "周末饭搭子",
        "members": ["林晓", "陈默", "赵一鸣", "周与安"],
    },
    {
        "platform": "douyin",
        "name": "剪辑互助群",
        "members": ["林晓", "苏打水", "阿橘"],
    },
]

WX_LINES = {
    "林晓": [
        "这周六有空吗，想去吃那家新开的川菜",
        "我看了下人均八十左右，还行",
        "那就这么定了，我订位",
        "到地铁口了，你出来吧",
        "照片我发你了，你看看喜不喜欢",
    ],
    "陈默": [
        "文件我改完了，放共享盘了",
        "第三页那个数字你再核一下",
        "我这边周五之前都能弄完",
        "收到，明天上午同步一下进度",
    ],
    "赵一鸣": [
        "周末去爬山不，天气预报说晴",
        "我带了护膝，上次下山膝盖有点疼",
        "山顶风大，记得多穿点",
    ],
    "周与安": [
        "上次说的那个相机我买了",
        "周末去江边试试镜头",
        "出片了发你看看",
    ],
}
DY_LINES = {
    "林晓": [
        "你那个视频剪辑用了什么转场",
        "我一般用叠化，比较自然",
        "这条点赞涨得挺快的",
        "下次一起拍个短的",
    ],
    "苏打水": [
        "剪到一半软件崩了，心态也崩了",
        "记得开自动保存",
        "素材我传到网盘了，链接发你",
        "这个 BGM 有点上头",
    ],
    "阿橘": [
        "这条片子的节奏我很喜欢",
        "前三秒留住人最重要",
        "我试试用你的开头方式",
    ],
    "周与安": [
        "你新发的那个作品封面做得不错",
        "用的什么字体",
        "我照着调了一下颜色",
    ],
}
SELF_LINES = [
    "好",
    "行，那就这样",
    "我看看",
    "收到",
    "等我十分钟",
    "哈哈可以",
    "没问题",
]
GROUP_LINES = [
    "今晚有人一起吃饭吗",
    "我七点能到",
    "地点定在哪儿",
    "老地方吧",
    "我带个朋友一起",
    "先到先占座",
]


# ── 小工具：生成极小的 PNG / WAV，避免引入 Pillow ───────────────────
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def make_wav(seconds: float = 0.6, rate: int = 16000) -> bytes:
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


# ── 生成微信解密目录 ────────────────────────────────────────────────
def build_wechat(out: Path) -> Path:
    account = out / "wechat" / "decrypted" / "wxid_demo0000_0001"
    msg_dir = account / "message"
    contact_dir = account / "contact"
    msg_dir.mkdir(parents=True, exist_ok=True)
    contact_dir.mkdir(parents=True, exist_ok=True)

    # 重复运行时先清掉上一次的库，保证结果可复现
    for stale in [contact_dir / "contact.db",
                  *msg_dir.glob("message_*.db"), *msg_dir.glob("media_*.db")]:
        if stale.exists():
            stale.unlink()

    con = sqlite3.connect(contact_dir / "contact.db")
    con.executescript(
        """
        CREATE TABLE contact(id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER,
            alias TEXT, encrypt_username TEXT, flag INTEGER, delete_flag INTEGER,
            remark TEXT, nick_name TEXT);
        CREATE TABLE chat_room(id INTEGER PRIMARY KEY, username TEXT, owner TEXT, ext_buffer BLOB);
        CREATE TABLE chatroom_member(room_id INTEGER, member_id INTEGER,
            CONSTRAINT room_member UNIQUE(room_id, member_id));
        CREATE TABLE name2id(username TEXT PRIMARY KEY);
        """
    )
    rows = [(1, SELF_WXID, 0, None, None, 0, 0, None, SELF_WX_NAME)]
    idx = 2
    wx_usernames: dict[str, str] = {}
    for name, has_wx, _has_dy, _uid in PEOPLE:
        if not has_wx:
            continue
        username = f"wxid_demo{idx:04d}{int(hashlib.md5(name.encode()).hexdigest()[:6], 16) % 100000:05d}"
        wx_usernames[name] = username
        rows.append((idx, username, 0, None, None, 0, 0, name, name))
        idx += 1
    con.executemany(
        "INSERT INTO contact(id, username, local_type, alias, encrypt_username, flag,"
        " delete_flag, remark, nick_name) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )

    group_members: dict[str, list[str]] = {}
    gid = 100
    for group in GROUPS:
        if group["platform"] != "wechat":
            continue
        gname = f"{gid}@chatroom"
        group_members[gname] = group["members"]
        con.execute(
            "INSERT INTO contact(id, username, local_type, alias, encrypt_username, flag,"
            " delete_flag, remark, nick_name) VALUES (?,?,?,?,?,?,?,?,?)",
            (gid, gname, 2, None, None, 0, 0, None, group["name"]),
        )
        con.execute("INSERT INTO chat_room(id, username, owner) VALUES (?,?,?)",
                    (gid, gname, wx_usernames.get(group["members"][0])))
        for offset, member in enumerate(group["members"]):
            con.execute("INSERT INTO chatroom_member(room_id, member_id) VALUES (?,?)",
                        (gid, 1000 + gid + offset))
        con.execute("INSERT INTO name2id(username) VALUES (?)", (gname,))
        gid += 1
    for username in [SELF_WXID, *wx_usernames.values()]:
        con.execute("INSERT INTO name2id(username) VALUES (?)", (username,))
    con.commit()
    con.close()

    # 会话 → 消息
    conversations: list[tuple[str, list[str], list[str]]] = []
    for name, has_wx, _has_dy, _uid in PEOPLE:
        if has_wx:
            conversations.append((f"private:{name}", [name], WX_LINES.get(name, SELF_LINES)))
    for gname, members in group_members.items():
        conversations.append((f"group:{gname}", members, GROUP_LINES))

    shard = sqlite3.connect(msg_dir / "message_0.db")
    shard.executescript(
        """
        CREATE TABLE Name2Id(rowid INTEGER PRIMARY KEY AUTOINCREMENT, user_name TEXT, is_session INTEGER);
        CREATE TABLE IF NOT EXISTS Msg_seed(local_id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER,
            message_content BLOB, compress_content BLOB, WCDB_CT_message_content INTEGER,
            packed_info_data BLOB);
        """
    )
    name2id: dict[str, int] = {}
    for username in [SELF_WXID, *wx_usernames.values()]:
        cur = shard.execute("INSERT INTO Name2Id(user_name, is_session) VALUES (?,0)", (username,))
        name2id[username] = cur.lastrowid
    for gname in group_members:
        cur = shard.execute("INSERT INTO Name2Id(user_name, is_session) VALUES (?,1)", (gname,))
        name2id[gname] = cur.lastrowid

    # 语音：微信把 blob 放在 media_*.db 的 VoiceInfo 里
    emoji_digests: list[tuple[str, str]] = []
    media = sqlite3.connect(msg_dir / "media_0.db")
    media.executescript(
        """
        CREATE TABLE Name2Id(rowid INTEGER PRIMARY KEY AUTOINCREMENT, user_name TEXT, is_session INTEGER);
        CREATE TABLE VoiceInfo(chat_name_id INTEGER, create_time INTEGER, local_id INTEGER,
            svr_id INTEGER, voice_data BLOB, data_index INTEGER);
        """
    )
    media_n2i: dict[str, int] = {}
    for username in [SELF_WXID, *wx_usernames.values()]:
        cur = media.execute("INSERT INTO Name2Id(user_name, is_session) VALUES (?,0)", (username,))
        media_n2i[username] = cur.lastrowid
    for gname in group_members:
        cur = media.execute("INSERT INTO Name2Id(user_name, is_session) VALUES (?,1)", (gname,))
        media_n2i[gname] = cur.lastrowid

    now = int(time.time())
    # 从 90 天前开始，按天推进
    day = now - 90 * 86400
    for conv_key, members, lines in conversations:
        talker = (
            wx_usernames[members[0]] if conv_key.startswith("private:") else conv_key.split(":", 1)[1]
        )
        table = "Msg_" + hashlib.md5(talker.encode()).hexdigest()
        shard.execute(
            f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY AUTOINCREMENT,'
            " server_id INTEGER, local_type INTEGER, real_sender_id INTEGER,"
            " create_time INTEGER, message_content BLOB, compress_content BLOB,"
            " WCDB_CT_message_content INTEGER, packed_info_data BLOB)"
        )
        ts = day + random.randint(0, 3600)
        for i, line in enumerate(lines):
            speaker = SELF_WXID if i % 2 else wx_usernames.get(random.choice(members))
            if talker.endswith("@chatroom"):
                speaker = random.choice([wx_usernames[m] for m in members] + [SELF_WXID])
            local_type = 1
            content: bytes | str = line
            packed = None
            is_voice = i == 1  # 每条会话放一条语音，用来演示播放
            if is_voice:
                local_type = 34
                content = '<msg><voicemsg length="1200" /></msg>'
            elif i == 2:
                local_type = 47  # 表情：md5 用来演示按需解密
                digest = hashlib.md5(f"emoji:{talker}:{i}".encode()).hexdigest()
                content = f'<msg><emoji md5="{digest}" len="562825" /></msg>'
                packed = b"\x08\x02\x10\x04"
                emoji_digests.append((digest, ("猫猫", "笑哭", "狗头")[i % 3]))
            elif i == 3 and conv_key.startswith("private:"):
                local_type = 3  # 图片
                digest = hashlib.md5(f"img:{talker}:{i}".encode()).hexdigest()
                content = (
                    f'<msg><img md5="{digest}" aeskey="'
                    + hashlib.md5(f"aes:{talker}".encode()).hexdigest()
                    + '" /></msg>'
                )
                packed = b"\x08\x04\x10\x02\x1a\x22\x22 " + digest.encode()
            elif i == 4:
                local_type = 43  # 视频
                digest = hashlib.md5(f"video:{talker}:{i}".encode()).hexdigest()
                content = f'<msg><videomsg md5="{digest}" length="3798003" playlength="32" /></msg>'
                packed = b"\x08\x04\x10\x02\x22\x22\x42 " + digest.encode()
            cur = shard.execute(
                f'INSERT INTO "{table}"(server_id, local_type, real_sender_id, create_time,'
                " message_content, compress_content, WCDB_CT_message_content, packed_info_data)"
                " VALUES (?,?,?,?,?,NULL,0,?)",
                (1000 + i, local_type, name2id[speaker], ts, content, packed),
            )
            if is_voice and talker in media_n2i:
                media.execute(
                    "INSERT INTO VoiceInfo(chat_name_id, create_time, local_id, svr_id,"
                    " voice_data, data_index) VALUES (?,?,?,0,?,0)",
                    (media_n2i[talker], ts, cur.lastrowid, make_wav(1.2)),
                )
            ts += random.randint(90, 3600)
    shard.commit()
    shard.close()
    media.commit()
    media.close()
    # 表情说明库（真实微信里就是 emoticon.db 的 kNonStoreEmoticonTable）
    emoticon_dir = account / "emoticon"
    emoticon_dir.mkdir(parents=True, exist_ok=True)
    emo = sqlite3.connect(emoticon_dir / "emoticon.db")
    emo.executescript(
        "DROP TABLE IF EXISTS kNonStoreEmoticonTable;"
        "CREATE TABLE kNonStoreEmoticonTable(md5 TEXT, caption TEXT);"
    )
    emo.executemany("INSERT INTO kNonStoreEmoticonTable(md5, caption) VALUES (?,?)",
                    emoji_digests)
    emo.commit()
    emo.close()
    return account


# ── 生成抖音 chat.db ───────────────────────────────────────────────
def dy_conv_id(uid: str) -> str:
    return f"0:1:{SELF_DY_UID}:{uid}"


SELF_DY_UID = "7000000000000000000"


def build_douyin(out: Path) -> Path:
    base = out / "douyin"
    media = base / "media"
    for sub in ("images", "emoji", "voice", "avatars", "videos"):
        (media / sub).mkdir(parents=True, exist_ok=True)
    db_path = base / "chat.db"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE users (uid TEXT PRIMARY KEY, nickname TEXT, avatar_url TEXT, unique_id TEXT);
        CREATE TABLE conversations (conv_id TEXT PRIMARY KEY, conv_type INTEGER DEFAULT 1,
            name TEXT, participant_uids TEXT DEFAULT '[]', last_message_time INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0, avatar_url TEXT);
        CREATE TABLE messages (msg_id TEXT PRIMARY KEY, conv_id TEXT NOT NULL, sender_uid TEXT,
            sender_name TEXT, content TEXT, msg_type INTEGER DEFAULT 1, media_url TEXT,
            media_local_path TEXT, timestamp INTEGER, seq INTEGER DEFAULT 0,
            raw_data TEXT, ref_msg TEXT);
        CREATE TABLE voice_transcriptions (msg_id TEXT PRIMARY KEY, message_id TEXT NOT NULL,
            text_result TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'success',
            error TEXT, updated_at INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX idx_messages_conv ON messages(conv_id, timestamp);
        """
    )
    con.execute("INSERT INTO users VALUES (?,?,?,?)", (SELF_DY_UID, SELF_WX_NAME, None, None))

    now = int(time.time())
    day = now - 88 * 86400
    seq = 0
    for name, _has_wx, has_dy, uid in PEOPLE:
        if not has_dy:
            continue
        con.execute("INSERT INTO users VALUES (?,?,?,?)", (uid, name, f"avatars/{uid}.png", f"dy{uid[-4:]}"))
        (media / "avatars" / f"{uid}.png").write_bytes(make_png(48, 48, (60, 90, 180)))
        conv_id = dy_conv_id(uid)
        con.execute(
            "INSERT INTO conversations(conv_id, conv_type, name, participant_uids,"
            " last_message_time, message_count, avatar_url) VALUES (?,1,?,'[]',?,?,?)",
            (conv_id, name, 0, 0, f"avatars/{uid}.png"),
        )
        ts = day + random.randint(0, 7200)
        for i, line in enumerate(DY_LINES.get(name, SELF_LINES)):
            seq += 1
            sender = SELF_DY_UID if i % 2 else uid
            msg_type = 1
            content = line
            media_path = None
            raw = {"aweType": 0}
            if i == 2:
                msg_type = 3
                fname = f"images/demo_{uid[-4:]}_{i}.png"
                (media / fname).write_bytes(make_png(160, 120, (200, 120, 90)))
                media_path = fname
                content = "[图片]"
                raw = {"aweType": 1001}
            elif i == 1:
                msg_type = 0
                fname = f"voice/demo_{uid[-4:]}_{i}.mpeg"
                (media / fname).write_bytes(make_wav(0.7))
                media_path = fname
                content = "[语音 1秒]"
                raw = {"aweType": 2702, "duration": 1}
            elif i == 3:
                msg_type = 2
                fname = f"emoji/demo_{uid[-4:]}.png"
                (media / fname).write_bytes(make_png(64, 64, (250, 210, 80)))
                media_path = fname
                content = "[表情]"
                raw = {"aweType": 2025}
            msg_id = f"demo_{uid[-6:]}_{i:03d}"
            con.execute(
                "INSERT INTO messages(msg_id, conv_id, sender_uid, sender_name, content,"
                " msg_type, media_url, media_local_path, timestamp, seq, raw_data, ref_msg)"
                " VALUES (?,?,?,?,?,?,NULL,?,?,?,?,NULL)",
                (msg_id, conv_id, sender, name, content, msg_type, media_path, ts, seq,
                 json.dumps(raw, ensure_ascii=False)),
            )
            if msg_type == 0:
                con.execute(
                    "INSERT INTO voice_transcriptions VALUES (?,?,?,?,NULL,?)",
                    (msg_id, msg_id, "（语音转写）这是一段合成的演示语音，用来展示转写文本。",
                     "success", now),
                )
            ts += random.randint(120, 5400)
        con.execute(
            "UPDATE conversations SET last_message_time=?, message_count=?"
            " WHERE conv_id=?",
            (ts, len(DY_LINES.get(name, SELF_LINES)), conv_id),
        )

    # 一个抖音群
    group_conv = "7649000000000000001"
    con.execute(
        "INSERT INTO conversations(conv_id, conv_type, name, participant_uids,"
        " last_message_time, message_count, avatar_url) VALUES (?,1,?,'[]',?,?,NULL)",
        (group_conv, "剪辑互助群", 0, 0),
    )
    ts = day + 4000
    for i, line in enumerate(GROUP_LINES):
        seq += 1
        member = PEOPLE[(i % 3) + 3][3] if (i % 3) + 3 < len(PEOPLE) else PEOPLE[0][3]
        member = [uid for _n, _w, h, uid in PEOPLE if h][i % 3]
        con.execute(
            "INSERT INTO messages(msg_id, conv_id, sender_uid, sender_name, content,"
            " msg_type, media_url, media_local_path, timestamp, seq, raw_data, ref_msg)"
            " VALUES (?,?,?,?,?,1,NULL,NULL,?,?,?,NULL)",
            (f"demo_group_{i:03d}", group_conv, member, "", line, ts, seq,
             json.dumps({"aweType": 0})),
        )
        ts += random.randint(200, 3000)
    con.execute("UPDATE conversations SET last_message_time=?, message_count=6 WHERE conv_id=?",
                (ts, group_conv))
    con.commit()
    con.close()
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description="生成合成演示数据")
    ap.add_argument("--out", default=str(ROOT / "demo"), help="输出目录")
    ap.add_argument("--import", dest="do_import", action="store_true",
                    help="生成后立刻导入统一库")
    ap.add_argument("--data", default=None, help="导入到哪个数据目录")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wx_dir = build_wechat(out)
    dy_dir = build_douyin(out)
    print(f"[✓] 微信演示库: {wx_dir}")
    print(f"[✓] 抖音演示库: {dy_dir}")

    if args.do_import:
        if args.data:
            os.environ["CHATWORK_DATA_DIR"] = str(Path(args.data).resolve())
        from app import db, paths  # noqa: E402
        from app.importers import douyin as dy
        from app.importers import wechat as wx

        paths.ensure_dirs()
        db.init()
        r1 = wx.import_account(wx_dir, progress=None)
        print(f"[✓] 导入微信: 会话 +{r1['conversations_added']} 消息 +{r1['messages_added']}")
        r2 = dy.import_chat_db(dy_dir / "chat.db", media_src=dy_dir / "media", copy_media=True)
        print(f"[✓] 导入抖音: 会话 +{r2['conversations_added']} 消息 +{r2['messages_added']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
