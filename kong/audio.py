"""Microphone capture at 16 kHz mono float32."""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

SR = 16000


def resample(x: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    """Windowed-sinc low-pass plus linear interpolation: plenty for speech recognition."""
    x = np.asarray(x, dtype=np.float32)
    if sr_in == sr_out or len(x) == 0:
        return x
    if sr_out < sr_in:
        cutoff = 0.45 * sr_out / sr_in
        n = 63
        taps = np.arange(n) - (n - 1) / 2
        h = np.sinc(2 * cutoff * taps) * np.hamming(n)
        x = np.convolve(x, (h / h.sum()).astype(np.float32), mode="same")
    n_out = int(len(x) * sr_out / sr_in)
    t_out = np.arange(n_out) * (sr_in / sr_out)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


def list_input_devices() -> list[str]:
    import sounddevice as sd

    return [d["name"] for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]


class Recorder:
    def __init__(self, device: str = "") -> None:
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._n = 0
        self._lock = threading.Lock()
        self._stream = None
        self._sr = SR
        self.level = 0.0

    def _device_index(self):
        if not self.device:
            return None
        import sounddevice as sd

        want = self.device.lower()
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and want in d["name"].lower():
                return i
        log.warning("mic %r not found; using the default", self.device)
        return None

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0].copy()
        with self._lock:
            self._chunks.append(mono)
            self._n += len(mono)
        if len(mono):
            self.level = float(np.sqrt(np.mean(mono * mono)))

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            self._chunks = []
            self._n = 0
        self.level = 0.0
        dev = self._device_index()
        try:
            stream = sd.InputStream(
                samplerate=SR, channels=1, dtype="float32", device=dev, callback=self._callback
            )
            self._sr = SR
        except Exception as e:
            # Some drivers refuse 16 kHz or mono: record at the device's own format and convert.
            info = sd.query_devices(dev, "input")
            sr = int(info["default_samplerate"])
            ch = max(1, min(2, int(info.get("max_input_channels", 1))))
            log.info("16 kHz mono refused (%s); recording at %d Hz x%d", e, sr, ch)
            stream = sd.InputStream(
                samplerate=sr, channels=ch, dtype="float32", device=dev, callback=self._callback
            )
            self._sr = sr
        stream.start()
        self._stream = stream

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def elapsed(self) -> float:
        return self._n / self._sr

    def snapshot(self, last_s: float | None = None) -> np.ndarray:
        with self._lock:
            chunks = list(self._chunks)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks)
        if last_s is not None:
            audio = audio[-int(last_s * self._sr):]
        return resample(audio, self._sr)

    def _close(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                log.exception("closing the mic stream failed")

    def stop(self) -> np.ndarray:
        self._close()
        return self.snapshot()

    def cancel(self) -> None:
        self._close()
        with self._lock:
            self._chunks = []
            self._n = 0


def is_silent(audio: np.ndarray, sr: int = SR) -> bool:
    """True when the clip is too short or too quiet to hold speech."""
    if len(audio) < sr * 0.3:
        return True
    return float(np.max(np.abs(audio))) < 0.01


def is_dead_mic(audio: np.ndarray) -> bool:
    """Exact digital silence: Windows feeds zeros when desktop apps may not use the mic."""
    return len(audio) > 0 and not np.any(audio)


def speech_threshold(rms: np.ndarray) -> float:
    """Frame loudness above which we call it speech.

    Relative to the quiet parts of the clip, but capped by its loud parts: a clip that is all
    speech (no pauses, or a noisy room) must not end up counted as all silence.
    """
    quiet = float(np.percentile(rms, 10))
    loud = float(np.percentile(rms, 90))
    return max(0.004, min(quiet * 3, loud * 0.3))


def trim_silence(audio: np.ndarray, sr: int = SR, pad_s: float = 0.25) -> np.ndarray:
    """Cut quiet head and tail: less to decode, and Whisper invents fewer words on silence."""
    frame = int(sr * 0.02)
    n = len(audio) // frame
    if n < 3:
        return audio
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = np.nonzero(rms > speech_threshold(rms))[0]
    if len(loud) == 0:
        return audio[:0]
    pad = int(pad_s / 0.02)
    start = max(0, loud[0] - pad) * frame
    end = min(n, loud[-1] + 1 + pad) * frame
    return audio[start:end]


def split_long(audio: np.ndarray, max_s: float = 25.0, sr: int = SR) -> list[np.ndarray]:
    """Split at the quietest moment before each `max_s` mark (Whisper sees 30 s at a time)."""
    max_n = int(max_s * sr)
    if len(audio) <= max_n:
        return [audio]
    frame = int(sr * 0.02)
    out, start = [], 0
    while len(audio) - start > max_n:
        lo, hi = start + int(max_n * 0.6), start + max_n
        window = audio[lo:hi]
        k = len(window) // frame
        rms = np.sqrt(np.mean(window[: k * frame].reshape(k, frame) ** 2, axis=1))
        cut = lo + int(np.argmin(rms)) * frame + frame // 2
        out.append(audio[start:cut])
        start = cut
    out.append(audio[start:])
    return out
