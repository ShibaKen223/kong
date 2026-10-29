import json

from kong.config import Config


def test_roundtrip_and_bad_values(tmp_path):
    p = tmp_path / "c.json"
    c = Config(hotkey="right_alt", claude_model="haiku")
    c.save(p)
    assert Config.load(p).hotkey == "right_alt"
    p.write_text(json.dumps({"hotkey": "nonsense", "double_tap_ms": "fast", "live_preview": 1, "unknown": 3}))
    c = Config.load(p)
    assert c.hotkey == "right_ctrl"
    assert c.double_tap_ms == 400
    assert c.live_preview is True


def test_broken_file_gives_defaults(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    assert Config.load(p) == Config()
