"""导出：ChatLab 结构、Markdown、平台过滤。"""
from __future__ import annotations

import json

from app import contacts, threads
from app.exporters import chatlab


def _merge_lin(library) -> str:
    items = [c for c in contacts.list_contacts() if c["display_name"] == "林晓"]
    wx = next(c for c in items if c["platforms"] == ["wechat"])
    dy = next(c for c in items if c["platforms"] == ["douyin"])
    contacts.merge_contacts(wx["id"], dy["id"])
    threads.invalidate_cache()
    return f"contact:{wx['id']}"


def test_chatlab_header_and_members(library):
    thread_id = _merge_lin(library)
    header, members, messages = chatlab.build_export(thread_id)

    assert header["chatlab"]["version"] == chatlab.CHATLAB_VERSION
    assert header["meta"]["platform"] == "mixed"
    assert set(header["meta"]["platforms"]) == {"wechat", "douyin"}
    assert header["meta"]["type"] == "private"
    assert len(members) >= 2
    assert len(messages) == 9
    assert all(m["platformMessageId"] for m in messages)


def test_chatlab_types_are_normalised(library):
    thread_id = _merge_lin(library)
    _header, _members, messages = chatlab.build_export(thread_id)
    types = {m["type"] for m in messages}
    assert types <= {0, 1, 5, 24, 26, 99}
    # 至少有一条图片(1)和一条表情(5)
    assert 1 in types and 5 in types


def test_export_jsonl_file_is_wellformed(library, tmp_path):
    thread_id = _merge_lin(library)
    target = chatlab.export_thread(thread_id, fmt="jsonl", out_dir=tmp_path)

    lines = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["_type"] == "header"
    assert any(line["_type"] == "member" for line in lines)
    messages = [line for line in lines if line["_type"] == "message"]
    assert len(messages) == 9
    assert messages[0]["timestamp"] <= messages[-1]["timestamp"]


def test_export_json_file(library, tmp_path):
    thread_id = _merge_lin(library)
    target = chatlab.export_thread(thread_id, fmt="json", out_dir=tmp_path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["chatlab"]["generator"] == chatlab.GENERATOR
    assert len(payload["messages"]) == 9
    assert payload["members"]


def test_export_single_platform(library, tmp_path):
    thread_id = _merge_lin(library)
    header, _members, messages = chatlab.build_export(thread_id, platform="douyin")
    assert header["meta"]["platform"] == "douyin"
    assert len(messages) == 4

    target = chatlab.export_thread(thread_id, fmt="jsonl", platform="douyin", out_dir=tmp_path)
    assert "_douyin_" in target.name


def test_export_markdown(library, tmp_path):
    thread_id = _merge_lin(library)
    target = chatlab.export_markdown(thread_id, out_dir=tmp_path)
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# 与林晓的对话")
    assert "## " in text                      # 有日期分组
    assert "林晓" in text


def test_export_listing(library):
    thread_id = _merge_lin(library)
    chatlab.export_thread(thread_id, fmt="jsonl")
    items = chatlab.list_exports()
    assert items
    assert {"name", "size", "url"} <= set(items[0])


def test_safe_filename_strips_illegal_chars():
    assert chatlab.safe_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert chatlab.safe_filename("   ") == "conversation"
    assert chatlab.safe_filename(None) == "conversation"
