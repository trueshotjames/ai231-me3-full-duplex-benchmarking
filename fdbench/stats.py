"""Machine stats received during a run (from the launcher on the Pi, or sampled here with --host-stats).

Each sample carries the sampling machine's monotonic time `t`. On arrival it is stored with this machine's monotonic
receive time. The clock offset is the smallest (receive − t) seen so far: the sample that crossed the network fastest,
so jitter on slower ones does not shift the timeline. `lt` = t + offset is the sample on this machine's clock, the
same clock the runner uses for trial start and end.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import List, Optional

from .util import append_jsonl, write_json


class StatsStore:
    def __init__(self, source: str = "remote"):
        self.source = source  # "remote" (launcher on the assistant's machine) or "host" (this machine)
        self.lock = threading.Lock()
        self.hello: Optional[dict] = None
        self.samples: List[dict] = []
        self.offset: Optional[float] = None
        self._flushed = 0

    def set_hello(self, hello: dict) -> None:
        with self.lock:
            self.hello = dict(hello, source=self.source)

    def add(self, sample: dict, recv: Optional[float] = None) -> None:
        recv = time.monotonic() if recv is None else recv
        if not isinstance(sample, dict) or "t" not in sample:
            return
        with self.lock:
            off = recv - float(sample["t"])
            if self.offset is None or off < self.offset:
                self.offset = off
            self.samples.append(sample)

    def handle_message(self, msg: str) -> bool:
        """A text frame from the launcher. True if it was a stats message."""
        try:
            d = json.loads(msg)
        except (ValueError, TypeError):
            return False
        if not isinstance(d, dict):
            return False
        ev = d.get("event")
        if ev == "stats_hello":
            self.set_hello(d.get("hello") or {})
            return True
        if ev == "stats":
            self.add(d.get("s") or {})
            return True
        return False

    # ------------------------------------------------------------------ reading
    def _mapped(self, s: dict) -> dict:
        return dict(s, lt=float(s["t"]) + (self.offset or 0.0))

    def window(self, t0: float, t1: float) -> List[dict]:
        """Samples whose time on this machine's clock falls in [t0, t1]."""
        with self.lock:
            return [self._mapped(s) for s in self.samples if t0 <= float(s["t"]) + (self.offset or 0.0) <= t1]

    def latest(self) -> Optional[dict]:
        with self.lock:
            return self._mapped(self.samples[-1]) if self.samples else None

    def flush(self, run_dir: Path) -> None:
        """Append new samples to run_dir/pi_stats.jsonl and (re)write the hello."""
        with self.lock:
            new = [self._mapped(s) for s in self.samples[self._flushed:]]
            self._flushed = len(self.samples)
            hello = self.hello
            offset = self.offset
        for s in new:
            append_jsonl(run_dir / "pi_stats.jsonl", s)
        if hello is not None:
            write_json(run_dir / "pi_stats_hello.json", dict(hello, clock_offset=offset))

    def status_line(self) -> str:
        """Short live summary, e.g. 'Pi 62 °C · CPU 48% · 1500 MHz · throttled'."""
        s = self.latest()
        if not s:
            return ""
        who = "Pi" if (self.hello or {}).get("pi_model") else ("host" if self.source == "host" else "her machine")
        parts = [who]
        if s.get("temp_c") is not None:
            parts.append(f"{s['temp_c']:.0f} °C")
        if s.get("cpu") is not None:
            parts.append(f"CPU {s['cpu']:.0f}%")
        if s.get("arm_mhz"):
            parts.append(f"{s['arm_mhz']:.0f} MHz")
        thr = s.get("throttled") or {}
        now = [k.replace("_now", "").replace("_", " ") for k, v in thr.items() if k.endswith("_now") and v]
        if now:
            parts.append("[red]" + ", ".join(now) + "[/red]")
        return " · ".join(parts) if len(parts) > 1 else ""

    def start_warnings(self) -> List[str]:
        """Things worth knowing before trusting the run: hot at start, under-voltage since boot."""
        with self.lock:
            first = self.samples[0] if self.samples else None
        if not first:
            return []
        out = []
        if first.get("temp_c") is not None and first["temp_c"] >= 70:
            out.append(f"the machine was already at {first['temp_c']:.0f} °C when the run started (Pi 4 slows down at 80 °C)")
        thr = first.get("throttled") or {}
        if thr.get("under_voltage_ever"):
            out.append("under-voltage happened since the last boot: use the official 5.1 V / 3 A power supply")
        if thr.get("throttled_ever") or thr.get("soft_temp_limit_ever"):
            out.append("the Pi was throttled since the last boot, before this run")
        return out


class HostSampler:
    """--host-stats: sample this machine in a thread (no process labels), for runs without the launcher."""

    def __init__(self, store: StatsStore, hz: float = 1.0):
        from .probe import Probe
        self.store = store
        self.period = 1.0 / max(0.1, hz)
        self.probe = Probe(root_pid=None, patterns={})
        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None

    def start(self) -> "HostSampler":
        self.store.set_hello(self.probe.hello())
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def _loop(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            try:
                s = self.probe.sample()
                self.store.add(s, recv=s["t"])
            except Exception:  # noqa: BLE001
                pass
            nxt += self.period
            self._stop.wait(max(0.0, nxt - time.monotonic()))

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=3)
