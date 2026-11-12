"""微信 XML 语义解析：引用、文件、合并转发、位置、名片、撤回等。"""
from __future__ import annotations

from app.wechat_message import has_revoke_marker, parse_message

QUOTE = (
    "<msg><appmsg><title>好啊，周六见</title><type>57</type>"
    "<refermsg><chatusr>wxid_demo0000001</chatusr><displayname>小林</displayname>"
    "<content>周末有空吗？</content><type>1</type></refermsg></appmsg></msg>"
)

FILE = (
    "<msg><appmsg><title>需求文档.pdf</title><type>6</type>"
    "<appattach><totallen>123456</totallen><fileext>pdf</fileext></appattach></appmsg></msg>"
)

FORWARD = (
    "<msg><appmsg><title>聊天记录</title><type>19</type><recorditem>"
    "<recordinfo><datalist><dataitem datatype='1'><datadesc>第一条</datadesc></dataitem>"
    "<dataitem datatype='1'><datadesc>第二条</datadesc></dataitem></datalist>"
    "</recordinfo></recorditem></appmsg></msg>"
)

LINK = (
    "<msg><appmsg><title>一篇好文章</title><des>摘要</des><type>5</type>"
    "<url>https://example.com/post</url></appmsg></msg>"
)

MINI = (
    "<msg><appmsg><title>点餐小程序</title><type>33</type>"
    "<sourcedisplayname>某餐厅</sourcedisplayname></appmsg></msg>"
)

LOCATION = '<msg><location x="31.23" y="121.47" label="上海市" poiname="外滩" /></msg>'
CARD = '<msg bigheadimgurl="http://example.com/a.jpg" nickname="老王" alias="wang" />'
REVOKE = (
    '<?xml version="1.0"?><sysmsg type="revokemsg"><revokemsg>'
    "<content>小林撤回了一条消息</content></revokemsg></sysmsg>"
)
VIDEO = '<msg><videomsg playlength="12" length="1234567" /></msg>'
STICKER = '<msg><emoji md5="0123456789abcdef0123456789abcdef" width="120" height="90" /></msg>'


def test_quote_reply():
    parsed = parse_message(49, QUOTE)
    assert parsed["kind"] == "text"
    assert parsed["text"] == "好啊，周六见"
    assert parsed["meta"]["quote"]["sender"] == "小林"
    assert parsed["meta"]["quote"]["content"] == "周末有空吗？"


def test_file_message():
    parsed = parse_message(49, FILE)
    assert parsed["kind"] == "file"
    assert parsed["text"] == "需求文档.pdf"
    assert parsed["meta"]["file"]["size"] == 123456
    assert parsed["meta"]["file"]["ext"] == "pdf"


def test_merged_forward():
    parsed = parse_message(49, FORWARD)
    assert parsed["kind"] == "forward"
    assert parsed["meta"]["forward"]["count"] == 2
    assert "2 条" in parsed["text"]


def test_link_share():
    parsed = parse_message(49, LINK)
    assert parsed["kind"] == "share"
    assert parsed["meta"]["url"] == "https://example.com/post"


def test_miniprogram():
    parsed = parse_message(49, MINI)
    assert parsed["kind"] == "miniprogram"
    assert parsed["meta"]["miniprogram"]["source"] == "某餐厅"


def test_location():
    parsed = parse_message(48, LOCATION)
    assert parsed["kind"] == "location"
    assert parsed["text"] == "外滩"
    assert parsed["meta"]["location"]["lat"] == "31.23"


def test_card():
    parsed = parse_message(42, CARD)
    assert parsed["kind"] == "card"
    assert parsed["text"] == "老王"
    assert parsed["meta"]["card"]["alias"] == "wang"


def test_revoke_xml():
    parsed = parse_message(10000, REVOKE)
    assert parsed["kind"] == "revoke"
    assert "撤回" in parsed["text"]


def test_revoke_plain_text():
    assert has_revoke_marker('"似水年华" 撤回了一条消息')
    assert not has_revoke_marker("普通系统提示")


def test_video_meta():
    parsed = parse_message(43, VIDEO)
    assert parsed["kind"] == "video"
    assert parsed["duration"] == 12
    assert parsed["meta"]["video"]["size"] == 1234567


def test_sticker_meta():
    parsed = parse_message(47, STICKER)
    assert parsed["kind"] == "sticker"
    assert parsed["meta"]["sticker"]["md5"].startswith("0123456789")


def test_text_message_unchanged():
    parsed = parse_message(1, "普通聊天内容")
    assert parsed["text"] == "普通聊天内容"


def test_quote_export_includes_reference():
    from app.exporters.chatlab import _ImageBudget, _message_content

    content = _message_content(
        {
            "kind": "text",
            "text": "好啊，周六见",
            "meta": {"quote": {"sender": "小林", "content": "周末有空吗？"}},
        },
        _ImageBudget(),
    )
    assert "好啊，周六见" in content
    assert "> 引用 小林" in content
