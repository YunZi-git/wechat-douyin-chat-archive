"""外观主题目录。

151 个风格来自 StyleKit（MIT），已归一化成我们的 CSS 变量表，见
``app/data/stylekit_themes.json``（由 ``scripts/fetch_stylekit_themes.py`` 生成）。
另外保留 4 个内置主题，方便老用户和首屏兜底。

这个模块只读文件 + 做一次缓存，不改任何状态。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data" / "stylekit_themes.json"

BUILTIN_SLUGS = ("dark", "light", "warm", "purple", "system")


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    try:
        payload = json.loads(DATA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"count": 0, "license": "MIT", "themes": []}
    return payload


@lru_cache(maxsize=1)
def by_slug() -> dict[str, dict[str, Any]]:
    return {t["slug"]: t for t in catalog().get("themes", [])}


def is_valid(slug: str | None) -> bool:
    if not slug:
        return False
    return slug in BUILTIN_SLUGS or slug in by_slug()


def list_themes() -> dict[str, Any]:
    data = catalog()
    return {
        "count": data.get("count", len(data.get("themes", []))),
        "license": data.get("license", "MIT"),
        "source": data.get("source", ""),
        "sourceRepo": data.get("sourceRepo", ""),
        "builtin": list(BUILTIN_SLUGS),
        "themes": data.get("themes", []),
    }
