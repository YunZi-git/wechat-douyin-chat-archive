"""统一的路径解析。

应用既可以跑在仓库里（开发），也可以跑在便携包里（打包后）。
优先级：CHATWORK_HOME > 冻结后的 exe 所在目录 > 仓库根目录。

所有会写入的状态都放在 ``<home>/data`` 下，可用 CHATWORK_DATA_DIR 覆盖，
这样测试和打包都只需要改一个环境变量。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_root() -> Path:
    env = os.environ.get("CHATWORK_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()

DATA_DIR = Path(
    os.environ.get("CHATWORK_DATA_DIR") or (APP_ROOT / "data")
).expanduser().resolve()

# ── 统一数据库与配置 ────────────────────────────────────────────────
UNIFIED_DB = DATA_DIR / "unified.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

# ── 媒体与导出 ─────────────────────────────────────────────────────
MEDIA_DIR = DATA_DIR / "media"
EXPORT_DIR = DATA_DIR / "exports"
LOG_DIR = DATA_DIR / "logs"

# ── 微信源数据 ─────────────────────────────────────────────────────
WECHAT_DIR = DATA_DIR / "wechat"
WECHAT_DECRYPTED = WECHAT_DIR / "decrypted"

# ── 抖音源数据（上游引擎原样使用） ─────────────────────────────────
# chat.db 与浏览器登录态留在 data/douyin/；媒体统一放 data/media/douyin/，
# 这样统一库里的 media_path 永远是 "douyin/<相对路径>"，只有一套解析规则。
DOUYIN_DIR = DATA_DIR / "douyin"
DOUYIN_DB = DOUYIN_DIR / "chat.db"
DOUYIN_MEDIA = MEDIA_DIR / "douyin"

# ── 内置引擎与前端产物 ─────────────────────────────────────────────
ENGINES_DIR = APP_ROOT / "engines"
SIWX_DIR = ENGINES_DIR / "siwx"
DOUYIN_ENGINE_DIR = ENGINES_DIR / "douyin"
WEB_DIST = APP_ROOT / "web" / "dist"


def ensure_dirs() -> None:
    """建好所有必须存在的目录（幂等）。"""
    for d in (
        DATA_DIR,
        MEDIA_DIR,
        EXPORT_DIR,
        LOG_DIR,
        WECHAT_DIR,
        WECHAT_DECRYPTED,
        DOUYIN_DIR,
        DOUYIN_MEDIA,
    ):
        d.mkdir(parents=True, exist_ok=True)


def media_url(rel_path: str | None) -> str | None:
    """把数据库里的相对媒体路径变成前端可访问的 URL。"""
    if not rel_path:
        return None
    return "/media/" + rel_path.replace("\\", "/").lstrip("/")


def to_media_relative(abs_path: str | os.PathLike | None) -> str | None:
    """把绝对路径收敛成相对 MEDIA_DIR 的路径；不在 MEDIA_DIR 内则返回 None。"""
    if not abs_path:
        return None
    try:
        p = Path(abs_path).resolve()
        return p.relative_to(MEDIA_DIR).as_posix()
    except (ValueError, OSError):
        return None
