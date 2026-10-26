"""Benchmark-mode transport for a Pipecat assistant: talk to fdbench directly, no mic or speaker.

Copy this file next to your assistant (or put fd-benchmark/adapters on PYTHONPATH) and, where you build
the transport, use it when FDBENCH_URL is set:

    import os
    if os.environ.get("FDBENCH_URL"):
        from pipecat_fdbench import make_benchmark_transport
        transport = make_benchmark_transport(os.environ["FDBENCH_URL"],
                                             in_sr=cfg.audio_in_sample_rate, out_sr=cfg.audio_out_sample_rate)
    else:
        transport = make_transport(cfg)          # your normal LocalAudioTransport

Then start fdbench first (it waits), and start the assistant with FDBENCH_URL=ws://127.0.0.1:8791.
Everything after the transport (VAD, STT, LLM, TTS, interruptions) runs exactly as in normal use.

Tested with Pipecat 1.13. Needs: pip install "pipecat-ai[websocket]"
"""
from __future__ import annotations

import json
from urllib.parse import urlencode, urlparse, urlunparse

from pipecat.frames.frames import Frame, InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.websocket.client import (WebsocketClientInputTransport, WebsocketClientOutputTransport,
                                                 WebsocketClientParams, WebsocketClientTransport)

FDBENCH_SR = 16000  # fdbench always sends 16 kHz mono


class FdbenchSerializer(FrameSerializer):
    """Raw PCM both ways; an interruption becomes {"event": "clear"}."""

    async def serialize(self, frame: Frame):
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, InterruptionFrame):
            return json.dumps({"event": "clear"})
        return None

    async def deserialize(self, data):
        if isinstance(data, (bytes, bytearray)):
            return InputAudioRawFrame(audio=bytes(data), sample_rate=FDBENCH_SR, num_channels=1)
        return None


class _Input(WebsocketClientInputTransport):
    async def on_message(self, websocket, message):
        # The client connects in setup(), before start() creates the audio queue, and fdbench streams at once.
        # Drop audio until the pipeline has started, or the error kills the receive loop.
        if getattr(self, "_audio_in_queue", None) is None:
            return
        await super().on_message(websocket, message)


class _Output(WebsocketClientOutputTransport):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            # tell fdbench to stop "playing" what was already sent (the client transport does not do this itself)
            await self._write_frame(frame)
            self._next_send_time = 0


class FdbenchTransport(WebsocketClientTransport):
    def input(self) -> WebsocketClientInputTransport:
        if not self._input:
            self._input = _Input(self, self._session, self._params)
        return self._input

    def output(self) -> WebsocketClientOutputTransport:
        if not self._output:
            self._output = _Output(self, self._session, self._params)
        return self._output


def make_benchmark_transport(url: str, in_sr: int = 16000, out_sr: int = 16000) -> FdbenchTransport:
    if in_sr != FDBENCH_SR:
        raise ValueError(f"set the assistant's input sample rate to {FDBENCH_SR} for benchmark mode (got {in_sr})")
    u = urlparse(url)
    uri = urlunparse(u._replace(query=urlencode({"out_sr": out_sr})))
    return FdbenchTransport(uri, WebsocketClientParams(
        audio_in_enabled=True, audio_out_enabled=True,
        audio_in_sample_rate=in_sr, audio_out_sample_rate=out_sr,
        audio_in_channels=1, audio_out_channels=1,
        add_wav_header=False, serializer=FdbenchSerializer(),
    ))
