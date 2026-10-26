"""Remembered answers (student number, devices, judge) and .env loading."""
from __future__ import annotations

import os
from pathlib import Path

from .util import ROOT, STATE_DIR, read_json, write_json

LAST_FILE = STATE_DIR / "last.json"


def load_last() -> dict:
    return read_json(LAST_FILE, {}) or {}


def save_last(**updates) -> None:
    data = load_last()
    data.update({k: v for k, v in updates.items() if v is not None})
    write_json(LAST_FILE, data)


def load_dotenv(path: Path | None = None) -> dict:
    """Minimal .env reader: KEY=VALUE lines, '#' comments. Does not override existing env vars."""
    path = path or ROOT / ".env"
    found = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            found[k] = v
            os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass
    return found
