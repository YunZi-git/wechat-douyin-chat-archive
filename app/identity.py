"""「我」是谁：查看 / 修改每个平台的本人身份。

聊天界面左右分栏依赖 ``messages.is_self``，而 ``is_self`` 在导入时确定：

* 微信：解密目录名就是登录账号（``wxid_xxx``），自动识别；
* 抖音：取私聊 ``conv_id`` 里出现率最高的 uid，属于**推断**；
* 导入别人导出的 ``chat.db`` 时，推断出来的是「导出者」，不是当前用户。

所以需要给用户一个显式修改入口：改完重算该平台所有消息的 ``is_self``。
"""
from __future__ import annotations

from typing import Any

from . import db, settings

PLATFORMS = ("wechat", "douyin")
SETTING_KEYS = {"wechat": "wechat_self_wxid", "douyin": "douyin_self_uid"}


def _contact_name(conn, platform: str, uid: str) -> str:
    row = conn.execute(
        """SELECT c.display_name FROM identities i
             JOIN contacts c ON c.id = i.contact_id
            WHERE i.platform = ? AND i.platform_uid = ? LIMIT 1""",
        (platform, uid),
    ).fetchone()
    return (row["display_name"] or "").strip() if row else ""


def _current(conn, platform: str) -> tuple[str, str]:
    cfg = settings.load()
    uid = str(cfg.get(SETTING_KEYS[platform]) or "").strip()
    if uid:
        return uid, "manual" if cfg.get(f"{platform}_self_confirmed") else "saved"
    row = conn.execute(
        """SELECT self_uid, COUNT(*) AS n FROM conversations
            WHERE platform = ? AND self_uid IS NOT NULL AND self_uid <> ''
            GROUP BY self_uid ORDER BY n DESC LIMIT 1""",
        (platform,),
    ).fetchone()
    if row and row["self_uid"]:
        return str(row["self_uid"]), "account" if platform == "wechat" else "guessed"
    row = conn.execute(
        """SELECT sender_uid FROM messages
            WHERE platform = ? AND is_self = 1 AND sender_uid IS NOT NULL
            GROUP BY sender_uid ORDER BY COUNT(*) DESC LIMIT 1""",
        (platform,),
    ).fetchone()
    if row and row["sender_uid"]:
        return str(row["sender_uid"]), "message"
    return "", ""


def overview() -> dict[str, Any]:
    cfg = settings.load()
    out: dict[str, Any] = {"platforms": {}, "needs_setup": False, "needs_confirm": False}
    with db.session(readonly=True) as conn:
        for platform in PLATFORMS:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE platform = ?", (platform,)
            ).fetchone()["n"]
            uid, source = _current(conn, platform)
            rows = conn.execute(
                """SELECT sender_uid AS uid, MAX(sender_name) AS name, COUNT(*) AS n
                     FROM messages
                    WHERE platform = ? AND sender_uid IS NOT NULL AND sender_uid <> ''
                    GROUP BY sender_uid ORDER BY n DESC LIMIT 20""",
                (platform,),
            ).fetchall()
            candidates = []
            for row in rows:
                name = (row["name"] or "").strip()
                if name == "我":
                    name = _contact_name(conn, platform, row["uid"]) or "我（自己）"
                candidates.append({
                    "uid": row["uid"],
                    "name": name or row["uid"],
                    "message_count": int(row["n"] or 0),
                    "is_self": row["uid"] == uid,
                })
            name = ""
            if uid:
                name = _contact_name(conn, platform, uid)
                if not name:
                    for c in candidates:
                        if c["uid"] == uid:
                            name = c["name"]
                            break
            needs_setup = bool(total) and not uid
            needs_confirm = (
                platform == "douyin" and bool(total) and bool(uid)
                and not bool(cfg.get("douyin_self_confirmed"))
            )
            out["platforms"][platform] = {
                "uid": uid,
                "name": name or ("我" if uid else ""),
                "source": source,
                "message_count": total,
                "candidates": candidates,
                "needs_confirm": needs_confirm,
            }
            out["needs_setup"] = out["needs_setup"] or needs_setup
            out["needs_confirm"] = out["needs_confirm"] or needs_confirm
    return out


def set_identity(platform: str, uid: str) -> dict[str, Any]:
    uid = (uid or "").strip()
    if platform not in PLATFORMS:
        raise ValueError(f"未知平台：{platform}")
    if not uid:
        raise ValueError("请选择「我」对应的账号")
    with db.session() as conn:
        exists = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE platform = ? AND sender_uid = ?",
            (platform, uid),
        ).fetchone()["n"]
        if not exists:
            raise ValueError("这个账号在该平台下没有消息，无法设为「我」")
        old_uid, _ = _current(conn, platform)
        # 先把旧的「我」改回真实昵称，避免换人后旧消息左侧还写着「我」
        if old_uid and old_uid != uid:
            conn.execute(
                """UPDATE messages SET sender_name = COALESCE(
                       (SELECT c.display_name FROM identities i
                          JOIN contacts c ON c.id = i.contact_id
                         WHERE i.platform = ? AND i.platform_uid = messages.sender_uid
                         LIMIT 1),
                       CASE WHEN sender_name = '我' THEN sender_uid ELSE sender_name END)
                    WHERE platform = ? AND sender_uid = ? AND sender_name = '我'""",
                (platform, platform, old_uid),
            )
        conn.execute(
            """UPDATE messages
                  SET is_self = CASE WHEN sender_uid = ? THEN 1 ELSE 0 END,
                      sender_name = CASE WHEN sender_uid = ? THEN '我' ELSE sender_name END
                WHERE platform = ?""",
            (uid, uid, platform),
        )
        conn.execute(
            "UPDATE conversations SET self_uid = ? WHERE platform = ?", (uid, platform)
        )
        conv_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM conversations WHERE platform = ?", (platform,)
            ).fetchall()
        ]
        db.refresh_conversation_stats(conn, conv_ids)
        conn.commit()
        self_count = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE platform = ? AND is_self = 1",
            (platform,),
        ).fetchone()["n"]
    settings.save({
        SETTING_KEYS[platform]: uid,
        f"{platform}_self_confirmed": True,
    })
    return {"platform": platform, "uid": uid, "self_messages": int(self_count)}
