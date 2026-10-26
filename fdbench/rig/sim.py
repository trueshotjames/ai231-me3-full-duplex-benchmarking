"""Simulated rig: a fake assistant in-process, for self-tests and for developing without audio devices.

The fake assistant hears what the rig plays, waits for the end of user speech (its own VAD), thinks for
`response_delay`, then speaks a snippet of real speech (so the Silero VAD on the rig side sees it as speech).
When the user talks over it, it stops after `stop_delay` (or never, if None) and comes back after
`resume_delay` with another snippet. Behaviour knobs let tests exercise each verdict.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .. import SR
from ..util import load_audio
from .base import RigBase, Recording, TrialScript
from .vad import LiveVAD


@dataclass
class FakeBehaviour:
    response_delay: float = 0.6       # s after user speech end before the bot speaks
    reply_seconds: float = 6.0        # how long the bot talks
    stop_delay: Optional[float] = 0.25  # s after overlap speech starts until the bot stops (None = never stops)
    resume_delay: float = 0.9         # s after overlap ends until the bot speaks again
    responds_to_foreign: float = 0.0  # probability of answering audio that is not addressed to it
    cut_in_on_pause: float = 0.0      # probability of answering during a mid-sentence pause
    min_user_speech: float = 0.3      # ignore shorter user speech (backchannel filter)
    reply_to_pause_prob: float = 1.0  # unused placeholder for symmetry
    end_silence_ms: int = 800         # silence the bot needs before it treats the user's turn as finished
    realtime: bool = False            # sleep to simulate real time (default: as fast as possible)
    seed: int = 0


class FakeAssistant:
    """Reacts to a 16 kHz stream in `chunk`-sized steps and produces its own 16 kHz output."""

    def __init__(self, voices: list[np.ndarray], behaviour: FakeBehaviour, chunk: int, ignore_user: bool = False):
        self.voices = voices
        self.b = behaviour
        self.chunk = chunk
        self.ignore_user = ignore_user  # "this audio is not for me": a perfect addressee detector
        self.rng = random.Random(behaviour.seed)
        self.vad = LiveVAD(min_silence_ms=behaviour.end_silence_ms, on_start=self._user_start, on_end=self._user_end)
        self.pos = 0
        self.speaking_until = None
        self.speech_buf = np.zeros(0, np.float32)
        self.pending_reply_at = None
        self.user_start_t = None
        self.has_replied_once = False
        self.interrupted = False

    def _user_start(self, t):
        self.user_start_t = t
        if self.speaking_until is not None and self.b.stop_delay is not None:
            # user talks over us: schedule a stop
            self.stop_at = t + self.b.stop_delay
        else:
            self.stop_at = None

    def _user_end(self, t):
        dur = t - (self.user_start_t or t)
        if dur < self.b.min_user_speech or self.ignore_user:
            return
        if self.interrupted:
            self.pending_reply_at = t + self.b.resume_delay
        else:
            self.pending_reply_at = t + self.b.response_delay

    def step(self, user_audio: np.ndarray) -> np.ndarray:
        self.vad.feed(user_audio)
        t = self.pos / SR
        n = len(user_audio)
        out = np.zeros(n, np.float32)
        # stop when interrupted
        if self.speaking_until is not None and getattr(self, "stop_at", None) is not None and t >= self.stop_at:
            self.speaking_until = None
            self.speech_buf = np.zeros(0, np.float32)
            self.stop_at = None
            self.interrupted = True
        # start a reply
        if self.pending_reply_at is not None and t >= self.pending_reply_at:
            self.pending_reply_at = None
            self._start_speaking()
        if self.speaking_until is not None:
            take = min(n, len(self.speech_buf))
            out[:take] = self.speech_buf[:take]
            self.speech_buf = self.speech_buf[take:]
            if len(self.speech_buf) == 0:
                self.speaking_until = None
        self.pos += n
        return out

    def _start_speaking(self):
        voice = self.rng.choice(self.voices)
        need = int(self.b.reply_seconds * SR)
        if len(voice) < need:
            reps = int(np.ceil(need / max(len(voice), 1)))
            voice = np.tile(voice, reps)
        start = self.rng.randrange(0, max(1, len(voice) - need))
        self.speech_buf = voice[start:start + need].copy()
        self.speaking_until = (self.pos + need) / SR
        self.has_replied_once = True


class SimRig(RigBase):
    """Runs a TrialScript against a FakeAssistant, as fast as possible."""

    name = "sim"

    def __init__(self, voices_dir: Path | None = None, behaviour: FakeBehaviour | None = None,
                 decide=None, chunk_ms: int = 32):
        super().__init__(chunk_ms)
        self.behaviour = behaviour or FakeBehaviour()
        self.decide = decide  # optional callable(script) -> FakeBehaviour override per trial
        voices_dir = voices_dir or (Path(__file__).resolve().parent.parent.parent / "tests" / "example_data")
        self.voices = [load_audio(p) for p in sorted(voices_dir.rglob("output.wav"))]
        if not self.voices:
            raise RuntimeError(f"No voice clips found under {voices_dir}")

    def run(self, script: TrialScript) -> Recording:
        beh = self.decide(script) if self.decide else self.behaviour
        rng = random.Random(hash((beh.seed, len(script.play_first))))
        ignore = script.tag == "foreign" and rng.random() >= beh.responds_to_foreign
        bot = FakeAssistant(self.voices, beh, self.chunk, ignore_user=ignore)
        if script.tag == "pause" and rng.random() < beh.cut_in_on_pause:
            bot.vad.min_silence = 0.3  # an eager bot treats a short pause as the end of the turn
        sess = self._Session(self, script)
        latency = np.zeros(self.chunk, np.float32)  # one chunk of transport delay, like a real cable
        while not sess.done.is_set():
            out = sess.next_out(self.chunk)
            heard = bot.step(out)
            sess.feed_in(latency)
            latency = heard
            if beh.realtime:
                import time
                time.sleep(self.chunk / SR)
        rec = sess.finish()
        rec.notes["rig"] = "sim"
        return rec
