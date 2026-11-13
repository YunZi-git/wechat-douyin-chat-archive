"""日志脱敏：去掉本机用户名和仓库绝对路径。

任务日志会直接显示给用户，也会写进 ``data/logs/app.log``；里面经常出现
``C:\\Users\\alice\\AppData\\...`` 这类路径。这里统一替换成占位符。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_PATTERNS = [
    # Windows 用户目录：C:\Users\alice\...
    (re.compile(r"([A-Za-z]:\\Users\\)[^\\\s\"'<>|]+"), r"\1<user>"),
    # Windows 旧式：C:\Documents and Settings\alice
    (re.compile(r"([A-Za-z]:\\Documents and Settings\\)[^\\\s\"'<>|]+"), r"\1<user>"),
    # macOS / Linux 家目录
    (re.compile(r"(/Users/)[^/\s\"'<>|]+"), r"\1<user>"),
    (re.compile(r"(/home/)[^/\s\"'<>|]+"), r"\1<user>"),
]


def redact(text: str) -> str:
    if not text:
        return text
    result = str(text)
    # 仓库路径整段替换，避免暴露安装位置
    for needle in {str(ROOT), ROOT.as_posix()}:
        if needle:
            result = result.replace(needle, "<app>")
    for pattern, repl in _PATTERNS:
        result = pattern.sub(repl, result)
    return result
