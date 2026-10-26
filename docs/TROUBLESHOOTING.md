# Troubleshooting

**Direct feed: she did not connect (laptop).** fdbench starts her with `ME3\.venv` and writes her output to the run
folder's `assistant.log`; read it. Usual causes: her `.env` keys missing (`python -m assistant.main --check`), her venv
without Pipecat, or fdbench could not find the project (pass `--project PATH`). Run `PROJECT_PYTHON launch_assistant.py --check`
(docs/GUIDE.md step 4) until it prints READY. You can also start her yourself in a second terminal:
`PROJECT_PYTHON launch_assistant.py`.

**Direct feed: she did not connect (Pi).** On the Pi the launcher prints "waiting for fdbench at ws://…": fdbench is
not running yet, the laptop IP is wrong, or Windows Firewall blocks port 8791 (`scripts\open_firewall_windows.ps1`).
Try the link test first: `launch_assistant.py --echo --url ws://LAPTOP-IP:8791`. Networks with client isolation
(campus Wi-Fi) block laptop↔Pi traffic; use an SSH tunnel (docs/GUIDE.md, step 12 option E) or Ethernet.

**Direct feed: she connected but never says anything.** Check `assistant.log` (or her terminal) for ASR/TTS/LLM
errors or missing keys. The run pauses after 5 silent trials so you can look.

**Port 8791 in use.** `python -m fdbench --port 8792` and `--url ws://…:8792` on her side.

**Pre-flight: cable 1 / cable 2 not found.** Install the virtual cables (docs/SETUP.md), then `python -m fdbench devices`.
If they are listed but not picked, pass `--to-bot NAME --from-bot NAME` with a fragment of the device names.
On Windows prefer the WASAPI entries (full names); MME truncates names.

**Both ends point at the same cable.** You have only one cable. Install a second one; the assistant would otherwise hear the test clips as itself.

**She never says anything (the run pauses after 5 silent trials).** Her output device is not cable 2, or she is not running.
Check her settings, then press `c` to continue or `q` to stop. Also check that Windows did not switch the cable's sample rate (Properties → Advanced, 48000 Hz).

**She answers every false turn-taking clip.** That is a real result: a plain VAD-triggered assistant answers any speech. The metric exists to show it.

**Overlap "not played" in interruption trials.** She did not start answering the first turn within 8 s, so there was nothing to interrupt. Counted as Unknown. Check latency with a 1-round run.

**`OSError: PortAudio library not found` (Pi).** `sudo apt install libportaudio2`.

**`soundfile` cannot open FLAC (Pi).** `sudo apt install libsndfile1`.

**Live table garbled.** Use Windows Terminal or PowerShell 7; or run with `--no-live` (plain lines go to `run.log` anyway).

**Scoring is very slow on the Pi.** Transcripts use faster-whisper. Use `--asr-model tiny`, or `--no-asr` on the Pi and later
`python -m fdbench score runs/…` on a laptop (copy the run folder). Voice metrics need `praat-parselmouth`; UTMOSv2 needs torch (laptop).

**Judge errors (HTTP 401/404).** Wrong key or model name. Keys are read from `.env` or the environment; `python -m fdbench check --judge-provider openai`
tests one call. Gemini uses its OpenAI-compatible endpoint; Hermes uses `NOUS_API_KEY`; custom servers need `JUDGE_BASE_URL`.

**Stopped mid-run.** `python -m fdbench run --resume runs/<student>/<run>` continues the same queue; `score` works on partial runs.

**Numbers differ from the papers.** Different ASR, live rig, smaller samples, live overlap timing; see docs/METHOD.md "Deviations".
Compare classmates to each other with `python -m fdbench compare runs/*`, same rounds and judge for everyone.
