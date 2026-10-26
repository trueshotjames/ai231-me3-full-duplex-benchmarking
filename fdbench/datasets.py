"""Dataset manifest: which clips exist, per group, with the details the live table and the scorer need."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np

from .util import DATASETS_DIR, load_audio, read_json


@dataclass
class Clip:
    id: str
    group: str
    files: Dict[str, str]          # role -> relative path (e.g. {"main": "...flac"} or {"turn":..., "overlap":...})
    duration: float                # seconds of the main/turn file
    meta: dict = field(default_factory=dict)
    root: Path = field(default=DATASETS_DIR, repr=False)

    def audio(self, role: str = "main") -> np.ndarray:
        return load_audio(self.root / self.files[role])

    def describe(self) -> str:
        """One-line clip description for the live table."""
        m = self.meta
        g = self.group
        if g in ("v1_pause_synth", "v1_pause_candor"):
            p = m.get("pause", [None, None])
            return f"{self.id} · {self.duration:.1f} s · pause {p[1]-p[0]:.1f} s at {p[0]:.1f} s"
        if g == "v1_turntaking":
            tail = m.get("last_words") or ""
            return f"{self.id} · turn ends {m.get('turn_end', 0):.1f} s · \"…{tail}\""
        if g == "v1_interrupt":
            return f"{self.id} · Q: \"{_short(m.get('context_text'), 34)}\" · cut-in: \"{_short(m.get('interrupt_text'), 34)}\""
        if g == "false_tt":
            return f"{self.id} · {m.get('kind_label', m.get('kind'))} · {self.duration:.1f} s · {m.get('level_db', 0):+.0f} dB"
        if g.startswith("v15_"):
            ov = m.get("overlap_text") or m.get("kind_label") or "overlap"
            return f"{self.id} · \"{_short(m.get('current_turn_text'), 30)}\" · over her: \"{_short(ov, 28)}\" {m.get('overlap_duration', 0):.1f} s"
        return f"{self.id} · {self.duration:.1f} s"


    def details(self, variant: str = "main") -> tuple:
        """(specs, [(label, transcript), ...]) for the multi-line live display. Transcripts are given in full."""
        m, g = self.meta, self.group
        q = lambda s: f"\"{s.strip()}\"" if (s or "").strip() else "(no transcript in the dataset)"  # noqa: E731
        if g in ("v1_pause_synth", "v1_pause_candor"):
            p = m.get("pause", [0.0, 0.0])
            return (f"{self.duration:.1f} s clip · pause {p[1]-p[0]:.1f} s at {p[0]:.1f} s",
                    [("user", q(m.get("text")))])
        if g == "v1_turntaking":
            return (f"{self.duration:.1f} s clip · user's turn ends at {m.get('turn_end', 0):.1f} s",
                    [("user", q(m.get("text") or m.get("last_words")))])
        if g == "v1_interrupt":
            return (f"{self.duration:.1f} s question · cut-in played while she answers",
                    [("user", q(m.get("context_text"))), ("cut-in", q(m.get("interrupt_text")))])
        if g == "false_tt":
            lines = [("sound", f"{m.get('kind_label', m.get('kind'))}, not addressed to her")]
            if m.get("text"):
                lines.append(("words", q(m["text"])))
            return (f"{self.duration:.1f} s · {m.get('kind_label', m.get('kind'))} · {m.get('level_db', 0):+.0f} dB", lines)
        if g.startswith("v15_"):
            if variant == "clean":
                return (f"{self.duration:.1f} s turn · clean twin, no overlap (reference for the judge)",
                        [("user", q(m.get("context_text")))])
            label = {"v15_interruption": "cut-in", "v15_backchannel": "backchannel",
                     "v15_talking_to_other": "side talk", "v15_background": "background"}.get(g, "overlap")
            return (f"{self.duration:.1f} s turn · {label} {m.get('overlap_duration', 0):.1f} s, played while she answers",
                    [("user", q(m.get("context_text"))), (label, q(m.get("overlap_text")))])
        return (f"{self.duration:.1f} s", [])


def _short(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


class Dataset:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or DATASETS_DIR)
        self.manifest = read_json(self.root / "manifest.json")
        if not self.manifest:
            raise FileNotFoundError(
                f"No manifest.json in {self.root}. Get the datasets first: python tools/fetch_datasets.py "
                f"(real data, on a laptop) or python tools/make_dev_dataset.py (tiny dev set).")
        self.groups: Dict[str, List[Clip]] = {}
        for gkey, g in self.manifest["groups"].items():
            self.groups[gkey] = [Clip(c["id"], gkey, c["files"], c["duration"], c.get("meta", {}), self.root)
                                 for c in g["clips"]]

    def clips(self, group: str) -> List[Clip]:
        return self.groups.get(group, [])

    def counts(self) -> Dict[str, int]:
        return {g: len(c) for g, c in self.groups.items()}

    def verify_checksums(self, max_files: int | None = None) -> List[str]:
        """Return a list of problems (missing or changed files)."""
        problems = []
        n = 0
        for gkey, g in self.manifest["groups"].items():
            for c in g["clips"]:
                for role, rel in c["files"].items():
                    p = self.root / rel
                    if not p.exists():
                        problems.append(f"missing: {rel}")
                        continue
                    want = (c.get("sha256") or {}).get(role)
                    if want:
                        h = hashlib.sha256(p.read_bytes()).hexdigest()
                        if h != want:
                            problems.append(f"changed: {rel}")
                    n += 1
                    if max_files and n >= max_files:
                        return problems
        return problems
