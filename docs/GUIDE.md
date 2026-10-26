# Guide: benchmarking on the laptop and on the Raspberry Pi

One guide, in the order you do things. Each step says where it runs: **[laptop]**, **[Pi]**, or both. The laptop
always runs fdbench (it plays the clips, records, scores, keeps every result and log). The assistant runs either on
the laptop or on the Pi. Nothing in your project is modified: the benchmark starts her through its own
`fd-benchmark/launch_assistant.py`, using the assistant's existing `build(transport=...)` hook. No real microphone
or speaker is used anywhere.

`ME3` stands for the project folder on each machine (`...\Machine Exercise 3` on Windows, `~/ME3` on the Pi).
Laptop commands are PowerShell; Pi commands are a terminal on the Pi (or an SSH session to it).

## 1. [laptop] Install fdbench (once, ~5 min)

```powershell
cd "ME3\fd-benchmark"
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

Creates `fd-benchmark\.venv` with **Python 3.13** (`py install 3.13` if you only have 3.14: onnxruntime and
sounddevice have no 3.14 wheels yet) and installs `requirements.txt` and `requirements-score.txt` (transcripts and
voice metrics, needed for the v1.5 behaviour labels). By hand, the same is:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-score.txt
```

Always run the tool as `.\.venv\Scripts\python -m fdbench`; plain `python -m fdbench` uses the system Python, which
has none of the packages.

LLM judge (interruption score, v1.5 behaviour labels): copy `.env.example` to `.env` and put one key in it, for
example `NOUS_API_KEY=...` (Hermes) or `OPENROUTER_API_KEY=...`. Without a key you still get every timing and
turn-taking metric, and `score` can add the judge later.

## 2. [laptop] Build the datasets (once, ~15 min, internet)

```powershell
.\.venv\Scripts\python -m pip install gdown
.\.venv\Scripts\python tools\build_datasets.py
```

Result: `datasets\` with about 280 clips in 9 groups. If the download finished but nothing was built, run
`.\.venv\Scripts\python tools\build_datasets.py --src downloads\fdb`. If Google Drive refuses automated downloads,
download the folder by hand from the link in `tools\build_datasets.py`, unzip, and use `--src PATH`. The datasets
stay on the laptop; the Pi never needs them.

## 3. Prepare the assistant

**[laptop]** Nothing to do. `launch_assistant.py` imports your `Assistant` and builds it with the benchmark transport.
In benchmark mode her turn and latency logs go to the run folder (`runs\...\assistant\`), not to `data\`; Hermes,
voice tone and tools run as usual. Her venv must have `websockets` (it is in the course `requirements.txt`).
Keep `AUDIO_IN_SAMPLE_RATE=16000` in her `.env` (`AUDIO_OUT_SAMPLE_RATE` may be 48000 on the Pi).

**[Pi]** She must already run on the Pi (`bash app/install_pi.sh`, `.env` with keys). Clone the benchmark into the
project folder (add `fd-benchmark/` to the project's `.gitignore` if it is not there yet):

```bash
git clone https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking.git ~/ME3/fd-benchmark
# later updates:  git -C ~/ME3/fd-benchmark pull
```

No pip install on the Pi: the launcher runs with her venv, which already has Pipecat and websockets.

Two things to know on either machine: alarms or reminders already set can ring during a run; and she is started
fresh for every run. For a different Pipecat assistant, edit `build_assistant()` in `launch_assistant.py` (about ten
lines) and pass `--project PATH` if the project is not the parent folder of fd-benchmark.

## 4. [laptop] Open the firewall for the Pi (once, only for Pi runs)

As administrator:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\open_firewall_windows.ps1
```

This allows inbound TCP 8791 on private networks. Both machines must be on the same network (same Wi-Fi, or an
Ethernet cable between them, which gives the steadiest timing). Find the laptop's address with `ipconfig`
("IPv4 Address" of the adapter in use); fdbench also prints it when it waits for the Pi.

**[Pi]** Link test, without starting her (echoes audio back to the laptop):

```bash
~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --echo --url ws://LAPTOP-IP:8791
```

It prints "waiting for fdbench" until a `python -m fdbench` run on the laptop reaches the connection step, then
"connected". If it never connects, see step 7 (option E) and Troubleshooting.

## 5. Run a benchmark

**[laptop]** always:

```powershell
cd "ME3\fd-benchmark"
.\.venv\Scripts\python -m fdbench
```

Answer: student number · rounds (`1` for a first check, ~7 min) · connection `a` (direct feed) · where she runs ·
judge (`n` = none, or a letter). Pre-flight runs; press `p`.

- **She runs on this laptop (`a`)**: fdbench starts her itself with `ME3\.venv`, writes her output to the run
  folder's `assistant.log`, waits for her, measures the link, runs, and shuts her down. One terminal, nothing else.
  (To start her yourself instead: in a second terminal, `ME3\.venv\Scripts\python fd-benchmark\launch_assistant.py`.)

- **She runs on the Pi (`b`)**: fdbench prints the Pi command with this laptop's IP and waits up to 5 minutes.
  **[Pi]**, in whichever form you chose in step 7:
  ```bash
  ~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --url ws://LAPTOP-IP:8791
  ```
  The Pi prints `connected to fdbench … benchmark mode`; the laptop prints `connected from <pi-ip> · link round trip
  N ms` and the live table starts. When the run ends, the launcher shuts her down and waits for the next run; the
  next `python -m fdbench` on the laptop finds the Pi already waiting. Ctrl+C on the Pi stops it.

Keys during the run: `space` pause, `s` skip clip, `q` stop early (everything done so far is scored).

## 6. [laptop] Results

Printed in the terminal and saved under `fd-benchmark\runs\<student>\<date-time>\`: `results.json`, `results.csv`,
`results.html` (open in a browser), `run.log`, `events.jsonl`, `session.json` (records where she ran and the link's
round trip), `assistant.log`, and every trial's audio. The v1.5 behaviour labels (Respond / Resume / Uncertain /
Unknown) need transcripts plus the judge; with timing only, the results show the timing shares (kept talking /
paused and came back / stopped). After the run you are asked whether to keep review recordings
(`k` all, `f` failed only, `r` raw only, `d` delete audio).

Later, on any run folder:

```powershell
.\.venv\Scripts\python -m fdbench report runs\<student>\<date-time>
.\.venv\Scripts\python -m fdbench score  runs\<student>\<date-time> --judge-provider hermes
.\.venv\Scripts\python -m fdbench compare runs\<student>\<laptop-run> runs\<student>\<pi-run>
```

Pi runs and laptop runs sit side by side in `compare`. Her own turn/latency logs from a Pi run stay on the Pi
(`~/ME3/fd-benchmark/runs/_assistant/`) unless she was started with option D below.

## 7. Keeping the Pi side up between runs

The launcher reconnects by itself for every run; these options only decide where it lives so you do not retype it.
A for a first try; B or C for a session of many runs; D to drive everything from the laptop; E only if A cannot
connect after step 4.

**A. One SSH window from the laptop, kept open.**
```powershell
ssh pi@raspberrypi.local
~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --url ws://LAPTOP-IP:8791
```
Serves every run while the window stays open.

**B. [Pi] tmux**: survives a closed SSH window, reattach any time.
```bash
sudo apt install -y tmux                       # once
tmux new -s bench '~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --url ws://LAPTOP-IP:8791'
# detach: Ctrl+B then D       reattach: tmux attach -t bench       stop: tmux kill-session -t bench
```

**C. [Pi] systemd service**: starts at boot, restarts if it dies, no terminal at all.
```bash
bash ~/ME3/fd-benchmark/scripts/install_pi_service.sh ws://LAPTOP-IP:8791
# log: journalctl -u fdbench-assistant -f      stop/remove: sudo systemctl disable --now fdbench-assistant
```
Re-run the script with a new URL if the laptop's IP changes. While the service runs she is in benchmark mode;
disable it to use her normally.

**D. [laptop] fdbench starts her on the Pi over SSH, each run.** Needs key-based SSH (`ssh-keygen`, then copy
`~\.ssh\id_ed25519.pub` into the Pi's `~/.ssh/authorized_keys`). Nothing is kept running on the Pi, her logs land in
the run folder:
```powershell
.\.venv\Scripts\python -m fdbench run --student 2025xxxxx --rounds 1 --connect direct --where remote `
  --assistant-cmd "ssh pi@raspberrypi.local ~/ME3/.venv/bin/python ~/ME3/fd-benchmark/launch_assistant.py --once --url ws://LAPTOP-IP:8791"
```

**E. [laptop] SSH tunnel instead of the firewall**, for networks that block laptop↔Pi traffic (campus Wi-Fi with
client isolation) or over a VPN. In its own window on the laptop:
```powershell
ssh -N -R 8791:127.0.0.1:8791 pi@raspberrypi.local
```
Then the Pi uses `--url ws://127.0.0.1:8791` (the tunnel carries it to the laptop), in form A, B or C.

## 8. Virtual cables instead of direct feed

Pick `b` at the connection question. **[laptop]** install VB-CABLE and a second VB-Audio cable (see
[SETUP.md](SETUP.md)); in her `.env` set `AUDIO_INPUT_DEVICE=CABLE Output`, `AUDIO_OUTPUT_DEVICE=Hi-Fi Cable Input`;
start her normally, then run fdbench. **[Pi]** fdbench must then run on the Pi too (`bash scripts/install_pi.sh`,
copy `datasets/` over, `bash scripts/setup_virtual_audio.sh` after every reboot, `AUDIO_INPUT_DEVICE=fdb_to_bot.monitor`,
`AUDIO_OUTPUT_DEVICE=fdb_from_bot`), and the run folder is scored on the laptop. `python -m fdbench devices` shows
what was auto-picked.

## 9. If something fails

- [laptop] "She did not connect" with `a`: read `runs\...\assistant.log` (her keys, `python -m assistant.main --check`,
  her venv missing Pipecat, or pass `--project PATH`).
- [Pi] stuck at "waiting for fdbench": fdbench is not at the connection step yet, the IP is wrong, or the firewall
  blocks 8791 (step 4). From another machine, `Test-NetConnection -ComputerName LAPTOP-IP -Port 8791` shows whether it is open.
- Round trip above 50 ms: Ethernet or the same access point; the delay is included in her latencies.
- [Pi] `vcgencmd measure_temp` and `vcgencmd get_throttled` (0x0 is good): a throttled Pi slows her down.
- Port 8791 busy: `python -m fdbench --port 8792` and `ws://…:8792` on her side.
- More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
