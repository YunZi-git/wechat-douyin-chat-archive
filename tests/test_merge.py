"""合并 / 拆分：跨平台时间线与不变式。"""
from __future__ import annotations

import pytest

from app import contacts, threads


def _find(name: str, platform: str) -> dict:
    for item in contacts.list_contacts():
        if item["display_name"] == name and item["platforms"] == [platform]:
            return item
    raise AssertionError(f"没找到 {name}（{platform}）")


def test_merge_creates_cross_platform_contact(library):
    wx = _find("林晓", "wechat")
    dy = _find("林晓", "douyin")

    merged = contacts.merge_contacts(wx["id"], dy["id"], display_name="林晓")
    assert merged["platforms"] == ["douyin", "wechat"]
    assert len(merged["identities"]) == 2
    assert len(merged["conversations"]) == 2

    # 被合并的那条应该消失
    names = [(c["display_name"], tuple(c["platforms"])) for c in contacts.list_contacts()]
    assert ("林晓", ("wechat",)) not in names
    assert ("林晓", ("douyin",)) not in names


def test_merged_timeline_interleaves(library):
    wx = _find("林晓", "wechat")
    dy = _find("林晓", "douyin")
    contacts.merge_contacts(wx["id"], dy["id"])

    thread_id = f"contact:{wx['id']}"
    result = threads.thread_messages(thread_id, limit=100)
    messages = result["items"]

    assert len(messages) == 9
    assert {m["platform"] for m in messages} == {"wechat", "douyin"}
    timestamps = [m["ts"] for m in messages]
    assert timestamps == sorted(timestamps), "时间线必须按时间升序"


def test_platform_filter_narrows_timeline(library):
    wx = _find("林晓", "wechat")
    dy = _find("林晓", "douyin")
    contacts.merge_contacts(wx["id"], dy["id"])

    thread_id = f"contact:{wx['id']}"
    only_dy = threads.thread_messages(thread_id, limit=100, platform="douyin")["items"]
    assert only_dy
    assert {m["platform"] for m in only_dy} == {"douyin"}

    only_wx = threads.thread_messages(thread_id, limit=100, platform="wechat")["items"]
    assert {m["platform"] for m in only_wx} == {"wechat"}


def test_unmerge_restores_two_contacts(library):
    wx = _find("林晓", "wechat")
    dy = _find("林晓", "douyin")
    contacts.merge_contacts(wx["id"], dy["id"])

    result = contacts.unmerge_contact(wx["id"], "douyin")
    new_id = result["contact_id"]
    assert new_id != wx["id"]

    left = contacts.get_contact(wx["id"])
    moved = contacts.get_contact(new_id)
    assert left["platforms"] == ["wechat"]
    assert moved["platforms"] == ["douyin"]
    assert left["conversations"] and moved["conversations"]


def test_merge_same_contact_rejected(library):
    wx = _find("林晓", "wechat")
    with pytest.raises(ValueError):
        contacts.merge_contacts(wx["id"], wx["id"])


def test_merge_unknown_contact_rejected(library):
    wx = _find("林晓", "wechat")
    with pytest.raises(ValueError):
        contacts.merge_contacts(wx["id"], 99999)


def test_unmerge_single_platform_rejected(library):
    wx = _find("林晓", "wechat")
    with pytest.raises(ValueError):
        contacts.unmerge_contact(wx["id"], "wechat")


def test_rename_contact(library):
    wx = _find("陈默", "wechat")
    updated = contacts.rename_contact(wx["id"], "陈默（同事）")
    assert updated["display_name"] == "陈默（同事）"
    with pytest.raises(ValueError):
        contacts.rename_contact(wx["id"], "   ")


def test_thread_info_reports_sources(library):
    wx = _find("林晓", "wechat")
    dy = _find("林晓", "douyin")
    contacts.merge_contacts(wx["id"], dy["id"])

    info = threads.thread_info(f"contact:{wx['id']}")
    assert info["merged"] is True
    assert set(info["platforms"]) == {"wechat", "douyin"}
    assert len(info["identities"]) == 2
    assert {c["platform"] for c in info["conversations"]} == {"wechat", "douyin"}
