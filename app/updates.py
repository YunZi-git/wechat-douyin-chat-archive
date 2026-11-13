"""版本与更新检查。

便携版没有安装器，所以「自动更新」做到：检查更新源、展示版本/说明/下载地址，
不尝试覆盖正在运行的程序（Windows 上也做不到安全自替换）。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

APP_VERSION = "1.1.0"
USER_AGENT = f"chatwork/{APP_VERSION}"


def parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(value or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _fetch_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check(url: str | None, *, current: str = APP_VERSION) -> dict[str, Any]:
    """检查更新清单。

    清单格式兼容两种：

    * 自定义：``{"version": "1.2.0", "notes": "...", "url": "..."}``
    * GitHub Release：``{"tag_name": "v1.2.0", "body": "...", "html_url": "..."}``
    """
    target = (url or "").strip()
    base = {"current": current, "checked": False, "available": False}
    if not target:
        return {**base, "reason": "还没有配置更新地址"}

    try:
        data = _fetch_json(target)
    except Exception as exc:  # noqa: BLE001 - 网络问题统一报给界面
        return {**base, "checked": True, "error": f"{type(exc).__name__}: {exc}"}

    latest = str(data.get("version") or data.get("tag_name") or "").strip()
    if not latest:
        return {**base, "checked": True, "error": "更新清单里没有 version/tag_name"}

    available = parse_version(latest) > parse_version(current)
    return {
        "current": current,
        "latest": latest,
        "available": available,
        "checked": True,
        "notes": str(data.get("notes") or data.get("body") or "")[:2000],
        "url": data.get("url") or data.get("html_url") or "",
    }
