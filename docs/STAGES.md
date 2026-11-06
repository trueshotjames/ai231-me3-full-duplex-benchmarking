# Stage latency: how your assistant reports its stages

The course template (section 5) asks for latency per stage, measured on one clock on the Raspberry Pi:

```
VAD end → ASR final → LLM 1st token → TTS 1st audio → speaker
E2E = speaker − VAD end      barge-in = stop − speech start
```

fdbench only sees audio going in and out, so it can measure E2E and barge-in by itself but not the stages in between.
Those come from your assistant as **stage marks**: small messages that say "this stage happened now", stamped with the
Pi's `time.monotonic()`. Pick the lowest level that fits your project.

| Level | Your project | What to do | What you get |
|---|---|---|---|
| 0 | anything | nothing | every Full-Duplex-Bench metric, E2E (from audio), barge-in, results card. Stage rows say "not reported". |
| 1 | Pipecat, started by `launch_assistant.py` | nothing: the launcher attaches `FdbenchObserver` to every pipeline | all stages, WER, context size, tool calls |
| 2 | any harness (onit, crewAI, own code), or extra stages in an MCP server | call `mark()` at each stage | whatever you mark |

`PROJ_PY launch_assistant.py --check` prints a `marks:` line saying which applies.

## Level 1: Pipecat

Nothing to change. `launch_assistant.py` patches `PipelineWorker` / `PipelineTask` so every pipeline your code
builds gets one `pipecat_marks.FdbenchObserver`. It reads standard frames only, so custom STT/TTS services that call
your own MCP servers work too:

| Stage | Pipecat frame |
|---|---|
| `vad_end` | `VADUserStoppedSpeakingFrame` (fallback: `UserStoppedSpeakingFrame`) |
| `asr_final` | `TranscriptionFrame` (its text is used for WER) |
| `llm_request` | `LLMContextFrame` (messages and characters in the context; `speculative=True` for an early call on a partial transcript) |
| `llm_cancel` | `EagerEndOfTurnCancelFrame` (that early call was discarded) |
| `llm_first_token` | first `LLMTextFrame` after `LLMFullResponseStartFrame` |
| `llm_usage` | `MetricsFrame` with token usage (set `enable_usage_metrics=True` in `PipelineParams` for exact token counts) |
| `tts_request` / `tts_first_audio` | `TTSStartedFrame` / first `TTSAudioRawFrame` after it |
| `audio_out` | `BotStartedSpeakingFrame` (the transport started playing) |
| `interrupted` | `InterruptionFrame` |
| `tool_start` / `tool_end` | `FunctionCallInProgressFrame` / `FunctionCallResultFrame` |

If a custom service skips one of these frames (for example a TTS service that never pushes `TTSStartedFrame`), add a
`mark()` call for that stage (level 2); marks from both sources are combined. `--no-marks` on the launcher turns all of this off.

Not started through `launch_assistant.py` (virtual-cable runs)? Add the observer yourself and set `FDBENCH_MARKS`:

```python
import sys; sys.path.insert(0, "fd-benchmark/adapters")
from pipecat_marks import FdbenchObserver
worker = PipelineWorker(pipeline, params=..., observers=[FdbenchObserver()])
```

## Level 2: `mark()` from any code

Copy `adapters/fdbench_marks.py` next to your code (standard library only, no dependencies) and call it:

```python
from fdbench_marks import mark

mark("vad_end")                          # your VAD decided the user finished
mark("asr_final", text=transcript)       # final transcript ready
mark("llm_request", prompt_tokens=n)     # LLM call starts; or messages=len(history)
mark("llm_request", speculative=True, prompt_tokens=n)  # an early call before the turn has ended ...
mark("llm_cancel")                       # ... that you then discarded (a confirmed one needs no extra mark)
mark("llm_first_token")
mark("tts_request")
mark("tts_first_audio")
mark("audio_out")                        # first reply audio handed to the speaker
mark("tool_start", name="get_weather"); mark("tool_end", name="get_weather")
```

`mark()` never raises and does nothing when fdbench is not running, so the calls can stay in your code. Inside
`launch_assistant.py` marks travel over the benchmark socket. From any other process they go by UDP to
`FDBENCH_MARKS=udp://FDBENCH-HOST:8792`; the launcher sets this for the processes it starts (your MCP servers inherit
it), and fdbench listens on UDP 8792 (`--marks-port`). With the assistant on a Pi, allow UDP 8792 in the laptop's
firewall too (`scripts/open_firewall_windows.ps1` does both ports) if marks come from MCP servers.

Stage names: `vad_start`, `vad_end`, `user_stopped`, `asr_partial`, `asr_final`, `llm_request`, `llm_cancel`,
`llm_first_token`, `llm_usage`, `tts_request`, `tts_first_audio`, `audio_out`, `interrupted`, `bot_stopped`, `tool_start`, `tool_end`
(`fdbench_marks.STAGES` describes each).

## Model versions for the results card

Add an optional `describe()` to your `assistant_entry.py`; the launcher sends it to fdbench:

```python
def describe() -> dict:
    return {"ASR": "Deepgram nova-3", "LLM": "claude-haiku-5-5", "TTS": "Cartesia sonic-2", "VAD": "Silero v5"}
```

Or pass them on the laptop: `--models "ASR=Deepgram nova-3,LLM=claude-haiku-5-5,TTS=Cartesia sonic-2"`.

## Checking

After a run, `results.html` → "Stage latency" → "Stage marks received" lists every stage with its count, and each
trial folder has `stages.jsonl` with the raw marks. A stage missing there shows as "not reported" in the summary.
Quick check without hardware: `python -m fdbench run --sim --datasets datasets-dev --student test --rounds 0 --no-asr`
(the simulated assistant sends every mark).
