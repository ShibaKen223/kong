import numpy as np

from kong import audio


def test_resample_length_and_pitch():
    sr = 48000
    x = np.sin(2 * np.pi * 440 * np.arange(sr) / sr).astype(np.float32)
    y = audio.resample(x, sr)
    assert len(y) == 16000
    spectrum = np.abs(np.fft.rfft(y))
    assert abs(np.argmax(spectrum) - 440) <= 2


def test_trim_silence_keeps_speech():
    sr = audio.SR
    speech = (0.3 * np.sin(np.arange(sr) * 0.1)).astype(np.float32)
    clip = np.concatenate([np.zeros(sr * 2, np.float32), speech, np.zeros(sr * 2, np.float32)])
    out = audio.trim_silence(clip)
    assert sr <= len(out) <= sr * 1.6


def test_silence_detection():
    assert audio.is_silent(np.zeros(16000, np.float32))
    assert audio.is_dead_mic(np.zeros(16000, np.float32))
    assert not audio.is_dead_mic(np.full(16000, 1e-5, np.float32))


def test_split_long_cuts_at_quiet_points():
    sr = audio.SR
    loud = (0.3 * np.sin(np.arange(sr * 9) * 0.1)).astype(np.float32)
    clip = np.concatenate([loud, np.zeros(sr // 2, np.float32)] * 7)  # ~66 s
    parts = audio.split_long(clip, max_s=25)
    assert len(parts) >= 3
    assert sum(len(p) for p in parts) == len(clip)
    assert all(len(p) <= 25 * sr for p in parts)


def test_continuous_speech_is_not_trimmed_away():
    # No pauses at all (fast talker, noisy room): nothing may be cut as "silence".
    t = np.arange(audio.SR * 3)
    clip = (0.2 * np.sin(t * 0.07) * (1 + 0.3 * np.sin(t * 0.0013))).astype(np.float32)
    out = audio.trim_silence(clip)
    assert len(out) > 0.95 * len(clip)
    assert not audio.is_silent(out)
