"""Review recordings: one stereo file per trial (left = what was played to her, right = what she said),
named with the verdict, so failures can be found and listened to. Also the keep/delete choice after a run."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import numpy as np

from .. import SR
from ..util import load_audio, read_json, save_audio

INDEX_HEADER = ("# Review recordings\n\nEach file is stereo: LEFT = the test audio played to the assistant, "
                "RIGHT = what the assistant said. The file name starts with ok/FAIL and the trial id.\n\n"
                "| file | test | clip | expected | actual | verdict |\n|---|---|---|---|---|---|\n")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:60]


def export_review(run_dir: Path, failed_only: bool = False) -> Path:
    out = run_dir / "recordings"
    out.mkdir(exist_ok=True)
    rows = []
    for tdir in sorted((run_dir / "trials").iterdir()):
        t = read_json(tdir / "timing.json")
        if not t or t.get("error"):
            continue
        final = t.get("final", {})
        ok = final.get("ok", t.get("ok"))
        verdict = final.get("verdict", t.get("verdict", ""))
        if failed_only and ok:
            continue
        played = load_audio(tdir / "played.flac")
        her = load_audio(tdir / "her.flac")
        n = max(len(played), len(her))
        stereo = np.zeros((n, 2), np.float32)
        stereo[:len(played), 0] = played
        stereo[:len(her), 1] = her
        name = f"{'ok' if ok else 'FAIL'}_{_safe(t['trial'])}.flac"
        import soundfile as sf
        sf.write(str(out / name), stereo, SR, subtype="PCM_16")
        rows.append(f"| {name} | {t['group']} | {t['clip']}{' (clean)' if t.get('variant') == 'clean' else ''} | "
                    f"{t.get('expected', '')} | {t.get('actual', '')} | {verdict} |")
    (out / "INDEX.md").write_text(INDEX_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return out


def delete_raw(run_dir: Path) -> int:
    """Remove the per-trial audio (keeps timings, transcripts, judge outputs and results)."""
    n = 0
    for p in (run_dir / "trials").rglob("*.flac"):
        p.unlink()
        n += 1
    rec = run_dir / "recordings"
    if rec.exists():
        shutil.rmtree(rec)
    return n


def folder_size_mb(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 2**20
