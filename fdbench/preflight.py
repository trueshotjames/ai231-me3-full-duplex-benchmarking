"""Automatic pre-flight (about 15 s). Produces warnings, never blocks."""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import __version__
from .sysinfo import summary
from .util import ROOT, DATASETS_DIR


@dataclass
class Check:
    ok: bool            # True = ✓, False = ! warning
    text: str
    fix: str = ""


@dataclass
class Preflight:
    checks: List[Check] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    devices: dict = field(default_factory=dict)

    @property
    def warnings(self) -> List[Check]:
        return [c for c in self.checks if not c.ok]

    def to_json(self) -> dict:
        return {"checks": [c.__dict__ for c in self.checks], "info": self.info,
                "devices": {k: (v.__dict__ if hasattr(v, "__dict__") else v) for k, v in self.devices.items()
                            if k in ("to_bot", "from_bot") and v is not None}}


def run_preflight(dataset_dir: Path, sim: bool = False, judge=None, mode: str = "cables", port: int = 8791,
                  where: str = "local", project: Optional[Path] = None, project_python: Optional[Path] = None, want_to_bot: str | None = None,
                  want_from_bot: str | None = None) -> Preflight:
    pf = Preflight()
    info = summary(ROOT)
    pf.info = info

    # python + packages
    pyv = sys.version_info
    pf.checks.append(Check(pyv >= (3, 10), f"Python {pyv.major}.{pyv.minor}.{pyv.micro}",
                           "Python 3.10 or newer is needed"))
    missing = []
    for mod in ("numpy", "scipy", "soundfile", "onnxruntime", "rich"):
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    pf.checks.append(Check(not missing, "packages: core " + ("ok" if not missing else "missing " + ", ".join(missing)),
                           "pip install -r requirements.txt"))
    optional = {}
    for mod, label in (("faster_whisper", "ASR (faster-whisper)"), ("parselmouth", "pitch/intensity (praat-parselmouth)"),
                       ("utmosv2", "UTMOSv2 quality")):
        try:
            importlib.import_module(mod)
            optional[label] = True
        except Exception:  # noqa: BLE001
            optional[label] = False
    off = [k for k, v in optional.items() if not v]
    pf.checks.append(Check(True, "scoring extras: " + (", ".join(k for k, v in optional.items() if v) or "none")
                           + (f" · not installed: {', '.join(off)}" if off else ""),
                           "pip install -r requirements-score.txt (laptop) to score transcripts and voice change"))
    pf.info["optional"] = optional

    # machine
    mach = info["pi_model"] or f"{info['os']} ({info['machine']})"
    pf.checks.append(Check(True, f"{mach} · {info['ram_gb'] or '?'} GB RAM · {info['disk_free_gb']} GB free"))
    if info["disk_free_gb"] < 1.0:
        pf.checks.append(Check(False, f"only {info['disk_free_gb']} GB free on disk", "a 10-round run needs about 0.5 GB"))
    if info["pi_temp_c"] is not None:
        t = info["pi_temp_c"]
        thr = info["pi_throttled"] or {}
        hot = t >= 75 or thr.get("throttled_now")
        pf.checks.append(Check(not hot, f"CPU {t:.0f} °C" + (" · throttling now" if thr.get("throttled_now") else
                               (" · was throttled earlier" if thr.get("throttled_ever") else "")),
                               "add a fan or heatsink; throttling slows the assistant and skews latencies"))
    net = info["network"]
    pf.checks.append(Check(net != "offline" and "wifi" not in net, f"network: {net}",
                           "use Ethernet if you can; Wi-Fi jitter widens latencies"))

    # datasets
    manifest = dataset_dir / "manifest.json"
    if manifest.exists():
        try:
            from .datasets import Dataset
            ds = Dataset(dataset_dir)
            counts = ds.counts()
            from .suites import GROUPS
            absent = [g for g in GROUPS if counts.get(g, 0) == 0]
            if absent:
                pf.checks.append(Check(False, f"datasets: {len(absent)} of {len(GROUPS)} test groups missing ({', '.join(absent)})",
                                       "python tools/fetch_datasets.py --force"))
            small = [g for g, n in counts.items() if n < 10]
            pf.checks.append(Check(not small, f"datasets: {sum(counts.values())} clips in {len(counts)} groups"
                                   + (f" · small groups: {', '.join(small)}" if small else ""),
                                   "python tools/fetch_datasets.py --force gets the full set"))
            problems = ds.verify_checksums(max_files=60)
            pf.checks.append(Check(not problems, "dataset checksums ok (sampled)" if not problems
                                   else f"dataset problems: {problems[:3]}", "rebuild the datasets"))
        except Exception as e:  # noqa: BLE001
            pf.checks.append(Check(False, f"datasets: {e}", "python tools/fetch_datasets.py --force"))
    else:
        pf.checks.append(Check(False, f"no datasets in {dataset_dir}", "python tools/fetch_datasets.py (laptop) or "
                               "python tools/make_dev_dataset.py (tiny dev set)"))

    # audio devices
    if sim:
        pf.checks.append(Check(True, "rig: simulated assistant (no audio devices used)"))
    elif mode == "direct":
        try:
            importlib.import_module("websockets.sync.server")
            pf.checks.append(Check(True, f"rig: direct feed on ws://127.0.0.1:{port} (no audio devices used)"))
        except Exception:  # noqa: BLE001
            pf.checks.append(Check(False, "rig: direct feed needs the websockets package", "pip install websockets"))
        if where == "local":
            pf.checks.append(Check(project is not None, "assistant project: " + (str(project) if project else "not found"),
                                   "clone fd-benchmark inside the project folder, or set FDBENCH_PROJECT in fd-benchmark/.env"))
            if project is not None:
                pf.checks.append(Check(project_python is not None, "assistant python: " + (str(project_python) if project_python
                                       else "no .venv in the project"),
                                       "create the project's venv, or set FDBENCH_ASSISTANT_PYTHON in fd-benchmark/.env"))
        else:
            pf.checks.append(Check(True, "assistant runs on another machine (Pi); fdbench will listen on all interfaces"))
        import socket
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                pf.checks.append(Check(False, f"port {port} is already in use", "close the other program or pass --port"))
    else:
        try:
            from .rig import devices as dv
            picked = dv.auto_pick()
            if want_to_bot:
                d = dv.find_by_name(want_to_bot, "to_bot")
                if d:
                    picked["to_bot"] = d
            if want_from_bot:
                d = dv.find_by_name(want_from_bot, "from_bot")
                if d:
                    picked["from_bot"] = d
            pf.devices = picked
            tb, fb = picked["to_bot"], picked["from_bot"]
            pf.checks.append(Check(tb is not None, "cable 1 (her mic ← us): " + (tb.short() if tb else "not found"),
                                   "install a virtual cable, see docs/SETUP.md"))
            pf.checks.append(Check(fb is not None, "cable 2 (her speaker → us): " + (fb.short() if fb else "not found"),
                                   "install a second virtual cable, see docs/SETUP.md"))
            if tb and fb and dv._same_cable(tb.name, fb.name):
                pf.checks.append(Check(False, "both ends point at the same cable; she would hear the test clips as her own voice",
                                       "use two different cables"))
            if (tb and tb.looks_physical) or (fb and fb.looks_physical):
                pf.checks.append(Check(False, "a physical mic or speaker is selected", "pick the virtual cables"))
            if picked.get("ambiguous"):
                pf.checks.append(Check(True, "more than one virtual device found; pass --to-bot/--from-bot to choose"))
        except Exception as e:  # noqa: BLE001
            pf.checks.append(Check(False, f"audio devices: {e}", "pip install sounddevice; on the Pi: sudo apt install libportaudio2"))

    # judge
    if judge is not None:
        try:
            ok, msg = judge.ping()
            pf.checks.append(Check(ok, f"judge: {judge.describe()} · {msg}", "check the API key / URL in .env"))
        except Exception as e:  # noqa: BLE001
            pf.checks.append(Check(False, f"judge: {e}", "check the API key / URL in .env"))
    else:
        pf.checks.append(Check(True, "judge: none (timing and turn-taking metrics only; judge later with `score`)"))
    pf.info["fdbench"] = __version__
    return pf
