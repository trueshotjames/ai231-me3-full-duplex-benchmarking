"""Stage latency, WER and context size from the assistant's stage marks (course template, section 5).

All stage-to-stage times use the marks' own `t`: the assistant machine's monotonic clock (one clock, as the template
asks). `tt` (seconds from trial start, on fdbench's audio clock) is used only to find the marks of the turn and for
the VAD endpointing delay (VAD end vs. the true end of speech in the played audio).
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Dict, List, Optional

from ..util import read_json
from . import metrics
from .wer import corpus, wer


def read_marks(tdir: Path) -> List[dict]:
    import json
    p = tdir / "stages.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return sorted(out, key=lambda m: float(m["t"]))


def _first(ms, stage, t_after=None, t_before=None):
    for m in ms:
        if m["stage"] == stage and (t_after is None or m["t"] >= t_after) and (t_before is None or m["t"] < t_before):
            return m
    return None


def _last(ms, stage, t_after=None, t_before=None):
    hit = None
    for m in ms:
        if m["stage"] == stage and (t_after is None or m["t"] >= t_after) and (t_before is None or m["t"] < t_before):
            hit = m
    return hit


def _d(a, b) -> Optional[float]:
    return round(float(b["t"]) - float(a["t"]), 4) if a is not None and b is not None else None


_SETTLES = ("llm_cancel", "llm_request", "user_stopped", "audio_out")


def answering_requests(ms: List[dict]) -> List[dict]:
    """The LLM calls that answer a final transcript, as the template's context size means.

    A normal call counts. An early (speculative) call, started on a partial transcript before the turn ended, counts
    only when the turn end confirms it: the next of llm_cancel / llm_request / user_stopped / audio_out is a turn
    end, so its reply is the one that was spoken and its context holds the final transcript. A call that was
    cancelled or replaced answered nothing and is left out."""
    out = []
    for i, m in enumerate(ms):
        if m["stage"] != "llm_request":
            continue
        if not m.get("speculative"):
            out.append(m)
            continue
        nxt = next((x for x in ms[i + 1:] if x["stage"] in _SETTLES), None)
        if nxt is not None and nxt["stage"] in ("user_stopped", "audio_out"):
            out.append(m)
    return out


def turn_stages(ms: List[dict], user_end: Optional[float], bot_start: Optional[float], ref_text: str = "") -> dict:
    """One clean user turn. user_end / bot_start: trial seconds from the audio (fdbench's VAD)."""
    out: dict = {"e2e_heard": (round(bot_start - user_end, 4) if (bot_start is not None and user_end is not None) else None)}
    if not ms:
        return out
    ue = user_end if user_end is not None else 0.0
    # the reply: first audio_out after the user's speech ended (small slack for clock mapping)
    reply = next((m for m in ms if m["stage"] == "audio_out" and m.get("tt", 0) > ue - 0.2), None)
    t_reply = reply["t"] if reply else None
    vad = _last(ms, "vad_end", t_before=t_reply) or _last(ms, "user_stopped", t_before=t_reply)
    if vad is not None and vad.get("tt", 0) < ue - 2.0:  # an older turn's VAD end: ignore
        vad = None
    normal = [m for m in ms if not m.get("speculative")]
    req = _first(normal, "llm_request", t_after=(vad["t"] if vad else None), t_before=t_reply)
    finals = [m for m in ms if m["stage"] == "asr_final" and m.get("tt", 0) >= -0.5
              and (req is None or m["t"] <= req["t"]) and (t_reply is None or m["t"] < t_reply)]
    asr = finals[-1] if finals else None
    after = max([x["t"] for x in (asr, req) if x is not None], default=None)
    tok = _first(ms, "llm_first_token", t_after=after, t_before=t_reply)
    tts_req = _first(ms, "tts_request", t_after=(tok["t"] - 0.001 if tok else after), t_before=t_reply)
    tts_aud = _first(ms, "tts_first_audio", t_after=(tts_req["t"] if tts_req else None), t_before=t_reply)
    asr_lat = _d(vad, asr)
    out.update({
        "e2e": _d(vad, reply), "e2e_source": ("marks" if (vad and reply) else None),
        "asr_final": (max(0.0, asr_lat) if asr_lat is not None else None),
        "llm_ttft": _d(asr, tok) if asr is not None else _d(req, tok),
        "tts_ttfb": _d(tts_req, tts_aud),
        "endpointing": (round(vad["tt"] - user_end, 4) if (vad is not None and user_end is not None and "tt" in vad) else None),
        "found": [s for s, m in (("vad_end", vad), ("asr_final", asr), ("llm_request", req), ("llm_first_token", tok),
                                  ("tts_request", tts_req), ("tts_first_audio", tts_aud), ("audio_out", reply)) if m],
    })
    if out["e2e"] is None and vad is not None and bot_start is not None and "tt" in vad:
        out["e2e"], out["e2e_source"] = round(bot_start - vad["tt"], 4), "audio"
    hyp = " ".join(str(m.get("text", "")) for m in finals).strip()
    if finals:
        out["transcript"] = hyp
        if ref_text:
            out["wer"] = wer(ref_text, hyp)
    # context size: the call that answered this turn (a confirmed early call counts; see answering_requests)
    prev = _last(ms, "audio_out", t_before=(vad["t"] if vad else None)) if vad else None
    creq = _last(answering_requests(ms), "llm_request", t_after=(prev["t"] if prev else None), t_before=t_reply)
    if creq is not None:
        req = creq
        usage = _first(ms, "llm_usage", t_after=req["t"])
        if usage is not None and usage.get("prompt_tokens"):
            out["context_tokens"], out["context_source"] = usage["prompt_tokens"], "usage"
        elif req.get("prompt_tokens"):
            out["context_tokens"], out["context_source"] = req["prompt_tokens"], "reported"
        elif req.get("est_tokens"):
            out["context_tokens"], out["context_source"] = req["est_tokens"], "estimate"
        if req.get("messages") is not None:
            out["context_messages"] = req["messages"]
    return out


def context_calls(ms: List[dict]) -> List[dict]:
    """Every LLM call that answered a final transcript, with its context size (usage if reported, else the
    request's own figures). Discarded early calls are left out (answering_requests)."""
    calls = []
    keep = {id(m) for m in answering_requests(ms)}
    for i, m in enumerate(ms):
        if id(m) not in keep:
            continue
        nxt = next((x for x in ms[i + 1:] if x["stage"] == "llm_request"), None)
        usage = next((x for x in ms[i + 1:] if x["stage"] == "llm_usage" and (nxt is None or x["t"] < nxt["t"])), None)
        tokens, src = None, None
        if usage is not None and usage.get("prompt_tokens"):
            tokens, src = usage["prompt_tokens"], "usage"
        elif m.get("prompt_tokens"):
            tokens, src = m["prompt_tokens"], "reported"
        elif m.get("est_tokens"):
            tokens, src = m["est_tokens"], "estimate"
        calls.append({"t": m["t"], "tokens": tokens, "source": src, "messages": m.get("messages")})
    return calls


def tool_calls(ms: List[dict]) -> List[dict]:
    open_: Dict[str, List[dict]] = {}
    out = []
    for m in ms:
        if m["stage"] == "tool_start":
            open_.setdefault(m.get("name", ""), []).append(m)
        elif m["stage"] == "tool_end":
            q = open_.get(m.get("name", ""))
            if q:
                s = q.pop(0)
                out.append({"name": m.get("name", ""), "duration": round(m["t"] - s["t"], 4)})
    return out


def _lat(vals):
    return metrics.latency([v for v in vals if v is not None])


def aggregate(trials: List[dict], run_dir: Path) -> dict:
    """Course metrics from scored trials. Writes stage_latency.json (every turn) next to results.json."""
    st = [t for t in trials if t["group"] == "stage_latency"]
    rows = []
    for t in st:
        s = (t.get("final") or {}).get("stages") or {}
        run = int(t["trial"][1:3]) if t["trial"][:1] == "r" and t["trial"][1:3].isdigit() else None
        rows.append({"trial": t["trial"], "run": run, "clip": t["clip"], "ref": t.get("clip_meta", {}).get("text", ""),
                     **{k: s.get(k) for k in ("e2e", "e2e_source", "e2e_heard", "endpointing", "asr_final", "llm_ttft",
                                               "tts_ttfb", "transcript", "context_tokens", "context_messages")},
                     "wer": (s.get("wer") or {}).get("wer"), "wer_detail": s.get("wer"), "found": s.get("found", [])})
    res: Dict[str, dict] = {}
    for k in ("e2e", "asr_final", "llm_ttft", "tts_ttfb", "endpointing", "e2e_heard"):
        res[f"course.{k}"] = _lat([r[k] for r in rows])
    srcs = {r["e2e_source"] for r in rows if r.get("e2e_source")}
    res["course.e2e"]["source"] = ("marks" if srcs == {"marks"} else "mixed" if len(srcs) > 1 else
                                   next(iter(srcs), None))
    if res["course.e2e"]["n"] == 0 and res["course.e2e_heard"]["n"]:
        # no marks at all: the template's E2E falls back to the audio (true end of speech -> reply audio)
        res["course.e2e"] = dict(res["course.e2e_heard"], source="audio (no stage marks)")
    res["course.wer"] = corpus([r["wer_detail"] for r in rows if r.get("wer_detail")])

    # barge-in: speech -> playback stopped, from the audio (interruption trials where she was speaking and stopped)
    bi = [t.get("t_stop") for t in trials if t["group"] in ("v1_interrupt", "v15_interruption")
          and t.get("variant", "main") == "main" and t.get("overlap_played") and t.get("bot_speaking_at_onset")
          and not t.get("spoke_through")]
    res["course.barge_in"] = _lat(bi)
    res["course.barge_in"]["kept_talking"] = sum(1 for t in trials if t["group"] in ("v1_interrupt", "v15_interruption")
                                                 and t.get("variant", "main") == "main" and t.get("spoke_through"))

    # context size per LLM call (all trials) and per run of the stage test
    calls, tools, seen = [], [], {}
    for t in trials:
        ms = read_marks(run_dir / "trials" / t["trial"])
        for m in ms:
            seen[m["stage"]] = seen.get(m["stage"], 0) + 1
        calls += context_calls(ms)
        tools += tool_calls(ms)
    calls.sort(key=lambda c: c["t"])
    toks = [c["tokens"] for c in calls if c["tokens"] is not None]
    msgs = [c["messages"] for c in calls if c["messages"] is not None]
    by_run: Dict[int, list] = {}
    for r in rows:
        if r["run"] is not None and r.get("context_tokens") is not None:
            by_run.setdefault(r["run"], []).append(r["context_tokens"])
    res["course.context"] = {
        "n": len(toks), "source": (sorted({c["source"] for c in calls if c["source"]}) or None),
        "tokens_median": (statistics.median(toks) if toks else None), "tokens_max": (max(toks) if toks else None),
        "tokens_first": (toks[0] if toks else None), "tokens_last": (toks[-1] if toks else None),
        "messages_median": (statistics.median(msgs) if msgs else None), "messages_max": (max(msgs) if msgs else None),
        "messages_first": (msgs[0] if msgs else None), "messages_last": (msgs[-1] if msgs else None),
        "per_run_median_tokens": {str(k): statistics.median(v) for k, v in sorted(by_run.items())},
    }
    res["course.tool_calls"] = _lat([x["duration"] for x in tools])
    res["course.tool_calls"]["by_name"] = {n: len([x for x in tools if x["name"] == n]) for n in sorted({x["name"] for x in tools})}
    res["course.stages_seen"] = seen
    runs = sorted({r["run"] for r in rows if r["run"] is not None})
    res["course.per_run"] = {str(k): {m: _lat([r[m] for r in rows if r["run"] == k])
                                      for m in ("e2e", "asr_final", "llm_ttft", "tts_ttfb")} for k in runs}
    res["course.phrases"] = len({r["clip"] for r in rows})
    res["course.runs"] = len(runs)
    res["course.turns"] = len(rows)
    from ..util import write_json
    write_json(run_dir / "stage_latency.json", {
        "about": "Stage latency per turn (course template, section 5). Times in seconds on the assistant machine's "
                 "monotonic clock; e2e = audio_out - vad_end; e2e_heard = reply audio start - true end of speech "
                 "(fdbench's audio clock); endpointing = vad_end - true end of speech.",
        "phrases": res["course.phrases"], "runs": res["course.runs"], "turns": rows,
        "per_run": res["course.per_run"]})
    return res
