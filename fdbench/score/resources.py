"""Machine resources during a run: summaries per run, per test group and per trial, latency vs load, flagged trials.

Input: run_dir/pi_stats.jsonl and pi_stats_hello.json (written by the runner from what the launcher sent, see
stats.py), and each trial's `mono` window in timing.json. Output: the `resources` block of results.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..probe import NOW_FLAGS, PROC_LABELS
from ..suites import GROUP_ORDER
from ..util import read_json

MAX_TIMELINE_POINTS = 900
LATENCY_KEYS = {"v1_turntaking": "latency", "v1_interrupt": "latency", "v15_interruption": "t_resp",
                "v15_backchannel": "t_resp", "v15_talking_to_other": "t_resp", "v15_background": "t_resp"}


def load_samples(run_dir: Path) -> tuple:
    """(hello, samples) with `lt` recomputed from the final clock offset."""
    hello = read_json(run_dir / "pi_stats_hello.json")
    path = run_dir / "pi_stats.jsonl"
    samples: List[dict] = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    continue
    off = (hello or {}).get("clock_offset")
    if off is not None:
        for s in samples:
            s["lt"] = float(s["t"]) + off
    samples.sort(key=lambda s: s.get("lt", s.get("t", 0)))
    return hello, samples


def _ordered_labels(samples) -> List[str]:
    """Process labels, the assistant first, then the MCP servers in a fixed order, then anything else."""
    found = {k for s in samples for k in (s.get("procs") or {})}
    order = list(PROC_LABELS)
    return sorted(found, key=lambda k: (order.index(k) if k in order else len(order), k))


def _vals(samples, key) -> List[float]:
    return [float(s[key]) for s in samples if s.get(key) is not None]


def _pct(v: List[float], q: float) -> Optional[float]:
    return float(np.percentile(v, q)) if v else None


def _throttled_now(s: dict) -> Optional[bool]:
    thr = s.get("throttled")
    if not thr:
        return None
    return any(thr.get(k) for k in NOW_FLAGS)


def summarize(samples: List[dict]) -> dict:
    """Stats over a list of samples (a whole run, a test group, or one trial)."""
    cpu = _vals(samples, "cpu")
    busiest = [max(s["cores"]) for s in samples if s.get("cores")]
    temp = _vals(samples, "temp_c")
    mhz = _vals(samples, "arm_mhz")
    avail = _vals(samples, "mem_avail_mb")
    swap = _vals(samples, "swap_used_mb")
    thr = [x for x in (_throttled_now(s) for s in samples) if x is not None]
    flags = {k: sum(1 for s in samples if (s.get("throttled") or {}).get(k)) for k in NOW_FLAGS}
    procs: Dict[str, dict] = {}
    for lab in _ordered_labels(samples):
        c = [s["procs"][lab]["cpu"] for s in samples if lab in (s.get("procs") or {}) and s["procs"][lab].get("cpu") is not None]
        r = [s["procs"][lab]["rss_mb"] for s in samples if lab in (s.get("procs") or {})]
        procs[lab] = {"label": PROC_LABELS.get(lab, lab), "cpu_mean": (float(np.mean(c)) if c else None),
                      "cpu_p95": _pct(c, 95), "rss_peak_mb": (max(r) if r else None)}
    return {
        "n": len(samples),
        "seconds": (float(samples[-1].get("lt", 0) - samples[0].get("lt", 0)) if len(samples) > 1 else 0.0),
        "cpu_median": _pct(cpu, 50), "cpu_p95": _pct(cpu, 95), "cpu_max": (max(cpu) if cpu else None),
        "cpu_mean": (float(np.mean(cpu)) if cpu else None),
        "busiest_core_p95": _pct(busiest, 95),
        "temp_max": (max(temp) if temp else None), "temp_median": _pct(temp, 50),
        "mhz_min": (min(mhz) if mhz else None), "mhz_median": _pct(mhz, 50),
        "mem_avail_min_mb": (min(avail) if avail else None), "swap_used_max_mb": (max(swap) if swap else None),
        "throttle_samples": len(thr), "throttled_share": (sum(thr) / len(thr) if thr else None),
        "throttled_any": (any(thr) if thr else None), "flag_counts": flags, "procs": procs,
    }


def _new_ever_flags(samples: List[dict]) -> List[str]:
    """Since-boot flags that were off in the first sample and on later: they happened during the run."""
    withthr = [s["throttled"] for s in samples if s.get("throttled")]
    if not withthr:
        return []
    first, out = withthr[0], []
    for k in first:
        if k.endswith("_ever") and not first.get(k) and any(t.get(k) for t in withthr[1:]):
            out.append(k)
    return out


def _timeline(samples: List[dict], t0: float) -> dict:
    labels = _ordered_labels(samples)
    step = max(1, int(np.ceil(len(samples) / MAX_TIMELINE_POINTS)))
    pts = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]

        def mean(key):
            v = _vals(chunk, key)
            return round(float(np.mean(v)), 2) if v else None

        p = {"t": round(float(chunk[0]["lt"]) - t0, 2), "cpu": mean("cpu"), "temp": mean("temp_c"), "mhz": mean("arm_mhz"),
             "thr": any(_throttled_now(s) for s in chunk)}
        procs = {}
        for lab in labels:
            c = [s["procs"][lab]["cpu"] for s in chunk if lab in (s.get("procs") or {}) and s["procs"][lab].get("cpu") is not None]
            procs[lab] = round(float(np.mean(c)), 2) if c else None
        p["procs"] = procs
        pts.append(p)
    return {"labels": labels, "points": pts}


def aggregate(run_dir: Path, trials: List[dict]) -> Optional[dict]:
    """The `resources` block of results.json, or None if the run has no stats."""
    hello, samples = load_samples(Path(run_dir))
    if not samples:
        return None if hello is None else {"hello": hello, "available": False}
    samples = [s for s in samples if "lt" in s]
    windows = [t for t in trials if t.get("mono")]
    t0 = min([t["mono"][0] for t in windows] + [samples[0]["lt"]])

    def in_window(t):
        a, b = t["mono"]
        return [s for s in samples if a <= s["lt"] <= b]

    per_trial = []
    for t in windows:
        sm = summarize(in_window(t))
        fin = t.get("final", {})
        lat_key = LATENCY_KEYS.get(t["group"])
        lat = fin.get(lat_key, t.get(lat_key)) if lat_key and t.get("variant", "main") == "main" else None
        per_trial.append({"trial": t["trial"], "group": t["group"], "variant": t.get("variant", "main"),
                          "start": round(t["mono"][0] - t0, 2), "end": round(t["mono"][1] - t0, 2),
                          "n": sm["n"], "cpu_mean": sm["cpu_mean"], "busiest_core_p95": sm["busiest_core_p95"],
                          "temp_max": sm["temp_max"], "mhz_min": sm["mhz_min"], "throttled": sm["throttled_any"],
                          "under_voltage": bool(sm["flag_counts"].get("under_voltage_now")),
                          "latency": lat, "ok": fin.get("ok", t.get("ok"))})

    per_group = {}
    for g in GROUP_ORDER:
        sel = [s for t in windows if t["group"] == g for s in in_window(t)]
        if sel:
            per_group[g] = summarize(sel)

    pairs = [p for p in per_trial if p["latency"] is not None and p["cpu_mean"] is not None]
    lat_vs_load = {"pairs": [{k: p[k] for k in ("trial", "group", "latency", "cpu_mean", "temp_max", "throttled")} for p in pairs]}
    if len(pairs) >= 5:
        x = np.array([p["cpu_mean"] for p in pairs])
        y = np.array([p["latency"] for p in pairs])
        if x.std() > 0 and y.std() > 0:
            lat_vs_load["r_cpu"] = float(np.corrcoef(x, y)[0, 1])
    thr_l = [p["latency"] for p in pairs if p["throttled"]]
    ok_l = [p["latency"] for p in pairs if p["throttled"] is False]
    lat_vs_load["median_latency_throttled"] = float(np.median(thr_l)) if thr_l else None
    lat_vs_load["median_latency_not_throttled"] = float(np.median(ok_l)) if ok_l else None

    flagged = [p for p in per_trial if p["throttled"] or p["under_voltage"]]
    from ..stats import StatsStore
    first = StatsStore()
    first.samples = samples[:1]
    run = summarize(samples)
    probe_ms = _vals(samples, "probe_ms")
    shares = _vals(samples, "probe_share")
    ncpu = (hello or {}).get("ncpu") or 1
    return {
        "available": True,
        "hello": hello,
        "source": (hello or {}).get("source", "remote"),
        "run": run,
        "new_flags_during_run": _new_ever_flags(samples),
        "start_warnings": first.start_warnings(),
        "probe": {"ms_median": _pct(probe_ms, 50), "share_of_one_core": (shares[-1] if shares else None),
                  "share_of_machine": ((shares[-1] / ncpu) if shares else None)},
        "per_group": per_group,
        "per_trial": per_trial,
        "lat_vs_load": lat_vs_load,
        "flagged": [{k: p[k] for k in ("trial", "group", "throttled", "under_voltage", "temp_max", "latency")} for p in flagged],
        "timeline": _timeline(samples, t0),
        "trial_lanes": [[p["start"], p["end"], p["group"]] for p in per_trial],
    }
