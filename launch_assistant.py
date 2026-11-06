#!/usr/bin/env python3
"""Run the voice assistant in benchmark mode: audio to/from fdbench over a socket, mic and speaker closed.

This is the only file that touches the assistant, and it lives in fd-benchmark, not in the project. It imports the
project's Assistant and plugs the benchmark transport into its existing `build(transport=...)` hook. The project's
own code is not changed and runs normally when started the usual way.

    python launch_assistant.py                              # connect to fdbench on this machine
    python launch_assistant.py --url ws://192.168.1.10:8791 # fdbench runs on another machine (laptop), this is the Pi
    python launch_assistant.py --echo --url ws://...        # link test only: echoes audio back, no assistant needed

Put FDBENCH_URL=ws://LAPTOP-IP:8791 (and FDBENCH_PROJECT if fd-benchmark is not inside the project) in
fd-benchmark/.env on the Pi, and the command is just `python launch_assistant.py`.

While connected it also sends her machine's CPU (per core and per process: assistant, ASR/TTS/tools MCP servers),
memory, temperature, ARM clock and throttle flags once a second over the same socket (fdbench/probe.py, standard
library only). `--no-stats` turns that off; `--stats-procs` names MCP servers that run as separate services.

It also attaches pipecat_marks.FdbenchObserver to her pipeline, so fdbench gets the time of every stage of a turn
(VAD end, ASR final, LLM first token, TTS first audio, audio out) on this machine's clock, plus transcripts for WER and
context size (docs/STAGES.md). Her processes get FDBENCH_MARKS so MCP servers can send marks too. `--no-marks` turns
that off.

By default it keeps running: when fdbench ends a run and closes the socket, the assistant shuts down cleanly and
the launcher waits for the next run, then starts her fresh. Ctrl+C stops it. `--once` exits after one run.

Run it with the PROJECT's Python (its venv has Pipecat), from anywhere:
    Windows:  "ME3\\.venv\\Scripts\\python" "ME3\\fd-benchmark\\launch_assistant.py"
    Pi:       ~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --url ws://LAPTOP:8791

Which assistant it starts: fd-benchmark/assistant_entry.py if you made one (from adapters/assistant_entry_template.py),
otherwise the course layout in adapters/entry_course.py. `--check` shows what it found.
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


def _load_env(path: Path) -> None:
    """fd-benchmark/.env: FDBENCH_URL, FDBENCH_PROJECT. Does not override variables already set."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.split("#")[0].strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_env(HERE / ".env")
DEFAULT_URL = os.environ.get("FDBENCH_URL") or "ws://127.0.0.1:8791"


def load_entry():
    """The assistant entry: $FDBENCH_ENTRY, else fd-benchmark/assistant_entry.py (yours), else the course layout."""
    import importlib.util
    for cand in (os.environ.get("FDBENCH_ENTRY"), HERE / "assistant_entry.py", HERE / "adapters" / "entry_course.py"):
        if cand and Path(cand).exists():
            spec = importlib.util.spec_from_file_location("fdbench_assistant_entry", str(cand))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for name in ("build", "audio_rates"):
                if not hasattr(mod, name):
                    sys.exit(f"{cand} has no {name}(); see adapters/assistant_entry_template.py")
            mod.__file__ = str(cand)
            return mod
    sys.exit("no assistant entry found (adapters/entry_course.py is missing: re-clone fd-benchmark)")


def find_project(explicit: str | None, marker: str) -> Path:
    """The assistant's project folder: --project, FDBENCH_PROJECT, or the folder that contains fd-benchmark."""
    for cand in (explicit, os.environ.get("FDBENCH_PROJECT"), HERE.parent):
        if cand and (Path(cand) / marker).exists():
            return Path(cand).resolve()
    sys.exit(f"project not found: no {marker} in --project, FDBENCH_PROJECT or {HERE.parent}. "
             "Clone fd-benchmark inside your project folder, or set FDBENCH_PROJECT in fd-benchmark/.env.")


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


async def stats_loop(ws, probe, hz: float) -> None:
    """Send a hello, then one stats sample every 1/hz s, as text frames on the benchmark socket."""
    import json
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fdbench-stats")
    period = 1.0 / max(0.1, hz)
    try:
        hello = probe.hello()
        try:  # which interface carries the benchmark link (the default route is the path to the cloud APIs)
            hello["link_network"] = probe.network_to(ws.remote_address[0])
        except Exception:  # noqa: BLE001
            pass
        await ws.send(json.dumps({"event": "stats_hello", "hello": hello}))
        nxt = loop.time()
        while True:
            sample = await loop.run_in_executor(pool, probe.sample)  # file reads off the event loop
            await ws.send(json.dumps({"event": "stats", "s": sample}))
            nxt += period
            await asyncio.sleep(max(0.0, nxt - loop.time()))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001  (socket closed, or the probe failed: stats never stop the assistant)
        print(f"stats: stopped ({type(e).__name__}: {e})")
    finally:
        pool.shutdown(wait=False)


def describe_assistant(entry) -> dict:
    """Model versions for the results card: entry.describe() if the entry has one, plus the Pipecat version."""
    out: dict = {}
    try:
        import pipecat
        out["harness"] = f"pipecat {getattr(pipecat, '__version__', '?')}"
    except Exception:  # noqa: BLE001
        pass
    fn = getattr(entry, "describe", None) if entry is not None else None
    if callable(fn):
        try:
            out.update({str(k): str(v) for k, v in (fn() or {}).items() if v})
        except Exception as e:  # noqa: BLE001
            print(f"describe(): failed ({e})")
    return out


async def marks_loop(ws, queue: "asyncio.Queue") -> None:
    """Send stage marks (fdbench_marks.mark) as text frames on the benchmark socket."""
    import json
    try:
        while True:
            d = await queue.get()
            await ws.send(json.dumps(d, default=str))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001  (socket closed: marks never stop the assistant)
        print(f"marks: stopped ({type(e).__name__}: {e})")


async def run_once(url: str, entry, log_dir: Path, echo: bool, stats_hz: float = 1.0,
                   stats_procs: str | None = None, marks: bool = True) -> None:
    from pipecat_fdbench import make_benchmark_transport
    import fdbench_marks

    loop = asyncio.get_running_loop()
    marks_q: asyncio.Queue = asyncio.Queue(maxsize=10000)
    marks_task = None
    if marks:
        from pipecat_marks import install_auto_observer
        install_auto_observer()

        def sink(d: dict) -> None:   # may be called from any thread
            def put():
                if marks_task is not None and not marks_q.full():
                    marks_q.put_nowait(d)
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:  # loop closed
                pass
        fdbench_marks.set_sink(sink)

    out_sr = 16000
    if not echo:
        in_sr, out_sr = entry.audio_rates()
        if in_sr != 16000:
            sys.exit("the assistant's input sample rate must be 16000 in benchmark mode (course: AUDIO_IN_SAMPLE_RATE=16000)")
    transport = make_benchmark_transport(url, in_sr=16000, out_sr=out_sr)
    if echo:
        _, run_coro, cancel = await build_echo(transport)
    else:
        run_coro, cancel = await entry.build(transport, log_dir)
    done = loop.create_future()
    stats_task = None

    @transport.event_handler("on_connected")
    async def on_connected(t, ws):
        nonlocal stats_task, marks_task
        print(f"connected to fdbench at {url}" + (" (echo test)" if echo else " · benchmark mode"))
        if marks and marks_task is None:
            import json
            try:
                await ws.send(json.dumps({"event": "describe", "models": describe_assistant(entry)}))
            except Exception:  # noqa: BLE001
                pass
            marks_task = asyncio.create_task(marks_loop(ws, marks_q))
        if stats_hz > 0 and stats_task is None:
            try:
                from fdbench.probe import Probe, parse_patterns
                probe = Probe(root_pid=os.getpid(), patterns=parse_patterns(stats_procs))
                stats_task = asyncio.create_task(stats_loop(ws, probe, stats_hz))
            except Exception as e:  # noqa: BLE001
                print(f"stats: not sent ({e})")

    @transport.event_handler("on_disconnected")
    async def on_disconnected(t, ws):
        if stats_task is not None:
            stats_task.cancel()
        if marks_task is not None:
            marks_task.cancel()
        if not done.done():
            done.set_result(True)
            print("fdbench closed the connection: run over, shutting the assistant down")
            await cancel()

    await run_coro


def check(entry, project, url) -> int:
    """Print what the launcher would use, and whether it is usable. Exit code 0 = ready."""
    ok = True
    print(f"python:   {sys.executable} ({sys.version.split()[0]})")
    try:
        import pipecat
        import websockets
        from pipecat_fdbench import make_benchmark_transport
        print(f"pipecat:  {getattr(pipecat, '__version__', '?')} · websockets {websockets.__version__}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"pipecat:  NOT USABLE ({e}). Run the launcher with the assistant's own venv python; "
              "it needs pipecat-ai[websocket].")
    if entry is not None:
        print(f"entry:    {entry.__file__}")
        print(f"project:  {project}")
        try:
            in_sr, out_sr = entry.audio_rates()
            print(f"audio:    in {in_sr} Hz, out {out_sr} Hz")
            if in_sr != 16000:
                ok = False
                print("          input must be 16000 Hz in benchmark mode")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"audio:    audio_rates() failed: {e}")
    try:
        from fdbench.probe import Probe
        caps = Probe(root_pid=os.getpid()).capabilities()
        have = [k for k in ("cpu", "memory", "processes", "temperature", "clock", "throttle") if caps.get(k)]
        print(f"stats:    {', '.join(have) if have else 'none on this machine'}"
              + (f" ({caps['backend']})" if caps.get("backend") else " (install psutil for CPU and RAM)"))
    except Exception as e:  # noqa: BLE001
        print(f"stats:    unavailable ({e})")
    try:
        from pipecat_marks import install_auto_observer
        patched = install_auto_observer()
        print(f"marks:    Pipecat observer attached to {', '.join(patched)} (stage latency, WER, context size)" if patched
              else "marks:    no Pipecat pipeline class found; call fdbench_marks.mark() yourself (docs/STAGES.md)")
    except Exception as e:  # noqa: BLE001
        print(f"marks:    unavailable ({e}); stage rows will show 'not reported'")
    if entry is not None:
        d = describe_assistant(entry)
        print(f"models:   {', '.join(f'{k} {v}' for k, v in d.items()) if d else 'not described (optional describe() in the entry file)'}")
    print(f"fdbench:  {url}")
    print("READY" if ok else "NOT READY: fix the lines above (docs/GUIDE.md, step 4)")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="run the assistant in benchmark mode (fd-benchmark)")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"fdbench address (default {DEFAULT_URL}, or $FDBENCH_URL)")
    ap.add_argument("--project", default=None, help="folder that holds assistant/ (default: the parent of fd-benchmark)")
    ap.add_argument("--log-dir", default=None, help="where her turn/latency logs go (default fd-benchmark/runs/_assistant)")
    ap.add_argument("--once", action="store_true", help="exit after one run instead of waiting for the next")
    ap.add_argument("--echo", action="store_true", help="link test: echo audio back, do not start the assistant")
    ap.add_argument("--wait", type=float, default=None, help="give up if fdbench is not reachable after this many seconds")
    ap.add_argument("--check", action="store_true", help="check the setup (entry, project, Pipecat, audio rates) and exit")
    ap.add_argument("--stats-hz", type=float, default=1.0, help="machine stats samples per second sent to fdbench (default 1)")
    ap.add_argument("--no-stats", action="store_true", help="do not send machine stats")
    ap.add_argument("--no-marks", action="store_true", help="do not send stage marks (stage latency, WER, context size)")
    ap.add_argument("--marks-port", type=int, default=8792,
                    help="UDP port on the fdbench machine for marks from her other processes (MCP servers); default 8792")
    ap.add_argument("--stats-procs", default=None,
                    help="label=command-line fragment pairs for her processes, comma separated "
                         "(default asr_mcp=mcp_servers.asr,tts_mcp=mcp_servers.tts,tools_mcp=mcp_servers.tools)")
    args = ap.parse_args()

    entry = project = None
    if not args.echo:
        entry = load_entry()
        project = find_project(args.project, getattr(entry, "PROJECT_MARKER", "."))
        os.chdir(project)
        sys.path.insert(0, str(project))
    if args.check:
        sys.exit(check(entry, project, args.url))
    log_dir = Path(args.log_dir) if args.log_dir else HERE / "runs" / "_assistant"
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    if not args.no_marks and not os.environ.get("FDBENCH_MARKS"):
        # her child processes (MCP servers) inherit this and can call fdbench_marks.mark() themselves
        os.environ["FDBENCH_MARKS"] = f"udp://{urlparse(args.url).hostname or '127.0.0.1'}:{args.marks_port}"
    runs = 0
    try:
        while True:
            if not wait_for_server(args.url, args.wait):
                sys.exit(f"fdbench not reachable at {args.url}")
            runs += 1
            print(f"--- run {runs}: starting " + ("echo" if args.echo else "the assistant"))
            asyncio.run(run_once(args.url, entry, log_dir, args.echo, stats_hz=(0.0 if args.no_stats else args.stats_hz),
                                 stats_procs=args.stats_procs, marks=not args.no_marks))
            if args.once:
                return
            print("--- waiting for the next run (Ctrl+C to quit)")
            time.sleep(2.0)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
