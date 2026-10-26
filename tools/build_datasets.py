#!/usr/bin/env python3
"""Build datasets/ from the Full-Duplex-Bench v1.0 + v1.5 release (run once, on a laptop with internet).

    pip install gdown
    python tools/build_datasets.py                 # downloads (~2 GB) to downloads/fdb, builds datasets/
    python tools/build_datasets.py --src PATH      # use an already downloaded/unzipped copy

The authors host the data on Google Drive:
https://drive.google.com/drive/folders/1DtoxMVO9_Y_nDs2peZtx3pw-U2qYgpd3 (see their dataset README).
Per group, 30 clips are picked with a fixed seed so everyone builds the same set. Licences: Candor-derived
v1.0 sets CC BY-NC 4.0; synthetic v1.0 and all v1.5 data MIT (authors' README). See datasets/DATA_LICENSES.md.
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fdbench import SR  # noqa: E402
from fdbench.dataset_build import (ManifestBuilder, last_words, make_noise, make_tv_radio,  # noqa: E402
                                   trim_trailing_silence)
from fdbench.util import load_audio, read_json  # noqa: E402

DRIVE_FOLDER = "1DtoxMVO9_Y_nDs2peZtx3pw-U2qYgpd3"
SEED = 20261107
PER_GROUP = 30
FOLDERS = {
    "synthetic_pause_handling": "v1_pause_synth", "candor_pause_handling": "v1_pause_candor",
    "candor_turn_taking": "v1_turntaking", "synthetic_user_interruption": "v1_interrupt",
    "user_interruption": "v15_interruption", "user_backchannel": "v15_backchannel",
    "talking_to_other": "v15_talking_to_other", "background_speech": "v15_background",
}
TITLES = {"v1_pause_synth": "v1 pause (synthetic)", "v1_pause_candor": "v1 pause (Candor)",
          "v1_turntaking": "v1 turn-taking (Candor)", "v1_interrupt": "v1 interruption",
          "v15_interruption": "v1.5 interruption", "v15_backchannel": "v1.5 backchannel",
          "v15_talking_to_other": "v1.5 talking to others", "v15_background": "v1.5 background speech"}
LIC = {"v1_pause_synth": "MIT (FDB synthetic)", "v1_pause_candor": "CC BY-NC 4.0 (FDB, from Candor)",
       "v1_turntaking": "CC BY-NC 4.0 (FDB, from Candor)", "v1_interrupt": "MIT (FDB synthetic)",
       "v15_interruption": "MIT (FDB v1.5)", "v15_backchannel": "MIT (FDB v1.5)",
       "v15_talking_to_other": "MIT (FDB v1.5)", "v15_background": "MIT (FDB v1.5)"}


def download(dst: Path) -> Path:
    try:
        import gdown
    except ImportError:
        sys.exit("pip install gdown  (needed to download from Google Drive)")
    dst.mkdir(parents=True, exist_ok=True)
    print(f"Downloading the Full-Duplex-Bench data folder to {dst} (about 2 GB, 10–30 min)…")
    kwargs = dict(id=DRIVE_FOLDER, output=str(dst), quiet=False, use_cookies=False)
    try:
        gdown.download_folder(**kwargs, remaining_ok=True)   # older gdown: lift the 50-file limit
    except TypeError:
        gdown.download_folder(**kwargs)                      # gdown >= 5: no limit, no such argument
    for z in dst.rglob("*.zip"):
        print(f"unzipping {z.name}")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(z.parent / z.stem)
    return dst


def _is_sample(d: Path) -> bool:
    return d.is_dir() and ((d / "input.wav").exists() or (d / "context.wav").exists())


def find_folders(src: Path) -> dict:
    """Map each release folder name to the directory that actually holds its numbered samples.

    The zips unpack as NAME/NAME/1, NAME/NAME/2, ... next to a __MACOSX copy, so take the deepest
    match that has sample folders and ignore __MACOSX."""
    found, best = {}, {}
    for p in src.rglob("*"):
        if not p.is_dir() or p.name not in FOLDERS or "__MACOSX" in p.parts:
            continue
        n = sum(1 for c in p.iterdir() if _is_sample(c))
        if n > best.get(p.name, 0):
            best[p.name], found[p.name] = n, p
    return found


def sample_ids(folder: Path, rng: random.Random, n: int) -> list:
    ids = sorted([p for p in folder.iterdir() if _is_sample(p)], key=lambda p: p.name)
    rng.shuffle(ids)
    return ids[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="folder with the downloaded data (skips the download)")
    ap.add_argument("--out", default=str(ROOT / "datasets"))
    ap.add_argument("--per-group", type=int, default=PER_GROUP)
    ap.add_argument("--downloads", default=str(ROOT / "downloads" / "fdb"))
    args = ap.parse_args()
    src = Path(args.src) if args.src else download(Path(args.downloads))
    folders = find_folders(src)
    missing = [k for k in FOLDERS if k not in folders]
    for k, v in folders.items():
        print(f"found {k}: {v}")
    if missing:
        print(f"warning: not found under {src}: {missing}")
    if len(missing) == len(FOLDERS):
        sys.exit(f"No Full-Duplex-Bench sample folders found under {src}; nothing built. "
                 "Check that the zips were unpacked (see docs/TROUBLESHOOTING.md).")
    rng = random.Random(SEED)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    mb = ManifestBuilder(out, "Full-Duplex-Bench v1.0 + v1.5 release (Lin et al.), subset built by tools/build_datasets.py")
    overlap_pool = {"side": [], "bg": [], "clean": []}   # (audio, transcript, source clip id)

    for fname, g in FOLDERS.items():
        if fname not in folders:
            continue
        picked = sample_ids(folders[fname], random.Random(f"{SEED}-{g}"), args.per_group)
        print(f"{g}: {len(picked)} clips")
        for d in picked:
            cid = f"{g}_{d.name}"
            try:
                if g in ("v1_pause_synth", "v1_pause_candor"):
                    a = load_audio(d / "input.wav")
                    pause = read_json(d / "pause.json")[0]["timestamp"]
                    words = read_json(d / "transcription.json") or []
                    if words and isinstance(words[0], dict) and "item" in words[0]:
                        words = [w for sp in words for w in sp["item"]]
                    mb.add(g, TITLES[g], cid, {"main": a}, {"pause": pause, "source_id": d.name,
                           "text": " ".join(w["text"] for w in words)}, licence=LIC[g])
                elif g == "v1_turntaking":
                    a = load_audio(d / "input.wav")
                    te = read_json(d / "turn_taking.json")[0]["timestamp"][0]
                    words = read_json(d / "transcription.json") or []
                    if words and isinstance(words[0], dict) and "item" in words[0]:
                        words = [w for sp in words for w in sp["item"]]
                    a = a[:int((te + 0.3) * SR)]
                    mb.add(g, TITLES[g], cid, {"main": a}, {"turn_end": te, "last_words": last_words(words, te),
                           "source_id": d.name}, licence=LIC[g])
                elif g == "v1_interrupt":
                    ctx = load_audio(d / "context.wav")
                    inter = load_audio(d / "interrupt.wav")
                    meta = read_json(d / "interrupt.json")[0]
                    mb.add(g, TITLES[g], cid, {"context": trim_trailing_silence(ctx), "interrupt": inter},
                           {"context_text": meta["context"], "interrupt_text": meta["interrupt"], "source_id": d.name},
                           duration_role="context", licence=LIC[g])
                    overlap_pool["clean"].append((inter, meta["interrupt"], cid))
                else:  # v1.5
                    noisy = load_audio(d / "input.wav")
                    clean = load_audio(d / "clean_input.wav")
                    meta = read_json(d / "metadata.json")
                    t0, t1 = meta["timestamps"]
                    ov = noisy[int(t0 * SR):int(t1 * SR)]
                    turn = trim_trailing_silence(clean[:int(t0 * SR)]) if t0 > 1.0 else trim_trailing_silence(clean)
                    label = {"v15_interruption": meta.get("current_turn_text") or "new request",
                             "v15_backchannel": meta.get("backchannel_text") or "backchannel",
                             "v15_talking_to_other": meta.get("current_turn_text") or "side talk to someone else",
                             "v15_background": meta.get("background_text") or "other voice, unrelated"}[g]
                    mb.add(g, TITLES[g], cid, {"turn": turn, "overlap": ov},
                           {"context_text": meta.get("context_text", ""), "current_turn_text": meta.get("current_turn_text", ""),
                            "overlap_text": label, "overlap_duration": round(len(ov) / SR, 2),
                            "overlap_window": [t0, t1], "source_id": d.name}, duration_role="turn", licence=LIC[g])
                    if g == "v15_talking_to_other":
                        overlap_pool["side"].append((ov, label, cid))
                    elif g == "v15_background":
                        overlap_pool["bg"].append((ov, label, cid))
            except Exception as e:  # noqa: BLE001
                print(f"  skip {d}: {e}")

    # false turn-taking: 10 per kind, from the v1.5 overlap windows (already processed by the authors) + synthesized
    n_kind = max(1, args.per_group // 3)
    for kind, label, pool, lvl in (("side_talk", "side talk", overlap_pool["side"], -8),
                                   ("background", "background speech", overlap_pool["bg"], -15)):
        rng.shuffle(pool)
        for i, (a, text, src) in enumerate(pool[:n_kind]):
            mb.add("false_tt", "false turn-taking", f"false_{kind}_{i:03d}", {"main": a},
                   {"kind": kind, "kind_label": label, "level_db": lvl, "text": text, "source": src},
                   licence="MIT (FDB v1.5 overlap windows)")
    clean = overlap_pool["clean"]
    rng.shuffle(clean)
    for i, (a, text, src) in enumerate(clean[:n_kind]):
        mb.add("false_tt", "false turn-taking", f"false_tv_radio_{i:03d}", {"main": make_tv_radio(a, rng)},
               {"kind": "tv_radio", "kind_label": "TV / radio", "level_db": -15, "text": text, "source": src}, licence="MIT (FDB synthetic, processed)")
    for i in range(n_kind):
        a, desc = make_noise(i, rng.uniform(3.0, 5.0), rng)
        mb.add("false_tt", "false turn-taking", f"false_noise_{i:03d}", {"main": a},
               {"kind": "noise", "kind_label": f"noise: {desc}", "level_db": -6}, licence="ours (synthesized)")

    p = mb.write()
    print(f"\nwrote {p}")
    for g, d in mb.groups.items():
        print(f"  {g:24s} {len(d['clips'])} clips")
    size = sum(f.stat().st_size for f in out.rglob("*.flac")) / 2**20
    print(f"total audio: {size:.0f} MB")


if __name__ == "__main__":
    main()
