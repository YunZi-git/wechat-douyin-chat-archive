#!/usr/bin/env python3
"""把 StyleKit 的 151 个设计风格归一化成这个应用能直接用的主题表。

数据来源：https://www.stylekit.top/api/styles/all.json
上游仓库：https://github.com/AnxForever/stylekit （MIT）

输出：``app/data/stylekit_themes.json``，每个主题包含

* 调色板（背景/面板/边框/文字/强调色的具体色值）
* 形状（圆角 px、边框宽度 px、阴影 CSS）
* 元信息（slug / 中文名 / 英文名 / 分类 / 是否深色 / 描述）

用法::

    python scripts/fetch_stylekit_themes.py            # 下载并生成
    python scripts/fetch_stylekit_themes.py --offline  # 用缓存/已有文件

仓库里已经提交了生成结果，正常开发不需要联网。
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "data" / "stylekit_themes.json"
CACHE = ROOT / ".cache" / "stylekit-all.json"
SOURCE = "https://www.stylekit.top"
CATALOG_URL = f"{SOURCE}/api/styles/all.json"

RADIUS_MAP = {
    "rounded-none": 0,
    "rounded-sm": 4,
    "rounded": 6,
    "rounded-md": 8,
    "rounded-lg": 12,
    "rounded-xl": 16,
    "rounded-2xl": 20,
    "rounded-3xl": 26,
    "rounded-full": 999,
}

BORDER_WIDTH_MAP = {
    "border": 1, "border-0": 0, "border-2": 2, "border-4": 4, "border-8": 8,
}

# 色板兜底：上游给的颜色偶尔是半透明或非常规写法
FALLBACK_ACCENTS = ["#6c8cff", "#f0647a", "#38c98a", "#e0a25c", "#9d7bff"]


# ── 颜色工具 ────────────────────────────────────────────────────────

def parse_color(value: str | None) -> tuple[int, int, int, float] | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3,8})", text)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
        if len(h) == 8:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16),
                    int(h[6:8], 16) / 255)
    m = re.fullmatch(
        r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)",
        text,
    )
    if m:
        r, g, b = (int(float(m.group(i))) for i in range(1, 4))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return r, g, b, a
    return None


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mix(a: str, b: str, t: float) -> str:
    ca, cb = parse_color(a), parse_color(b)
    if not ca or not cb:
        return a
    return to_hex(tuple(ca[i] * (1 - t) + cb[i] * t for i in range(3)))


def rgba(hex_color: str, alpha: float) -> str:
    c = parse_color(hex_color)
    if not c:
        return hex_color
    return f"rgba({c[0]}, {c[1]}, {c[2]}, {alpha:g})"


def saturation(rgb: tuple[int, int, int]) -> float:
    mx, mn = max(rgb), min(rgb)
    return 0.0 if mx == 0 else (mx - mn) / mx


def pick_accent(colors: dict[str, Any], bg: str, dark: bool) -> tuple[str, list[str]]:
    """从上游 accent 列表里挑一个跟背景对比度足够的颜色。"""
    raw = colors.get("accent") or []
    if isinstance(raw, str):
        raw = [raw]
    accents = [c for c in (parse_color(str(x)) for x in raw) if c and c[3] > 0.5]
    bg_parsed = parse_color(bg) or (255, 255, 255, 1)
    bg_lum = luminance(bg_parsed[:3])

    def score(c: tuple[int, int, int, float]) -> float:
        contrast = abs(luminance(c[:3]) - bg_lum)
        # 深色背景上偏亮、浅色背景上偏暗，同时鼓励饱和度高一点
        direction = (luminance(c[:3]) - bg_lum) if dark else (bg_lum - luminance(c[:3]))
        return contrast * 2 + direction + saturation(c[:3]) * 0.4

    if not accents:
        return FALLBACK_ACCENTS[0], []
    best = max(accents, key=score)
    return to_hex(best[:3]), [to_hex(c[:3]) for c in accents]


# ── 形状工具 ────────────────────────────────────────────────────────

def parse_radius(token: str | None) -> int:
    text = str(token or "").strip()
    m = re.search(r"rounded-\[(\d+)px\]", text)
    if m:
        return int(m.group(1))
    # 按空格拆 token、去掉 md:/lg: 之类前缀后精确匹配，
    # 否则 "rounded-lg" 会同时命中 "rounded"（6px）
    found = []
    for raw in text.split():
        part = raw.split(":")[-1]
        if part in RADIUS_MAP:
            found.append(RADIUS_MAP[part])
    return min(found) if found else 8


def parse_border_width(token: str | None) -> int:
    text = str(token or "").strip()
    found = [BORDER_WIDTH_MAP[name] for name in BORDER_WIDTH_MAP
             if re.search(rf"(?<![\w-]){name}(?![\w-])", text)]
    return max(found) if found else 1


def parse_border_color(token: str | None, fallback: str) -> str:
    """上游用 class 名描述边框色，能解析就解析，解析不出用推导值。"""
    text = str(token or "")
    if "border-black" in text:
        return "#111111"
    if "border-white" in text:
        return "#ffffff"
    m = re.search(r"border-\[(#[0-9a-fA-F]{3,8})\]", text)
    if m:
        c = parse_color(m.group(1))
        if c:
            return to_hex(c[:3])
    return fallback


def parse_shadow(token: str | None, dark: bool) -> str:
    text = str(token or "")
    if "shadow-none" in text and "shadow-[" not in text:
        return "none"
    m = re.search(r"shadow-\[([^\]]+)\]", text)
    if m:
        value = m.group(1).replace("_", " ")
        if value.startswith("inset"):
            return "none"
        return value
    if "shadow-sm" in text:
        return "0 2px 6px rgba(0,0,0,0.18)"
    if "shadow-md" in text or "shadow-lg" in text or "shadow-xl" in text:
        return "0 8px 24px rgba(0,0,0,0.24)" if dark else "0 8px 24px rgba(0,0,0,0.12)"
    return "0 8px 24px rgba(0,0,0,0.24)" if dark else "0 6px 18px rgba(0,0,0,0.10)"


# ── 单个风格 → 主题 ─────────────────────────────────────────────────

def build_theme(style: dict[str, Any]) -> dict[str, Any]:
    colors = style.get("colors") or {}
    tokens = style.get("tokens") or {}
    primary_raw = parse_color(str(colors.get("primary") or ""))
    secondary_raw = parse_color(str(colors.get("secondary") or ""))

    primary = to_hex(primary_raw[:3]) if primary_raw and primary_raw[3] > 0.5 else None
    secondary = to_hex(secondary_raw[:3]) if secondary_raw and secondary_raw[3] > 0.5 else None

    primary_lum = luminance(parse_color(primary)[:3]) if primary else 0.5
    secondary_lum = luminance(parse_color(secondary)[:3]) if secondary else 0.95

    # 半透明配色（玻璃拟态这类）没有可用的实色底，按深色玻璃处理
    translucent = (
        (primary_raw is None or primary_raw[3] < 0.5)
        and (secondary_raw is None or secondary_raw[3] < 0.5)
    )

    # 判断底色：优先用「更暗的那个」当深色主题背景
    if translucent:
        dark = True
        bg = "#0b1220"
        text = "#e8eefc"
    elif secondary and secondary_lum < 0.42:
        dark = True
        bg = secondary
        text = "#eceff6" if primary_lum < 0.6 else (primary or "#eceff6")
    elif primary and primary_lum < 0.42:
        dark = False
        bg = secondary if secondary and secondary_lum > 0.6 else "#f7f8fa"
        text = primary
    else:
        # 两个都偏亮（neumorphism）或半透明（glassmorphism）
        dark = False
        bg = secondary if secondary and secondary_lum > 0.55 else "#eef1f6"
        text = "#20242d"

    accent, all_accents = pick_accent(colors, bg, dark)

    panel = mix(bg, "#ffffff" if dark else "#ffffff", 0.06 if dark else 0.62)
    panel2 = mix(bg, "#ffffff" if dark else "#000000", 0.10 if dark else 0.05)
    panel3 = mix(bg, "#ffffff" if dark else "#000000", 0.15 if dark else 0.09)
    border = mix(bg, "#ffffff" if dark else "#000000", 0.14)
    border_soft = mix(bg, "#ffffff" if dark else "#000000", 0.09 if dark else 0.08)
    border_color = parse_border_color((tokens.get("border") or {}).get("color"), border)
    border_soft_color = mix(border_color, bg, 0.45)
    text_dim = mix(text, bg, 0.34)
    text_faint = mix(text, bg, 0.55)

    dark_bg = mix(bg, "#000000", 0.18) if dark else mix(bg, "#000000", 0.06)
    radius = parse_radius((tokens.get("border") or {}).get("radius"))
    border_width = parse_border_width((tokens.get("border") or {}).get("width"))
    shadow = parse_shadow((tokens.get("shadow") or {}).get("md")
                          or (tokens.get("shadow") or {}).get("sm"), dark)

    return {
        "slug": style.get("slug"),
        "name": style.get("name") or style.get("nameEn") or style.get("slug"),
        "nameEn": style.get("nameEn") or "",
        "category": style.get("category") or "modern",
        "description": (style.get("description") or "").strip()[:180],
        "dark": dark,
        "accents": all_accents or [accent],
        "palette": {
            "bg": bg,
            "bgRail": dark_bg,
            "panel": panel,
            "panel2": panel2,
            "panel3": panel3,
            "border": border_color,
            "borderSoft": border_soft_color,
            "borderStrong": border_color,
            "text": text,
            "textDim": text_dim,
            "textFaint": text_faint,
            "accent": accent,
            "accentSoft": rgba(accent, 0.16),
            "shadow": shadow,
        },
        "shape": {
            "radius": radius,
            "borderWidth": border_width,
        },
        "source": "stylekit",
    }


def build_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    styles = payload.get("styles") or []
    themes = []
    seen: set[str] = set()
    for style in styles:
        slug = str(style.get("slug") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        themes.append(build_theme(style))
    return {
        "license": "MIT",
        "source": SOURCE,
        "sourceRepo": "https://github.com/AnxForever/stylekit",
        "count": len(themes),
        "themes": themes,
    }


def load_payload(offline: bool) -> dict[str, Any]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    if offline:
        raise SystemExit(f"离线模式但缓存不存在：{CACHE}")
    print(f"[i] 下载 {CATALOG_URL}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(CATALOG_URL, timeout=120) as resp:
        CACHE.write_bytes(resp.read())
    return json.loads(CACHE.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 StyleKit 主题表")
    ap.add_argument("--offline", action="store_true", help="只用本地缓存")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    payload = load_payload(args.offline)
    catalog = build_catalog(payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[✓] {catalog['count']} 个主题 -> {out} "
          f"({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
