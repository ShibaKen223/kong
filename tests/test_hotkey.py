from kong.hotkey import TapDetector, map_win_key


def make():
    events = []
    d = TapDetector("right_ctrl", 0.4, on_tap=lambda: events.append("tap"), on_double=lambda: events.append("double"),
                    on_other=lambda k: events.append(k))
    return d, events


def tap(d, t, key="right_ctrl", hold=0.05):
    d.feed(key, True, t)
    d.feed(key, False, t + hold)


def test_double_tap():
    d, ev = make()
    tap(d, 0.0)
    tap(d, 0.2)
    assert ev == ["tap", "double"]


def test_taps_too_far_apart_are_two_singles():
    d, ev = make()
    tap(d, 0.0)
    tap(d, 1.0)
    assert ev == ["tap", "tap"]


def test_third_tap_is_a_single_again():
    d, ev = make()
    tap(d, 0.0)
    tap(d, 0.2)
    tap(d, 0.4)
    assert ev == ["tap", "double", "tap"]


def test_ctrl_c_is_not_a_tap():
    d, ev = make()
    d.feed("right_ctrl", True, 0.0)
    d.feed("vk43", True, 0.05)
    d.feed("vk43", False, 0.08)
    d.feed("right_ctrl", False, 0.1)
    tap(d, 0.2)
    assert ev == ["vk43", "tap"]


def test_key_between_taps_breaks_double():
    d, ev = make()
    tap(d, 0.0)
    d.feed("vk41", True, 0.1)
    d.feed("vk41", False, 0.12)
    tap(d, 0.2)
    assert ev == ["tap", "vk41", "tap"]


def test_long_hold_is_not_a_tap_even_with_autorepeat():
    d, ev = make()
    d.feed("right_ctrl", True, 0.0)
    for i in range(1, 80):  # 2.4 s of auto-repeat
        d.feed("right_ctrl", True, i * 0.03)
    d.feed("right_ctrl", False, 2.4)
    assert ev == []


def test_holding_another_key_blocks_taps():
    d, ev = make()
    d.feed("left_shift", True, 0.0)
    tap(d, 0.1)
    assert ev == ["left_shift"]


def test_stuck_key_expires():
    d, ev = make()
    d.feed("vk5b", True, 0.0)  # Win key down, key-up lost (lock screen)
    tap(d, 5.0)
    tap(d, 5.2)
    assert ev == ["vk5b", "tap", "double"]


def test_win_key_mapping():
    assert map_win_key(0xA3, 0x1D, 0x01) == "right_ctrl"
    assert map_win_key(0x11, 0x1D, 0x01) == "right_ctrl"
    assert map_win_key(0x11, 0x1D, 0x00) == "left_ctrl"
    assert map_win_key(0xA5, 0x38, 0x01) == "right_alt"
    assert map_win_key(0xA2, 0x21D, 0x00) is None  # AltGr's fake Ctrl
    assert map_win_key(0x10, 0x36, 0x00) == "right_shift"
    assert map_win_key(0x1B, 0x01, 0x00) == "esc"
