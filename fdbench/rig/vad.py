"""Silero VAD (ONNX, CPU). Streaming use during a trial and offline segmentation during scoring.

The model file is vendored in models/silero_vad.onnx (MIT, snakers4/silero-vad v5 family).
Segmentation parameters follow silero_vad.get_speech_timestamps defaults.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np

from .. import SR
from ..util import MODELS_DIR

WINDOW = 512  # samples at 16 kHz
CONTEXT = 64

_session = None


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        _session = ort.InferenceSession(str(MODELS_DIR / "silero_vad.onnx"), sess_options=opts,
                                        providers=["CPUExecutionProvider"])
    return _session


class SileroVAD:
    """Frame-level speech probability. Feed 512-sample float32 chunks at 16 kHz."""

    def __init__(self):
        self.session = _get_session()
        self.reset()

    def reset(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def prob(self, chunk: np.ndarray) -> float:
        if chunk.shape[0] != WINDOW:
            chunk = np.pad(chunk, (0, WINDOW - chunk.shape[0]))
        x = np.concatenate([self._context, chunk.reshape(1, -1).astype(np.float32)], axis=1)
        out, state = self.session.run(None, {"input": x, "state": self._state, "sr": np.array(SR, dtype=np.int64)})
        self._state = state
        self._context = x[:, -CONTEXT:]
        return float(out[0][0])


def speech_probs(audio: np.ndarray) -> np.ndarray:
    vad = SileroVAD()
    n = int(np.ceil(len(audio) / WINDOW))
    probs = np.zeros(n, dtype=np.float32)
    for i in range(n):
        probs[i] = vad.prob(audio[i * WINDOW:(i + 1) * WINDOW])
    return probs


def segments_from_probs(probs: np.ndarray, threshold=0.5, neg_threshold=None, min_speech_ms=250,
                        min_silence_ms=100, pad_ms=30, total_samples: int | None = None) -> List[Tuple[float, float]]:
    """Hysteresis segmentation, same rules as silero_vad.get_speech_timestamps. Returns (start, end) in seconds."""
    neg_threshold = neg_threshold if neg_threshold is not None else max(threshold - 0.15, 0.01)
    min_speech = min_speech_ms * SR / 1000
    min_silence = min_silence_ms * SR / 1000
    pad = pad_ms * SR / 1000
    total = total_samples if total_samples is not None else len(probs) * WINDOW

    speeches = []
    triggered = False
    cur = {}
    temp_end = 0
    for i, p in enumerate(probs):
        pos = i * WINDOW
        if p >= threshold and temp_end:
            temp_end = 0
        if p >= threshold and not triggered:
            triggered = True
            cur["start"] = pos
            continue
        if p < neg_threshold and triggered:
            if not temp_end:
                temp_end = pos
            if pos - temp_end < min_silence:
                continue
            cur["end"] = temp_end
            if cur["end"] - cur["start"] > min_speech:
                speeches.append(cur)
            cur = {}
            triggered = False
            temp_end = 0
    if cur and total - cur["start"] > min_speech:
        cur["end"] = total
        speeches.append(cur)

    out = []
    for i, s in enumerate(speeches):
        if i == 0:
            s["start"] = max(0, s["start"] - pad)
        if i != len(speeches) - 1:
            gap = speeches[i + 1]["start"] - s["end"]
            if gap < 2 * pad:
                s["end"] += gap // 2
                speeches[i + 1]["start"] = max(0, speeches[i + 1]["start"] - gap // 2)
            else:
                s["end"] = min(total, s["end"] + pad)
                speeches[i + 1]["start"] = max(0, speeches[i + 1]["start"] - pad)
        else:
            s["end"] = min(total, s["end"] + pad)
        out.append((s["start"] / SR, s["end"] / SR))
    return out


def speech_segments(audio: np.ndarray, **kw) -> List[Tuple[float, float]]:
    return segments_from_probs(speech_probs(audio), total_samples=len(audio), **kw)


def merge_segments(seg: List[Tuple[float, float]], gap: float) -> List[Tuple[float, float]]:
    """Merge segments separated by <= gap seconds (paper's get_timing.py)."""
    if not seg:
        return []
    seg = sorted(seg)
    merged = [list(seg[0])]
    for s, e in seg[1:]:
        if s - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [tuple(m) for m in merged]


class LiveVAD:
    """Streaming speech on/off detector with the same hysteresis, fed from the audio callback.

    on_start(t) / on_end(t) are called with the time in seconds (sample clock of the stream).
    """

    def __init__(self, threshold=0.5, min_silence_ms=300, min_speech_ms=120, on_start=None, on_end=None):
        self.vad = SileroVAD()
        self.threshold = threshold
        self.neg = max(threshold - 0.15, 0.01)
        self.min_silence = min_silence_ms / 1000
        self.min_speech = min_speech_ms / 1000
        self.on_start = on_start
        self.on_end = on_end
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0  # samples consumed
        self.speaking = False
        self._cand_start = None
        self._silence_since = None
        self.speech_start = None
        self.last_speech_end = None
        self.probs: list = []

    def feed(self, audio16k: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, audio16k.astype(np.float32)])
        while len(self._buf) >= WINDOW:
            chunk, self._buf = self._buf[:WINDOW], self._buf[WINDOW:]
            t = self._pos / SR
            self._pos += WINDOW
            p = self.vad.prob(chunk)
            self.probs.append(p)
            self._step(p, t)

    def _step(self, p: float, t: float) -> None:
        if not self.speaking:
            if p >= self.threshold:
                if self._cand_start is None:
                    self._cand_start = t
                elif t - self._cand_start >= self.min_speech:
                    self.speaking = True
                    self.speech_start = self._cand_start
                    self._silence_since = None
                    if self.on_start:
                        self.on_start(self.speech_start)
            else:
                self._cand_start = None
        else:
            if p < self.neg:
                if self._silence_since is None:
                    self._silence_since = t
                elif t - self._silence_since >= self.min_silence:
                    self.speaking = False
                    self.last_speech_end = self._silence_since
                    self._cand_start = None
                    if self.on_end:
                        self.on_end(self.last_speech_end)
            else:
                self._silence_since = None
