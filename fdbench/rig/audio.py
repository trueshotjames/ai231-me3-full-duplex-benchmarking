"""Real rig: one duplex sounddevice stream. Output goes to the assistant's virtual mic, input comes
from the assistant's virtual speaker. The stream callback is the single sample clock.
"""
from __future__ import annotations

import queue
import threading
from typing import Optional

import numpy as np

from .. import SR
from ..util import resample
from .base import RigBase, Recording, TrialScript


class AudioRig(RigBase):
    name = "audio"

    def __init__(self, out_device, in_device, device_sr: Optional[int] = None, chunk_ms: int = 32,
                 out_gain: float = 1.0):
        super().__init__(chunk_ms)
        import sounddevice as sd

        self.sd = sd
        self.out_device = out_device
        self.in_device = in_device
        if device_sr is None:
            device_sr = int(sd.query_devices(out_device)["default_samplerate"])
        self.device_sr = int(device_sr)
        self.out_gain = out_gain
        self.block = int(self.device_sr * chunk_ms / 1000)

    def run(self, script: TrialScript) -> Recording:
        sess = self._Session(self, script)
        in_q: "queue.Queue[np.ndarray]" = queue.Queue()
        err: list = []
        up = self.device_sr != SR

        # Pre-generate output in device rate per callback block. We keep a small ring of 16 kHz output
        # so resampling happens in the callback at block granularity.
        def callback(indata, outdata, frames, time_info, status):
            try:
                if status and "overflow" not in str(status).lower():
                    err.append(str(status))
                n16 = int(round(frames * SR / self.device_sr))
                out16 = sess.next_out(n16)
                out = resample(out16, SR, self.device_sr) if up else out16
                if len(out) < frames:
                    out = np.pad(out, (0, frames - len(out)))
                outdata[:, 0] = out[:frames] * self.out_gain
                if outdata.shape[1] > 1:
                    outdata[:, 1:] = outdata[:, :1]
                mono = indata[:, 0].astype(np.float32).copy()
                in_q.put(mono)
            except Exception as e:  # noqa: BLE001
                err.append(repr(e))
                raise self.sd.CallbackAbort from e

        stream = self.sd.Stream(samplerate=self.device_sr, blocksize=self.block, dtype="float32",
                                channels=(1, 1), device=(self.in_device, self.out_device), callback=callback)

        def consumer():
            while not sess.done.is_set():
                try:
                    block = in_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                mono16 = resample(block, self.device_sr, SR) if up else block
                sess.feed_in(mono16)

        th = threading.Thread(target=consumer, daemon=True)
        with stream:
            th.start()
            sess.done.wait(timeout=script.max_duration + 5)
        th.join(timeout=2)
        rec = sess.finish()
        rec.notes["rig"] = "audio"
        rec.notes["device_sr"] = self.device_sr
        if err:
            rec.notes["stream_errors"] = err[:5]
        return rec

    def play_silence(self, seconds: float) -> None:
        """Keep the output open with silence between trials (lets the assistant settle)."""
        import time
        time.sleep(seconds)
