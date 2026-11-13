"""联系人的合并与拆分。

合并的语义：两个「人」其实是同一个人 —— 把身份和会话都挂到一个联系人下，
时间线自然就交错在一起了。拆分是它的逆操作。

所有操作都在一个事务里完成，失败不会留下半个合并。
"""
from __future__ import annotations

import time
from typing import Any

from . import db


def _contact(conn, contact_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    return dict(row) if row else None


def list_contacts(*, q: str | None = None, merged: bool | None = None) -> list[dict[str, Any]]:
    """联系人列表（合并页面用），附带平台身份与会话数。"""
    like = f"%{q.strip()}%" if q else None
    with db.session(readonly=True) as conn:
        rows = conn.execute(
            """SELECT ct.*,
                      (SELECT GROUP_CONCAT(i.platform || ':' || i.platform_uid)
                         FROM identities i WHERE i.contact_id = ct.id) AS identities_raw,
                      (SELECT GROUP_CONCAT(i.platform_name, ' / ')
                         FROM identities i WHERE i.contact_id = ct.id) AS platform_names,
                      (SELECT COUNT(*) FROM conversations c WHERE c.contact_id = ct.id) AS conversation_count,
                      (SELECT COUNT(*) FROM messages m
                         JOIN conversations c2 ON c2.id = m.conversation_id
                        WHERE c2.contact_id = ct.id) AS msg_count,
                      (SELECT MAX(m.ts) FROM messages m
                         JOIN conversations c3 ON c3.id = m.conversation_id
                        WHERE c3.contact_id = ct.id) AS last_ts,
                      COALESCE(ct.avatar_url, (
                          SELECT i2.avatar_url FROM identities i2
                           WHERE i2.contact_id = ct.id
                             AND i2.avatar_url IS NOT NULL AND i2.avatar_url <> ''
                           ORDER BY i2.id LIMIT 1)) AS display_avatar
                 FROM contacts ct
                WHERE (? IS NULL OR ct.display_name LIKE ?)
                ORDER BY last_ts DESC NULLS LAST, ct.display_name""",
            (like, like),
        ).fetchall()

    items = []
    for r in rows:
        item = dict(r)
        ids = []
        for token in (item.pop("identities_raw", None) or "").split(","):
            if ":" in token:
                platform, _, uid = token.partition(":")
                ids.append({"platform": platform, "platform_uid": uid})
        item["identities"] = ids
        item["platforms"] = sorted({i["platform"] for i in ids})
        item["merged"] = len(item["platforms"]) > 1
        if merged is not None and item["merged"] != merged:
            continue
        items.append(item)
    return items


def merge_contacts(primary_id: int, secondary_id: int, *, display_name: str | None = None) -> dict[str, Any]:
    if primary_id == secondary_id:
        raise ValueError("不能和自己合并")
    with db.session() as conn:
        primary = _contact(conn, primary_id)
        secondary = _contact(conn, secondary_id)
        if not primary or not secondary:
            raise ValueError("联系人不存在")

        conn.execute(
            "UPDATE identities SET contact_id=? WHERE contact_id=?",
            (primary_id, secondary_id),
        )
        conn.execute(
            "UPDATE conversations SET contact_id=? WHERE contact_id=?",
            (primary_id, secondary_id),
        )
        patch = {"updated_at": int(time.time())}
        if display_name:
            patch["display_name"] = display_name
        if not primary.get("note") and secondary.get("note"):
            patch["note"] = secondary["note"]
        if not primary.get("avatar_url") and secondary.get("avatar_url"):
            patch["avatar_url"] = secondary["avatar_url"]
        sets = ", ".join(f"{k}=?" for k in patch)
        conn.execute(
            f"UPDATE contacts SET {sets} WHERE id=?", [*patch.values(), primary_id]
        )
        conn.execute("DELETE FROM contacts WHERE id=?", (secondary_id,))

    return get_contact(primary_id) or {}


def unmerge_contact(contact_id: int, platform: str) -> dict[str, Any]:
    """把某个平台的身份拆出去，单独成为一个联系人。"""
    with db.session() as conn:
        identity = conn.execute(
            "SELECT * FROM identities WHERE contact_id=? AND platform=?",
            (contact_id, platform),
        ).fetchone()
        if not identity:
            raise ValueError(f"该联系人没有 {platform} 身份")
        total = conn.execute(
            "SELECT COUNT(*) FROM identities WHERE contact_id=?", (contact_id,)
        ).fetchone()[0]
        if total <= 1:
            raise ValueError("只剩一个平台身份，无法拆分")

        new_id = db.ensure_contact(
            conn,
            identity["platform_name"] or "未命名",
            avatar_url=identity["avatar_url"],
        )
        conn.execute(
            "UPDATE identities SET contact_id=? WHERE id=?", (new_id, identity["id"])
        )
        conn.execute(
            "UPDATE conversations SET contact_id=? WHERE contact_id=? AND platform=?",
            (new_id, contact_id, platform),
        )
        conn.execute(
            "UPDATE contacts SET updated_at=? WHERE id=?",
            (int(time.time()), contact_id),
        )
    return {"contact_id": new_id, "original": get_contact(contact_id)}


def rename_contact(contact_id: int, display_name: str, *, note: str | None = None) -> dict[str, Any]:
    name = (display_name or "").strip()
    if not name:
        raise ValueError("名字不能为空")
    with db.session() as conn:
        cur = conn.execute(
            "UPDATE contacts SET display_name=?, note=COALESCE(?, note), updated_at=?"
            " WHERE id=?",
            (name, note, int(time.time()), contact_id),
        )
        if cur.rowcount == 0:
            raise ValueError("联系人不存在")
    return get_contact(contact_id) or {}


def get_contact(contact_id: int) -> dict[str, Any] | None:
    with db.session(readonly=True) as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not row:
            return None
        identities = conn.execute(
            "SELECT * FROM identities WHERE contact_id=? ORDER BY platform",
            (contact_id,),
        ).fetchall()
        convs = conn.execute(
            "SELECT * FROM conversations WHERE contact_id=? ORDER BY platform",
            (contact_id,),
        ).fetchall()
    item = dict(row)
    item["identities"] = [dict(r) for r in identities]
    item["conversations"] = [dict(r) for r in convs]
    item["platforms"] = sorted({r["platform"] for r in identities})
    item["merged"] = len(item["platforms"]) > 1
    return item
