"""Template: how fd-benchmark starts YOUR assistant in benchmark mode.

Copy this file to fd-benchmark/assistant_entry.py (that copy is yours; git ignores it) and fill in the three parts.
Then check it with:   PROJECT_PYTHON fd-benchmark/launch_assistant.py --check
Your project code stays as it is, unless it has no way to accept a Pipecat transport (see step 4B of docs/GUIDE.md).

The launcher has already: put the project folder on sys.path, made it the working directory, and created
`transport` (a Pipecat transport that carries 16 kHz mono audio to/from fdbench, and tells fdbench when the bot
is interrupted). Use it exactly where your code would use LocalAudioTransport / DailyTransport / etc.
"""
from __future__ import annotations

from pathlib import Path

# 1. A file that identifies your project folder (relative to it). Used to find the project automatically.
PROJECT_MARKER = "main.py"                       # e.g. "assistant/main.py", "bot.py", "src/app.py"


# 2. Your assistant's audio sample rates (input must be 16000 for fdbench; output may be anything, e.g. 24000).
def audio_rates() -> tuple[int, int]:
    return 16000, 16000


# 3. Build your pipeline with `transport` and return (a coroutine that runs it, an async function that stops it).
async def build(transport, log_dir: Path):
    # --- example for a plain Pipecat app; replace with your own construction ---------------------------------
    # from my_bot import make_pipeline                 # your module
    # from pipecat.pipeline.runner import WorkerRunner  # Pipecat >= 1.3 (older: PipelineRunner/PipelineTask)
    # from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    #
    # pipeline = make_pipeline(transport)               # transport.input() first, transport.output() near the end
    # worker = PipelineWorker(pipeline, params=PipelineParams(audio_in_sample_rate=16000,
    #                                                         audio_out_sample_rate=audio_rates()[1]))
    # runner = WorkerRunner(handle_sigint=False)
    #
    # async def run():
    #     await runner.add_workers(worker)
    #     await runner.run()
    #
    # async def cancel():
    #     await worker.cancel()
    #
    # return run(), cancel
    raise NotImplementedError("fill in build() in fd-benchmark/assistant_entry.py (docs/GUIDE.md, step 4B)")
