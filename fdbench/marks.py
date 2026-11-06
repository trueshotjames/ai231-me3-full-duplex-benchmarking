"""Stage marks received during a run (adapters/fdbench_marks.py on the assistant's machine; docs/STAGES.md).

Each mark carries the sending machine's monotonic time `t`. On arrival it is stored with this machine's monotonic
receive time. Per sender, the clock offset is the smallest (receive − t) seen: the mark that crossed fastest, as for
the machine stats (stats.py). Stage-to-stage times use only `t` (one clock: the assistant's machine); the offset is
used to place marks on the trial's audio timeline (`tt`, seconds from trial start).
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .util import append_jsonl

DEFAULT_UDP_PORT = 8792


class MarksStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.marks: List[dict] = []
        self.offsets: Dict[str, float] = {}
        self.models: dict = {}
        self.stages_seen: Dict[str, int] = {}
        self._flushed = 0

    def add(self, d: dict, recv: Optional[float] = None, src: str = "ws") -> None:
        recv = time.monotonic() if recv is None else recv
        if not isinstance(d, dict) or "t" not in d or "stage" not in d:
            return
        try:
            t = float(d["t"])
        except (TypeError, ValueError):
            return
        d = {k: v for k, v in d.items() if k != "event"}
        d["src"] = src
        d["recv"] = recv
        with self.lock:
            off = recv - t
            if src not in self.offsets or off < self.offsets[src]:
                self.offsets[src] = off
            self.marks.append(d)
            self.stages_seen[d["stage"]] = self.stages_seen.get(d["stage"], 0) + 1

    def handle_message(self, msg, src: str = "ws") -> bool:
        """A text frame / datagram. True if it was a mark or a describe message."""
        try:
            d = json.loads(msg)
        except (ValueError, TypeError):
            return False
        if not isinstance(d, dict):
            return False
        ev = d.get("event")
        if ev == "mark":
            self.add(d, src=src)
            return True
        if ev == "describe":
            with self.lock:
                self.models.update({str(k): str(v) for k, v in (d.get("models") or {}).items()})
            return True
        return False

    def _lt(self, m: dict) -> float:
        return float(m["t"]) + self.offsets.get(m["src"], 0.0)

    def window(self, t0: float, t1: float, pad: float = 0.5) -> List[dict]:
        """Marks whose time on this machine's clock falls in [t0 - pad, t1 + pad], with `lt` (this machine's clock)."""
        with self.lock:
            out = []
            for m in self.marks:
                lt = self._lt(m)
                if t0 - pad <= lt <= t1 + pad:
                    out.append(dict(m, lt=lt))
            return out

    def flush(self, run_dir: Path) -> None:
        with self.lock:
            new = [dict(m, lt=self._lt(m)) for m in self.marks[self._flushed:]]
            self._flushed = len(self.marks)
        for m in new:
            append_jsonl(run_dir / "marks.jsonl", m)

    def summary(self) -> dict:
        with self.lock:
            return {"stages_seen": dict(self.stages_seen), "n": len(self.marks), "clock_offsets": dict(self.offsets),
                    "models": dict(self.models)}


class UdpMarksListener:
    """Receives marks from processes that are not on the benchmark socket (MCP servers, cable runs, other harnesses)."""

    def __init__(self, store: MarksStore, host: str = "0.0.0.0", port: int = DEFAULT_UDP_PORT):
        self.store = store
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(0.5)
        self.port = port
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fdbench-marks-udp")

    def start(self) -> "UdpMarksListener":
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.store.handle_message(data.decode("utf-8", errors="replace"), src=f"udp:{addr[0]}")
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


def start_udp(store: MarksStore, port: int, host: str = "0.0.0.0") -> Optional[UdpMarksListener]:
    if not port:
        return None
    try:
        return UdpMarksListener(store, host=host, port=port).start()
    except OSError:
        return None
