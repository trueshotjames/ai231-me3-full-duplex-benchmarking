# Setup

## What your assistant must have

- Listens continuously, **no wake word** and no push-to-talk.
- Either a way to build it with a given Pipecat transport (direct feed, below; the course assistant's
  `build(transport=...)`) or a way to **choose its input and output audio device** for the virtual cables.
- Accepts 16 kHz mono input in direct feed; 16 or 48 kHz with the cables (the tool resamples).
- Stays running for the whole session (a 10-round run is about an hour).
- Speaks through its output device (no Bluetooth speaker, no browser tab on another machine).
- Optional: a command that resets its conversation, passed with `--reset-cmd` (otherwise it gets 6 s of silence between trials).

The benchmark and the assistant run **on the same machine**. The tool never opens a real microphone or speaker.

## Direct feed (default, no audio devices)

fdbench opens a WebSocket server on `ws://127.0.0.1:8791` and waits. The assistant connects to it instead of opening
its microphone and speaker:

- fdbench → assistant: binary messages of 16 kHz mono int16 PCM, sent in real time (her microphone).
- assistant → fdbench: binary messages of mono int16 PCM (her speaker; sample rate from `?out_sr=` in the URL,
  default 16000). A text message `{"event": "clear"}` means "I was interrupted, drop what you have not played yet".

fdbench "plays" her audio in real time, like a speaker would, so her timing is measured as a listener would hear it.

**The course assistant and other Pipecat assistants:** `launch_assistant.py` in this folder builds the assistant with
`make_benchmark_transport(url)` from `adapters/pipecat_fdbench.py` through its `build(transport=...)` hook and runs it.
Nothing in the project is modified. For another Pipecat assistant, copy `adapters/assistant_entry_template.py` to
`fd-benchmark/assistant_entry.py` and fill in `build(transport, log_dir)` (docs/GUIDE.md step 4B); in short:

```python
worker = make_my_pipeline_worker(transport)   # where your code would use LocalAudioTransport
```

`PROJECT_PYTHON launch_assistant.py --check` must print READY. Everything after the transport (VAD, turn-taking, STT, LLM, TTS, interruptions) runs exactly as in normal use.
On a laptop fdbench starts the launcher itself (`--where local`); on a Pi you run it there against the laptop's
address (`--where remote`, see docs/GUIDE.md). The launcher reconnects for every run until stopped.

Other frameworks: anything that can open a WebSocket and send/receive PCM can use the protocol above directly.
If port 8791 is taken, pass `--port`. Needs `pip install websockets` (in requirements.txt).

## Virtual audio cables (alternative, assistant unchanged)

Two cables are needed: cable 1 is the assistant's microphone (the tool plays into it), cable 2 is the assistant's
speaker (the tool records from it). One cable cannot do both, or the assistant would hear the test clip as its own voice.

### Windows 10 / 11

1. Install **VB-CABLE** (free): https://vb-audio.com/Cable/ → "VB-CABLE Driver Pack", run `VBCABLE_Setup_x64.exe` as administrator, reboot.
   This gives `CABLE Input` (playback end) and `CABLE Output` (recording end) of one cable.
2. Install a **second cable**: VB-Audio "Hi-Fi Cable" or "VB-CABLE A+B" (donationware from the same page), or Voicemeeter.
   You then have e.g. `Hi-Fi Cable Input` / `Hi-Fi Cable Output` or `CABLE-A Input` / `CABLE-A Output`.
3. In your assistant: microphone = `CABLE Output`, speaker = `Hi-Fi Cable Input` (the *Input* end of cable 2 is what the assistant plays into).
4. In the tool nothing needs configuring: `python -m fdbench devices` shows what it auto-picked. If it picked wrong,
   pass `--to-bot "CABLE Input" --from-bot "Hi-Fi Cable Output"` (name fragments) to `run` or the wizard.
5. Windows Settings → Sound: make sure the cables are *not* your default devices (otherwise other apps play into them),
   and in each cable's Properties → Advanced set 48000 Hz for both ends.

`scripts/install_windows.ps1` creates the venv, installs requirements and lists the devices.

### Raspberry Pi 4 / Pi 5 (Raspberry Pi OS 64-bit, Bookworm, PipeWire)

```
sudo apt install -y libportaudio2 libsndfile1 python3-venv
bash scripts/setup_virtual_audio.sh      # creates two null sinks: fdb_to_bot and fdb_from_bot (PipeWire)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
The script creates two PipeWire/PulseAudio null sinks. For the assistant: microphone = `fdb_to_bot.monitor`,
speaker = `fdb_from_bot`. For the tool they appear in `python -m fdbench devices` as `fdb_to_bot` (output) and
`Monitor of fdb_from_bot` (input) and are auto-picked. The sinks disappear at reboot; re-run the script (or add it to autostart).

ALSA-only assistants (no PulseAudio/PipeWire): `sudo modprobe snd-aloop` gives `hw:Loopback,0,x` / `hw:Loopback,1,x`
pairs; use `--to-bot Loopback --from-bot Loopback` and pass `--device-sr 48000`.

Pi notes: use Ethernet; add a heatsink or fan (pre-flight shows the temperature and throttling); scoring with
transcripts is slow on a Pi 4 (`--asr-model tiny`), or copy the run folder to a laptop and run `python -m fdbench score RUN_DIR` there.

## LLM judge (optional)

Needed for the v1 interruption relevance score and the v1.5 behaviour labels. Any of these keys in a `.env` file in this
folder is picked up automatically (copy `.env.example`): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
`GROQ_API_KEY`, `OPENROUTER_API_KEY`, `NOUS_API_KEY` (Hermes), or `JUDGE_BASE_URL` + `JUDGE_API_KEY` for any
OpenAI-compatible server (Ollama needs no key). GPT-4o is what the papers used; other judges are reported as such.
No key: all timing and turn-taking metrics are still produced, and `python -m fdbench score RUN_DIR --judge-provider …`
fills the judge metrics later.

## Datasets

The authors' data is on Google Drive, so build once on a laptop: `pip install gdown && python tools/build_datasets.py`
(about 2 GB download, ~15 min, result ~120 MB in `datasets/`). Copy `datasets/` to the Pi. If Drive refuses automated
downloads, download the folder by hand from the link in `tools/build_datasets.py`, unzip, and run
`python tools/build_datasets.py --src PATH`.
