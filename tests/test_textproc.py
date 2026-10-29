from kong import textproc as t


def test_simplified_becomes_taiwan_traditional():
    assert t.to_taiwan("这个软件的视频信息") == "這個軟體的影片資訊"


def test_basic_cleanup_spacing_and_punctuation():
    assert t.basic_cleanup("我觉得这个设计很elegant,但是performance可能会有问题") == "我覺得這個設計很 elegant，但是 performance 可能會有問題"


def test_numbers_and_versions_stay_half_width():
    assert t.basic_cleanup("版本是v1.2 ,价格3.5元,好吗?") == "版本是 v1.2，價格3.5元，好嗎？"


def test_english_sentence_untouched():
    assert t.basic_cleanup("Hello, world. How are you?") == "Hello, world. How are you?"


def test_fillers_dropped():
    assert t.basic_cleanup("呃 我想问一下, 嗯, 这个可以吗") == "我想問一下，這個可以嗎"
    assert t.basic_cleanup("嗯嗯") == ""


def test_finalize_strips_decorations():
    assert t.finalize("「這是結果。」") == "這是結果。"
    assert t.finalize("整理後：好的。") == "好的。"
    assert t.finalize("```\n你好\n```") == "你好"
    assert t.finalize("<transcript>好</transcript>") == "好"
    assert t.finalize("「他說「好」」") == "「他說「好」」"


def test_finalize_keeps_paragraphs():
    assert t.finalize("第一段。\n\n\n\n第二段。") == "第一段。\n\n第二段。"
