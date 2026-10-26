"""v1.5 voice change: speech features of the assistant before vs after the overlap (paper's Table 3).

Features per the authors' eval_general_before_after.py: WPM (speech-only, from word timestamps), pitch mean/SD
(Praat via parselmouth), intensity mean/SD (dB), UTMOSv2 (predicted MOS). All optional dependencies; a feature
that cannot be computed is returned as None and reported as "not scored".
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from .. import SR


def available() -> Dict[str, bool]:
    out = {}
    for mod in ("parselmouth", "utmosv2"):
        try:
            __import__(mod)
            out[mod] = True
        except Exception:  # noqa: BLE001
            out[mod] = False
    return out


def _slice(audio: np.ndarray, segs: List[tuple]) -> np.ndarray:
    parts = [audio[int(s * SR):int(e * SR)] for s, e in segs]
    parts = [p for p in parts if p.size]
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)


def wpm(words: List[dict]) -> Optional[float]:
    """Words per minute over speech time only (sum of word durations incl. short gaps), as in the paper's code."""
    if len(words) < 2:
        return None
    t0 = words[0]["timestamp"][0]
    t1 = words[-1]["timestamp"][1]
    dur = max(t1 - t0, 1e-3)
    return 60.0 * len(words) / dur


def pitch_stats(audio: np.ndarray, fmin=75, fmax=500) -> tuple:
    try:
        import parselmouth
    except Exception:  # noqa: BLE001
        return None, None
    if audio.size < SR * 0.3:
        return None, None
    snd = parselmouth.Sound(audio.astype(np.float64), sampling_frequency=SR)
    pitch = snd.to_pitch(time_step=0.01, pitch_floor=fmin, pitch_ceiling=fmax)
    f0 = pitch.selected_array["frequency"]
    f0 = f0[f0 > 0]
    if f0.size < 5:
        return None, None
    return float(np.mean(f0)), float(np.std(f0))


def intensity_stats(audio: np.ndarray) -> tuple:
    """Frame RMS in dB (25 ms frames, 10 ms hop), speech frames only (above -50 dBFS)."""
    if audio.size < SR * 0.3:
        return None, None
    frame, hop = int(0.025 * SR), int(0.010 * SR)
    n = 1 + (len(audio) - frame) // hop
    if n < 3:
        return None, None
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt(np.mean(np.square(audio[idx], dtype=np.float64), axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    db = db[db > -50]
    if db.size < 3:
        return None, None
    return float(np.mean(db)), float(np.std(db))


_utmos = None


def utmos(audio: np.ndarray) -> Optional[float]:
    global _utmos
    try:
        import utmosv2
    except Exception:  # noqa: BLE001
        return None
    if audio.size < SR * 1.0:
        return None
    try:
        if _utmos is None:
            _utmos = utmosv2.create_model(pretrained=True)
        import tempfile
        import soundfile as sf
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, SR)
            path = f.name
        return float(_utmos.predict(input_path=path))
    except Exception:  # noqa: BLE001
        return None


def features(audio: np.ndarray, segs: List[tuple], words: List[dict], with_utmos: bool = True) -> dict:
    sp = _slice(audio, segs)
    pm, ps = pitch_stats(sp)
    im, isd = intensity_stats(sp)
    return {"wpm": wpm(words), "pitch_mean": pm, "pitch_sd": ps, "intensity_mean": im, "intensity_sd": isd,
            "utmosv2": utmos(sp) if with_utmos else None, "speech_s": float(sp.size / SR)}


def before_after(heard: np.ndarray, bot_segs: List[tuple], words: List[dict], overlap_start: float,
                 with_utmos: bool = True) -> dict:
    pre_segs = [(s, min(e, overlap_start)) for s, e in bot_segs if s < overlap_start]
    post_segs = [(max(s, overlap_start), e) for s, e in bot_segs if e > overlap_start]
    pre_w = [w for w in words if w["timestamp"][0] < overlap_start]
    post_w = [w for w in words if w["timestamp"][0] >= overlap_start]
    pre = features(heard, pre_segs, pre_w, with_utmos)
    post = features(heard, post_segs, post_w, with_utmos)
    delta = {k: (post[k] - pre[k]) if (pre.get(k) is not None and post.get(k) is not None) else None
             for k in ("wpm", "pitch_mean", "pitch_sd", "intensity_mean", "intensity_sd", "utmosv2")}
    return {"pre": pre, "post": post, "delta": delta}
