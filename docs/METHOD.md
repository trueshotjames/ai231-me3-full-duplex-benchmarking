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

Rates: k/n with a 95 % Wilson interval. Latencies: median, **p95** and mean (the papers report means), with a bootstrap
interval on the median; with fewer than 20 trials p95 is flagged as close to the slowest trial.

## Queue

Trials are drawn without replacement per group from a seeded shuffle and arranged in rounds; each round holds one trial
of every group (two false turn-taking clips, rotating the four kinds). Stopping after any trial leaves every group
represented; the per-group counts are in `results.json`. The seed is in `session.json` and `queue.json` and a stopped run resumes with `run --resume RUN_DIR`.

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
