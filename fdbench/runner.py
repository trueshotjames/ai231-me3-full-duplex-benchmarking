"""Runs the queue: one trial at a time, live table, per-trial files, resumable."""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Optional

from . import SR, __version__
from .datasets import Dataset
from .live import KeyWatcher, LiveTable, console
from .queue import Plan, build_plan, estimate_round_seconds
from .rig.base import RigBase
from .suites import GROUPS, TrialPlan, expected_text, make_script, provisional
from .util import Stopwatch, append_jsonl, now_iso, read_json, save_audio, write_json

GAP_BETWEEN_TRIALS = 6.0     # s of silence the assistant gets between trials
NO_REPLY_PAUSE_AFTER = 5     # pause once if she has not spoken in this many trials that expect a reply


class RunLog:
    def __init__(self, run_dir: Path):
        self.path = run_dir / "run.log"
        self.events = run_dir / "events.jsonl"

    def log(self, msg: str) -> None:
        line = f"{now_iso()}  {msg}"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def event(self, kind: str, **data) -> None:
        append_jsonl(self.events, {"t": now_iso(), "event": kind, **data})


class Runner:
    def __init__(self, run_dir: Path, ds: Dataset, rig: RigBase, session: dict, rounds: int, seed: int,
                 live: bool = True, gap: float = GAP_BETWEEN_TRIALS, reset_cmd: Optional[str] = None,
                 realtime_gap: bool = True):
        self.run_dir = run_dir
        self.ds = ds
        self.rig = rig
        self.session = session
        self.log = RunLog(run_dir)
        self.live_enabled = live
        self.gap = gap
        self.reset_cmd = reset_cmd
        self.realtime_gap = realtime_gap
        plan_file = run_dir / "queue.json"
        existing = read_json(plan_file)
        if existing:
            self.plan = Plan.from_json(existing)
            self.log.log(f"resuming queue with {sum(1 for i in self.plan.items if i.status == 'pending')} pending trials")
        else:
            self.plan = build_plan(ds, rounds, seed)
            write_json(plan_file, self.plan.to_json())
        self.clips = {g: {c.id: c for c in ds.clips(g)} for g in ds.groups}
        self.trial_times: list = []

    # ------------------------------------------------------------------
    def _save_plan(self):
        write_json(self.run_dir / "queue.json", self.plan.to_json())

    def _est_remaining(self, pending: int) -> Optional[float]:
        if not self.trial_times:
            return None
        per_item = sum(self.trial_times) / len(self.trial_times)
        return per_item * pending

    def run(self) -> dict:
        items = [i for i in self.plan.items if i.status == "pending"]
        total = len(self.plan.items)
        done_before = total - len(items)
        sw = Stopwatch()
        keys = KeyWatcher().start()
        quiet_streak = 0
        asked_about_silence = False
        stopped_early = False
        session_info = self.session
        self.log.log(f"fdbench {__version__} run start · {total} trials · {self.plan.rounds} rounds · seed {self.plan.seed}")
        self.log.event("run_start", total=total, rounds=self.plan.rounds, seed=self.plan.seed)

        with LiveTable(session_info.get("student", "?"), total, self.plan.rounds, enabled=self.live_enabled) as table:
            n_done = done_before
            for item in items:
                if keys.quit:
                    stopped_early = True
                    break
                while keys.paused and not keys.quit:
                    table.set_status("[yellow]paused — press space to continue[/yellow]")
                    time.sleep(0.2)
                table.set_status("")
                table.set_header(n_done, item.round, sw.elapsed(), self._est_remaining(total - n_done))
                clip = self.clips[item.group][item.clip_id]
                group = GROUPS[item.group]
                variants = ["main", "clean"] if group.paired else ["main"]
                t_item = time.monotonic()
                item_results = []
                for variant in variants:
                    if keys.skip:
                        keys.skip = False
                        item.status = "skipped"
                        self.log.log(f"trial {item.index} skipped by user")
                        break
                    plan = TrialPlan(f"r{item.round:02d}_{item.group}_{clip.id}" + ("_clean" if variant == "clean" else ""),
                                     item.group, clip, variant)
                    plan.expected = expected_text(plan)
                    row = table.add(item.index + 1, group.title + (" (clean)" if variant == "clean" else ""),
                                    clip.id, plan.expected, details=plan.details())
                    res = self._run_trial(plan, row, table)
                    item_results.append(res)
                    if res.get("error"):
                        item.status = "failed"
                    expects_reply = item.group in ("v1_turntaking", "v1_interrupt") or item.group.startswith("v15_")
                    if expects_reply and not res.get("error"):
                        if res.get("bot_segments"):
                            quiet_streak = 0
                        else:
                            quiet_streak += 1
                    # settle gap
                    if variant != variants[-1] or item is not items[-1]:
                        self._gap(table)
                if item.status == "pending":
                    item.status = "done"
                n_done += 1
                self.trial_times.append(time.monotonic() - t_item)
                self._save_plan()
                if quiet_streak >= NO_REPLY_PAUSE_AFTER and not asked_about_silence:
                    asked_about_silence = True
                    table.set_status("[red]She has not said anything in the last 5 trials that expected a reply. "
                                     + ("Is she still connected and running?" if getattr(self.rig, "name", "") == "direct"
                                        else "Is her output device set to cable 2?") + "  \\[c] continue  \\[q] stop[/red]")
                    while True:
                        if keys.quit:
                            stopped_early = True
                            break
                        time.sleep(0.2)
                        # any key other than q continues (space toggles pause; clear it)
                        if keys.paused or keys.skip:
                            keys.paused = False
                            keys.skip = False
                            break
                    table.set_status("")
                    if stopped_early:
                        break
            table.set_header(n_done, items[-1].round if items else self.plan.rounds, sw.elapsed(), 0.0)
        keys.stop()
        self._save_plan()
        completed = sum(1 for i in self.plan.items if i.status == "done")
        self.log.log(f"run end · {completed}/{total} trials done · stopped_early={stopped_early} · {sw.elapsed():.0f} s")
        self.log.event("run_end", completed=completed, total=total, stopped_early=stopped_early, seconds=sw.elapsed())
        return {"completed": completed, "total": total, "stopped_early": stopped_early, "seconds": sw.elapsed()}

    # ------------------------------------------------------------------
    def _gap(self, table: LiveTable):
        if self.reset_cmd:
            import subprocess
            table.set_status(f"[dim]running reset command: {self.reset_cmd}[/dim]")
            try:
                subprocess.run(self.reset_cmd, shell=True, timeout=60)
            except Exception as e:  # noqa: BLE001
                self.log.log(f"reset command failed: {e}")
        if self.realtime_gap and self.gap > 0:
            table.set_status(f"[dim]waiting {self.gap:.0f} s for her to settle…[/dim]")
            time.sleep(self.gap)
        table.set_status("")

    def _run_trial(self, plan: TrialPlan, row: dict, table: LiveTable) -> dict:
        tdir = self.run_dir / "trials" / plan.trial_id
        tdir.mkdir(parents=True, exist_ok=True)
        self.log.log(f"trial {plan.trial_id} start · {plan.describe_clip()} · expected: {plan.expected}")
        self.log.event("trial_start", trial=plan.trial_id, group=plan.group, clip=plan.clip.id, variant=plan.variant)

        def on_event(kind, t):
            if kind == "bot_start":
                table.update(row, actual=f"▶ she started speaking at {t:.2f} s")
            elif kind == "overlap_scheduled":
                table.update(row, actual=f"▶ she is speaking; overlap at {t:.2f} s")
            elif kind == "bot_end":
                table.update(row, actual=f"▶ she stopped at {t:.2f} s")

        try:
            script = make_script(plan, on_event=on_event)
            rec = self.rig.run(script)
        except Exception as e:  # noqa: BLE001
            self.log.log(f"trial {plan.trial_id} ERROR: {e}\n{traceback.format_exc()}")
            table.update(row, actual=f"error: {e}", verdict="✗ error", ok=False, live=False)
            write_json(tdir / "timing.json", {"error": str(e), "trial": plan.trial_id})
            return {"error": str(e)}

        save_audio(tdir / "played.flac", rec.played)
        save_audio(tdir / "her.flac", rec.heard)
        facts = provisional(plan, rec)
        timing = {
            "trial": plan.trial_id, "group": plan.group, "clip": plan.clip.id, "variant": plan.variant,
            "expected": plan.expected, "duration": rec.duration,
            "events": [{"name": e.name, "t": e.t, "len": len(e.audio) / SR} for e in rec.events],
            "live_bot_starts": rec.bot_starts, "live_bot_ends": rec.bot_ends, "rig": rec.notes,
            "clip_meta": plan.clip.meta, **facts,
        }
        write_json(tdir / "timing.json", timing)
        table.update(row, actual=facts.get("actual", ""), verdict=facts.get("verdict", ""), ok=facts.get("ok"),
                     live=False)
        self.log.log(f"trial {plan.trial_id} end · actual: {facts.get('actual')} · verdict: {facts.get('verdict')}")
        self.log.event("trial_end", trial=plan.trial_id, actual=facts.get("actual"), verdict=facts.get("verdict"),
                       ok=facts.get("ok"))
        return timing
