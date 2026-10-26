"""Command line: `python -m fdbench` (wizard) or the sub-commands check / run / score / report / compare / devices."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from . import __version__
from .settings import load_dotenv
from .util import DATASETS_DIR, ROOT, RUNS_DIR, fmt_dur, now_iso, read_json, run_stamp, write_json

console = Console()


def _add_common(p):
    p.add_argument("--datasets", default=None, help="dataset folder (default: fd-benchmark/datasets)")
    p.add_argument("--sim", action="store_true", help="use the simulated assistant instead of audio devices")
    p.add_argument("--sim-profile", default="good", choices=["good", "floor", "eager", "deaf", "mixed"],
                   help="behaviour of the simulated assistant")
    p.add_argument("--connect", default=None, choices=["direct", "cables"],
                   help="direct: the assistant connects to fdbench in benchmark mode (no audio devices); "
                        "cables: through two virtual audio cables")
    p.add_argument("--port", type=int, default=8791, help="port for --connect direct")
    p.add_argument("--where", default=None, choices=["local", "remote"],
                   help="direct feed: local = fdbench starts her on this machine; remote = she runs on another machine (Pi)")
    p.add_argument("--project", default=None, help="the assistant's project folder (default: the folder that holds fd-benchmark)")
    p.add_argument("--assistant-cmd", default=None, help="custom command fdbench runs to start her (local direct feed)")


def _add_judge(p):
    p.add_argument("--judge-provider", default=None, help="openai | anthropic | gemini | groq | openrouter | hermes | ollama | custom | none")
    p.add_argument("--judge-model", default=None)
    p.add_argument("--judge-url", default=None, help="base URL for the custom provider")


def _add_score(p):
    p.add_argument("--no-asr", action="store_true", help="skip speech-to-text (no word counts / transcripts / judge)")
    p.add_argument("--asr-model", default="small", help="faster-whisper size: tiny | base | small | medium")
    p.add_argument("--no-voice", action="store_true", help="skip v1.5 voice-change features")
    p.add_argument("--no-utmos", action="store_true", help="skip UTMOSv2 (slow, needs torch)")
    p.add_argument("--rescore", action="store_true", help="recompute cached transcripts and judge calls")


def build_parser():
    ap = argparse.ArgumentParser(prog="fdbench", description="Black-box full-duplex voice assistant benchmark")
    ap.add_argument("--version", action="version", version=f"fdbench {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    w = sub.add_parser("wizard", help="(default) ask the three questions, pre-flight, run, score, report")
    _add_common(w); _add_judge(w); _add_score(w)
    w.add_argument("--yes", action="store_true", help="do not stop at pre-flight warnings")
    w.add_argument("--to-bot", default=None, help="name fragment of the virtual device that is her microphone")
    w.add_argument("--from-bot", default=None, help="name fragment of the virtual device that is her speaker")
    w.add_argument("--no-live", action="store_true", help="plain log lines instead of the live table")
    w.add_argument("--recordings", default=None, choices=["k", "f", "r", "d"],
                   help="skip the question after the run: k keep+review files, f failed-only review, r raw only, d delete")

    c = sub.add_parser("check", help="pre-flight only")
    _add_common(c); _add_judge(c)
    c.add_argument("--to-bot", default=None); c.add_argument("--from-bot", default=None)

    r = sub.add_parser("run", help="run the benchmark without the wizard (all options on the command line)")
    _add_common(r); _add_judge(r); _add_score(r)
    r.add_argument("--student", required=True)
    r.add_argument("--rounds", type=int, default=1)
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--to-bot", default=None); r.add_argument("--from-bot", default=None)
    r.add_argument("--device-sr", type=int, default=None, help="device sample rate (default: device default)")
    r.add_argument("--gap", type=float, default=6.0, help="seconds of silence between trials")
    r.add_argument("--reset-cmd", default=None, help="shell command to run between trials (e.g. restart the assistant)")
    r.add_argument("--resume", default=None, help="run folder to resume")
    r.add_argument("--yes", action="store_true"); r.add_argument("--no-live", action="store_true")
    r.add_argument("--no-score", action="store_true", help="record only; score later with `score`")
    r.add_argument("--recordings", default=None, choices=["k", "f", "r", "d"],
                   help="skip the question after the run: k keep+review files, f failed-only review, r raw only, d delete")

    s = sub.add_parser("score", help="score (or re-score) a run folder")
    s.add_argument("run_dir"); _add_judge(s); _add_score(s)

    rp = sub.add_parser("report", help="print the results of a run folder")
    rp.add_argument("run_dir")

    cp = sub.add_parser("compare", help="one table across several runs")
    cp.add_argument("run_dirs", nargs="+")
    cp.add_argument("--csv", default=None, help="also write the table to this CSV file")

    d = sub.add_parser("devices", help="list audio devices and the auto-picked cables")

    rc = sub.add_parser("recordings", help="write review recordings for a run (stereo: left = test audio, right = her)")
    rc.add_argument("run_dir")
    rc.add_argument("--failed-only", action="store_true")
    rc.add_argument("--delete", action="store_true", help="delete the raw audio of the run instead")
    return ap


# ---------------------------------------------------------------------- helpers

def _dataset(args):
    from .datasets import Dataset
    root = Path(args.datasets) if getattr(args, "datasets", None) else DATASETS_DIR
    return Dataset(root)


def _judge(args, from_wizard=None):
    from .score.judge import Judge, JudgeConfig
    if from_wizard is not None:
        return Judge(from_wizard)
    prov = getattr(args, "judge_provider", None)
    if not prov or prov == "none":
        return None
    from .score.judge import PROVIDERS
    model = getattr(args, "judge_model", None) or (PROVIDERS[prov]["models"][0] if PROVIDERS[prov]["models"] else None)
    if not model:
        console.print("[red]--judge-model is needed for this provider[/red]")
        sys.exit(2)
    return Judge(JudgeConfig(provider=prov, model=model, base_url=getattr(args, "judge_url", None)))


def _sim_rig(profile: str):
    from .rig.sim import FakeBehaviour, SimRig
    profiles = {
        "good": FakeBehaviour(response_delay=0.6, stop_delay=0.25, resume_delay=0.9),
        "floor": FakeBehaviour(response_delay=1.2, stop_delay=None, resume_delay=2.5),       # holds the floor
        "eager": FakeBehaviour(response_delay=0.3, stop_delay=0.15, resume_delay=0.6, responds_to_foreign=1.0,
                               min_user_speech=0.05, cut_in_on_pause=1.0, end_silence_ms=300),
        "deaf": FakeBehaviour(response_delay=99.0),
    }
    if profile == "mixed":
        import random
        rng = random.Random(1)
        pool = [profiles["good"], profiles["floor"], profiles["eager"]]
        return SimRig(decide=lambda script: rng.choice(pool))
    return SimRig(behaviour=profiles[profile])


def _mode(args) -> str:
    return "sim" if args.sim else (getattr(args, "connect", None) or "direct")


def _find_project(args) -> Optional[Path]:
    """The assistant's project folder: --project, FDBENCH_PROJECT (.env), or the folder that holds fd-benchmark."""
    import os
    for cand in (getattr(args, "project", None), os.environ.get("FDBENCH_PROJECT"), ROOT.parent):
        if cand and (Path(cand) / "assistant" / "main.py").exists():
            return Path(cand).resolve()
    return None


def _project_python(project: Path) -> Optional[Path]:
    """The assistant's own interpreter: FDBENCH_ASSISTANT_PYTHON (.env), else the project's venv."""
    import os
    env = os.environ.get("FDBENCH_ASSISTANT_PYTHON")
    if env and Path(env).exists():
        return Path(env)
    for rel in ("venv/Scripts/python.exe", ".venv/Scripts/python.exe", ".venv/bin/python", "venv/bin/python"):
        p = project / rel
        if p.exists():
            return p
    return None


def _lan_ips() -> list:
    import socket
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the interface with a default route
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


def _direct_rig(args, run_dir: Optional[Path] = None):
    from .rig.direct import DirectRig
    where = getattr(args, "where", None) or "local"
    port = getattr(args, "port", 8791)
    rig = DirectRig(host=("0.0.0.0" if where == "remote" else "127.0.0.1"), port=port)
    try:
        rig.open()
    except OSError as e:
        console.print(f"[red]Could not listen on port {port}: {e}. Try another --port.[/red]")
        sys.exit(2)
    launched = None
    if where == "remote":
        ips = _lan_ips()
        console.print(f"\n[bold]On the other machine (Pi), start her in benchmark mode[/bold] "
                      f"(PROJECT = her project folder there, e.g. ~/ME3):")
        for ip in ips or ["<this laptop's IP>"]:
            console.print(f"  PROJECT/.venv/bin/python PROJECT/fd-benchmark/launch_assistant.py --url ws://{ip}:{port}")
        console.print("[dim]Already running there with this address (tmux / service)? It reconnects by itself.[/dim]")
        console.print("[dim]If it cannot connect, allow TCP port %d in this laptop's firewall (docs/GUIDE.md step 4). "
                      "Waiting up to 5 min… (Ctrl+C to cancel)[/dim]" % port)
    else:
        project = _find_project(args)
        py = _project_python(project) if project else None
        cmd = getattr(args, "assistant_cmd", None)
        if cmd or py:
            import shlex
            import subprocess
            if not cmd:
                cmd_list = [str(py), str(ROOT / "launch_assistant.py"), "--url", f"ws://127.0.0.1:{port}", "--once",
                            "--project", str(project)]
            else:
                cmd_list = shlex.split(cmd, posix=(sys.platform != "win32"))
            if run_dir:
                cmd_list += ["--log-dir", str(run_dir / "assistant")] if not cmd else []
                log = open(run_dir / "assistant.log", "ab")
            else:
                log = subprocess.DEVNULL
            console.print(f"\n[bold]Starting her in benchmark mode[/bold]: {' '.join(cmd_list[:2])} …"
                          + (f"\n[dim]her output goes to {run_dir / 'assistant.log'}[/dim]" if run_dir else ""))
            try:
                launched = subprocess.Popen(cmd_list, stdout=log, stderr=subprocess.STDOUT, cwd=str(project or ROOT))
            except OSError as e:
                console.print(f"[red]could not start her: {e}[/red]")
        if launched is None:
            console.print(f"\n[bold]Start her in benchmark mode in another terminal[/bold] (her venv's python):\n"
                          f"  python fd-benchmark/launch_assistant.py --url ws://127.0.0.1:{port}\n"
                          f"[dim]waiting up to 5 min… (Ctrl+C to cancel)[/dim]")
    if not rig.wait_for_assistant():
        if launched is not None:
            launched.terminate()
        console.print("[red]She did not connect. See docs/TROUBLESHOOTING.md (direct feed)"
                      + (f"; her output is in {run_dir / 'assistant.log'}" if (launched is not None and run_dir) else "") + ".[/red]")
        sys.exit(2)
    rtt = rig.measure_rtt()
    console.print(f"[green]connected[/green] from {rig.peer or '?'} · link round trip {rtt:.1f} ms" if rtt is not None
                  else "[green]connected[/green]")
    if rtt is not None and rtt > 50:
        console.print("[yellow]slow link: use Ethernet or the same Wi-Fi; the delay adds to her latencies[/yellow]")
    console.print("giving her 5 s to settle")
    import time
    time.sleep(5)
    rig.launched = launched
    return rig


def _audio_rig(args, pf):
    from .rig.audio import AudioRig
    tb, fb = pf.devices.get("to_bot"), pf.devices.get("from_bot")
    if tb is None or fb is None:
        console.print("[red]Virtual cables not found. See docs/SETUP.md, or use --sim to try the tool without them.[/red]")
        sys.exit(2)
    return AudioRig(out_device=tb.index, in_device=fb.index, device_sr=getattr(args, "device_sr", None))


def _print_preflight(pf):
    from rich.table import Table
    t = Table(show_header=False, box=None, pad_edge=False)
    for c in pf.checks:
        mark = "[green]✓[/green]" if c.ok else "[yellow]![/yellow]"
        fix = f"  [dim]→ {c.fix}[/dim]" if (not c.ok and c.fix) else ""
        t.add_row(mark, c.text + fix)
    console.print("\n[bold]Pre-flight[/bold]")
    console.print(t)


def _score_and_report(run_dir: Path, judge, args, session):
    from .report.files import write_results
    from .report.terminal import print_results
    from .score.scorer import Scorer

    def prog(i, n, name):
        console.print(f"[dim]scoring {i+1}/{n} {name}[/dim]", end="\r")

    sc = Scorer(run_dir, judge=judge, use_asr=not getattr(args, "no_asr", False), asr_model=getattr(args, "asr_model", "small"),
                with_voice=not getattr(args, "no_voice", False), with_utmos=not getattr(args, "no_utmos", False),
                progress=prog, force=getattr(args, "rescore", False))
    console.print("\n[bold]Scoring…[/bold] " + ("(transcribing with faster-whisper, this takes a while on a Pi)" if sc.use_asr else "(timing only)"))
    sc.score_trials()
    summary = sc.aggregate()
    write_results(run_dir, summary, session, read_json(run_dir / "check.json"), read_json(run_dir / "queue.json"))
    print_results(summary, session)
    console.print(f"\nSaved: {run_dir / 'results.json'}, results.csv, results.html · log: {run_dir / 'run.log'}")
    return summary


def _recordings_prompt(run_dir: Path, choice: str | None):
    """After a run: keep the recordings for review (stereo files named by verdict) or delete the audio."""
    from rich.prompt import Prompt
    from .report.recordings import delete_raw, export_review, folder_size_mb
    size = folder_size_mb(run_dir / "trials") if (run_dir / "trials").exists() else 0
    if choice is None:
        console.print(f"\nRecordings of every trial are in {run_dir / 'trials'} ({size:.0f} MB).")
        console.print("  \\[k] keep them and also write review files: one stereo file per trial "
                      "(left = test audio, right = her reply), named ok_/FAIL_ so you can listen to what went wrong")
        console.print("  \\[f] keep, review files for failed trials only")
        console.print("  \\[r] keep the raw recordings only")
        console.print("  \\[d] delete the audio (keeps timings, transcripts and results)")
        choice = Prompt.ask(">", choices=["k", "f", "r", "d"], default="k")
    if choice in ("k", "f"):
        out = export_review(run_dir, failed_only=(choice == "f"))
        console.print(f"Review files: {out} (see INDEX.md there)")
    elif choice == "d":
        n = delete_raw(run_dir)
        console.print(f"Deleted {n} audio files.")


# ---------------------------------------------------------------------- commands

def cmd_devices(args):
    from .rig import devices as dv
    try:
        devs = dv.list_devices()
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]sounddevice not working: {e}[/red]"); return 1
    for d in devs:
        tag = " [virtual]" if d.is_virtual else (" [physical]" if d.looks_physical else "")
        console.print(d.short() + tag)
    pick = dv.auto_pick()
    console.print(f"\nauto-pick → cable 1 (her mic): {pick['to_bot'].short() if pick['to_bot'] else 'none'}")
    console.print(f"auto-pick → cable 2 (her speaker): {pick['from_bot'].short() if pick['from_bot'] else 'none'}")
    return 0


def cmd_check(args, judge=None):
    from .preflight import run_preflight
    ds_dir = Path(args.datasets) if args.datasets else DATASETS_DIR
    proj = _find_project(args) if _mode(args) == "direct" and (getattr(args, "where", None) or "local") == "local" else None
    pf = run_preflight(ds_dir, sim=args.sim, mode=_mode(args), port=getattr(args, "port", 8791),
                       where=(getattr(args, "where", None) or "local"), project=proj,
                       project_python=(_project_python(proj) if proj else None), judge=judge, want_to_bot=getattr(args, "to_bot", None),
                       want_from_bot=getattr(args, "from_bot", None))
    _print_preflight(pf)
    return pf


def _start_run(args, student: str, rounds: int, judge, pf, seed=None, resume=None):
    from .queue import estimate_round_seconds
    from .runner import Runner
    ds = _dataset(args)
    if resume:
        run_dir = Path(resume)
        session = read_json(run_dir / "session.json") or {}
    else:
        run_dir = RUNS_DIR / student / run_stamp()
        run_dir.mkdir(parents=True, exist_ok=True)
        import random
        seed = seed if seed is not None else random.randrange(1, 10**6)
        session = {"student": student, "run_id": run_dir.name, "started": now_iso(), "rounds": rounds, "seed": seed,
                   "fdbench": __version__, "rig": ("sim:" + args.sim_profile if args.sim else _mode(args)),
                   "judge": (judge.describe() if judge else None), "machine": pf.info,
                   "devices": pf.to_json().get("devices"), "warnings_overridden": [c.text for c in pf.warnings],
                   "dataset": str(ds.root), "dataset_counts": ds.counts()}
        write_json(run_dir / "session.json", session)
        write_json(run_dir / "check.json", pf.to_json())
    mode = _mode(args)
    rig = (_sim_rig(args.sim_profile) if mode == "sim" else _direct_rig(args, run_dir) if mode == "direct"
           else _audio_rig(args, pf))
    session["where"] = getattr(args, "where", None) or "local"
    runner = Runner(run_dir, ds, rig, session, rounds, seed or session.get("seed", 0),
                    live=not getattr(args, "no_live", False), gap=(0.0 if args.sim else getattr(args, "gap", 6.0)),
                    reset_cmd=getattr(args, "reset_cmd", None), realtime_gap=not args.sim)
    if not runner.plan.items:
        console.print("[red]No trials to run: the datasets folder has no test clips for the benchmark groups. "
                      "Rebuild it with python tools/build_datasets.py (see docs/GUIDE.md step 2).[/red]")
        rig.close()
        sys.exit(2)
    est = estimate_round_seconds(ds) * rounds
    console.print(f"\nRun folder: {run_dir}\nPlanned: {len(runner.plan.items)} trials in {rounds} round(s), "
                  f"about {fmt_dur(est)}. Press q to stop early; everything done so far is kept and scored.\n")
    try:
        outcome = runner.run()
    finally:
        rig.close()
        if getattr(rig, "launched", None) is not None:
            try:
                rig.launched.wait(timeout=15)
            except Exception:  # noqa: BLE001
                rig.launched.kill()
    if hasattr(rig, "link_info"):
        session["link"] = rig.link_info()
    session["finished"] = now_iso()
    session["outcome"] = outcome
    write_json(run_dir / "session.json", session)
    return run_dir, session


def cmd_wizard(args):
    from .queue import estimate_round_seconds
    from .wizard import confirm_proceed, run_wizard
    try:
        ds = _dataset(args)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]"); return 2
    answers = run_wizard(estimate_round_seconds(ds), ask_connect=not args.sim and not args.connect,
                         ask_where=not args.sim and args.connect != "cables" and not args.where)
    if answers.get("connect"):
        args.connect = answers["connect"]
    if answers.get("where"):
        args.where = answers["where"]
    judge = _judge(args, from_wizard=answers["judge"]) if answers["judge"] else None
    pf = cmd_check(args, judge=judge)
    if not args.yes and not confirm_proceed(len(pf.warnings)):
        return 0
    run_dir, session = _start_run(args, answers["student"], answers["rounds"], judge, pf)
    _score_and_report(run_dir, judge, args, session)
    _recordings_prompt(run_dir, args.recordings)
    return 0


def cmd_run(args):
    judge = _judge(args)
    pf = cmd_check(args, judge=judge)
    if pf.warnings and not args.yes:
        from .wizard import confirm_proceed
        if not confirm_proceed(len(pf.warnings)):
            return 0
    run_dir, session = _start_run(args, args.student, args.rounds, judge, pf, seed=args.seed, resume=args.resume)
    if not args.no_score:
        _score_and_report(run_dir, judge, args, session)
        _recordings_prompt(run_dir, args.recordings)
    return 0


def cmd_score(args):
    run_dir = Path(args.run_dir)
    session = read_json(run_dir / "session.json") or {"student": run_dir.parent.name, "run_id": run_dir.name}
    judge = _judge(args)
    _score_and_report(run_dir, judge, args, session)
    return 0


def cmd_report(args):
    from .report.terminal import print_results
    run_dir = Path(args.run_dir)
    res = read_json(run_dir / "results.json")
    if not res:
        console.print("[red]no results.json; run `score` first[/red]"); return 2
    print_results(res, res.get("session", {}))
    return 0


def cmd_compare(args):
    from rich.table import Table
    from .report.files import compare_table
    files = []
    for d in args.run_dirs:
        p = Path(d)
        files += sorted(p.rglob("results.json")) if p.is_dir() else [p]
    rows = compare_table(files)
    t = Table(show_header=True, header_style="bold dim")
    for h in rows[0]:
        t.add_column(h, overflow="fold")
    for r in rows[1:]:
        t.add_row(*r)
    console.print(t)
    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        console.print(f"written {args.csv}")
    return 0


def main(argv=None):
    load_dotenv()
    ap = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"wizard", "check", "run", "score", "report", "compare", "devices", "recordings", "-h", "--help", "--version"}
    if not argv or argv[0] not in known:
        argv = ["wizard"] + argv  # bare `python -m fdbench [flags]` is the wizard
    args = ap.parse_args(argv)
    cmd = args.cmd
    try:
        if cmd == "wizard":
            return cmd_wizard(args)
        if cmd == "check":
            judge = _judge(args)
            cmd_check(args, judge=judge)
            return 0
        if cmd == "run":
            return cmd_run(args)
        if cmd == "score":
            return cmd_score(args)
        if cmd == "report":
            return cmd_report(args)
        if cmd == "compare":
            return cmd_compare(args)
        if cmd == "devices":
            return cmd_devices(args)
        if cmd == "recordings":
            from .report.recordings import delete_raw, export_review
            if args.delete:
                console.print(f"deleted {delete_raw(Path(args.run_dir))} audio files")
            else:
                console.print(f"written {export_review(Path(args.run_dir), failed_only=args.failed_only)}")
            return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130
    return 0
