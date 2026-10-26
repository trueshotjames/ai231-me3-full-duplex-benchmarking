"""Small shared helpers: paths, JSON, time, audio file I/O."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import SR

ROOT = Path(__file__).resolve().parent.parent  # the fd-benchmark folder
MODELS_DIR = ROOT / "models"
PROMPTS_DIR = ROOT / "prompts"
DATASETS_DIR = ROOT / "datasets"
RUNS_DIR = ROOT / "runs"
STATE_DIR = ROOT / ".fdbench"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: Path, data, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False, default=_json_default)
    os.replace(tmp, path)


def append_jsonl(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")


def _json_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


# ---------------------------------------------------------------- audio I/O

def load_audio(path: Path, sr: int = SR) -> np.ndarray:
    """Load any wav/flac as float32 mono at `sr`."""
    import soundfile as sf

    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if file_sr != sr:
        mono = resample(mono, file_sr, sr)
    return np.ascontiguousarray(mono, dtype=np.float32)


def save_audio(path: Path, audio: np.ndarray, sr: int = SR) -> None:
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(audio, dtype=np.float32), sr, subtype="PCM_16")


def resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return audio
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(src_sr, dst_sr)
    return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)


def rms_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return 20 * np.log10(max(r, 1e-9))


def fmt_s(x) -> str:
    """Seconds -> '0.84 s' or '—'."""
    if x is None:
        return "—"
    return f"{x:.2f} s"


def fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


class Stopwatch:
    def __init__(self):
        self.t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0
