"""外观：归一化规则、主题目录 API、设置校验。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_normalizer():
    spec = importlib.util.spec_from_file_location(
        "fetch_stylekit", ROOT / "scripts" / "fetch_stylekit_themes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


norm = _load_normalizer()


# ── 形状解析 ───────────────────────────────────────────────────────

def test_parse_radius():
    assert norm.parse_radius("rounded-none") == 0
    assert norm.parse_radius("rounded-lg") == 12
    assert norm.parse_radius("rounded-[24px]") == 24
    # 响应式写法取较小值，保证不出现过度圆角
    assert norm.parse_radius("rounded-md md:rounded-lg") == 8
    assert norm.parse_radius(None) == 8


def test_parse_border_width():
    assert norm.parse_border_width("border") == 1
    assert norm.parse_border_width("border-2 md:border-4") == 4
    assert norm.parse_border_width("border-0") == 0
    assert norm.parse_border_width(None) == 1


def test_parse_shadow():
    hard = norm.parse_shadow("shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]", False)
    assert hard == "4px 4px 0px 0px rgba(0,0,0,1)"
    assert norm.parse_shadow("shadow-none", True) == "none"
    assert "rgba" in norm.parse_shadow("shadow-lg", True)


def test_parse_border_color():
    assert norm.parse_border_color("border-black", "#123456") == "#111111"
    assert norm.parse_border_color("border-[#ff006e]", "#123456") == "#ff006e"
    assert norm.parse_border_color("", "#123456") == "#123456"


# ── 主题推导 ───────────────────────────────────────────────────────

def _style(**over):
    base = {
        "slug": "demo",
        "name": "演示",
        "nameEn": "Demo",
        "category": "modern",
        "colors": {"primary": "#111111", "secondary": "#ffffff",
                   "accent": ["#ff006e", "#00d9ff"]},
        "tokens": {
            "border": {"width": "border-2", "radius": "rounded-none", "color": "border-black"},
            "shadow": {"md": "shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]"},
        },
    }
    base.update(over)
    return base


def test_build_light_theme():
    theme = norm.build_theme(_style())
    assert theme["dark"] is False
    assert theme["palette"]["bg"] == "#ffffff"
    assert theme["palette"]["border"] == "#111111"
    assert theme["shape"]["radius"] == 0
    assert theme["shape"]["borderWidth"] == 2
    assert theme["palette"]["shadow"].startswith("4px 4px")


def test_build_dark_theme():
    theme = norm.build_theme(_style(
        colors={"primary": "#3b82f6", "secondary": "#0f172a",
                "accent": ["#22c55e", "#f59e0b"]},
        tokens={"border": {"width": "border", "radius": "rounded-lg", "color": "border-gray-700"},
                "shadow": {"md": "shadow-lg"}},
    ))
    assert theme["dark"] is True
    assert theme["palette"]["bg"] == "#0f172a"
    # 深色背景上的文字必须够亮
    assert norm.luminance(norm.parse_color(theme["palette"]["text"])[:3]) > 0.5


def test_translucent_style_falls_back_to_dark():
    theme = norm.build_theme(_style(
        colors={"primary": "rgba(255, 255, 255, 0.08)",
                "secondary": "rgba(255, 255, 255, 0.05)",
                "accent": ["#0B1322", "#7C9CC4", "#E4B863"]},
    ))
    assert theme["dark"] is True
    assert theme["palette"]["bg"] == "#0b1220"


def test_accent_is_visible_against_bg():
    theme = norm.build_theme(_style())
    bg = norm.luminance(norm.parse_color(theme["palette"]["bg"])[:3])
    accent = norm.luminance(norm.parse_color(theme["palette"]["accent"])[:3])
    assert abs(bg - accent) > 0.08


# ── 目录与 API ─────────────────────────────────────────────────────

def test_catalog_has_151_themes():
    from app import themes

    data = themes.list_themes()
    assert data["count"] == 151
    assert len(data["themes"]) == 151
    slugs = [t["slug"] for t in data["themes"]]
    assert len(set(slugs)) == 151
    assert "neo-brutalist" in slugs


def test_theme_entries_have_palette():
    from app import themes

    required = {"bg", "panel", "border", "text", "textDim", "accent", "accentSoft"}
    for theme in themes.list_themes()["themes"]:
        assert required <= set(theme["palette"]), theme["slug"]
        assert theme["palette"]["bg"].startswith("#")
        assert theme["shape"]["radius"] >= 0


def test_themes_endpoint(client):
    data = client.get("/api/themes").json()
    assert data["count"] == 151
    assert data["license"] == "MIT"
    assert "stylekit" in data["sourceRepo"]


def test_settings_accepts_stylekit_theme(client):
    res = client.patch("/api/settings", json={"theme": "neo-brutalist"})
    assert res.status_code == 200
    assert res.json()["theme"] == "neo-brutalist"


def test_settings_rejects_unknown_theme(client):
    res = client.patch("/api/settings", json={"theme": "not-a-real-theme"})
    assert res.status_code == 400
