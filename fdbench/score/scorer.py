"""Scores a run folder: per-trial final verdicts (ASR word counts, judge labels, voice change) and run-level metrics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .. import SR, __version__
from ..suites import GROUPS, GROUP_ORDER, KIND_LABELS, RECORD_AFTER, TURN_DURATION_THRESHOLD, TURN_WORDS_THRESHOLD, took_turn
from ..util import load_audio, now_iso, read_json, write_json
from . import asr, metrics, voice
from .judge import Judge

VOICE_FEATURES = ["wpm", "pitch_mean", "pitch_sd", "intensity_mean", "intensity_sd", "utmosv2"]
VOICE_EXPECT = {"wpm": "higher", "pitch_mean": "higher", "pitch_sd": "higher", "intensity_mean": "lower",
                "intensity_sd": "higher", "utmosv2": "no drop"}


def _trial_dirs(run_dir: Path) -> List[Path]:
    t = run_dir / "trials"
    return sorted(p for p in t.iterdir() if p.is_dir()) if t.exists() else []


def _words(chunks, t0, t1):
    return [c for c in chunks if t0 <= c["timestamp"][0] < t1]


class Scorer:
    def __init__(self, run_dir: Path, judge: Optional[Judge] = None, use_asr: bool = True, asr_model: str = "small",
                 with_voice: bool = True, with_utmos: bool = True, progress=None, force: bool = False):
        self.run_dir = Path(run_dir)
        self.judge = judge
        self.use_asr = use_asr and asr.available()
        self.asr_model = asr_model
        self.with_voice = with_voice
        self.with_utmos = with_utmos
        self.progress = progress or (lambda *a, **k: None)
        self.force = force
        self.notes: List[str] = []
        if use_asr and not asr.available():
            self.notes.append("faster-whisper not installed: word counts, transcripts, judge labels and WPM not scored")
        if judge is None:
            self.notes.append("no judge: interruption relevance and v1.5 behaviour labels not scored")

    # ------------------------------------------------------------------ per trial
    def score_trials(self) -> None:
        dirs = _trial_dirs(self.run_dir)
        for i, tdir in enumerate(dirs):
            timing = read_json(tdir / "timing.json")
            if not timing or timing.get("error"):
                continue
            self.progress(i, len(dirs), tdir.name)
            try:
                self._score_trial(tdir, timing)
            except Exception as e:  # noqa: BLE001
                timing["score_error"] = f"{type(e).__name__}: {e}"
                write_json(tdir / "timing.json", timing)
        # v1.5 judge needs both twins: second pass
        if self.judge is not None and self.use_asr:
            self._judge_v15(dirs)

    def _asr_for(self, tdir: Path, name: str) -> Optional[dict]:
        out = tdir / f"asr_{name}.json"
        cached = read_json(out)
        if cached and not self.force:
            return cached
        if not self.use_asr:
            return None
        audio = load_audio(tdir / f"{name}.flac")
        res = asr.transcribe(audio, model=self.asr_model)
        write_json(out, res)
        return res

    def _score_trial(self, tdir: Path, t: dict) -> None:
        g = t["group"]
        her = self._asr_for(tdir, "her")
        chunks = her["chunks"] if her else None
        final = dict(t.get("final", {}))
        bseg = [tuple(s) for s in t.get("bot_segments", [])]

        def rule(window):
            n_words = len(_words(chunks, *window)) if chunks is not None else None
            tor, start, dur = took_turn(bseg, window, n_words)
            return tor, start, dur, n_words

        if g in ("v1_pause_synth", "v1_pause_candor"):
            tor, start, dur, nw = rule((0.0, t["play_first"][1]))
            final.update(tor=tor, words=nw, verdict=("✓ TOR 0" if tor == 0 else "✗ TOR 1"), ok=(tor == 0))
        elif g == "v1_turntaking":
            te = t["turn_end"]
            tor, start, dur, nw = rule((te, te + RECORD_AFTER))
            final.update(tor=tor, words=nw, latency=(max(0.0, start - te) if (tor and start is not None) else None),
                         verdict=(f"✓ {max(0.0, start - te):.2f} s" if tor else "✗ missed"), ok=bool(tor))
        elif g == "false_tt":
            tor, start, dur, nw = rule((0.0, t["play_first"][1] + RECORD_AFTER))
            final.update(tor=tor, words=nw, verdict=("✓ silent" if tor == 0 else "✗ took turn"), ok=(tor == 0))
        elif g == "v1_interrupt":
            if not t.get("overlap_played"):
                final.update(tor=0, verdict="? no answer to first turn", ok=False, unknown=True)
            else:
                o0, o1 = t["overlap"]
                tor, start, dur, nw = rule((o1, o1 + RECORD_AFTER))
                final.update(tor=tor, words=nw, latency=(max(0.0, start - o1) if (tor and start is not None) else None),
                             t_stop=t.get("t_stop"), verdict=(f"✓ yielded, {max(0.0, start - o1):.2f} s" if tor else "✗ no answer"),
                             ok=bool(tor))
                if tor and chunks is not None and self.judge is not None and ("rating" not in final or self.force):
                    resp = " ".join(c["text"] for c in _words(chunks, o1, o1 + RECORD_AFTER + 30))
                    try:
                        r = self.judge.rate_interruption(t["clip_meta"].get("context_text", ""),
                                                         t["clip_meta"].get("interrupt_text", ""), resp)
                        final["rating"] = r["rating"]
                        write_json(tdir / "judge.json", {"task": "interruption_rating", "response_text": resp, **r,
                                                         "judge": self.judge.describe()})
                        if r["rating"] is not None:
                            final["verdict"] += f", relevance {r['rating']}/5"
                    except Exception as e:  # noqa: BLE001
                        final["judge_error"] = str(e)[:200]
        elif g.startswith("v15_") and t["variant"] == "main":
            if not t.get("overlap_played"):
                final.update(label="Unknown", verdict="? no answer to first turn", ok=False, unknown=True)
            else:
                o0, o1 = t["overlap"]
                final.update(t_stop=t.get("t_stop"), t_resp=t.get("t_resp"))
                final.setdefault("label", t.get("label_provisional"))  # only "Unknown" (silent after overlap) without a judge
                if self.with_voice and chunks is not None and t.get("bot_speaking_at_onset"):
                    heard = load_audio(tdir / "her.flac")
                    vc = voice.before_after(heard, bseg, chunks, o0, with_utmos=self.with_utmos)
                    write_json(tdir / "voice.json", vc)
                    final["voice_delta"] = vc["delta"]
        else:  # clean twin
            pass
        if chunks is not None and "transcript" not in final:
            final["transcript"] = her["text"][:400]
        t["final"] = final
        t["scored_at"] = now_iso()
        write_json(tdir / "timing.json", t)

    def _judge_v15(self, dirs: List[Path]) -> None:
        by_name = {d.name: d for d in dirs}
        for tdir in dirs:
            t = read_json(tdir / "timing.json") or {}
            if not (t.get("group", "").startswith("v15_") and t.get("variant") == "main" and t.get("overlap_played")):
                continue
            final = t.get("final", {})
            if final.get("judge_label") and not self.force:
                continue
            twin = by_name.get(tdir.name + "_clean")
            if twin is None:
                continue
            tw = read_json(twin / "timing.json") or {}
            try:
                her_n = read_json(tdir / "asr_her.json") or self._asr_for(tdir, "her")
                her_c = read_json(twin / "asr_her.json") or self._asr_for(twin, "her")
                in_n = self._asr_for(tdir, "played")
                in_c = self._asr_for(twin, "played")
                if not all([her_n, her_c, in_n, in_c]):
                    continue
                r = self.judge.label_behaviour(in_c["chunks"], in_n["chunks"], her_c["chunks"], her_n["chunks"])
                write_json(tdir / "judge.json", {"task": "behaviour", **r, "judge": self.judge.describe()})
                final["judge_label"] = r["label"]
                final["label"] = r["label"]
                want = "Respond" if t["group"] == "v15_interruption" else "Resume"
                final["ok"] = (r["label"] == want)
                final["verdict"] = ("✓ " if final["ok"] else "✗ ") + r["label"]
            except Exception as e:  # noqa: BLE001
                final["judge_error"] = str(e)[:200]
            t["final"] = final
            write_json(tdir / "timing.json", t)

    # ------------------------------------------------------------------ run level
    def aggregate(self) -> dict:
        trials = []
        for tdir in _trial_dirs(self.run_dir):
            t = read_json(tdir / "timing.json")
            if t and not t.get("error"):
                trials.append(t)

        def fin(t, key, default=None):
            return t.get("final", {}).get(key, t.get(key, default))

        by = {g: [t for t in trials if t["group"] == g and t.get("variant", "main") == "main"] for g in GROUP_ORDER}
        res: Dict[str, dict] = {}

        for g in ("v1_pause_synth", "v1_pause_candor"):
            res[f"{g}.tor"] = metrics.rate([fin(t, "tor", 0) for t in by[g]])
        tt = by["v1_turntaking"]
        res["v1_turntaking.tor"] = metrics.rate([fin(t, "tor", 0) for t in tt])
        res["v1_turntaking.missed_rate"] = metrics.rate([0 if fin(t, "tor", 0) else 1 for t in tt])
        res["v1_turntaking.latency"] = metrics.latency([fin(t, "latency") for t in tt if fin(t, "tor", 0)])
        ft = by["false_tt"]
        res["false_tt.rate"] = metrics.rate([fin(t, "tor", 0) for t in ft])
        for kind in GROUPS["false_tt"].kinds:
            sub = [t for t in ft if t.get("clip_meta", {}).get("kind") == kind]
            res[f"false_tt.rate.{kind}"] = metrics.rate([fin(t, "tor", 0) for t in sub])
        it = [t for t in by["v1_interrupt"] if t.get("overlap_played")]
        res["v1_interrupt.tor"] = metrics.rate([fin(t, "tor", 0) for t in it])
        res["v1_interrupt.latency"] = metrics.latency([fin(t, "latency") for t in it if fin(t, "tor", 0)])
        ratings = [fin(t, "rating") for t in it if fin(t, "rating") is not None]
        res["v1_interrupt.rating"] = {"n": len(ratings), "mean": (float(np.mean(ratings)) if ratings else None)}
        res["v1_interrupt.t_stop"] = metrics.latency([fin(t, "t_stop") for t in it])
        res["v1_interrupt.not_played"] = len(by["v1_interrupt"]) - len(it)

        for g in ("v15_interruption", "v15_backchannel", "v15_talking_to_other", "v15_background"):
            played = [t for t in by[g] if t.get("overlap_played")]
            # Content labels need the judge. Without it, only the timing classes are reported.
            judged = [t for t in played if fin(t, "judge_label") or fin(t, "label") == "Unknown"]
            if any(fin(t, "judge_label") for t in played):
                labels = [fin(t, "label") for t in judged] + ["Unknown"] * (len(by[g]) - len(played))
                for lab in ("Respond", "Resume", "Uncertain", "Unknown"):
                    res[f"{g}.{lab.lower()}"] = metrics.share(labels, lab)
            else:
                for lab in ("Respond", "Resume", "Uncertain", "Unknown"):
                    res[f"{g}.{lab.lower()}"] = {"value": None, "n": 0}
            classes = [t.get("overlap_class") for t in played]
            for c in ("through", "paused", "stopped"):
                res[f"{g}.class.{c}"] = metrics.rate([1 if x == c else 0 for x in classes])
            res[f"{g}.t_stop"] = metrics.latency([fin(t, "t_stop") for t in played if not t.get("spoke_through")])
            res[f"{g}.t_resp"] = metrics.latency([fin(t, "t_resp") for t in played])
            res[f"{g}.judged"] = sum(1 for t in played if fin(t, "judge_label"))
            res[f"{g}.n"] = len(by[g])

        # voice change: v1.5 interruption clips labelled Respond (paper), fall back to all with voice data
        vi = [t for t in by["v15_interruption"] if fin(t, "voice_delta")]
        resp = [t for t in vi if (fin(t, "label") or "").startswith("Respond")]
        pool = resp if resp else vi
        res["voice.basis"] = "Respond-labelled interruption clips" if resp else "all interruption clips with voice data"
        for f in VOICE_FEATURES:
            res[f"voice.{f}"] = metrics.paired_t([fin(t, "voice_delta", {}).get(f) for t in pool])
            res[f"voice.{f}"]["expected"] = VOICE_EXPECT[f]

        summary = {
            "fdbench": __version__, "scored_at": now_iso(), "trials": len(trials),
            "per_group": {g: len(v) for g, v in by.items()},
            "notes": self.notes,
            "judge": (self.judge.describe() if self.judge else None),
            "asr": (f"faster-whisper/{self.asr_model}" if self.use_asr else None),
            "metrics": res,
        }
        return summary
