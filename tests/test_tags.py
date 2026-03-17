from zsxq_pdf.util.tags import TagDef, match_registry, parse_hashtags


def test_parse_hashtag_e_markup_decodes_title():
    text = '<e type="hashtag" hid="10000000000001" title="%23%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEA%23" />'
    parsed = parse_hashtags(text)
    assert len(parsed) == 1
    assert parsed[0].hid == "10000000000001"
    assert parsed[0].name == "示例标签A"


def test_match_registry_returns_tagdefs_in_priority_order():
    tags = [
        TagDef(name="示例标签A", tag_id="10000000000001", url="https://example.com/a"),
        TagDef(name="示例标签B", tag_id="10000000000002", url="https://example.com/b"),
    ]
    text = (
        '<e type="hashtag" hid="10000000000002" title="%23%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEB%23" />'
        '<e type="hashtag" hid="10000000000001" title="%23%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEA%23" />'
    )
    parsed = parse_hashtags(text)
    matched = match_registry(parsed, tags=tags)
    assert [m.name for m in matched][:2] == ["示例标签A", "示例标签B"]


def test_plain_text_fallback():
    parsed = parse_hashtags("这里是 #示例标签A# 的内容")
    assert parsed and parsed[0].name == "示例标签A"
