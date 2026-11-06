"""Targets for the summary table (course template, section 5). Defaults below; override with --targets,
targets.json in the fd-benchmark folder, or the wizard."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from .util import ROOT

# key: (label, default, unit)
TARGETS = {
    "e2e": ("E2E latency", 4.0, "s"),     # class agreement; leaves ~1 s for hand-offs, network and tool calls
    "asr_final": ("ASR final", 0.8, "s"),
    "llm_ttft": ("LLM TTFT", 1.5, "s"),   # includes tool time when a reply calls a tool
    "tts_ttfb": ("TTS TTFB", 0.6, "s"),
    "barge_in": ("Barge-in", 0.8, "s"),
    "wer": ("WER", 0.15, "ratio"),
}
DEFAULTS: Dict[str, float] = {k: v[1] for k, v in TARGETS.items()}
TARGETS_FILE = ROOT / "targets.json"


def parse(text: str) -> Dict[str, float]:
    """'e2e=1.0,llm_ttft=0.4,wer=10%' -> {'e2e': 1.0, 'llm_ttft': 0.4, 'wer': 0.10}."""
    out: Dict[str, float] = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ValueError(f"target '{part}' is not key=value (keys: {', '.join(TARGETS)})")
        k, v = (x.strip() for x in part.split("=", 1))
        k = k.lower()
        if k not in TARGETS:
            raise ValueError(f"unknown target '{k}' (keys: {', '.join(TARGETS)})")
        pct = v.endswith("%")
        num = float(v.rstrip("%").rstrip("s").strip())
        out[k] = num / 100 if pct else num
    return out


def load(cli: Optional[str] = None, saved: Optional[dict] = None, path: Path = TARGETS_FILE) -> Dict[str, float]:
    """Defaults, then targets.json, then the run's saved targets, then --targets."""
    t = dict(DEFAULTS)
    try:
        if path.exists():
            t.update({k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k in TARGETS})
    except (ValueError, OSError):
        pass
    if saved:
        t.update({k: float(v) for k, v in saved.items() if k in TARGETS})
    if cli:
        t.update(parse(cli))
    return t


def fmt(key: str, value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f} %" if TARGETS[key][2] == "ratio" else f"{value:.2f} s"
