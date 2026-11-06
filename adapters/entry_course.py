"""Assistant entry for the AI 231 ME3 course layout (assistant/config.py + assistant/main.py with
`Assistant.build(transport=...)`). Used by launch_assistant.py when there is no fd-benchmark/assistant_entry.py.

To benchmark a different assistant, copy adapters/assistant_entry_template.py to fd-benchmark/assistant_entry.py
and fill it in (docs/GUIDE.md, step 4B).

Benchmark mode: the test clips must never reach real tools. This entry sets BENCHMARK_MODE=1, and it refuses to start
an assistant that does not honour it, unless FDBENCH_ALLOW_TOOLS=1 is set in fd-benchmark/.env.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["BENCHMARK_MODE"] = "1"     # before assistant.config is imported

PROJECT_MARKER = "assistant/main.py"   # a file that exists in the project folder


def safety() -> str:
    """One line for `launch_assistant.py --check`: are her tools off?"""
    from assistant.config import load
    if getattr(load(), "benchmark_mode", False):
        return "tools, delegation, memory and audio devices off (BENCHMARK_MODE)"
    return "NOT SUPPORTED by this assistant: her real tools would run"


def audio_rates() -> tuple[int, int]:
    from assistant.config import load
    cfg = load()
    return cfg.audio_in_sample_rate, cfg.audio_out_sample_rate


async def build(transport, log_dir: Path):
    """Build the assistant with `transport` instead of mic/speaker. Return (run_coroutine, async_cancel_function)."""
    from assistant.config import load
    from assistant.main import Assistant, setup_logging

    cfg = load()
    if not getattr(cfg, "benchmark_mode", False) and os.environ.get("FDBENCH_ALLOW_TOOLS") != "1":
        sys.exit("this assistant has no BENCHMARK_MODE, so test clips could reach her real tools. Update it, or set "
                 "FDBENCH_ALLOW_TOOLS=1 in fd-benchmark/.env to run with tools on.")
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

    return app.run(), cancel
