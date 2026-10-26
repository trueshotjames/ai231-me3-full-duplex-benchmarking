# fd-benchmark

Black-box benchmark for full-duplex voice assistants, adapted from **Full-Duplex-Bench v1 (Lin et al., 2025)**
and **v1.5 (Lin et al., 2026)**, plus this course's split of turn-taking into a **missed turn-taking rate** and a
**false turn-taking rate**. It feeds the papers' test clips to your assistant, records what the assistant says, and scores it with the
papers' rules and the authors' released judge prompts. No real microphone or speaker is used.

Works on Windows laptops and Raspberry Pi 4 / Pi 5 (64-bit Pi OS). Two ways to connect, chosen in the wizard:

- **Direct feed** (default): `launch_assistant.py` (in this folder, nothing in the project changes) starts the
  assistant with a benchmark transport that exchanges audio with fdbench over a socket. On a laptop fdbench starts
  her itself; for a Raspberry Pi, the Pi runs only her and the laptop runs fdbench and keeps all results.
- **Virtual audio cables**: the assistant is unchanged and uses two virtual devices as its microphone and speaker.

Step-by-step: **[docs/GUIDE.md](docs/GUIDE.md)**, laptop and Raspberry Pi together, every command in order.

## Quick start (5 steps)

1. **Install** (once). Clone this repo into your assistant's project folder as `fd-benchmark`
   (`git clone https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking.git fd-benchmark`). Step-by-step in docs/GUIDE.md.
   ```
   python -m venv .venv
   .venv\Scripts\activate          # Windows      (Pi: source .venv/bin/activate)
   pip install -r requirements.txt
   pip install -r requirements-score.txt   # laptop: transcripts + voice metrics (optional on a Pi)
   ```
2. **Datasets** (once, on a laptop with internet, ~15 min): `pip install gdown && python tools/build_datasets.py`.
   Copy the resulting `datasets/` folder to the Pi if you benchmark there.
3. **Connect your assistant.** Direct feed on a laptop: nothing to do, fdbench starts her. On a Pi: run
   `launch_assistant.py --url ws://LAPTOP-IP:8791` there (docs/GUIDE.md). Cables: point her microphone at cable 1 and
   her speaker at cable 2 and start her before the tool. Either way she must listen without a wake word.
4. **Run** `python -m fdbench`. It asks for your student number, how many rounds, and which LLM judge (if any),
   runs a 15-second pre-flight, then benchmarks. One round plays every test once (about 6–7 min).
   Start with **1 round** to check that everything works. Press `q` to stop early; everything done is kept.
5. **Results** appear in the terminal and are saved under `runs/<student>/<date-time>/` as `results.json`,
   `results.csv`, `results.html`, with `run.log`, `events.jsonl` and every trial's recording. Afterwards you can keep
   review recordings (stereo: left = test audio, right = your assistant) named `ok_…` / `FAIL_…`.

Other commands: `python -m fdbench check` (pre-flight only), `run` (no questions, all flags), `score RUN_DIR`
(score later or with another judge), `report RUN_DIR`, `compare runs/*`, `devices`, `recordings RUN_DIR`.
Try it without any audio device: `python tools/make_dev_dataset.py` then
`python -m fdbench run --sim --datasets datasets-dev --student test --rounds 1 --no-asr`.

## What is measured

| Paper | Metric | Better |
|---|---|---|
| v1 | TOR pause handling (synthetic, Candor) | lower |
| v1 | TOR turn-taking (Candor), **missed turn-taking rate** (= 1 − TOR) | higher / lower |
| ours | **False turn-taking rate**: side talk, TV/radio, background speech, noise played while idle | lower |
| v1 | Latency turn-taking (median, p95, mean) | lower, < 1 s |
| v1 | TOR interruption, judge score 0–5, latency interruption | higher / higher / lower |
| v1.5 | Respond / Resume / Uncertain / Unknown for interruption, backchannel, talking to others, background speech | scenario-dependent |
| v1.5 | Stop latency t_stop, response latency t_resp | lower |
| v1.5 | Voice change after an interruption: Δ WPM, Δ pitch mean/SD, Δ intensity mean/SD, Δ UTMOSv2, paired t-test | see METHOD |

Every formula, the paper source and each deviation from the papers is in [docs/METHOD.md](docs/METHOD.md).

## Layout

```
fdbench/        the tool (wizard, pre-flight, queue, rig, suites, scoring, reports)
datasets/       clips + manifest.json (built by tools/build_datasets.py)   datasets-dev/  tiny dev set
prompts/        the authors' judge prompts      models/   Silero VAD (ONNX)
tools/          dataset build scripts           scripts/  installers, virtual audio setup
docs/           SETUP, METHOD, TROUBLESHOOTING  runs/     results (git-ignored)
```

## Credits and licences

Benchmarks, data and judge prompts: Lin et al., Full-Duplex-Bench (https://github.com/DanielLin94144/Full-Duplex-Bench);
data licences in `datasets/DATA_LICENSES.md`. Silero VAD: snakers4/silero-vad (MIT). This tool: MIT.
