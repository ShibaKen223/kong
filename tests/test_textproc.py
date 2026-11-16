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


def test_words_taiwan_uses_are_left_alone():
    # OpenCC's phrase table rewrote all of these (支援、行政程式法、專案、檔案).
    for s in ("我支持這個方案", "依照行政程序法辦理", "這個項目的預算", "標準作業程序要更新", "把文件寄給我"):
        assert t.basic_cleanup(s) == s
        assert t.finalize(s) == s
    assert t.basic_cleanup("我支持这个方案") == "我支持這個方案"


def test_traditional_text_keeps_its_characters():
    for s in ("里長說干擾很大", "咸豐年間的鄰里", "台灣的平台", "那個傢伙", "風采"):
        assert t.basic_cleanup(s) == s
        assert t.finalize(s) == s
    # No character settles it either way: the model's answer is taken as 繁體.
    for s in ("范先生在台北", "干杯", "里民大會"):
        assert t.finalize(s) == s


def test_simplified_text_is_still_fully_converted():
    assert t.basic_cleanup("干杯") == "乾杯"
    assert t.basic_cleanup("这个头发以后再剪") == "這個頭髮以後再剪"
    assert t.basic_cleanup("台湾的平台") == "台灣的平台"
    assert t.finalize("这个软件的视频信息") == "這個軟體的影片資訊"


def test_filler_characters_inside_words_stay():
    assert t.basic_cleanup("額外的部分我們明天再討論") == "額外的部分我們明天再討論"
    assert t.basic_cleanup("好，額度不夠的話再跟我說") == "好，額度不夠的話再跟我說"
    assert t.basic_cleanup("恩典教會的活動改到週六") == "恩典教會的活動改到週六"
    assert t.basic_cleanup("恩，我覺得可以") == "我覺得可以"
    assert t.basic_cleanup("額 這個我再想想") == "這個我再想想"
