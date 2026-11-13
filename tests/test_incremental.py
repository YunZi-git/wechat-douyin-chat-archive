"""微信账号、增量导入与表情说明。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

from app import db
from app.importers import wechat


def test_accounts_endpoint(client, library):
    data = client.get("/api/accounts").json()
    assert data["items"]
    account = data["items"][0]
    assert account["uid"]
    assert account["messages"] > 0

    filtered = client.get("/api/threads", params={"account": account["uid"]}).json()
    assert filtered["total"] >= 1


def test_watermark_saved(library):
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT k, v FROM meta WHERE k LIKE 'wx_wm:%' LIMIT 1"
        ).fetchone()
    assert row is not None
    assert int(row["v"]) > 0


def _append_source_message(account: Path, text: str) -> int:
    shard = sorted((account / "message").glob("message_*.db"))[0]
    con = sqlite3.connect(shard)
    con.row_factory = sqlite3.Row
    try:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'Msg_%'"
            " AND name != 'Msg_seed' LIMIT 1"
        ).fetchone()["name"]
        sender = con.execute(
            f'SELECT real_sender_id FROM "{table}" ORDER BY local_id DESC LIMIT 1'
        ).fetchone()["real_sender_id"]
        ts = int(time.time()) + 3600
        con.execute(
            f'INSERT INTO "{table}"(server_id, local_type, real_sender_id,'
            " create_time, message_content, WCDB_CT_message_content)"
            " VALUES (?,?,?,?,?,0)",
            (990001, 1, sender, ts, text.encode("utf-8")),
        )
        con.commit()
        return ts
    finally:
        con.close()


def test_incremental_import_picks_up_new_messages(library, tmp_path):
    # 复制一份再改，避免污染 session 级的演示数据（别的用例还在用）
    source = Path(library["wechat"]["source"])
    account = tmp_path / "account" / source.name
    shutil.copytree(source, account)
    _append_source_message(account, "增量导入的新消息")

    report = wechat.import_account(account, incremental=True)
    assert report["messages_added"] == 1
    assert report["scan_after_ts"] > 0

    again = wechat.import_account(account, incremental=True)
    assert again["messages_added"] == 0


def test_sticker_caption_from_emoticon_db(library):
    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT extra FROM messages WHERE kind='sticker' LIMIT 1"
        ).fetchone()
    assert row is not None
    meta = json.loads(row["extra"])
    assert meta["sticker"].get("caption")


def test_reimport_merges_new_metadata(library, tmp_path):
    import shutil

    source = Path(library["wechat"]["source"])
    account = tmp_path / "account" / source.name
    shutil.copytree(source, account)

    with db.session(readonly=True) as conn:
        row = conn.execute(
            "SELECT id, platform_msg_id, extra FROM messages"
            " WHERE platform='wechat' AND kind='sticker' LIMIT 1"
        ).fetchone()
    assert row is not None
    old_meta = json.loads(row["extra"])
    assert old_meta["sticker"].get("cdnurl")

    stripped = json.loads(row["extra"])
    stripped["sticker"].pop("cdnurl", None)
    stripped["sticker"].pop("caption", None)
    with db.session() as conn:
        conn.execute(
            "UPDATE messages SET extra=? WHERE id=?",
            (json.dumps(stripped, ensure_ascii=False), row["id"]),
        )

    report = wechat.import_account(account)
    assert report.get("metadata_updated", 0) >= 1
    with db.session(readonly=True) as conn:
        merged = json.loads(
            conn.execute("SELECT extra FROM messages WHERE id=?", (row["id"],)).fetchone()["extra"]
        )
    assert merged["sticker"].get("cdnurl")
    assert merged["sticker"].get("caption")
