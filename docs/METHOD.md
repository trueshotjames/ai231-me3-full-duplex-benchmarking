# Method

Sources: Lin et al., *Full-Duplex-Bench* (v1.0, arXiv 2503.04721, 2025); Lin et al., *Full-Duplex-Bench v1.5*
(arXiv 2507.23159, v4 2026); released code https://github.com/DanielLin94144/Full-Duplex-Bench (`v1_v1.5/evaluation`).

## Rig

The assistant is a black box with an audio input and an audio output. The tool plays a test clip into the
assistant's input and records the assistant's output on the same sample clock, 16 kHz mono internally: either over a
local socket (direct feed, the assistant in benchmark mode) or through two virtual audio cables. Two tracks per trial: `played.flac` (user side) and `her.flac` (assistant side).
Silero VAD (ONNX, the model the papers' timing code uses) runs live on the assistant track to time overlaps, and
offline on both tracks for scoring.

## Trials

| Group | What is played | Window scored |
|---|---|---|
| v1 pause (synthetic / Candor) | the clip, which contains a mid-sentence pause (`pause.json`) | the clip's duration (as the paper: output synchronous with input) |
| v1 turn-taking (Candor) | the clip up to 0.3 s after the annotated turn end | turn end → +8 s |
| v1 interruption | `context.wav`; 1.5 s after the assistant starts speaking, `interrupt.wav` | interruption end → +8 s |
| false turn-taking (ours) | audio not addressed to the assistant, while idle | clip start → clip end + 8 s |
| v1.5 four scenarios | the clean user turn; 1.5 s after the assistant starts, the overlap window cut from `input.wav`; then the same clip again **without** overlap (clean twin) | overlap onset → end of trial |

Between trials the assistant gets 6 s of silence (or `--reset-cmd`).

## Rules (from the released code)

- **Taking the turn** (`eval_pause_handling.py`, `eval_smooth_turn_taking.py`, `eval_user_interruption.py`):
  the assistant took the turn if its speech in the window lasts ≥ 1 s, or is shorter but has > 3 words.
  Word counts come from the transcript (faster-whisper, word timestamps). Without a transcript the duration rule
  alone is used and the verdict is marked provisional (`?`).
- **TOR** = share of trials where it took the turn. Pause handling: lower is better; turn-taking and interruption: higher.
- **Missed turn-taking rate** = 1 − TOR on the turn-taking clips (speech meant for her that she did not answer within 8 s).
- **False turn-taking rate** = share of not-addressed clips (side talk, TV/radio, background speech, noise) on which she
  took the turn, by the same rule. Reported overall and per kind. Mid-sentence cut-ins stay in pause handling.
- **Latency, turn-taking** = first assistant speech start − annotated turn end (negative clamped to 0, as the code).
- **Latency, interruption** = first assistant speech start after the interruption − interruption end.
- **Judge score, interruption** = 0–5 relevance of the reply to the interrupting turn, authors' prompt (`prompts/v1_interruption_rating.txt`),
  temperature 0; the user message lists context turn, interrupting turn and the transcript of the reply after the interruption.
- **v1.5 behaviour** (`eval_behavior.py`, `instruction/behavior.txt`): the judge sees word-level transcripts of
  `input_clean`, `input_noisy`, `output_clean`, `output_noisy` and returns C_RESPOND / C_RESUME / C_UNCERTAIN_HANDLING / C_UNKNOWN
  → Respond / Resume / Uncertain / Unknown. Trials where the assistant never answered the first turn (overlap not played) count as Unknown.
  The label is about **content**, not timing: pausing and then continuing the original answer is Resume, as is talking
  straight through; pausing and addressing the overlap is Respond. So the live table only shows timing
  (kept talking / paused, came back / stopped, silent) and marks pauses "content: judge". Without ASR + judge the results
  report those timing shares and leave the four labels empty; the one label timing can settle is Unknown (no speech
  after the overlap).
- **t_stop, t_resp** (`get_timing.py`): Silero segments, user segments merged with a 0.6 s gap and assistant segments with 0.5 s;
  t_stop = end of the assistant segment running at overlap onset − onset; t_resp = first assistant segment start after the overlap end − overlap end.
  Onset/end are the VAD-detected speech inside the played overlap window.
- **Voice change** (`eval_general_before_after.py`, Table 3): on v1.5 interruption clips labelled Respond (or all with voice data if none
  are judged), assistant speech before vs after the overlap onset: WPM from word timestamps, pitch mean/SD (Praat via parselmouth),
  intensity mean/SD (frame RMS dB), UTMOSv2 if installed; Δ = post − pre, paired t-test (`ttest_1samp` on Δ), significant if p < 0.05.
  Expected directions (paper): WPM ↑, pitch mean ↑, pitch SD ↑, intensity mean ↓, intensity SD ↑, UTMOSv2 no drop.

## Aggregation

Rates: k/n. Latencies: median, **p95** and mean (the papers report means; the course template asks for mean + p95);
with fewer than 20 trials p95 is flagged as close to the slowest trial.

## Queue

Trials are drawn without replacement per group from a seeded shuffle and arranged in rounds; each round holds one trial
of every group (two false turn-taking clips, rotating the four kinds). Stopping after any trial leaves every group
represented; the per-group counts are in `results.json`. The seed is in `session.json` and `queue.json` and a stopped run resumes with `run --resume RUN_DIR`.

## Stage latency (course template, section 5)

The machine exercise's 2-page template asks for a standardized latency method: one clock (the Raspberry Pi's
monotonic clock), every stage timestamped (VAD end → ASR final → LLM 1st token → TTS 1st audio → speaker),
N phrases × M runs, mean + p95, a JSON log per run. fdbench is black-box, so the stage times come from the assistant
as **stage marks** (docs/STAGES.md): `adapters/fdbench_marks.py`, or `adapters/pipecat_marks.py`, which the launcher
attaches to any Pipecat pipeline.

**Trials.** Group "stage latency": N phrases (default 10, `--phrases`) drawn once from the clean user turns that come
with a reference transcript (v1 interruption questions and v1.5 user turns), or your own (`--phrases-dir`: audio + a
same-name `.txt`), each played once per run for M runs (default 3, `--runs`), before the Full-Duplex-Bench rounds. A
trial plays the phrase and records up to 8 s after it, ending 1.2 s after she goes quiet; 6 s of silence between trials.

**Clock.** Each mark carries the assistant machine's `time.monotonic()`. All stage times below are differences of
these, so network delay does not enter them. To find a turn's marks and to compare with the audio, marks are mapped to
fdbench's clock with the offset min(receive − t) over the run, as for the machine stats.

**Per turn** (the reply = the first `audio_out` after the user's speech ends):
- VAD end = last `vad_end` before the reply (fallback `user_stopped`); LLM request = first `llm_request` after it;
  ASR final = last `asr_final` before that request (several segments are joined for WER).
- **E2E** = `audio_out` − `vad_end`. Without an `audio_out` mark: reply audio start (fdbench's VAD) − `vad_end`, marked
  "audio". With no marks at all, the summary falls back to **E2E heard** (below), marked "from audio".
- **ASR final** = `asr_final` − `vad_end` (0 if the transcript was already final before the VAD end, as with streaming STT).
- **LLM TTFT** = `llm_first_token` − `asr_final`. **TTS TTFB** = `tts_first_audio` − `tts_request`.
- **E2E heard** = start of her reply in the recording − true end of speech in the played clip (fdbench's Silero VAD).
  **VAD endpointing delay** = `vad_end` (mapped) − true end of speech: the silence her VAD waits for plus input buffering.
- **WER** = word edits / reference words, after lower-casing and removing punctuation, `[UH]` annotations and filler
  words (uh, um, …); corpus WER over all turns is the headline, the mean per-phrase WER is also reported.

**Barge-in** = t_stop (above) on the v1 and v1.5 interruption trials where she was speaking at the overlap onset and
stopped; trials she talked through are counted separately, not as a latency.

**Early (speculative) LLM calls.** Some assistants start the LLM on a partial transcript before the turn has ended
(Pipecat speculation; marked `llm_request` with `speculative=True`). Latency uses only normal `llm_request` marks, so
LLM TTFT still counts from the final transcript; when an early call's reply is the one spoken, its first token can come
before the final transcript and LLM TTFT then reads short (E2E is not affected). For context size, only calls that
answer a final transcript count: normal calls, and an early call that the turn end confirms (no `llm_cancel` or newer
`llm_request` before the next `user_stopped` / `audio_out`). Discarded early calls are left out.

**Context size** = prompt tokens per LLM call that answered a final transcript, over all trials: the LLM's reported usage (input incl. cache) when sent,
else the request's own `prompt_tokens`, else characters / 4 (marked "estimate"); median, max, first → last call, and
the median per run of the stage test. Tool calls: `tool_end` − `tool_start` per call.

**Targets** (summary table): E2E 4.0 s, ASR final 0.8 s, LLM TTFT 1.5 s, TTS TTFB 0.6 s, barge-in 0.8 s, WER 15 %;
change with the wizard, `--targets`, or `targets.json` (see `targets.example.json`). "met" = mean and p95 under the
target; "p95 over" = mean under, p95 over; "over" = mean over.

**Reported:** `deck_summary` first in `results.json`, the deck-styled summary at the top of `results.html`,
`summary.md` and `latency_chart.svg` to paste into slides, course rows at the top of the metrics table, and
`stage_latency.json` (every turn, per-run mean and p95).

## Machine resources (ours)

Not in the papers. It answers whether a slow or missed turn came from her machine (a Raspberry Pi 4 here) or from the
cloud APIs.

**What.** Once a second: CPU per core and in total (`/proc/stat`); CPU and resident memory of her processes, labelled
assistant (the launcher process: Pipecat, Silero VAD), ASR, TTS and tools MCP servers (her child processes, matched by
command line; `--stats-procs` for servers started separately); free RAM and swap (`/proc/meminfo`); SoC temperature
(`thermal_zone0`). Every 2 s, because each call starts a subprocess: the actual ARM clock (`vcgencmd measure_clock arm`;
the cpufreq file shows the requested clock, not the firmware-capped one) and all 8 flags of `vcgencmd get_throttled`
(under-voltage, frequency capped, throttled, soft temperature limit; now and since boot). CPU is percent of the whole
machine, so per-process figures add up to her share.

**How.** `launch_assistant.py` runs the sampler (`fdbench/probe.py`, standard library only) and sends each sample as a
text frame on the benchmark socket. fdbench stores it with its own receive time. The clock offset between the two
machines is the smallest receive − sample time seen in the run (the fastest network crossing), so network jitter does
not shift samples. The runner records each trial's start and end on the same clock (`mono` in `timing.json`) and keeps
the trial's samples in `trials/<id>/pi_stats.jsonl`; the whole run is in `pi_stats.jsonl`. At 1 sample per second the
stats describe a trial as a whole, not a single turn.

**Reported** (`resources` in `results.json`, "Machine resources" in `results.html`): peak temperature, share of
samples with any "now" throttle flag, under-voltage, CPU median and p95, p95 of the busiest core (near 100 % means one
thread, often Python's, is the bottleneck), lowest free RAM and ARM clock; a timeline; the same per test group and per
process; latency against mean CPU per trial with Pearson r (n ≥ 5) and the median latency of throttled vs other trials;
the trials that ran while throttled. Since-boot flags count only when they turn on during the run.

**Overhead.** Tracked processes are read every sample; the full `/proc` scan for new child processes runs every 5 s.
The sampler measures its own CPU time (plus the wall time of the `vcgencmd` calls, an upper bound) and the report
prints it as a share of one core. About 400 bytes per second go over the link.

**Other machines.** Every stat is optional. Linux without `vcgencmd`: no clock or throttle flags. Windows and macOS:
CPU and RAM through psutil when installed, otherwise nothing; the report says what was not available. `--no-stats` on
the launcher turns it off; an older launcher simply sends none. For `--sim` and virtual-cable runs, `--host-stats`
samples the machine fdbench runs on, without process labels.

## Deviations from the papers

| Topic | Papers | Here | Effect |
|---|---|---|---|
| Audio path | file streamed to the model offline | live playback over a local socket or a virtual cable | direct feed through Pipecat adds about 0.1–0.25 s round trip (measured with an echo pipeline), comparable to a USB speakerphone's buffers; it is included in the latencies |
| Overlap timing (v1 interruption, v1.5) | fixed time in the file | 1.5 s after the assistant starts speaking; skipped (Unknown) if it never starts | needed for live systems |
| Speech-to-text | Parakeet-TDT 0.6B (CrisperWhisper in v1.0) | faster-whisper small int8 (`--asr-model`) | slightly different word timings and counts |
| Judge | GPT-4o (gpt-4-turbo in the interruption script) | any provider; GPT-4o marked paper-comparable | labels may shift with other judges |
| Fresh session per clip | yes | `--reset-cmd` or 6 s silence | context may carry over |
| Sample size | all clips | 30 per group available, rounds decide how many | wider intervals |
| Candor turn-taking clips | full clip incl. silence | cut 0.3 s after the turn end | none |
| v1.5 UTMOSv2 | always | optional (heavy) | reported as not scored when missing |
| Stage latency (course) | — | needs the assistant's stage marks | stage rows "not reported" without them; E2E and barge-in still from audio |
