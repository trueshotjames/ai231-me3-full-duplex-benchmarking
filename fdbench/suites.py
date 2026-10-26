"""The test groups (strata): what each plays, what the paper expects, and the provisional verdict from timing.

Final verdicts (word counts, judge labels, voice change) are computed in score/scorer.py.
Paper rules (Lin et al. 2025, released code): a reply counts as taking the turn when it lasts >= 1 s,
or when it is shorter but has more than 3 words.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from . import SR
from .datasets import Clip
from .rig.base import Recording, TrialScript
from .rig.vad import merge_segments, speech_segments

TURN_DURATION_THRESHOLD = 1.0   # s, paper
TURN_WORDS_THRESHOLD = 3        # words, paper
OVERLAP_AFTER_BOT_START = 1.5   # s, our live adaptation
RECORD_AFTER = 8.0              # s to wait for a reply (window for TOR)
SETTLE_SILENCE = 1.2            # s of bot silence that ends a trial early


@dataclass
class Group:
    key: str
    title: str
    paper: str                   # "v1" | "v1.5" | "ours"
    per_round: int
    expected: str                # plain-words expectation shown in the live table
    better: str                  # what a good assistant does, for the report
    kinds: List[str] = field(default_factory=list)  # sub-kinds rotated per round (false_tt)
    paired: bool = False         # v1.5: each clip is played twice (overlap + clean twin)


GROUPS: Dict[str, Group] = {g.key: g for g in [
    Group("v1_pause_synth", "v1 pause (synthetic)", "v1", 1, "stay silent through the pause", "TOR lower"),
    Group("v1_pause_candor", "v1 pause (Candor)", "v1", 1, "stay silent through the pause", "TOR lower"),
    Group("v1_turntaking", "v1 turn-taking (Candor)", "v1", 1, "reply within 8 s", "TOR higher, latency lower"),
    Group("v1_interrupt", "v1 interruption", "v1", 1, "stop, then answer the new request", "TOR higher, relevance higher"),
    Group("false_tt", "false turn-taking", "ours", 2, "stay silent", "rate lower",
          kinds=["side_talk", "tv_radio", "background", "noise"]),
    Group("v15_interruption", "v1.5 interruption", "v1.5", 1, "stop, answer the new request (Respond)", "Respond higher"),
    Group("v15_backchannel", "v1.5 backchannel", "v1.5", 1, "keep talking (Resume)", "Resume higher"),
    Group("v15_talking_to_other", "v1.5 talking to others", "v1.5", 1, "keep talking (Resume)", "Resume higher"),
    Group("v15_background", "v1.5 background speech", "v1.5", 1, "keep talking (Resume)", "Resume higher"),
]}
for _g in GROUPS.values():
    if _g.key.startswith("v15_"):
        _g.paired = True

GROUP_ORDER = list(GROUPS.keys())
KIND_LABELS = {"side_talk": "side talk", "tv_radio": "TV / radio", "background": "background speech", "noise": "noise"}


# ------------------------------------------------------------------ scripts

@dataclass
class TrialPlan:
    trial_id: str
    group: str
    clip: Clip
    variant: str = "main"        # "main" | "clean" (v1.5 twin)
    script: Optional[TrialScript] = None
    expected: str = ""

    def details(self) -> tuple:
        return self.clip.details(self.variant)

    def describe_clip(self) -> str:
        d = self.clip.describe()
        if self.variant == "clean":
            d = f"{self.clip.id} · clean twin (no overlap)"
        return d


def make_script(plan: TrialPlan, on_event=None) -> TrialScript:
    g, c = plan.group, plan.clip
    if g in ("v1_pause_synth", "v1_pause_candor"):
        a = c.audio("main")
        return TrialScript(play_first=a, record_after=4.0, end_on_silence=SETTLE_SILENCE,
                           max_duration=len(a) / SR + 25, on_event=on_event, tag="pause")
    if g == "v1_turntaking":
        a = c.audio("main")
        tail = max(0.0, RECORD_AFTER - (len(a) / SR - c.meta["turn_end"]))
        return TrialScript(play_first=a, record_after=tail, end_on_silence=SETTLE_SILENCE,
                           max_duration=len(a) / SR + 30, on_event=on_event, tag="turn")
    if g == "false_tt":
        a = c.audio("main")
        return TrialScript(play_first=a, record_after=RECORD_AFTER, end_on_silence=SETTLE_SILENCE,
                           max_duration=len(a) / SR + 30, on_event=on_event, tag="foreign")
    if g == "v1_interrupt":
        ctx, inter = c.audio("context"), c.audio("interrupt")
        return TrialScript(play_first=ctx, overlap=inter, overlap_after_bot_start=OVERLAP_AFTER_BOT_START,
                           overlap_fallback_t=None, record_after=RECORD_AFTER, end_on_silence=SETTLE_SILENCE,
                           max_duration=len(ctx) / SR + 45, on_event=on_event, tag="interrupt")
    if g.startswith("v15_"):
        turn = c.audio("turn")
        if plan.variant == "clean":
            return TrialScript(play_first=turn, record_after=RECORD_AFTER, end_on_silence=SETTLE_SILENCE,
                               max_duration=len(turn) / SR + 30, on_event=on_event, tag="turn")
        ov = c.audio("overlap")
        return TrialScript(play_first=turn, overlap=ov, overlap_after_bot_start=OVERLAP_AFTER_BOT_START,
                           overlap_fallback_t=None, record_after=RECORD_AFTER, end_on_silence=SETTLE_SILENCE,
                           max_duration=len(turn) / SR + 45, on_event=on_event, tag=g)
    raise KeyError(g)


def expected_text(plan: TrialPlan) -> str:
    if plan.variant == "clean":
        return "answer the turn (reference for the judge)"
    return GROUPS[plan.group].expected


# ------------------------------------------------------------- timing facts

def bot_segments(rec: Recording) -> List[tuple]:
    """Offline Silero VAD on the assistant track, merged with the paper's 0.5 s gap."""
    return merge_segments(speech_segments(rec.heard), 0.5)


def user_segments(rec: Recording) -> List[tuple]:
    return merge_segments(speech_segments(rec.played), 0.6)


def took_turn(segs: List[tuple], window: tuple, n_words: Optional[int] = None) -> tuple:
    """Paper's rule inside a time window. Returns (TOR 0/1, first_start or None, spoken_duration)."""
    w0, w1 = window
    inside = [(max(s, w0), min(e, w1)) for s, e in segs if e > w0 and s < w1]
    inside = [(s, e) for s, e in inside if e > s]
    if not inside:
        return 0, None, 0.0
    first = inside[0][0]
    duration = inside[-1][1] - inside[0][0]
    if duration >= TURN_DURATION_THRESHOLD:
        return 1, first, duration
    if n_words is not None and n_words > TURN_WORDS_THRESHOLD:
        return 1, first, duration
    return 0, first, duration


def overlap_timing(user_seg, bot_seg, overlap_window) -> dict:
    """t_stop (overlap onset -> bot audio stops) and t_resp (overlap end -> bot speaks again), paper's get_timing.py.

    The onset/end are the VAD-detected speech inside the played overlap window (the paper runs Silero on the user
    track too), falling back to the play window for non-speech overlaps (noise)."""
    p0, p1 = overlap_window
    inside = [(s, e) for s, e in user_seg if e > p0 - 0.05 and s < p1 + 0.05]
    o0 = max(p0, inside[0][0]) if inside else p0
    o1 = min(p1, inside[-1][1]) if inside else p1
    # stop: the bot segment that is running at overlap onset
    t_stop = None
    running = [(s, e) for s, e in bot_seg if s <= o0 + 0.05 and e > o0]
    if running:
        t_stop = running[0][1] - o0
    # response: first bot segment starting after the overlap end
    t_resp = None
    after = [s for s, e in bot_seg if s > o1]
    if after:
        t_resp = after[0] - o1
    bot_speaking_at_onset = bool(running)
    spoke_through = bool(running) and running[0][1] >= o1 - 0.05
    return {"t_stop": t_stop, "t_resp": t_resp, "bot_speaking_at_onset": bot_speaking_at_onset,
            "spoke_through": spoke_through, "overlap": [o0, o1]}


def provisional(plan: TrialPlan, rec: Recording) -> dict:
    """Timing-only facts and a provisional verdict, right after the trial (no ASR, no judge)."""
    g, c = plan.group, plan.clip
    bseg = bot_segments(rec)
    first = rec.play_window("first")
    out: dict = {"bot_segments": [[round(s, 3), round(e, 3)] for s, e in bseg], "play_first": first}
    clip_end = first[1]

    if g in ("v1_pause_synth", "v1_pause_candor"):
        tor, start, dur = took_turn(bseg, (0.0, clip_end))
        p = c.meta.get("pause", [0, 0])
        in_pause = any(p[0] <= s < p[1] for s, _ in bseg)
        out.update(tor=tor, first_start=start, spoke_s=dur, cut_in_during_pause=in_pause,
                   actual=("silent during clip" if dur == 0 else f"spoke {dur:.1f} s at {start:.1f} s"
                           + (" (in the pause)" if in_pause else "")),
                   verdict=("✓ TOR 0" if tor == 0 else "✗ TOR 1"), ok=(tor == 0))
        if tor == 0 and dur > 0:
            out["verdict"] = "✓ TOR 0?"  # short speech: word count decides
    elif g == "v1_turntaking":
        turn_end = first[0] + c.meta["turn_end"]
        tor, start, dur = took_turn(bseg, (turn_end, turn_end + RECORD_AFTER))
        lat = max(0.0, start - turn_end) if start is not None else None
        out.update(tor=tor, first_start=start, spoke_s=dur, latency=lat, turn_end=turn_end,
                   actual=("no reply" if start is None else f"replied after {lat:.2f} s, spoke {dur:.1f} s"),
                   verdict=(f"✓ {lat:.2f} s" if tor else ("✗ missed" if start is None else "✗ too short?")), ok=bool(tor))
    elif g == "false_tt":
        tor, start, dur = took_turn(bseg, (0.0, clip_end + RECORD_AFTER))
        out.update(tor=tor, first_start=start, spoke_s=dur,
                   actual=("silent" if start is None else f"spoke {dur:.1f} s after {start - clip_end:+.1f} s"),
                   verdict=("✓ silent" if tor == 0 else "✗ took turn"), ok=(tor == 0))
        if tor == 0 and dur > 0:
            out["verdict"] = "✓ silent?"
    elif g == "v1_interrupt" or (g.startswith("v15_") and plan.variant == "main"):
        ov = rec.play_window("overlap")
        if ov is None:
            out.update(tor=0, actual="she never answered the first turn; overlap not played", verdict="? no answer",
                       ok=False, overlap_played=False)
            return out
        ot = overlap_timing(user_segments(rec), bseg, ov)
        tor, start, dur = took_turn(bseg, (ov[1], ov[1] + RECORD_AFTER))
        out.update(overlap_played=True, **ot, tor=tor, first_start=start, spoke_s=dur,
                   latency=(max(0.0, start - ov[1]) if start is not None else None))
        stop_txt = (f"stopped after {ot['t_stop']:.2f} s" if ot["t_stop"] is not None and not ot["spoke_through"]
                    else ("kept talking" if ot["bot_speaking_at_onset"] else "was already silent"))
        back_txt = (f", back {ot['t_resp']:.2f} s after" if ot["t_resp"] is not None else ", did not come back")
        out["actual"] = stop_txt + back_txt
        if g == "v1_interrupt":
            out["verdict"] = (f"✓ yielded, {out['latency']:.2f} s" if tor else "✗ no answer")
            out["ok"] = bool(tor)
        else:
            # Live verdict is timing only. The v1.5 label (Respond / Resume / Uncertain / Unknown) is about the
            # CONTENT of what she says after the overlap and is set by the judge at scoring. Pausing and then
            # continuing the original answer is Resume; pausing and addressing the overlap is Respond.
            want_respond = g == "v15_interruption"
            if ot["spoke_through"]:
                timing = "through"
            elif ot["t_resp"] is not None:
                timing = "paused"
            else:
                timing = "stopped"
            out["overlap_class"] = timing
            out["label_provisional"] = "Unknown" if timing == "stopped" else None  # no speech after overlap = C_UNKNOWN
            if timing == "through":
                out["verdict"], out["ok"] = (("✗ kept talking", False) if want_respond else ("✓ kept talking", True))
            elif timing == "paused":
                out["verdict"], out["ok"] = ("◐ " + ("replied" if want_respond else "paused, came back")
                                             + " · content: judge", None)
            else:
                out["verdict"], out["ok"] = "✗ stopped, silent", False
    else:  # v1.5 clean twin
        tor, start, dur = took_turn(bseg, (clip_end, clip_end + RECORD_AFTER))
        out.update(tor=tor, first_start=start, spoke_s=dur,
                   actual=("no reply" if start is None else f"replied after {start - clip_end:.2f} s, spoke {dur:.1f} s"),
                   verdict=("✓ answered" if tor else "✗ no answer"), ok=bool(tor))
    return out
