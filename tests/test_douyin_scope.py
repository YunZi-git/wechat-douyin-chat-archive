"""抖音采集范围：会话列表、include/exclude 过滤、参数校验。"""
from __future__ import annotations

import shutil

import pytest

from app import paths, services


@pytest.fixture()
def douyin_db(demo_sources):
    paths.DOUYIN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(demo_sources["douyin"] / "chat.db", paths.DOUYIN_DB)
    yield paths.DOUYIN_DB


def test_conversations_endpoint_lists_local_db(client, library, douyin_db):
    data = client.get("/api/douyin/conversations").json()
    assert data["available"] is True
    assert data["total"] == len(data["items"]) == 5
    names = {item["name"] for item in data["items"]}
    assert "剪辑互助群" in names
    assert all("message_count" in item for item in data["items"])


def test_conversations_fallback_to_unified(client, library):
    """没有引擎库时退回统一库（先导入 chat.db、后补采集的场景）。"""
    if paths.DOUYIN_DB.exists():
        paths.DOUYIN_DB.unlink()
    data = client.get("/api/douyin/conversations").json()
    assert data["available"] is True
    assert data["source"] == "unified"
    assert data["total"] == 5


def test_filter_all_is_none():
    assert services._douyin_filter("all", None) is None


def test_filter_include_joins_names():
    result = services._douyin_filter("include", ["A", "B", "A"])
    assert result == "A,B"


def test_filter_include_requires_selection():
    with pytest.raises(ValueError):
        services._douyin_filter("include", [])


def test_filter_include_caps_count():
    with pytest.raises(ValueError):
        services._douyin_filter("include", [f"n{i}" for i in range(501)])


def test_filter_exclude_subtracts_known(douyin_db):
    known = [item["name"] for item in services.douyin_conversations()["items"]]
    drop = known[0]
    result = services._douyin_filter("exclude", [drop])
    kept = set(result.split(","))
    assert drop not in kept
    assert len(kept) == len(known) - 1


def test_filter_exclude_without_local_list(client, library):
    if paths.DOUYIN_DB.exists():
        paths.DOUYIN_DB.unlink()
    # 统一库也清空后，才真的没有可选列表
    from app import db
    with db.session() as conn:
        conn.execute("DELETE FROM conversations WHERE platform='douyin'")
    with pytest.raises(ValueError, match="本地还没有会话列表"):
        services._douyin_filter("exclude", ["任意"])


def test_filter_unknown_mode():
    with pytest.raises(ValueError):
        services._douyin_filter("nope", ["A"])


def test_collect_rejects_empty_include(client, library):
    res = client.post("/api/douyin/collect", json={
        "mode": "include", "conversations": [],
    })
    assert res.status_code == 400
    assert "勾选" in res.json()["detail"]


def test_refresh_rejects_empty_include(client, library):
    res = client.post("/api/douyin/refresh", json={
        "mode": "include", "conversations": [],
    })
    assert res.status_code == 400


def test_collect_rejects_unknown_mode(client, library):
    res = client.post("/api/douyin/collect", json={
        "mode": "nope", "conversations": ["A"],
    })
    assert res.status_code == 422
