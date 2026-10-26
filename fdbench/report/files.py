"""results.json, results.csv and a self-contained results.html with inline SVG charts."""
from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import List

from ..suites import GROUPS, KIND_LABELS
from ..score.scorer import VOICE_FEATURES
from ..util import read_json, write_json

ROWS = [
    ("v1", "TOR, pause handling (synthetic)", "v1_pause_synth.tor", "rate", "lower"),
    ("v1", "TOR, pause handling (Candor)", "v1_pause_candor.tor", "rate", "lower"),
    ("v1", "TOR, turn-taking (Candor, col 6)", "v1_turntaking.tor", "rate", "higher"),
    ("v1", "Missed turn-taking rate", "v1_turntaking.missed_rate", "rate", "lower"),
    ("v1", "False turn-taking rate", "false_tt.rate", "rate", "lower"),
    ("v1", "False turn-taking rate · side talk", "false_tt.rate.side_talk", "rate", "lower"),
    ("v1", "False turn-taking rate · TV / radio", "false_tt.rate.tv_radio", "rate", "lower"),
    ("v1", "False turn-taking rate · background speech", "false_tt.rate.background", "rate", "lower"),
    ("v1", "False turn-taking rate · noise", "false_tt.rate.noise", "rate", "lower"),
    ("v1", "Latency, turn-taking", "v1_turntaking.latency", "latency", "lower"),
    ("v1", "TOR, interruption", "v1_interrupt.tor", "rate", "higher"),
    ("v1", "Judge score, interruption (0–5)", "v1_interrupt.rating", "score", "higher"),
    ("v1", "Latency, interruption", "v1_interrupt.latency", "latency", "lower"),
]
for _g in ("v15_interruption", "v15_backchannel", "v15_talking_to_other", "v15_background"):
    _t = GROUPS[_g].title.replace("v1.5 ", "")
    _want = "Respond" if _g == "v15_interruption" else "Resume"
    for _lab in ("respond", "resume", "uncertain", "unknown"):
        ROWS.append(("v1.5", f"{_t} · {_lab.capitalize()}", f"{_g}.{_lab}", "rate",
                     "higher" if _lab.capitalize() == _want else ("lower" if _lab in ("respond", "resume") else "")))
    ROWS.append(("v1.5", f"{_t} · stop latency", f"{_g}.t_stop", "latency", "lower"))
    ROWS.append(("v1.5", f"{_t} · response latency", f"{_g}.t_resp", "latency", "lower"))
for _f in VOICE_FEATURES:
    ROWS.append(("v1.5 voice", f"Δ {_f}", f"voice.{_f}", "delta", ""))


def write_results(run_dir: Path, summary: dict, session: dict, preflight: dict | None, queue: dict | None) -> None:
    out = {"session": session, "preflight": preflight, "queue_summary": _queue_summary(queue), **summary}
    write_json(run_dir / "results.json", out)
    _write_csv(run_dir / "results.csv", summary, session)
    (run_dir / "results.html").write_text(render_html(out), encoding="utf-8")


def _queue_summary(q):
    if not q:
        return None
    items = q.get("items", [])
    return {"rounds": q.get("rounds"), "seed": q.get("seed"), "total": len(items),
            "done": sum(1 for i in items if i["status"] == "done"),
            "skipped": sum(1 for i in items if i["status"] == "skipped"),
            "failed": sum(1 for i in items if i["status"] == "failed")}


def _write_csv(path: Path, summary: dict, session: dict) -> None:
    m = summary["metrics"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["student", "paper", "metric", "key", "value", "n", "k", "ci_low", "ci_high", "median", "p95", "mean",
                    "p_value", "better"])
        for paper, name, key, kind, better in ROWS:
            v = m.get(key, {})
            if kind == "rate":
                w.writerow([session.get("student"), paper, name, key, v.get("value"), v.get("n"), v.get("k"),
                            *(v.get("ci95") or [None, None]), "", "", "", "", better])
            elif kind == "latency":
                w.writerow([session.get("student"), paper, name, key, v.get("median"), v.get("n"), "", "", "",
                            v.get("median"), v.get("p95"), v.get("mean"), "", better])
            elif kind == "score":
                w.writerow([session.get("student"), paper, name, key, v.get("mean"), v.get("n"), "", "", "", "", "",
                            v.get("mean"), "", better])
            else:
                w.writerow([session.get("student"), paper, name, key, v.get("mean_delta"), v.get("n"), "", "", "", "",
                            "", v.get("mean_delta"), v.get("p"), v.get("expected", "")])


# ------------------------------------------------------------------ HTML

def _bar(value, lo, hi, color, width=260):
    if value is None:
        return '<span class="na">—</span>'
    x = value * width
    ci = ""
    if lo is not None and hi is not None:
        ci = f'<line x1="{lo*width:.1f}" x2="{hi*width:.1f}" y1="9" y2="9" stroke="#555" stroke-width="2"/>'
    return (f'<svg width="{width+60}" height="18" style="vertical-align:middle">'
            f'<rect x="0" y="3" width="{width}" height="12" fill="#eee" rx="2"/>'
            f'<rect x="0" y="3" width="{x:.1f}" height="12" fill="{color}" rx="2"/>{ci}'
            f'<text x="{width+6}" y="14" font-size="12" fill="#222">{value*100:.0f}%</text></svg>')


def _lat_bar(v, width=260, scale=3.0):
    if not v or v.get("median") is None:
        return '<span class="na">—</span>'
    med, p95 = v["median"], v["p95"]
    xm, xp = min(med, scale) / scale * width, min(p95, scale) / scale * width
    target = 1.0 / scale * width
    return (f'<svg width="{width+140}" height="18" style="vertical-align:middle">'
            f'<rect x="0" y="3" width="{width}" height="12" fill="#eee" rx="2"/>'
            f'<rect x="0" y="3" width="{xp:.1f}" height="12" fill="#f5c89a" rx="2"/>'
            f'<rect x="0" y="3" width="{xm:.1f}" height="12" fill="#d9822b" rx="2"/>'
            f'<line x1="{target:.1f}" x2="{target:.1f}" y1="0" y2="18" stroke="#1f8a4c" stroke-dasharray="3 2"/>'
            f'<text x="{width+6}" y="14" font-size="12" fill="#222">med {med:.2f} · p95 {p95:.2f} s · n={v["n"]}</text></svg>')


def render_html(out: dict) -> str:
    m = out["metrics"]
    s = out.get("session", {})
    rows = []
    for paper, name, key, kind, better in ROWS:
        v = m.get(key, {})
        if kind == "rate":
            color = "#1f8a4c" if better == "higher" else "#c43d3d"
            lo, hi = v.get("ci95") or [None, None]
            cell = _bar(v.get("value"), lo, hi, color) + (f' <span class="n">({v.get("k")}/{v.get("n")})</span>' if v.get("n") else "")
        elif kind == "latency":
            cell = _lat_bar(v)
        elif kind == "score":
            cell = f'{v.get("mean"):.2f} (n={v.get("n")})' if v.get("mean") is not None else '<span class="na">— not judged</span>'
        else:
            if v.get("n"):
                p = f'p={v["p"]:.3f}' if v.get("p") is not None else "p=—"
                sig = ' <b style="color:#1f8a4c">significant</b>' if v.get("significant") else ""
                cell = f'Δ {v["mean_delta"]:+.2f} ({p}, n={v["n"]}){sig} · expected {v.get("expected")}'
            else:
                cell = '<span class="na">— not scored</span>'
        rows.append(f"<tr><td>{html.escape(paper)}</td><td>{html.escape(name)}</td><td>{cell}</td><td>{html.escape(better)}</td></tr>")
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in out.get("notes", []))
    pg = out.get("per_group", {})
    counts = "".join(f"<tr><td>{html.escape(GROUPS[g].title)}</td><td>{n}</td></tr>" for g, n in pg.items() if g in GROUPS)
    qs = out.get("queue_summary") or {}
    pf = out.get("preflight") or {}
    info = pf.get("info", {})
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>fdbench results · {html.escape(str(s.get('student','')))}</title>
<style>body{{font-family:system-ui,Segoe UI,sans-serif;margin:24px auto;max-width:1000px;padding:0 16px;color:#222}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:middle}}
th{{font-size:12px;text-transform:uppercase;color:#666}}.na{{color:#999}}.n{{color:#666;font-size:12px}}h2{{margin-top:32px}}
.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 24px;font-size:14px}}.meta b{{color:#666;font-weight:500}}
small{{color:#666}}</style></head><body>
<h1>Full-duplex benchmark results</h1>
<div class="meta">
<div><b>Student</b><br>{html.escape(str(s.get('student','')))}</div>
<div><b>Run</b><br>{html.escape(str(s.get('run_id','')))}</div>
<div><b>Machine</b><br>{html.escape(str(info.get('pi_model') or info.get('os','')))} · {info.get('ram_gb','?')} GB · {html.escape(str(info.get('network','')))}</div>
<div><b>Rounds</b><br>{qs.get('done','?')}/{qs.get('total','?')} trials done · {qs.get('rounds','?')} rounds planned · seed {qs.get('seed','')}</div>
<div><b>Judge</b><br>{html.escape(str(out.get('judge') or 'none'))}</div>
<div><b>ASR</b><br>{html.escape(str(out.get('asr') or 'none'))}</div>
<div><b>fdbench</b><br>{html.escape(str(out.get('fdbench','')))} · scored {html.escape(str(out.get('scored_at','')))}</div>
</div>
<h2>Metrics</h2>
<table><thead><tr><th>paper</th><th>metric</th><th>value</th><th>better</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p><small>Rates show the 95% Wilson interval as a dark line. Latency bars: dark = median, light = p95, dashed green line = 1 s target (axis 0–3 s). Metrics follow Lin et al., Full-Duplex-Bench v1 (2025) and v1.5 (2026); missed and false turn-taking rates are this course's split of turn-taking. See docs/METHOD.md.</small></p>
<h2>Trials per group</h2><table><tbody>{counts}</tbody></table>
{('<h2>Notes</h2><ul>' + notes + '</ul>') if notes else ''}
</body></html>"""


def compare_table(result_files: List[Path]) -> List[List[str]]:
    """Rows: metric, then one column per run (student · run)."""
    runs = [(p, read_json(p)) for p in result_files]
    runs = [(p, r) for p, r in runs if r]
    header = ["metric"] + [f"{r.get('session', {}).get('student', '?')} · {r.get('session', {}).get('run_id', p.parent.name)}" for p, r in runs]
    table = [header]
    for paper, name, key, kind, better in ROWS:
        row = [f"{name} ({better})" if better else name]
        for _, r in runs:
            v = r["metrics"].get(key, {})
            if kind == "rate":
                row.append(f"{v['value']*100:.0f}% ({v['k']}/{v['n']})" if v.get("value") is not None else "—")
            elif kind == "latency":
                row.append(f"{v['median']:.2f}/{v['p95']:.2f} s" if v.get("median") is not None else "—")
            elif kind == "score":
                row.append(f"{v['mean']:.2f}" if v.get("mean") is not None else "—")
            else:
                row.append(f"{v['mean_delta']:+.2f}" + ("*" if v.get("significant") else "") if v.get("n") else "—")
        table.append(row)
    return table
