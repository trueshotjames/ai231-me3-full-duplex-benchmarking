"""The "Machine resources" section of results.html: tiles, a timeline, per-group and per-process tables, latency vs
CPU, and trials that ran while throttled. Inline SVG only, like the rest of the report."""
from __future__ import annotations

import html
from typing import List, Optional

from ..probe import PROC_LABELS
from ..suites import GROUPS

PROC_COLORS = {"assistant": "#2f6f5e", "asr_mcp": "#4a7fc1", "tts_mcp": "#c47a2c", "tools_mcp": "#8a63b8",
               "other": "#b5bdb9"}
EXTRA_COLORS = ["#5aa0a0", "#a0705a", "#7a8f3a", "#b0588a"]
GROUP_COLORS = {"v1_pause_synth": "#9aa5a0", "v1_pause_candor": "#6f7a75", "v1_turntaking": "#2f6f5e",
                "v1_interrupt": "#4a7fc1", "false_tt": "#8a63b8", "v15_interruption": "#c47a2c",
                "v15_backchannel": "#d9a441", "v15_talking_to_other": "#b0588a", "v15_background": "#5aa0a0"}
BAD = "#c43d3d"
WARN = "#b7791f"
GOOD = "#1f8a4c"


FLAG_WORDS = {"under_voltage_ever": "Under-voltage", "freq_capped_ever": "ARM frequency capping",
              "throttled_ever": "Throttling", "soft_temp_limit_ever": "The soft temperature limit (80 °C)"}


def _n_trials(n: int) -> str:
    return f"{n} trial" + ("" if n == 1 else "s")


def _f(v, fmt="{:.0f}", unit="", na="—") -> str:
    return na if v is None else fmt.format(v) + unit


def _proc_color(lab: str, i: int) -> str:
    return PROC_COLORS.get(lab, EXTRA_COLORS[i % len(EXTRA_COLORS)])


def _tile(label: str, value: str, sub: str = "", tone: str = "") -> str:
    color = {"bad": BAD, "warn": WARN, "good": GOOD}.get(tone, "#222")
    return (f'<div class="tile"><span>{html.escape(label)}</span><b style="color:{color}">{html.escape(value)}</b>'
            f'<span>{html.escape(sub)}</span></div>')


def _timeline_svg(r: dict) -> str:
    tl = r.get("timeline") or {}
    pts = tl.get("points") or []
    if len(pts) < 2:
        return '<p class="na">Too few samples for a timeline.</p>'
    labels = list(tl.get("labels") or [])
    W, L, R, T, CH = 940, 46, 52, 10, 170
    tmax = max(p["t"] for p in pts) or 1.0
    lanes = r.get("trial_lanes") or []
    tmax = max([tmax] + [b for _a, b, _g in lanes])
    tmin = min([0.0] + [p["t"] for p in pts])
    pw = W - L - R

    def x(t):
        return L + (t - tmin) / (tmax - tmin or 1) * pw

    def yc(v):
        return T + CH - max(0.0, min(100.0, v)) / 100 * CH

    out: List[str] = []
    for v in (0, 25, 50, 75, 100):
        out.append(f'<line x1="{L}" x2="{W-R}" y1="{yc(v):.1f}" y2="{yc(v):.1f}" stroke="#e3e6e4"/>'
                   f'<text x="{L-6}" y="{yc(v)+3:.1f}" text-anchor="end">{v}%</text>')
    # throttle bands
    start = None
    for i, p in enumerate(pts + [{"thr": False, "t": pts[-1]["t"]}]):
        if p.get("thr") and start is None:
            start = p["t"]
        elif not p.get("thr") and start is not None:
            out.append(f'<rect x="{x(start):.1f}" y="{T}" width="{max(2.0, x(p["t"]) - x(start)):.1f}" height="{CH}" '
                       f'fill="{BAD}" fill-opacity="0.14"/>')
            start = None
    # stacked CPU by process; "other" = total minus the labelled processes
    series = []
    for lab in labels:
        series.append((lab, [p["procs"].get(lab) or 0.0 for p in pts]))
    total = [p.get("cpu") or 0.0 for p in pts]
    if labels:
        other = [max(0.0, tot - sum(s[1][i] for s in series)) for i, tot in enumerate(total)]
        series.append(("other", other))
    else:
        series.append(("other", total))
    base = [0.0] * len(pts)
    for i, (lab, vals) in enumerate(series):
        top = [b + v for b, v in zip(base, vals)]
        d = "M" + " L".join(f"{x(p['t']):.1f},{yc(v):.1f}" for p, v in zip(pts, top))
        d += " L" + " L".join(f"{x(p['t']):.1f},{yc(v):.1f}" for p, v in zip(reversed(pts), reversed(base))) + " Z"
        out.append(f'<path d="{d}" fill="{_proc_color(lab, i)}" fill-opacity="0.85"/>')
        base = top
    # temperature (right axis 30–90 °C)
    temps = [(p["t"], p["temp"]) for p in pts if p.get("temp") is not None]
    if temps:
        def yt(v):
            return T + CH - (max(30.0, min(90.0, v)) - 30) / 60 * CH
        d = "M" + " L".join(f"{x(t):.1f},{yt(v):.1f}" for t, v in temps)
        out.append(f'<path d="{d}" fill="none" stroke="#1c2421" stroke-width="1.5"/>')
        out.append(f'<line x1="{L}" x2="{W-R}" y1="{yt(80):.1f}" y2="{yt(80):.1f}" stroke="{WARN}" stroke-dasharray="4 3"/>')
        for v in (40, 60, 80):
            out.append(f'<text x="{W-R+6}" y="{yt(v)+3:.1f}">{v} °C</text>')
    y = T + CH + 8
    # ARM clock strip
    mhz = [(p["t"], p["mhz"]) for p in pts if p.get("mhz")]
    if mhz:
        top_mhz = max(v for _t, v in mhz)
        out.append(f'<text x="{L-6}" y="{y+8}" text-anchor="end">MHz</text>')
        for i, (t, v) in enumerate(mhz):
            t2 = mhz[i + 1][0] if i + 1 < len(mhz) else t + (tmax - tmin) / len(pts)
            low = v < top_mhz - 1
            out.append(f'<rect x="{x(t):.1f}" y="{y}" width="{max(0.8, x(t2) - x(t)):.1f}" height="9" '
                       f'fill="{BAD if low else "#2f6f5e"}" fill-opacity="{0.85 if low else 0.3}"/>')
        out.append(f'<text x="{W-R+6}" y="{y+8}">{top_mhz:.0f}</text>')
        y += 16
    # trial lanes
    out.append(f'<text x="{L-6}" y="{y+9}" text-anchor="end">trials</text>')
    for a, b, g in lanes:
        out.append(f'<rect x="{x(a):.1f}" y="{y}" width="{max(1.5, x(b) - x(a)):.1f}" height="11" rx="2" '
                   f'fill="{GROUP_COLORS.get(g, "#999")}"><title>{html.escape(GROUPS[g].title if g in GROUPS else g)}</title></rect>')
    y += 26
    step = 60 if tmax - tmin <= 900 else 300
    t = 0.0
    while t <= tmax:
        out.append(f'<text x="{x(t):.1f}" y="{y}" text-anchor="middle">{t/60:.0f} min</text>')
        t += step
    H = y + 6
    return (f'<div class="scroll"><svg viewBox="0 0 {W} {H}" style="width:100%;min-width:640px;height:auto" '
            f'role="img" aria-label="CPU by process, temperature, clock and throttling over the run">{"".join(out)}</svg></div>')


def _legend(r: dict) -> str:
    labels = list((r.get("timeline") or {}).get("labels") or []) + ["other"]
    items = [f'<span><i style="background:{_proc_color(lab, i)}"></i>{html.escape(PROC_LABELS.get(lab, lab))}</span>'
             for i, lab in enumerate(labels)]
    items.append(f'<span><i style="background:{BAD};opacity:.3"></i>throttled or capped</span>')
    items.append('<span><i style="background:#1c2421;height:2px;vertical-align:3px"></i>temperature (right axis; dashed = 80 °C)</span>')
    groups = sorted({g for _a, _b, g in (r.get("trial_lanes") or [])}, key=lambda g: list(GROUP_COLORS).index(g) if g in GROUP_COLORS else 99)
    lanes = [f'<span><i style="background:{GROUP_COLORS.get(g, "#999")}"></i>{html.escape(GROUPS[g].title if g in GROUPS else g)}</span>'
             for g in groups]
    return f'<div class="legend">{"".join(items)}</div><div class="legend">Trials: {"".join(lanes)}</div>'


def _scatter_svg(pairs: List[dict]) -> str:
    if not pairs:
        return '<p class="na">No trials with both a latency and CPU samples.</p>'
    W, H, L, B, T, R = 520, 240, 48, 34, 10, 14
    xmax = max(10.0, max(p["cpu_mean"] for p in pairs) * 1.1)
    ymax = max(2.0, max(p["latency"] for p in pairs) * 1.1)

    def x(v):
        return L + v / xmax * (W - L - R)

    def y(v):
        return H - B - v / ymax * (H - B - T)

    out = []
    for v in range(0, int(ymax) + 1):
        out.append(f'<line x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e3e6e4"/>'
                   f'<text x="{L-6}" y="{y(v)+3:.1f}" text-anchor="end">{v} s</text>')
    out.append(f'<line x1="{L}" x2="{W-R}" y1="{y(1):.1f}" y2="{y(1):.1f}" stroke="{GOOD}" stroke-dasharray="3 2"/>')
    xs = 10 if xmax <= 60 else 25
    v = 0
    while v <= xmax:
        out.append(f'<text x="{x(v):.1f}" y="{H-B+14}" text-anchor="middle">{v}%</text>')
        v += xs
    out.append(f'<text x="{(L+W-R)/2:.0f}" y="{H-4}" text-anchor="middle">mean CPU during the trial (all cores)</text>')
    for p in pairs:
        c = BAD if p.get("throttled") else GROUP_COLORS.get(p["group"], "#555")
        out.append(f'<circle cx="{x(p["cpu_mean"]):.1f}" cy="{y(p["latency"]):.1f}" r="4" fill="{c}" fill-opacity="0.85">'
                   f'<title>{html.escape(p["trial"])}: {p["latency"]:.2f} s at {p["cpu_mean"]:.0f}% CPU'
                   f'{" (throttled)" if p.get("throttled") else ""}</title></circle>')
    return (f'<div class="scroll"><svg viewBox="0 0 {W} {H}" style="width:100%;max-width:{W}px;min-width:360px;height:auto" '
            f'role="img" aria-label="Latency against CPU per trial">{"".join(out)}</svg></div>')


def render(r: Optional[dict]) -> str:
    head = '<h2>Machine resources</h2>'
    if not r:
        return (head + '<p class="na">No machine stats in this run. With the direct feed, an up-to-date '
                'launch_assistant.py sends them by itself; for --sim or virtual cables pass --host-stats.</p>')
    hello = r.get("hello") or {}
    if not r.get("available"):
        return head + '<p class="na">The launcher connected but sent no samples.</p>'
    run = r["run"]
    caps = hello.get("caps") or {}
    who = hello.get("pi_model") or f'{hello.get("os", "?")} ({hello.get("machine", "?")})'
    src = "sent by the launcher on her machine" if r.get("source") == "remote" else "sampled on this machine (--host-stats)"
    parts = [head, f'<p><b>{html.escape(str(who))}</b> · {hello.get("ncpu", "?")} cores · {html.escape(src)} · '
                   f'{run["n"]} samples over {run["seconds"]/60:.1f} min</p>']
    warns = list(r.get("start_warnings") or [])
    for k in r.get("new_flags_during_run") or []:
        warns.append(f"{FLAG_WORDS.get(k, k)} happened during this run")
    if warns:
        parts.append('<ul class="warn">' + "".join(f"<li>{html.escape(w)}</li>" for w in warns) + "</ul>")

    # tiles
    t = []
    if caps.get("temperature") or run["temp_max"] is not None:
        tm = run["temp_max"]
        t.append(_tile("Peak temperature", _f(tm, "{:.1f}", " °C"), "Pi 4 soft limit 80 °C",
                       "bad" if (tm or 0) >= 80 else "warn" if (tm or 0) >= 70 else "good"))
    if caps.get("throttle") or run["throttled_share"] is not None:
        sh = run["throttled_share"]
        t.append(_tile("Time throttled or capped", _f(sh * 100 if sh is not None else None, "{:.1f}", "%"),
                       _n_trials(sum(1 for p in r["per_trial"] if p["throttled"])) + " affected",
                       "bad" if sh else "good"))
        uv = run["flag_counts"].get("under_voltage_now", 0)
        t.append(_tile("Under-voltage", "seen" if uv else "none", f"{uv} samples" if uv else "during the run",
                       "bad" if uv else "good"))
    if run["cpu_median"] is not None:
        t.append(_tile("CPU, all cores", f'p95 {_f(run["cpu_p95"])}%', f'median {_f(run["cpu_median"])}%'))
        t.append(_tile("Busiest core, p95", _f(run["busiest_core_p95"], unit="%"),
                       "near 100% = one thread is the bottleneck", "warn" if (run["busiest_core_p95"] or 0) >= 90 else ""))
    if run["mem_avail_min_mb"] is not None:
        rss = sum((p["rss_peak_mb"] or 0) for p in run["procs"].values())
        t.append(_tile("Free RAM, lowest", _f(run["mem_avail_min_mb"], unit=" MB"),
                       (f"her processes peak {rss:.0f} MB" if rss else "") +
                       (f" · swap {run['swap_used_max_mb']:.0f} MB" if run.get("swap_used_max_mb") else ""),
                       "bad" if run["mem_avail_min_mb"] < 300 else ""))
    if run["mhz_min"] is not None:
        t.append(_tile("ARM clock, lowest", _f(run["mhz_min"], unit=" MHz"), f'median {_f(run["mhz_median"])} MHz'))
    pr = r.get("probe") or {}
    if pr.get("share_of_one_core") is not None:
        t.append(_tile("Sampler overhead", _f(pr["share_of_one_core"] * 100, "{:.2f}", "%"),
                       f'of one core · {_f(pr.get("ms_median"), "{:.1f}", " ms")} per sample'))
    parts.append(f'<div class="tiles">{"".join(t)}</div>')

    # timeline
    if run["cpu_median"] is not None:
        parts.append("<h3>Over the run</h3>" + _timeline_svg(r) + _legend(r))
    else:
        parts.append('<p class="na">CPU not available on this machine (no /proc; install psutil on Windows or macOS).</p>')

    # per group
    rows = []
    for g, s in (r.get("per_group") or {}).items():
        sh = s["throttled_share"]
        rows.append(f'<tr><td>{html.escape(GROUPS[g].title if g in GROUPS else g)}</td><td>{s["n"]}</td>'
                    f'<td>{_f(s["cpu_median"])}% / {_f(s["cpu_p95"])}%</td><td>{_f(s["busiest_core_p95"], unit="%")}</td>'
                    f'<td>{_f(s["temp_max"], "{:.1f}", " °C")}</td>'
                    f'<td style="color:{BAD if sh else "inherit"}">{_f(sh * 100 if sh is not None else None, "{:.0f}", "%")}</td></tr>')
    if rows:
        parts.append('<h3>Per test group</h3><div class="scroll"><table><thead><tr><th>group</th><th>samples</th>'
                     '<th>CPU median / p95</th><th>busiest core p95</th><th>peak temp</th><th>time throttled</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div>')

    # per process
    prow = []
    for i, (lab, p) in enumerate(run["procs"].items()):
        prow.append(f'<tr><td><i class="sw" style="background:{_proc_color(lab, i)}"></i>{html.escape(p["label"])}</td>'
                    f'<td>{_f(p["cpu_mean"], "{:.1f}", "%")}</td><td>{_f(p["cpu_p95"], "{:.1f}", "%")}</td>'
                    f'<td>{_f(p["rss_peak_mb"], unit=" MB")}</td></tr>')
    if prow:
        parts.append('<h3>Per process</h3><div class="scroll"><table><thead><tr><th>process</th><th>CPU mean</th>'
                     '<th>CPU p95</th><th>RAM peak</th></tr></thead>'
                     f'<tbody>{"".join(prow)}</tbody></table></div>'
                     '<p><small>CPU is percent of all cores, so the rows add up to her share of the machine.</small></p>')

    # latency vs load
    lv = r.get("lat_vs_load") or {}
    pairs = lv.get("pairs") or []
    if pairs:
        bits = []
        if lv.get("r_cpu") is not None:
            bits.append(f'correlation of latency with CPU r = {lv["r_cpu"]:+.2f} (n={len(pairs)})')
        if lv.get("median_latency_throttled") is not None:
            bits.append(f'median latency {lv["median_latency_throttled"]:.2f} s when throttled vs '
                        f'{_f(lv.get("median_latency_not_throttled"), "{:.2f}", " s")} otherwise')
        parts.append('<h3>Latency vs load</h3><p>Turn-taking, interruption and v1.5 response latencies, one dot per '
                     'trial; red = throttled during the trial; dashed green = 1 s target.'
                     + (" " + html.escape("; ".join(bits)) + "." if bits else "") + "</p>" + _scatter_svg(pairs))

    # flagged
    fl = r.get("flagged") or []
    if fl:
        li = "".join(f'<li>{html.escape(f["trial"])} · ' + ", ".join(
            x for x in (("throttled" if f["throttled"] else ""), ("under-voltage" if f["under_voltage"] else ""),
                        (f'{f["temp_max"]:.0f} °C' if f.get("temp_max") is not None else ""),
                        (f'latency {f["latency"]:.2f} s' if f.get("latency") is not None else "")) if x) + "</li>"
                     for f in fl)
        parts.append(f'<h3>Trials that ran while throttled ({len(fl)})</h3><p>Consider rerunning these or reporting '
                     f'them separately.</p><ul>{li}</ul>')
    missing = [k for k in ("temperature", "clock", "throttle", "processes") if not caps.get(k)]
    if missing:
        parts.append(f'<p><small>Not available on this machine: {", ".join(missing)}.</small></p>')
    return "\n".join(parts)


CSS = (".tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}"
       ".tile{border:1px solid #ddd;border-radius:6px;padding:8px 12px;display:flex;flex-direction:column;gap:2px}"
       ".tile b{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}.tile span{font-size:12px;color:#666}"
       ".legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#555;margin:6px 0}"
       ".legend i,.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}"
       ".scroll{overflow-x:auto}svg text{font-size:10px;fill:#555;font-family:ui-monospace,Consolas,monospace}"
       "ul.warn{color:#b7791f}h3{margin-top:22px}")
