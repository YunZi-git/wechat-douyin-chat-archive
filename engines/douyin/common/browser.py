"""浏览器选择：优先用系统自带的 Edge，省掉下载 Chromium。

上游原本固定用 Playwright 自带的 Chromium；在便携包里那意味着额外 ~150MB。
Windows 10/11 一定装有 Edge，而 Playwright 支持 ``channel="msedge"`` 直接驱动它，
所以这里做一层「有 Edge 就用 Edge，没有就回退 Chromium」的封装。

环境变量 ``CHATWORK_BROWSER_CHANNEL`` 可强制指定：
``msedge`` / ``chrome`` / 空字符串（表示用内置 Chromium）。
"""
from __future__ import annotations

import os
from pathlib import Path

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def edge_path() -> str | None:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def browsers_root() -> Path | None:
    """Playwright 下载的浏览器默认放在这里。"""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env not in ("0", "1"):
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ms-playwright"
    return None


def bundled_chromium() -> bool:
    root = browsers_root()
    if not root or not root.is_dir():
        return False
    try:
        return any(root.glob("chromium-*"))
    except OSError:
        return False


def preferred_channel() -> str:
    """默认 msedge；没有 Edge 就返回空串（走内置 Chromium）。"""
    forced = os.environ.get("CHATWORK_BROWSER_CHANNEL")
    if forced is not None:
        return forced.strip()
    return "msedge" if edge_path() else ""


async def launch_persistent(pw, user_data_dir, **kwargs):
    """启动持久化上下文；Edge 失败时自动回退到内置 Chromium。"""
    channel = preferred_channel()
    if channel:
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir, channel=channel, **kwargs
            )
            print(f"[i] 已用系统浏览器启动（{channel}）")
            return context
        except Exception as exc:  # noqa: BLE001 - 回退比中断更友好
            print(f"[!] 用系统浏览器（{channel}）启动失败：{exc}")
            print("[i] 回退到内置 Chromium（需要先执行 playwright install chromium）")
    return await pw.chromium.launch_persistent_context(user_data_dir, **kwargs)
