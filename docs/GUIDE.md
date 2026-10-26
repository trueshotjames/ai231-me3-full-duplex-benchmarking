# Guide: benchmark your voice assistant (laptop, or laptop + Raspberry Pi)

Follow the steps in order. Every step ends with a **Check**: do not go on until it passes. If a check fails, the
step says what to do. This works for a person or a coding agent following it literally.

**Who does what.** The laptop always runs fdbench: it plays the test clips, records her answers, scores them and keeps
every result and log. The assistant runs either on the same laptop (steps 1–7) or on a Raspberry Pi (steps 8–11).
No microphone or speaker is opened anywhere, and your project's code is not changed: fdbench starts the assistant
through its own `fd-benchmark/launch_assistant.py`.

**Names used below.**

| Name | Means | Example |
|---|---|---|
| `PROJECT` | your assistant's project folder | `C:\Users\you\ME3`, `~/ME3` |
| `FDB` | fd-benchmark's own Python | Windows `.\.venv\Scripts\python` · macOS/Linux `.venv/bin/python` |
| `PROJ_PY` | your assistant's Python (its venv, which has Pipecat) | Windows `..\.venv\Scripts\python` · macOS/Linux/Pi `../.venv/bin/python` |
| `LAPTOP-IP` | the laptop's address on the network (step 8) | `192.168.1.10` |

All commands are run from inside `PROJECT/fd-benchmark` unless a step says otherwise. Windows commands are PowerShell.

---

## 1. [laptop] Get fd-benchmark into your project folder

```powershell
cd PROJECT
git clone https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking.git fd-benchmark
cd fd-benchmark
```

Add the line `fd-benchmark/` to `PROJECT/.gitignore` so your project's git ignores it.

If fd-benchmark must live somewhere else: `cp .env.example .env` and set `FDBENCH_PROJECT=` to the project folder
(and `FDBENCH_ASSISTANT_PYTHON=` to her Python if her venv is not `PROJECT/.venv` or `PROJECT/venv`).

**Check:** `PROJECT/fd-benchmark/launch_assistant.py` exists.

## 2. [laptop] Install fdbench (once, about 5 min)

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

macOS / Linux (Python 3.10–3.13):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-score.txt
```

Use Python 3.13 or older (onnxruntime and sounddevice have no 3.14 wheels yet). On Windows with only 3.14:
`py install 3.13`, then rerun the script.

Optional LLM judge (interruption scores, v1.5 behaviour labels): copy `.env.example` to `.env` (if not done) and add
one key, for example `NOUS_API_KEY=...` (Hermes) or `OPENROUTER_API_KEY=...`. Without a key every timing and
turn-taking metric still works, and `score` can add the judge later.

**Check:** `FDB -m fdbench --version` prints a version. (Always run fdbench with `FDB`; plain `python` lacks the packages.)

## 3. [laptop] Build the datasets (once, about 15 min, needs internet)

```powershell
FDB -m pip install gdown
FDB tools/build_datasets.py
```

If the download finished but nothing was built: `FDB tools/build_datasets.py --src downloads/fdb`. If Google Drive
refuses the download, download the folder by hand from the link at the top of `tools/build_datasets.py`, unzip it,
and pass `--src THAT_FOLDER`.

**Check:** the script ends with about 280 clips in 9 groups, and `datasets/` has 9 subfolders.

## 4. [laptop] Connect your assistant to the launcher

Your assistant's own venv must have Pipecat with WebSocket support: `PROJ_PY -m pip install "pipecat-ai[websocket]"`
(skip if it already has `websockets`).

**A. The course assistant** (`PROJECT/assistant/main.py` with `class Assistant` and `build(transport=...)`):
nothing to do; `adapters/entry_course.py` is used automatically. Keep `AUDIO_IN_SAMPLE_RATE=16000` in her `.env`.

**B. Any other Pipecat assistant:**

```powershell
cp adapters/assistant_entry_template.py assistant_entry.py
```

Open `assistant_entry.py` and fill in its three parts: a file that identifies your project folder, your audio rates
(input 16000), and `build(transport, log_dir)`, which builds your pipeline with the given `transport` where your code
uses `LocalAudioTransport` (or Daily, etc.) and returns `(run_coroutine, cancel_function)`. The file has a worked
example. If your code builds its transport deep inside and cannot take one from outside, add a `transport=None`
parameter to the function that creates the pipeline; that is the only project change ever needed.

Then, for both A and B:

```powershell
PROJ_PY launch_assistant.py --check
```

**Check:** the last line is `READY`. If it says `NOT READY`, fix the line it points at: `pipecat: NOT USABLE` means
you used the wrong Python (use `PROJ_PY`) or Pipecat lacks `[websocket]`; `project not found` means fd-benchmark is
not inside the project (set `FDBENCH_PROJECT`, step 1); `audio: input must be 16000` means change her input rate.

## 5. [laptop] First run, assistant on the laptop (about 7 min)

Interactive:

```powershell
FDB -m fdbench
```

Answer: student number · rounds `1` · connection `a` (direct feed) · where she runs `a` (this machine) · judge `n`
(or a letter). Pre-flight runs; press `p`.

Non-interactive (for agents or scripts), same thing:

```powershell
FDB -m fdbench run --student 2025xxxxx --rounds 1 --connect direct --where local --judge-provider none --yes --recordings k
```

fdbench starts the assistant itself with `PROJ_PY`, waits for her, measures the link, runs every clip, and shuts her
down at the end. One terminal, nothing else. Keys during the run: `space` pause, `s` skip clip, `q` stop early.

**Check:** the live table shows one row per clip with verdicts, and the run ends with a results table. If it stops
with "Her launcher exited" or "She did not connect", read the last lines it prints and `runs/<student>/<run>/assistant.log`
(usually her API keys or `.env`); fix, then repeat step 4's check and this step.

## 6. [laptop] Results

Each run is saved under `runs/<student>/<date-time>/`: `results.html` (open in a browser), `results.json`,
`results.csv`, `run.log`, `events.jsonl`, `session.json` (where she ran, link round trip), `assistant.log`, her own
turn/latency logs in `assistant/`, and the audio of every trial.

The v1.5 behaviour labels (Respond / Resume / Uncertain / Unknown) need transcripts plus a judge. Without a judge the
results show timing shares only (kept talking / paused and came back / stopped). Later, on any run folder:

```powershell
FDB -m fdbench report  runs/<student>/<date-time>
FDB -m fdbench score   runs/<student>/<date-time> --judge-provider hermes
FDB -m fdbench compare runs/<student>/<run-1> runs/<student>/<run-2>
```

**Check:** `results.html` opens and shows the metrics.

## 7. [laptop] Longer runs

Repeat step 5 with `--rounds 2` or more (about 7 min per round). Steps 1–6 are the complete laptop benchmark; continue
only if you also benchmark on a Raspberry Pi.

---

## 8. [laptop] Prepare the laptop for the Pi (once)

1. Put the laptop and the Pi on the same network (same Wi-Fi, or an Ethernet cable between them for steadiest timing).
2. Allow the Pi to reach fdbench on TCP port 8791:
   - Windows, in PowerShell **as administrator**: `powershell -ExecutionPolicy Bypass -File scripts\open_firewall_windows.ps1`
   - macOS: allow incoming connections for the Python in `fd-benchmark/.venv` when macOS asks. Linux with ufw: `sudo ufw allow 8791/tcp`.
3. Find `LAPTOP-IP`: Windows `ipconfig` ("IPv4 Address" of the adapter in use); macOS `ipconfig getifaddr en0`;
   Linux `hostname -I`. fdbench also prints it when it waits for the Pi.

**Check:** you have `LAPTOP-IP` written down.

## 9. [Pi] Prepare the Pi (once)

The assistant must already run normally on the Pi (for the course: `bash app/install_pi.sh` and her `.env` with keys).
Then, in a terminal on the Pi (or `ssh pi@raspberrypi.local` from the laptop):

```bash
git clone https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking.git ~/ME3/fd-benchmark
cd ~/ME3/fd-benchmark
echo "FDBENCH_URL=ws://LAPTOP-IP:8791" > .env
```

Replace `~/ME3` with your project folder on the Pi. For a non-course assistant, copy your filled-in
`assistant_entry.py` from step 4B into `~/ME3/fd-benchmark/` too. No pip install for fd-benchmark is needed on the
Pi: the launcher runs with her venv. Updates later: `git -C ~/ME3/fd-benchmark pull`.

```bash
~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --check
```

**Check:** the last line is `READY` and the `fdbench:` line shows `ws://LAPTOP-IP:8791`. If not, see step 4's check.

## 10. Link test: laptop ↔ Pi, without the assistant (2 min)

**[laptop]** start a run that waits for the Pi:

```powershell
FDB -m fdbench run --student linktest --rounds 1 --connect direct --where remote --judge-provider none --no-score --yes --recordings d
```

**[Pi]** while the laptop waits:

```bash
~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --echo
```

**Check:** the Pi prints `connected to fdbench … (echo test)` and the laptop prints `connected from <pi-ip> · link
round trip N ms`; the run then plays clips (the "assistant" just echoes them, so verdicts are meaningless). Press
`q` on the laptop and Ctrl+C on the Pi.
If the Pi stays at "waiting for fdbench": the laptop is not at the waiting step yet, `LAPTOP-IP` is wrong, the
firewall is closed (step 8), or the network isolates devices (campus Wi-Fi). Then use option E in step 12.
A round trip above 50 ms is usable but adds to her measured latencies; Ethernet fixes it.

## 11. Benchmark with the assistant on the Pi

**[Pi]** start the launcher (it serves this run and every later run until stopped; step 12 has ways to keep it up):

```bash
~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py
```

**[laptop]**, interactive: `FDB -m fdbench`, answering where she runs with `b` (another machine). Or non-interactive:

```powershell
FDB -m fdbench run --student 2025xxxxx --rounds 1 --connect direct --where remote --judge-provider none --yes --recordings k
```

**Check:** the laptop shows `connected from <pi-ip>` and the live table fills; results land on the laptop under
`runs/<student>/<date-time>/` as in step 6, with `session.json` recording `"where": "remote"`. After the run, the
Pi prints that it is waiting for the next run; the next laptop run connects straight away.

Compare the laptop and Pi runs with `FDB -m fdbench compare runs/<student>/<laptop-run> runs/<student>/<pi-run>`.
Her own turn/latency logs from a Pi run stay on the Pi in `~/ME3/fd-benchmark/runs/_assistant/` (except option D).

## 12. Keeping the Pi side running between runs (pick one)

The launcher already reconnects for every run; these options only decide where it lives, so you do not retype it.
A for a first try; B or C for many runs; D to drive everything from the laptop; E if step 10 never connects.

**A. One SSH window from the laptop, kept open.**

```powershell
ssh pi@raspberrypi.local
~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py
```

**B. [Pi] tmux**: survives a closed SSH window.

```bash
sudo apt install -y tmux        # once
tmux new -s bench '~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py'
# detach: Ctrl+B then D     reattach: tmux attach -t bench     stop: tmux kill-session -t bench
```

**C. [Pi] systemd service**: starts at boot, restarts if it dies.

```bash
bash ~/ME3/fd-benchmark/scripts/install_pi_service.sh ws://LAPTOP-IP:8791     # add the project path if not ~/ME3
# log: journalctl -u fdbench-assistant -f     stop/remove: sudo systemctl disable --now fdbench-assistant
```

Re-run it with the new URL if the laptop's IP changes. While the service runs she is in benchmark mode; disable it
to use her normally.

**D. [laptop] fdbench starts her on the Pi over SSH, each run.** Needs key-based SSH (`ssh-keygen`, then add the
laptop's `~/.ssh/id_ed25519.pub` to the Pi's `~/.ssh/authorized_keys`). Nothing stays running on the Pi:

```powershell
FDB -m fdbench run --student 2025xxxxx --rounds 1 --connect direct --where remote --yes `
  --assistant-cmd "ssh pi@raspberrypi.local ~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --once --url ws://LAPTOP-IP:8791"
```

(macOS/Linux: put the command on one line, or end the first line with `\` instead of the backtick.)

**E. [laptop] SSH tunnel instead of the firewall**, for networks that block laptop↔Pi traffic. In its own window on the laptop:

```powershell
ssh -N -R 8791:127.0.0.1:8791 pi@raspberrypi.local
```

Then on the Pi set `FDBENCH_URL=ws://127.0.0.1:8791` in `~/ME3/fd-benchmark/.env` and use A, B or C.

**Check:** two laptop runs in a row both connect without touching the Pi in between.

---

## Notes

- **Virtual cables instead of direct feed** (not needed for the steps above): answer `b` at the connection question
  and follow [SETUP.md](SETUP.md). With cables on a Pi, fdbench must run on the Pi too (`bash scripts/install_pi.sh`,
  copy `datasets/` over, `bash scripts/setup_virtual_audio.sh` after each reboot) and the run folder is scored on the laptop.
- Alarms or reminders she already has set can ring during a run. She is started fresh for every run.
- Port 8791 busy: add `--port 8792` to the laptop command and use `ws://…:8792` on her side.
- Pi health: `vcgencmd measure_temp` and `vcgencmd get_throttled` (`0x0` is good); a throttled Pi slows her down.
- More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
