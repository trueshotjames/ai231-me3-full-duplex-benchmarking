"""Shared dataset-building code: cutting clips, making false turn-taking clips, writing the manifest.

Used by tools/build_datasets.py (real Full-Duplex-Bench data) and tools/make_dev_dataset.py (tiny set from
the authors' example folders).
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from scipy.signal import butter, sosfilt

from . import SR
from .rig.vad import speech_segments
from .util import load_audio, now_iso, save_audio


# ------------------------------------------------------------------ audio helpers

def trim_trailing_silence(a: np.ndarray, keep: float = 0.25) -> np.ndarray:
    segs = speech_segments(a)
    if not segs:
        return a
    end = min(len(a), int((segs[-1][1] + keep) * SR))
    return a[:end]


def db_gain(db):
    return 10 ** (np.asarray(db, dtype=np.float64) / 20) if np.ndim(db) else float(10 ** (db / 20))


def lowpass(a: np.ndarray, hz: float) -> np.ndarray:
    sos = butter(4, hz / (SR / 2), btype="low", output="sos")
    return sosfilt(sos, a).astype(np.float32)


def bandpass(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    sos = butter(4, [lo / (SR / 2), hi / (SR / 2)], btype="band", output="sos")
    return sosfilt(sos, a).astype(np.float32)


def add_echo(a: np.ndarray, delay_ms: float, gain_db: float) -> np.ndarray:
    d = int(delay_ms * SR / 1000)
    out = a.copy()
    out[d:] += a[:-d] * db_gain(gain_db)
    return out


def compress(a: np.ndarray, ratio: float = 4.0, thresh_db: float = -20.0) -> np.ndarray:
    env = np.abs(a) + 1e-6
    # simple one-pole envelope follower
    alpha = np.exp(-1 / (0.01 * SR))
    e = np.empty_like(env)
    acc = 0.0
    for i, v in enumerate(env):
        acc = alpha * acc + (1 - alpha) * v
        e[i] = acc
    db = 20 * np.log10(e)
    over = np.maximum(db - thresh_db, 0)
    gain = db_gain(-over * (1 - 1 / ratio))
    return (a * gain).astype(np.float32)


def normalize_peak(a: np.ndarray, peak: float = 0.5) -> np.ndarray:
    m = float(np.max(np.abs(a))) if a.size else 0.0
    return a if m == 0 else (a / m * peak).astype(np.float32)


# --- the v1.5 paper's processing for "talking to others" and "background speech" (Lin et al. 2026, §3) --------

def make_side_talk(a: np.ndarray) -> np.ndarray:
    """Off-axis, farther away: -8 dB, >4 kHz cut by 5 dB, reflections at 45 ms (-6 dB) and 120 ms (-12 dB)."""
    hi = a - lowpass(a, 4000)
    out = lowpass(a, 4000) + hi * db_gain(-5)
    out = add_echo(add_echo(out, 45, -6), 120, -12)
    return (out * db_gain(-8)).astype(np.float32)


def make_background_speech(a: np.ndarray) -> np.ndarray:
    """Distant different voice: -15 dB, low-pass 3 kHz, 100 ms echo at -10 dB, compression."""
    out = add_echo(lowpass(a, 3000), 100, -10)
    out = compress(out, ratio=3.0, thresh_db=-25)
    return (out * db_gain(-15)).astype(np.float32)


def make_tv_radio(a: np.ndarray, rng: random.Random) -> np.ndarray:
    """Speech through a small radio/TV speaker in another room: band-limited 300–3400 Hz, compressed,
    with a bed of low music-like tones, -15 dB overall."""
    sp = compress(bandpass(a, 300, 3400), ratio=6.0, thresh_db=-25)
    t = np.arange(len(a)) / SR
    bed = np.zeros_like(a)
    for _ in range(3):
        f = rng.choice([110, 147, 165, 196, 220, 262])
        bed += 0.15 * np.sin(2 * np.pi * f * t + rng.random() * 6.28) * (0.6 + 0.4 * np.sin(2 * np.pi * rng.uniform(0.5, 2) * t))
    out = normalize_peak(sp, 0.6) + lowpass(bed.astype(np.float32), 800) * 0.4
    return (normalize_peak(out, 0.5) * db_gain(-15)).astype(np.float32)


def make_noise(kind_idx: int, seconds: float, rng: random.Random) -> tuple:
    """Synthesized household noises at a realistic level: door slam, keyboard, hiss/fan, dishes."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    g = np.random.default_rng(rng.randrange(1 << 30))
    kinds = ["door slam", "keyboard typing", "fan / hiss", "dishes clatter"]
    k = kinds[kind_idx % 4]
    out = np.zeros(n, np.float32)
    if k == "door slam":
        for _ in range(rng.randint(1, 2)):
            s = int(rng.uniform(0.3, seconds - 1.0) * SR)
            burst = g.normal(0, 1, int(0.25 * SR)) * np.exp(-np.linspace(0, 8, int(0.25 * SR)))
            out[s:s + len(burst)] += lowpass(burst.astype(np.float32), 1500) * 0.9
    elif k == "keyboard typing":
        for _ in range(int(seconds * rng.uniform(4, 7))):
            s = int(rng.uniform(0.1, seconds - 0.1) * SR)
            click = g.normal(0, 1, int(0.012 * SR)) * np.exp(-np.linspace(0, 6, int(0.012 * SR)))
            out[s:s + len(click)] += bandpass(click.astype(np.float32), 1500, 6000) * 0.5
    elif k == "fan / hiss":
        out = lowpass(g.normal(0, 1, n).astype(np.float32), 2500) * 0.08
        out += 0.03 * np.sin(2 * np.pi * 50 * t).astype(np.float32)
    else:
        for _ in range(rng.randint(3, 6)):
            s = int(rng.uniform(0.1, seconds - 0.2) * SR)
            f = rng.uniform(2500, 5000)
            ring = np.sin(2 * np.pi * f * t[:int(0.15 * SR)]) * np.exp(-np.linspace(0, 10, int(0.15 * SR)))
            out[s:s + len(ring)] += ring.astype(np.float32) * 0.4
    return normalize_peak(out, 0.4) * db_gain(-6), k


# ------------------------------------------------------------------ manifest

class ManifestBuilder:
    def __init__(self, out_root: Path, source: str):
        self.root = Path(out_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.groups: Dict[str, dict] = {}
        self.source = source

    def add(self, group: str, title: str, clip_id: str, audios: Dict[str, np.ndarray], meta: dict,
            duration_role: str = "main", licence: str = "") -> None:
        g = self.groups.setdefault(group, {"title": title, "licence": licence, "clips": []})
        files, shas = {}, {}
        for role, a in audios.items():
            rel = f"{group}/{clip_id}" + ("" if role == "main" else f"_{role}") + ".flac"
            save_audio(self.root / rel, a)
            files[role] = rel
            shas[role] = hashlib.sha256((self.root / rel).read_bytes()).hexdigest()
        g["clips"].append({"id": clip_id, "files": files, "sha256": shas,
                           "duration": round(len(audios[duration_role]) / SR, 3), "meta": meta})

    def write(self) -> Path:
        man = {"version": 1, "built": now_iso(), "source": self.source, "sample_rate": SR, "groups": self.groups}
        p = self.root / "manifest.json"
        p.write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
        return p


def last_words(words: List[dict], before_t: float, n: int = 4) -> str:
    ws = [w["text"] for w in words if w["timestamp"][1] <= before_t + 0.05]
    return " ".join(ws[-n:])
