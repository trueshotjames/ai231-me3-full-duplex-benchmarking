"""Direct-feed rig: no microphone, no speaker, no virtual cables.

fdbench runs a small WebSocket server on this machine. The assistant, started in benchmark mode, connects
to it instead of opening her audio devices (see adapters/pipecat_fdbench.py):

  fdbench -> assistant   binary messages: 16 kHz mono int16 PCM, sent in real time (her "microphone")
  assistant -> fdbench   binary messages: mono int16 PCM at `out_sr` (default 16 kHz), her TTS output
                         text message {"event": "clear"}: she was interrupted, drop what is still queued

What she sends goes into a playout buffer that fdbench drains in real time, like a speaker would. A
"clear" empties it, like a speaker that stops playing. One clock thread ticks every chunk: it sends the
next chunk of test audio and takes the next chunk of her playout, so both tracks share one clock.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import numpy as np

from .. import SR
from ..util import resample
from .base import RigBase, Recording, TrialScript

DEFAULT_PORT = 8791


class _Playout:
    """Her audio, queued for real-time "playback"."""

    def __init__(self, max_seconds: float = 120.0):
        self.lock = threading.Lock()
        self.buf = np.zeros(0, np.float32)
        self.max = int(max_seconds * SR)
        self.clears = 0

    def push(self, audio: np.ndarray) -> None:
        with self.lock:
            self.buf = np.concatenate([self.buf, audio])[-self.max:]

    def pop(self, n: int) -> np.ndarray:
        with self.lock:
            take = self.buf[:n]
            self.buf = self.buf[n:]
        if len(take) < n:
            take = np.pad(take, (0, n - len(take)))
        return take

    def clear(self) -> None:
        with self.lock:
            self.buf = np.zeros(0, np.float32)
            self.clears += 1


class DirectRig(RigBase):
    name = "direct"

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, chunk_ms: int = 32,
                 connect_timeout: float = 300.0):
        super().__init__(chunk_ms)
        self.host, self.port = host, port
        self.connect_timeout = connect_timeout
        self.playout = _Playout()
        self.conn = None
        self.out_sr = SR
        self.connected = threading.Event()
        self.disconnects = 0
        self._sess = None
        self._sess_lock = threading.Lock()
        self._stop = threading.Event()
        self._server = None
        self._threads: list = []
        self.peer: Optional[str] = None
        self.rtt_ms: Optional[float] = None
        self.launched = None

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    # ------------------------------------------------------------ server
    def open(self) -> None:
        try:
            from websockets.sync.server import serve
        except ImportError as e:
            raise RuntimeError("direct feed needs the websockets package: pip install websockets") from e
        self._server = serve(self._handler, self.host, self.port, max_size=None, compression=None,
                             ping_interval=None)  # localhost: a dropped peer closes the socket anyway
        t1 = threading.Thread(target=self._server.serve_forever, daemon=True)
        t2 = threading.Thread(target=self._clock, daemon=True)
        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def wait_for_assistant(self, timeout: Optional[float] = None) -> bool:
        return self.connected.wait(timeout if timeout is not None else self.connect_timeout)

    def measure_rtt(self, n: int = 5) -> Optional[float]:
        """Median WebSocket ping round trip to the assistant's machine, in ms (None if not connected)."""
        conn = self.conn
        if conn is None:
            return None
        samples = []
        for _ in range(n):
            try:
                t0 = time.perf_counter()
                ev = conn.ping()
                if ev.wait(2.0):
                    samples.append((time.perf_counter() - t0) * 1000.0)
            except Exception:  # noqa: BLE001
                break
        if samples:
            samples.sort()
            self.rtt_ms = samples[len(samples) // 2]
        return self.rtt_ms

    def link_info(self) -> dict:
        return {"peer": self.peer, "rtt_ms": self.rtt_ms, "host": self.host, "port": self.port,
                "disconnects": self.disconnects}

    def close(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()

    def _handler(self, conn) -> None:
        if self.connected.is_set():
            conn.close(1013, "fdbench already has an assistant connected")
            return
        q = parse_qs(urlparse(getattr(conn.request, "path", "") or "").query)
        self.out_sr = int(q.get("out_sr", [SR])[0])
        self.conn = conn
        try:
            self.peer = f"{conn.remote_address[0]}:{conn.remote_address[1]}"
        except Exception:  # noqa: BLE001
            self.peer = None
        self.playout.clear()
        self.playout.clears = 0
        self.connected.set()
        try:
            for msg in conn:
                if isinstance(msg, (bytes, bytearray)):
                    a = np.frombuffer(msg, dtype="<i2").astype(np.float32) / 32768.0
                    if self.out_sr != SR:
                        a = resample(a, self.out_sr, SR)
                    self.playout.push(a)
                else:
                    try:
                        ev = json.loads(msg).get("event")
                    except (ValueError, AttributeError):
                        ev = None
                    if ev == "clear":
                        self.playout.clear()
        except Exception:  # noqa: BLE001  (connection dropped)
            pass
        finally:
            self.conn = None
            self.connected.clear()
            self.disconnects += 1

    # ------------------------------------------------------------- clock
    def _clock(self) -> None:
        n = self.chunk
        period = n / SR
        next_t = time.monotonic()
        while not self._stop.is_set():
            with self._sess_lock:
                sess = self._sess
            out = sess.next_out(n) if sess is not None else np.zeros(n, np.float32)
            conn = self.conn
            if conn is not None:
                try:
                    conn.send((np.clip(out, -1, 1) * 32767).astype("<i2").tobytes())
                except Exception:  # noqa: BLE001
                    pass
            heard = self.playout.pop(n)  # drained even between trials, so nothing stale is left over
            if sess is not None:
                sess.feed_in(heard)
                if sess.done.is_set():
                    with self._sess_lock:
                        self._sess = None
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.5:  # fell far behind (machine stalled): resync instead of bursting
                next_t = time.monotonic()

    # --------------------------------------------------------------- run
    def run(self, script: TrialScript) -> Recording:
        if not self.connected.is_set() and not self.wait_for_assistant(30):
            raise RuntimeError(f"assistant is not connected to {self.url}")
        drops_before, clears_before = self.disconnects, self.playout.clears
        sess = self._Session(self, script)
        with self._sess_lock:
            self._sess = sess
        sess.done.wait(timeout=script.max_duration + 5)
        with self._sess_lock:
            self._sess = None
        rec = sess.finish()
        rec.notes.update({"rig": "direct", "her_out_sr": self.out_sr,
                          "clears": max(0, self.playout.clears - clears_before)})
        if self.disconnects != drops_before:
            rec.notes["disconnected"] = True
            raise RuntimeError("assistant disconnected during the trial")
        return rec
