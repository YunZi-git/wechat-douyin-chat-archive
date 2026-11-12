#!/usr/bin/env python3
"""便携包的入口：起后端 + 开原生窗口。

放在包根目录，由 启动.cmd 调用。没有控制台时可读的错误会写进
data/logs/launcher.log，并用系统弹窗提示，避免"双击没反应"。
"""
from __future__ import annotations

import sys
import traceback


def _write_log(text: str) -> None:
    try:
        from app import paths

        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (paths.LOG_DIR / "launcher.log").write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _message_box(title: str, text: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        from app.desktop import run

        return run()
    except Exception:  # noqa: BLE001 - 双击启动时没有控制台，必须自己兜住
        detail = traceback.format_exc()
        _write_log(detail)
        _message_box(
            "聊天信息工作台启动失败",
            "启动时出错，详细信息已写入：\n"
            "data\\logs\\launcher.log\n\n"
            + detail.strip().splitlines()[-1][:300],
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
