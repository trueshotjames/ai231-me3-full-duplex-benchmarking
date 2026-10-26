#!/usr/bin/env python3
"""Run the voice assistant in benchmark mode: audio to/from fdbench over a socket, mic and speaker closed.

This is the only file that touches the assistant, and it lives in fd-benchmark, not in the project. It imports the
project's Assistant and plugs the benchmark transport into its existing `build(transport=...)` hook. The project's
own code is not changed and runs normally when started the usual way.

    python launch_assistant.py                              # connect to fdbench on this machine
    python launch_assistant.py --url ws://192.168.1.10:8791 # fdbench runs on another machine (laptop), this is the Pi
    python launch_assistant.py --echo --url ws://...        # link test only: echoes audio back, no assistant needed

By default it keeps running: when fdbench ends a run and closes the socket, the assistant shuts down cleanly and
the launcher waits for the next run, then starts her fresh. Ctrl+C stops it. `--once` exits after one run.

Run it with the PROJECT's Python (its venv has Pipecat), from anywhere:
    Windows:  "ME3\\.venv\\Scripts\\python" "ME3\\fd-benchmark\\launch_assistant.py"
    Pi:       ~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --url ws://LAPTOP:8791

Adapting to another assistant: change `build_assistant()` below (about ten lines).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "adapters"))

DEFAULT_URL = os.environ.get("FDBENCH_URL", "ws://127.0.0.1:8791")


def find_project(explicit: str | None) -> Path:
    """The assistant's project folder: --project, FDBENCH_PROJECT, or the folder that contains fd-benchmark."""
    for cand in (explicit, os.environ.get("FDBENCH_PROJECT"), HERE.parent):
        if cand and (Path(cand) / "assistant" / "main.py").exists():
            return Path(cand).resolve()
    sys.exit("assistant/main.py not found. Pass --project PATH (the folder that holds assistant/).")


# ----------------------------------------------------------------------------- the project-specific part
async def build_assistant(project: Path, transport, log_dir: Path):
    """Build the course assistant with the benchmark transport. Returns (app, run_coroutine, cancel_coroutine).

    For another Pipecat assistant: import your app, pass `transport` where it builds its pipeline, and return
    something that runs it and something that cancels it.
    """
    from assistant.config import load
    from assistant.main import Assistant, setup_logging

    cfg = load()
    setup_logging(cfg.log_level)
    problems = cfg.validate()
    for p in problems:
        print(f"config problem: {p}", file=sys.stderr)
    if problems:
        sys.exit("fix the assistant's .env and try again")
    log_dir.mkdir(parents=True, exist_ok=True)
    cfg.turns_log = log_dir / "turns.jsonl"          # keep benchmark conversations out of her normal logs
    cfg.latency_log = log_dir / "latency.jsonl"
    app = Assistant(cfg)
    worker = await app.build(transport=transport)

    async def cancel():
        await worker.cancel()

    return app, app.run(), cancel


# ----------------------------------------------------------------------------- link test (no assistant)
async def build_echo(transport):
    from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import WorkerRunner
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor

    class Echo(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                await self.push_frame(OutputAudioRawFrame(frame.audio, frame.sample_rate, 1))
            else:
                await self.push_frame(frame, direction)

    worker = PipelineWorker(Pipeline([transport.input(), Echo(), transport.output()]),
                            params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000))
    runner = WorkerRunner(handle_sigint=not sys.platform.startswith("win"))

    async def run():
        await runner.add_workers(worker)
        await runner.run()

    async def cancel():
        await worker.cancel()

    return None, run(), cancel


# ----------------------------------------------------------------------------- connection handling
def wait_for_server(url: str, stop_after: float | None) -> bool:
    """Block until fdbench is listening (TCP connect succeeds). Prints one line while waiting."""
    u = urlparse(url)
    host, port = u.hostname or "127.0.0.1", u.port or 8791
    t0, said = time.monotonic(), False
    while True:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            pass
        if stop_after is not None and time.monotonic() - t0 > stop_after:
            return False
        if not said:
            print(f"waiting for fdbench at {url} … (start `python -m fdbench` on that machine; Ctrl+C to quit)")
            said = True
        time.sleep(1.0)


async def run_once(url: str, project: Path | None, log_dir: Path, echo: bool) -> None:
    from pipecat_fdbench import make_benchmark_transport

    out_sr = 16000
    if not echo:
        from assistant.config import load
        cfg = load()
        out_sr = cfg.audio_out_sample_rate
        if cfg.audio_in_sample_rate != 16000:
            sys.exit("set AUDIO_IN_SAMPLE_RATE=16000 in the assistant's .env for benchmark mode")
    transport = make_benchmark_transport(url, in_sr=16000, out_sr=out_sr)
    app, run_coro, cancel = (await build_echo(transport)) if echo else (await build_assistant(project, transport, log_dir))
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    @transport.event_handler("on_connected")
    async def on_connected(t, ws):
        print(f"connected to fdbench at {url}" + (" (echo test)" if echo else " · benchmark mode"))

    @transport.event_handler("on_disconnected")
    async def on_disconnected(t, ws):
        if not done.done():
            done.set_result(True)
            print("fdbench closed the connection: run over, shutting the assistant down")
            await cancel()

    await run_coro


def main() -> None:
    ap = argparse.ArgumentParser(description="run the assistant in benchmark mode (fd-benchmark)")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"fdbench address (default {DEFAULT_URL}, or $FDBENCH_URL)")
    ap.add_argument("--project", default=None, help="folder that holds assistant/ (default: the parent of fd-benchmark)")
    ap.add_argument("--log-dir", default=None, help="where her turn/latency logs go (default fd-benchmark/runs/_assistant)")
    ap.add_argument("--once", action="store_true", help="exit after one run instead of waiting for the next")
    ap.add_argument("--echo", action="store_true", help="link test: echo audio back, do not start the assistant")
    ap.add_argument("--wait", type=float, default=None, help="give up if fdbench is not reachable after this many seconds")
    args = ap.parse_args()

    project = None
    if not args.echo:
        project = find_project(args.project)
        os.chdir(project)
        sys.path.insert(0, str(project))
    log_dir = Path(args.log_dir) if args.log_dir else HERE / "runs" / "_assistant"
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    runs = 0
    try:
        while True:
            if not wait_for_server(args.url, args.wait):
                sys.exit(f"fdbench not reachable at {args.url}")
            runs += 1
            print(f"--- run {runs}: starting " + ("echo" if args.echo else "the assistant"))
            asyncio.run(run_once(args.url, project, log_dir, args.echo))
            if args.once:
                return
            print("--- waiting for the next run (Ctrl+C to quit)")
            time.sleep(2.0)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
