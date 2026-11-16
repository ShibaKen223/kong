import wave

import numpy as np

from kong import audio
from kong.meeting import PIECE_MAX_S, Meeting, notes_timeout, stamp

SR = audio.SR


def speech(seconds, amp=0.3):
    t = np.arange(int(seconds * SR))
    return (amp * np.sin(t * 0.07) * (1 + 0.3 * np.sin(t * 0.0013))).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SR), np.float32)


class FakeRecorder:
    """Hands out the clip a second at a time, like the mic between ticks."""

    def __init__(self, clip):
        self.clip, self.pos = clip, 0

    def take(self):
        out = self.clip[self.pos : self.pos + SR]
        self.pos += len(out)
        return out

    def stop(self):
        out = self.clip[self.pos :]
        self.pos = len(self.clip)
        return out

    def elapsed(self):
        return self.pos / SR


class FakeEngine:
    def __init__(self):
        self.lengths = []

    def transcribe(self, clip, glossary=None):
        self.lengths.append(len(clip) / SR)
        return f"第{len(self.lengths)}段"


def run(m, rec):
    while rec.pos < len(rec.clip) - SR:
        m._take()
        m._drain(final=False)
    m.finish()  # the thread was never started: finish() must not wait for it


def test_recognised_at_pauses_while_recording(tmp_path):
    # Two minutes: 9 s sentences with 0.8 s pauses.
    clip = np.concatenate([np.concatenate([speech(9), silence(0.8)]) for _ in range(12)])
    rec, eng = FakeRecorder(clip), FakeEngine()
    lines = []
    m = Meeting(rec, eng, tmp_path, on_line=lines.append)
    rec.pos = 0
    while rec.pos < 60 * SR:  # one minute in, most of it is already text
        m._take()
        m._drain(final=False)
    assert len(m.lines) >= 3
    assert all(n <= PIECE_MAX_S + 0.5 for n in eng.lengths)
    running = m.partial.read_text(encoding="utf-8-sig")
    assert "[00:00:00] 第1段" in running  # written to disk as it goes
    run(m, rec)
    assert lines == m.lines
    times = [ln.t for ln in m.lines]
    assert times == sorted(times) and times[-1] > 90


def test_nonstop_talk_is_cut_at_the_window(tmp_path):
    rec, eng = FakeRecorder(speech(80)), FakeEngine()
    m = Meeting(rec, eng, tmp_path)
    run(m, rec)
    assert len(eng.lengths) >= 3
    assert all(n <= PIECE_MAX_S + 0.5 for n in eng.lengths)


def test_silence_is_skipped(tmp_path):
    rec, eng = FakeRecorder(np.concatenate([silence(30), speech(5), silence(30)])), FakeEngine()
    m = Meeting(rec, eng, tmp_path)
    run(m, rec)
    assert len(m.lines) == 1 and 25 < m.lines[0].t < 31


def test_markdown_file_and_audio(tmp_path):
    rec = FakeRecorder(np.concatenate([speech(9), silence(1), speech(6)]))
    m = Meeting(rec, FakeEngine(), tmp_path, keep_audio=True)
    run(m, rec)
    path = m.write("## 摘要\n測試會議。", engine_label="假的引擎")
    text = path.read_text(encoding="utf-8-sig")
    assert path.suffix == ".md" and "會議記錄" in path.name
    assert "## 摘要\n測試會議。" in text and "## 逐字稿" in text and "**[00:00:00]** 第1段" in text
    assert "假的引擎" in text and "錄音檔" in text
    assert not m.partial.exists()  # the running .txt goes once the .md is written
    with wave.open(str(m.wav_path)) as w:
        assert w.getframerate() == SR and abs(w.getnframes() - len(rec.clip)) < SR


def test_without_notes_says_why(tmp_path):
    rec = FakeRecorder(speech(5))
    m = Meeting(rec, FakeEngine(), tmp_path)
    run(m, rec)
    text = m.write(note_error="Claude Code 的登入過期了").read_text(encoding="utf-8-sig")
    assert "> 沒有整理重點：Claude Code 的登入過期了" in text


def test_helpers():
    assert stamp(3725.9) == "01:02:05"
    assert notes_timeout(0) == 180 and notes_timeout(10**7) == 900


class StallingRecorder:
    """Speech, then nothing arrives (unplugged), then speech again."""

    def __init__(self, script):
        self.script, self.i, self.n = script, 0, 0

    def take(self):
        out = self.script[self.i] if self.i < len(self.script) else np.zeros(0, np.float32)
        self.i += 1
        self.n += len(out)
        return out

    def stop(self):
        return np.zeros(0, np.float32)

    def elapsed(self):
        return self.n / SR


def test_a_mic_that_stops_is_reported_and_marked(tmp_path):
    empty = np.zeros(0, np.float32)
    script = [speech(1)] * 3 + [empty] * 6 + [speech(1)] * 2
    said = []
    m = Meeting(StallingRecorder(script), FakeEngine(), tmp_path, on_problem=said.append)
    for _ in script:
        m._take()
    assert len(said) == 2 and "停止傳送" in said[0] and said[1] == ""
    assert m.mic_gaps == 1 and m.problem == "" and len(m.marks) == 2
    m.finish()
    text = m.write().read_text(encoding="utf-8-sig")
    assert "麥克風中途有 1 次沒有聲音" in text and "從這裡開始麥克風沒有聲音" in text and "麥克風恢復" in text


def test_digital_silence_is_reported_once(tmp_path):
    script = [speech(1)] + [silence(1)] * 40
    said = []
    m = Meeting(StallingRecorder(script), FakeEngine(), tmp_path, on_problem=said.append)
    for _ in script:
        m._take()
    assert len(said) == 1 and "完全靜音" in said[0] and m.mic_gaps == 1


def test_a_short_gap_is_not_a_problem(tmp_path):
    empty = np.zeros(0, np.float32)
    script = [speech(1), empty, empty, speech(1), silence(1), silence(1), speech(1)]
    said = []
    m = Meeting(StallingRecorder(script), FakeEngine(), tmp_path, on_problem=said.append)
    for _ in script:
        m._take()
    assert said == [] and m.mic_gaps == 0


def test_two_meetings_in_the_same_minute_keep_both_files(tmp_path):
    files = []
    for words in ("第一場", "第二場"):
        rec = FakeRecorder(speech(3))
        m = Meeting(rec, FakeEngine(), tmp_path, keep_audio=True)
        m.finish()
        m.lines[0].text = words
        files.append(m.write())
    assert files[0] != files[1]
    assert "第一場" in files[0].read_text(encoding="utf-8-sig") and "第二場" in files[1].read_text(encoding="utf-8-sig")
    assert len(list(tmp_path.glob("*.wav"))) == 2
