"""results.json, results.csv and a self-contained results.html with inline SVG charts."""
from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import List

from ..suites import GROUPS, KIND_LABELS
from ..score.scorer import VOICE_FEATURES
from ..util import read_json, write_json
from . import deck as deck_mod
from . import resources_html
from ..targets import DEFAULTS as TARGET_DEFAULTS

# machine resources rows (results.csv and compare): label, path in results["resources"]["run"], unit, better
RES_ROWS = [
    ("Peak temperature", "temp_max", "°C", "lower"),
    ("Time throttled or capped", "throttled_share", "rate", "lower"),
    ("CPU median, all cores", "cpu_median", "%", "lower"),
    ("CPU p95, all cores", "cpu_p95", "%", "lower"),
    ("Busiest core p95", "busiest_core_p95", "%", "lower"),
    ("Lowest ARM clock", "mhz_min", "MHz", "higher"),
    ("Lowest free RAM", "mem_avail_min_mb", "MB", "higher"),
]

# course template (machine exercise section 5): stage latency from the assistant's marks; key -> target key
COURSE_ROWS = [
    ("course", "E2E latency (VAD end → audio out, assistant clock)", "course.e2e", "latency", "lower", "e2e"),
    ("course", "E2E heard (true end of speech → reply audio)", "course.e2e_heard", "latency", "lower", "e2e"),
    ("course", "VAD endpointing delay (true end of speech → VAD end)", "course.endpointing", "latency", "lower", None),
    ("course", "ASR final (VAD end → transcript)", "course.asr_final", "latency", "lower", "asr_final"),
    ("course", "LLM TTFT (transcript → first token)", "course.llm_ttft", "latency", "lower", "llm_ttft"),
    ("course", "TTS TTFB (text → first audio chunk)", "course.tts_ttfb", "latency", "lower", "tts_ttfb"),
    ("course", "Barge-in (speech → playback stopped)", "course.barge_in", "latency", "lower", "barge_in"),
    ("course", "MCP / tool call duration", "course.tool_calls", "latency", "lower", None),
    ("course", "WER, assistant's ASR vs reference", "course.wer", "wer", "lower", "wer"),
    ("course", "Context size, prompt tokens per LLM call", "course.context", "context", "", None),
]

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
    deck = deck_mod.build(out)
    out = {"deck_summary": deck, **out}  # the numbers for the slides first; details after
    write_json(run_dir / "results.json", out)
    _write_csv(run_dir / "results.csv", summary, session)
    (run_dir / "results.html").write_text(render_html({**out, "_stage_rows": read_json(run_dir / "stage_latency.json")}),
                                          encoding="utf-8")
    (run_dir / "summary.md").write_text(deck_mod.markdown(deck), encoding="utf-8")
    (run_dir / "latency_chart.svg").write_text(deck_mod.chart_svg(deck), encoding="utf-8")


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
        w.writerow(["student", "paper", "metric", "key", "value", "n", "k", "median", "p95", "mean",
                    "p_value", "better", "target"])
        targets = {**TARGET_DEFAULTS, **(session.get("targets") or {})}
        for paper, name, key, kind, better, tkey in COURSE_ROWS:
            v = m.get(key, {}) or {}
            tgt = targets.get(tkey) if tkey else ""
            if kind == "latency":
                w.writerow([session.get("student"), paper, name, key, v.get("mean"), v.get("n"), "", v.get("median"),
                            v.get("p95"), v.get("mean"), "", better, tgt])
            elif kind == "wer":
                w.writerow([session.get("student"), paper, name, key, v.get("wer"), v.get("n"), v.get("edits"), "", "",
                            v.get("mean_phrase_wer"), "", better, tgt])
            else:
                w.writerow([session.get("student"), paper, name, key, v.get("tokens_median"), v.get("n"), "", v.get("tokens_median"),
                            "", "", "", better, ""])
        for paper, name, key, kind, better in ROWS:
            v = m.get(key, {})
            if kind == "rate":
                w.writerow([session.get("student"), paper, name, key, v.get("value"), v.get("n"), v.get("k"),
                            "", "", "", "", better, ""])
            elif kind == "latency":
                w.writerow([session.get("student"), paper, name, key, v.get("median"), v.get("n"), "",
                            v.get("median"), v.get("p95"), v.get("mean"), "", better, ""])
            elif kind == "score":
                w.writerow([session.get("student"), paper, name, key, v.get("mean"), v.get("n"), "", "", "",
                            v.get("mean"), "", better, ""])
            else:
                w.writerow([session.get("student"), paper, name, key, v.get("mean_delta"), v.get("n"), "", "",
                            "", v.get("mean_delta"), v.get("p"), v.get("expected", ""), ""])
        run = ((summary.get("resources") or {}).get("run")) or {}
        for name, key, unit, better in RES_ROWS:
            if run.get(key) is not None:
                w.writerow([session.get("student"), "resources", f"{name} ({unit})", f"resources.{key}", run[key],
                            run.get("n"), "", "", "", "", "", better, ""])


# ------------------------------------------------------------------ HTML

def _bar(value, color, width=260):
    if value is None:
        return '<span class="na">—</span>'
    x = value * width
    return (f'<svg width="{width+60}" height="18" style="vertical-align:middle">'
            f'<rect x="0" y="3" width="{width}" height="12" fill="#eee" rx="2"/>'
            f'<rect x="0" y="3" width="{x:.1f}" height="12" fill="{color}" rx="2"/>'
            f'<text x="{width+6}" y="14" font-size="12" fill="#222">{value*100:.0f}%</text></svg>')


def _lat_bar(v, width=260, scale=3.0, target=1.0, with_mean=False):
    if not v or v.get("median") is None:
        return '<span class="na">—</span>'
    med, p95 = v["median"], v["p95"]
    xm, xp = min(med, scale) / scale * width, min(p95, scale) / scale * width
    tline = ""
    if target is not None:
        xt = min(target, scale) / scale * width
        tline = f'<line x1="{xt:.1f}" x2="{xt:.1f}" y1="0" y2="18" stroke="#1f8a4c" stroke-dasharray="3 2"/>'
    mean = f'mean {v["mean"]:.2f} · ' if with_mean and v.get("mean") is not None else ""
    tgt = f" · target {target:g}" if (with_mean and target is not None) else ""
    return (f'<svg width="{width+240}" height="18" style="vertical-align:middle">'
            f'<rect x="0" y="3" width="{width}" height="12" fill="#eee" rx="2"/>'
            f'<rect x="0" y="3" width="{xp:.1f}" height="12" fill="#f5c89a" rx="2"/>'
            f'<rect x="0" y="3" width="{xm:.1f}" height="12" fill="#d9822b" rx="2"/>{tline}'
            f'<text x="{width+6}" y="14" font-size="12" fill="#222">{mean}med {med:.2f} · p95 {p95:.2f} s · n={v["n"]}{tgt}</text></svg>')


NEW_ROW = ' style="background:#fdf1e6"'


def _course_rows(m: dict, targets: dict) -> List[str]:
    rows = []
    for paper, name, key, kind, better, tkey in COURSE_ROWS:
        v = m.get(key) or {}
        tgt = targets.get(tkey) if tkey else None
        if kind == "latency":
            cell = _lat_bar(v, target=tgt, with_mean=True) if v.get("n") else '<span class="na">— not reported</span>'
            if key == "course.e2e" and v.get("source") and v.get("source") != "marks":
                cell += f' <span class="n">({html.escape(str(v["source"]))})</span>'
        elif kind == "wer":
            cell = (f'{v["wer"]*100:.1f}% ({v["edits"]} errors / {v["ref_words"]} words, {v["n"]} phrases; mean per phrase '
                    f'{v["mean_phrase_wer"]*100:.1f}%)' + (f' · target {tgt*100:.0f}%' if tgt is not None else "")
                    if v.get("wer") is not None else '<span class="na">— not reported</span>')
        else:
            cell = (f'median {v["tokens_median"]:.0f} · max {v["tokens_max"]:.0f} · first → last {v["tokens_first"]:.0f} → '
                    f'{v["tokens_last"]:.0f} ({", ".join(v.get("source") or [])}, {v["n"]} calls)'
                    if v.get("n") else '<span class="na">— not reported</span>')
        rows.append(f"<tr{NEW_ROW}><td>{paper}</td><td>{html.escape(name)}</td><td>{cell}</td><td>{better}</td></tr>")
    return rows


def _stage_section(run_dir_rows: dict, m: dict) -> str:
    """Stage latency per run and per turn (from stage_latency.json rows passed in results)."""
    turns = run_dir_rows.get("turns") or []
    if not turns:
        return ('<h2 id="stage-latency">Stage latency (course template)</h2><p class="na">No stage-latency trials in this run '
                '(--phrases 0, or stopped before).</p>')
    f = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
    per_run = []
    for k, st in (m.get("course.per_run") or {}).items():
        cells = "".join(f'<td>{f(st[x].get("mean"))} / {f(st[x].get("p95"))}</td>' for x in ("e2e", "asr_final", "llm_ttft", "tts_ttfb"))
        ctx = (m.get("course.context") or {}).get("per_run_median_tokens", {}).get(k)
        per_run.append(f"<tr><td>run {k}</td><td>{st['e2e'].get('n', 0)}</td>{cells}<td>{'—' if ctx is None else f'{ctx:.0f}'}</td></tr>")
    rows = []
    for r in turns:
        w = "—" if r.get("wer") is None else f'{r["wer"]*100:.0f}%'
        rows.append(f'<tr><td>{r.get("run") or "—"}</td><td>{html.escape(r.get("ref") or "")}</td>'
                    f'<td>{html.escape(r.get("transcript") or "—")}</td><td>{w}</td><td>{f(r.get("endpointing"))}</td>'
                    f'<td>{f(r.get("asr_final"))}</td><td>{f(r.get("llm_ttft"))}</td><td>{f(r.get("tts_ttfb"))}</td>'
                    f'<td>{f(r.get("e2e"))}</td><td>{f(r.get("e2e_heard"))}</td></tr>')
    seen = m.get("course.stages_seen") or {}
    seen_txt = " · ".join(f"{k} {v}" for k, v in sorted(seen.items())) or "none (the assistant sent no stage marks; docs/STAGES.md)"
    return f"""<h2 id="stage-latency">Stage latency (course template)</h2>
<p><small>{run_dir_rows.get('phrases', 0)} phrases × {run_dir_rows.get('runs', 0)} runs. Mean / p95 in seconds, on the assistant
machine's monotonic clock. Every turn with its raw marks: <code>stage_latency.json</code>, <code>trials/&lt;id&gt;/stages.jsonl</code>.</small></p>
<table><thead><tr><th>run</th><th>turns</th><th>E2E</th><th>ASR final</th><th>LLM TTFT</th><th>TTS TTFB</th><th>median context tokens</th></tr></thead>
<tbody>{''.join(per_run)}</tbody></table>
<h3>Per turn</h3>
<div class="scroll"><table><thead><tr><th>run</th><th>reference</th><th>her transcript</th><th>WER</th><th>endpoint</th><th>ASR</th>
<th>LLM</th><th>TTS</th><th>E2E</th><th>E2E heard</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p><small>endpoint = VAD end − true end of speech; E2E = audio out − VAD end (assistant clock); E2E heard = reply audio − true end of
speech (fdbench clock, includes the endpointing delay and the link).</small></p>
<h3>Stage marks received</h3><p><small>{html.escape(seen_txt)}</small></p>"""


def render_html(out: dict) -> str:
    m = out["metrics"]
    s = out.get("session", {})
    rows = []
    for paper, name, key, kind, better in ROWS:
        v = m.get(key, {})
        if kind == "rate":
            color = "#1f8a4c" if better == "higher" else "#c43d3d"
            cell = _bar(v.get("value"), color) + (f' <span class="n">({v.get("k")}/{v.get("n")})</span>' if v.get("n") else "")
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
    targets = {**TARGET_DEFAULTS, **(s.get("targets") or {})}
    rows = _course_rows(m, targets) + rows
    deck = out.get("deck_summary") or deck_mod.build(out)
    stage_rows = out.get("_stage_rows") or {}
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
small{{color:#666}}.scroll{{overflow-x:auto}}{resources_html.CSS}</style></head><body>
{deck_mod.render_html(deck)}
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
<h2 id="metrics">Metrics</h2>
<table><thead><tr><th>paper</th><th>metric</th><th>value</th><th>better</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p><small>Latency bars: dark = median, light = p95, dashed green line = the row's target (course rows) or 1 s (axis 0–3 s). Course rows follow the machine exercise template (section 5), from the assistant's stage marks (docs/STAGES.md). The other metrics follow Lin et al., Full-Duplex-Bench v1 (2025) and v1.5 (2026); missed and false turn-taking rates are this course's split of turn-taking. See docs/METHOD.md.</small></p>
{_stage_section(stage_rows, m)}
<h2 id="trials">Trials per group</h2><table><tbody>{counts}</tbody></table>
{('<h2>Notes</h2><ul>' + notes + '</ul>') if notes else ''}
<div id="resources">{resources_html.render(out.get("resources"))}</div>
</body></html>"""


def compare_table(result_files: List[Path]) -> List[List[str]]:
    """Rows: metric, then one column per run (student · run)."""
    runs = [(p, read_json(p)) for p in result_files]
    runs = [(p, r) for p, r in runs if r]
    header = ["metric"] + [f"{r.get('session', {}).get('student', '?')} · {r.get('session', {}).get('run_id', p.parent.name)}" for p, r in runs]
    table = [header]
    for paper, name, key, kind, better, _t in COURSE_ROWS:
        row = [f"{name} ({better})" if better else name]
        for _, r in runs:
            v = r["metrics"].get(key) or {}
            if kind == "latency":
                row.append(f"{v['mean']:.2f}/{v['p95']:.2f} s" if v.get("mean") is not None else "—")
            elif kind == "wer":
                row.append(f"{v['wer']*100:.1f}%" if v.get("wer") is not None else "—")
            else:
                row.append(f"{v['tokens_median']:.0f}" if v.get("n") else "—")
        table.append(row)
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
    for name, key, unit, better in RES_ROWS:
        row = [f"{name} ({better})"]
        for _, r in runs:
            v = ((r.get("resources") or {}).get("run") or {}).get(key)
            if v is None:
                row.append("—")
            elif unit == "rate":
                row.append(f"{v*100:.1f}%")
            else:
                row.append(f"{v:.0f} {unit}")
        table.append(row)
    return table
