"""The rig: plays audio into the assistant and records what it says, on one sample clock.

A Trial is run through `Rig.run(script)`, where `script` is a TrialScript describing what to play and
when. The rig returns a Recording with two 16 kHz tracks aligned on the same clock:
  played: everything the rig played (the "user")
  heard:  everything the assistant said
plus timing events (when each play started, when live VAD heard the assistant start/stop).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

from .. import SR
from .vad import LiveVAD


@dataclass
class PlayEvent:
    name: str
    audio: np.ndarray  # 16 kHz float32
    t: float  # seconds on the trial clock when playback started


@dataclass
class TrialScript:
    """What to play.

    play_first:   audio played at t=0 (the user's turn, or a foreign clip)
    overlap:      optional audio to play `overlap_after_bot_start` seconds after the assistant starts speaking
    overlap_fallback_t: if the assistant has not started by this time, play the overlap anyway at this time (None: skip)
    record_after: keep recording this many seconds after the last play ended
    end_on_silence: stop early if the assistant has been silent this long after it spoke (None: wait full record_after)
    max_duration: hard cap on the trial length
    """
    play_first: np.ndarray
    overlap: Optional[np.ndarray] = None
    overlap_after_bot_start: float = 1.5
    overlap_fallback_t: Optional[float] = None
    record_after: float = 8.0
    end_on_silence: Optional[float] = None
    max_duration: float = 60.0
    on_event: Optional[Callable[[str, float], None]] = None  # live notifications (bot_start, bot_end, play...)
    tag: str = ""  # what kind of trial this is; only the simulated assistant looks at it


@dataclass
class Recording:
    played: np.ndarray
    heard: np.ndarray
    events: List[PlayEvent] = field(default_factory=list)
    bot_starts: List[float] = field(default_factory=list)
    bot_ends: List[float] = field(default_factory=list)
    duration: float = 0.0
    notes: dict = field(default_factory=dict)

    def play_window(self, name: str):
        for e in self.events:
            if e.name == name:
                return e.t, e.t + len(e.audio) / SR
        return None


class RigBase:
    """Common scheduling logic. Subclasses implement the audio transport by calling `self._tick`."""

    name = "base"

    def __init__(self, chunk_ms: int = 32):
        self.chunk = int(SR * chunk_ms / 1000)

    # -- to be implemented by transports ---------------------------------
    def open(self) -> None:  # noqa: D401
        pass

    def close(self) -> None:
        pass

    def run(self, script: TrialScript) -> Recording:
        raise NotImplementedError

    # -- shared scheduler --------------------------------------------------
    class _Session:
        def __init__(self, rig: "RigBase", script: TrialScript):
            self.rig = rig
            self.script = script
            self.played_chunks: list = []
            self.heard_chunks: list = []
            self.pos = 0  # samples elapsed on the clock
            self.queue: list = []  # (start_sample, name, audio, offset)
            self.events: List[PlayEvent] = []
            self.bot_starts: List[float] = []
            self.bot_ends: List[float] = []
            self.overlap_fired = False
            self.last_play_end = 0.0
            self.done = threading.Event()
            self.lock = threading.Lock()
            self.vad = LiveVAD(on_start=self._bot_start, on_end=self._bot_end)
            self.schedule("first", script.play_first, 0)

        def t(self) -> float:
            return self.pos / SR

        def schedule(self, name: str, audio: np.ndarray, at_sample: int) -> None:
            with self.lock:
                self.queue.append([at_sample, name, audio, 0])
                self.last_play_end = max(self.last_play_end, (at_sample + len(audio)) / SR)
                self.events.append(PlayEvent(name, audio, at_sample / SR))

        def _notify(self, kind: str, t: float) -> None:
            cb = self.script.on_event
            if cb:
                try:
                    cb(kind, t)
                except Exception:  # noqa: BLE001
                    pass

        def _bot_start(self, t: float) -> None:
            self.bot_starts.append(t)
            self._notify("bot_start", t)
            s = self.script
            if s.overlap is not None and not self.overlap_fired and len(self.bot_starts) == 1:
                self.overlap_fired = True
                at = int((t + s.overlap_after_bot_start) * SR)
                at = max(at, self.pos + self.rig.chunk)
                self.schedule("overlap", s.overlap, at)
                self._notify("overlap_scheduled", at / SR)

        def _bot_end(self, t: float) -> None:
            self.bot_ends.append(t)
            self._notify("bot_end", t)

        def next_out(self, n: int) -> np.ndarray:
            """Mix of everything scheduled for the next n samples. Advances the clock by n."""
            out = np.zeros(n, dtype=np.float32)
            start = self.pos
            with self.lock:
                for item in self.queue:
                    at, name, audio, off = item
                    a0 = max(at, start)
                    a1 = min(at + len(audio), start + n)
                    if a1 > a0:
                        out[a0 - start:a1 - start] += audio[a0 - at:a1 - at]
                self.queue = [q for q in self.queue if q[0] + len(q[2]) > start + n]
            self.played_chunks.append(out.copy())
            return out

        def feed_in(self, audio: np.ndarray) -> None:
            self.heard_chunks.append(audio.astype(np.float32))
            self.vad.feed(audio)
            self.pos += len(audio)
            self._check_done()

        def _check_done(self) -> None:
            s, t = self.script, self.t()
            if t >= s.max_duration:
                self.done.set()
                return
            if s.overlap is not None and not self.overlap_fired:
                if s.overlap_fallback_t is not None and t >= s.overlap_fallback_t:
                    self.overlap_fired = True
                    self.schedule("overlap", s.overlap, self.pos + self.rig.chunk)
                    self._notify("overlap_fallback", t)
                    return
                if s.overlap_fallback_t is None and t >= self.last_play_end + s.record_after:
                    self.done.set()  # bot never spoke; give up
                return
            if t >= self.last_play_end + s.record_after:
                if s.end_on_silence is None or not self.vad.speaking:
                    self.done.set()
                    return
            # early end only once she has spoken AFTER the last thing we played and then gone quiet
            if s.end_on_silence is not None and self.bot_ends and not self.vad.speaking:
                if self.bot_ends[-1] > self.last_play_end and t - self.bot_ends[-1] >= s.end_on_silence:
                    self.done.set()

        def finish(self) -> Recording:
            played = np.concatenate(self.played_chunks) if self.played_chunks else np.zeros(0, np.float32)
            heard = np.concatenate(self.heard_chunks) if self.heard_chunks else np.zeros(0, np.float32)
            n = min(len(played), len(heard))
            return Recording(played=played[:n], heard=heard[:n], events=self.events, bot_starts=self.bot_starts,
                             bot_ends=self.bot_ends, duration=n / SR)
