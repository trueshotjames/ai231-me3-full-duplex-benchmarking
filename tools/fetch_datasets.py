#!/usr/bin/env python3
"""Download the ready-built datasets/ from this repo's GitHub Release (no Google Drive, no gdown).

    python tools/fetch_datasets.py            # about 30 MB, under a minute
    python tools/fetch_datasets.py --force    # replace an existing datasets/ folder

It is the same set tools/build_datasets.py builds (fixed seed), packaged once. The archive's SHA-256 is checked
before anything is unpacked. Standard library only, so it runs before or without the venv packages.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "trueshotjames/ai231-me3-full-duplex-benchmarking"
TAG = "datasets-v2"
ASSET = "fdbench-datasets-v2.zip"   # v2: same audio as v1, full transcripts in manifest.json
URL = f"https://github.com/{REPO}/releases/download/{TAG}/{ASSET}"


def _get(url: str, dst: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total > 1_000_000:
                print(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    if total > 1_000_000:
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description="download the built datasets from the GitHub Release")
    ap.add_argument("--out", default=str(ROOT / "datasets"))
    ap.add_argument("--force", action="store_true", help="replace an existing datasets folder")
    args = ap.parse_args()
    out = Path(args.out)
    if (out / "manifest.json").exists() and not args.force:
        sys.exit(f"{out} already has datasets (manifest.json). Use --force to replace them.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        print(f"Downloading {URL}")
        try:
            _get(URL + ".sha256", tmp / "sum")
            _get(URL, tmp / ASSET)
        except OSError as e:
            sys.exit(f"download failed: {e}\nFallback: python tools/build_datasets.py (builds the same set from "
                     "the authors' Google Drive).")
        want = (tmp / "sum").read_text().split()[0].lower()
        h = hashlib.sha256()
        with open(tmp / ASSET, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != want:
            sys.exit("checksum mismatch: the download is incomplete or altered. Run the command again.")
        with zipfile.ZipFile(tmp / ASSET) as zf:
            names = zf.namelist()
            if any(n.startswith("/") or ".." in Path(n).parts for n in names):
                sys.exit("archive has unsafe paths; not unpacking")
            stage = tmp / "unz"
            zf.extractall(stage)
        src = stage / "datasets"
        if not (src / "manifest.json").exists():
            sys.exit("archive does not contain datasets/manifest.json")
        keep = {p.name: p.read_bytes() for p in (out / "README.md", out / "DATA_LICENSES.md") if p.exists()}
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(src, out)
        for name, data in keep.items():
            (out / name).write_bytes(data)

    groups = [p.name for p in out.iterdir() if p.is_dir()]
    clips = sum(1 for _ in out.rglob("*.flac"))
    print(f"datasets ready: {clips} audio files in {len(groups)} groups → {out}")
    print("Licences: datasets/DATA_LICENSES.md (Candor-derived clips are CC BY-NC 4.0: non-commercial, with credit).")


if __name__ == "__main__":
    main()
