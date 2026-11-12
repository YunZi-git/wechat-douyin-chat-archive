#!/usr/bin/env python3
"""命令行导入工具（不启动界面也能用）。

用法::

    python scripts/import_cli.py wechat  <解密后的账号目录>
    python scripts/import_cli.py douyin  <chat.db> [--media DIR] [--no-copy]
    python scripts/import_cli.py stats

数据目录默认是仓库下的 ``data/``，可用 ``--data DIR`` 或环境变量
``CHATWORK_DATA_DIR`` 覆盖。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _progress(payload: dict) -> None:
    stage = payload.get("stage", "")
    if "messages" in payload:
        sys.stdout.write(f"\r[{stage}] 已写入 {payload['messages']} 条   ")
    elif "copied" in payload:
        sys.stdout.write(f"\r[{stage}] 已复制 {payload['copied']} 个文件   ")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="聊天信息工作台 · 导入工具")
    parser.add_argument("--data", help="数据目录（默认 ./data）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_wx = sub.add_parser("wechat", help="导入已解密的微信账号目录")
    p_wx.add_argument("account_dir")
    p_wx.add_argument("--limit", type=int, default=None, help="最多导入多少条（调试用）")

    p_dy = sub.add_parser("douyin", help="导入抖音 chat.db")
    p_dy.add_argument("chat_db")
    p_dy.add_argument("--media", default=None, help="媒体目录（默认取 chat.db 同级的 media/）")
    p_dy.add_argument("--no-copy", action="store_true", help="不复制媒体文件")
    p_dy.add_argument("--limit", type=int, default=None)

    sub.add_parser("stats", help="查看统一库统计")

    args = parser.parse_args()
    if args.data:
        os.environ["CHATWORK_DATA_DIR"] = str(Path(args.data).resolve())

    from app import db, paths  # noqa: E402  必须在设置环境变量之后导入

    paths.ensure_dirs()
    db.init()

    if args.cmd == "stats":
        print(json.dumps(db.stats(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "wechat":
        from app.importers import wechat

        report = wechat.import_account(
            Path(args.account_dir), progress=_progress, limit_messages=args.limit
        )
        print()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "douyin":
        from app.importers import douyin

        chat_db = Path(args.chat_db)
        media = Path(args.media) if args.media else (chat_db.parent / "media")
        report = douyin.import_chat_db(
            chat_db,
            media_src=media,
            copy_media=not args.no_copy,
            progress=_progress,
            limit_messages=args.limit,
        )
        print()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
