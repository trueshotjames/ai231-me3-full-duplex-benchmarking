"""Assistant entry for the AI 231 ME3 course layout (assistant/config.py + assistant/main.py with
`Assistant.build(transport=...)`). Used by launch_assistant.py when there is no fd-benchmark/assistant_entry.py.

To benchmark a different assistant, copy adapters/assistant_entry_template.py to fd-benchmark/assistant_entry.py
and fill it in (docs/GUIDE.md, step 4B).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_MARKER = "assistant/main.py"   # a file that exists in the project folder


def audio_rates() -> tuple[int, int]:
    from assistant.config import load
    cfg = load()
    return cfg.audio_in_sample_rate, cfg.audio_out_sample_rate


async def build(transport, log_dir: Path):
    """Build the assistant with `transport` instead of mic/speaker. Return (run_coroutine, async_cancel_function)."""
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

    return app.run(), cancel
