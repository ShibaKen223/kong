import json
import sys
import time
from pathlib import Path

import pytest

from kong.config import Config
from kong.polish import claude_code as cc

FAKE = Path(__file__).parent / "fake" / "claude"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake CLI is a shebang script")


def polisher(monkeypatch, mode="ok", **kw):
    monkeypatch.setenv("FAKE_CLAUDE", mode)
    p = cc.ClaudeCodePolisher(Config(claude_path=str(FAKE), claude_timeout_s=kw.pop("timeout", 10), **kw))
    p._setup()
    return p


def test_answer_without_waiting_for_result(monkeypatch):
    p = polisher(monkeypatch)
    time.sleep(0.5)
    streamed = []
    t = time.monotonic()
    out = p.polish("你好", on_text=streamed.append)
    assert out == "整理：你好"
    assert time.monotonic() - t < 2.5  # the fake waits 3 s before its result event
    assert streamed[-1] == "整理：你好"
    p.shutdown()


def test_each_sentence_gets_a_fresh_process(monkeypatch):
    p = polisher(monkeypatch)
    first = p._warm
    p.polish("一")
    time.sleep(3.5)
    assert p._warm is not None and p._warm is not first
    assert p.polish("二") == "整理：二"
    p.shutdown()


def test_empty_answer_returns_at_message_stop(monkeypatch):
    p = polisher(monkeypatch, "empty")
    t = time.monotonic()
    assert p.polish("嗯嗯") == ""
    assert time.monotonic() - t < 2.5
    p.shutdown()


def test_not_logged_in_is_an_error_not_an_answer(monkeypatch):
    p = polisher(monkeypatch, "login")
    with pytest.raises(cc.PolishError, match="登入"):
        p.polish("你好")
    p.shutdown()


def test_expired_login_says_so():
    # What Claude Code 2.1 prints when its saved subscription login can no longer be refreshed.
    msg = cc._explain("Failed to authenticate: OAuth session expired and could not be refreshed")
    assert "過期" in msg and "claude" in msg
    assert "過期" not in cc._explain("Not logged in · Please run /login")


def test_timeout(monkeypatch):
    p = polisher(monkeypatch, "hang", timeout=1)
    with pytest.raises(cc.PolishError, match="秒"):
        p.polish("你好")
    p.shutdown()


def test_slow_start_gives_up_early(monkeypatch):
    monkeypatch.setattr(cc, "FIRST_TOKEN_S", 1.0)
    p = polisher(monkeypatch, "hang", timeout=10)
    t = time.monotonic()
    with pytest.raises(cc.PolishError, match="原稿"):
        p.polish("你好")
    assert time.monotonic() - t < 3
    p.shutdown()


def test_cold_start_gets_longer_to_begin(monkeypatch):
    monkeypatch.setattr(cc, "FIRST_TOKEN_S", 0.5)
    monkeypatch.setattr(cc, "COLD_FIRST_TOKEN_S", 2.0)
    p = polisher(monkeypatch, "hang", timeout=10, claude_prewarm=False)
    t = time.monotonic()
    with pytest.raises(cc.PolishError, match="2 秒"):
        p.polish("你好")
    assert 1.8 < time.monotonic() - t < 4
    p.shutdown()


def test_waiting_process_is_refreshed(monkeypatch):
    monkeypatch.setattr(cc, "REFRESH_S", 0.5)
    p = polisher(monkeypatch)
    first = p._warm
    time.sleep(4.5)  # the refresh waits 3 s to see the new one stay up
    assert p._warm is not None and p._warm is not first and p._warm.alive()
    assert first.proc.poll() is not None
    assert p.polish("你好") == "整理：你好"
    p.shutdown()


def test_stale_warm_process_is_replaced(monkeypatch):
    p = polisher(monkeypatch)
    stale = p._warm
    stale.started -= cc.WARM_MAX_AGE_S + 1
    assert p.polish("你好") == "整理：你好"
    assert stale.proc.poll() is not None
    p.shutdown()


def test_crash_reports_stderr(monkeypatch):
    p = polisher(monkeypatch, "crash")
    with pytest.raises(cc.PolishError, match="boom"):
        p.polish("你好")
    p.shutdown()


def test_old_cli_flags_are_dropped(monkeypatch, tmp_path):
    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    p = polisher(monkeypatch, "old")
    assert ("--safe-mode", None) not in p._flags
    assert p.polish("你好") == "整理：你好"
    args = json.loads(log.read_text().splitlines()[-1])
    assert "--safe-mode" not in args and "--tools" in args
    p.shutdown()


def test_glossary_edit_applies_to_next_sentence(monkeypatch, tmp_path):
    from kong import paths

    p = polisher(monkeypatch)
    warm = p._warm
    paths.glossary_file().write_text("Kubernetes\n", encoding="utf-8")
    p.polish("你好")
    assert "Kubernetes" in p._prompt_file.read_text(encoding="utf-8")
    assert warm.proc.poll() is not None  # the stale warm process was not used
    p.shutdown()


def test_api_key_is_removed_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = polisher(monkeypatch)
    assert "ANTHROPIC_API_KEY" not in p._env()
    p.shutdown()


def test_missing_cli(monkeypatch):
    monkeypatch.setattr(cc, "_candidates", lambda explicit: [])
    p = cc.ClaudeCodePolisher(Config())
    p._setup()
    assert not p.available
    with pytest.raises(cc.PolishError, match="找不到"):
        p.polish("你好")
