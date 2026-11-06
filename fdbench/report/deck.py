"""The deck summary: the numbers the course's 2-page template asks for, laid out like the deck, at the top of
results.html, first in results.json (`deck_summary`), and as summary.md + latency_chart.svg to paste into slides."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Optional

from ..targets import DEFAULTS, TARGETS, fmt

ROWS = [  # key, metric (template wording), definition (template wording), metrics key
    ("e2e", "E2E latency", "end of speech → audio out", "course.e2e"),
    ("asr_final", "ASR final", "end of speech → transcript", "course.asr_final"),
    ("llm_ttft", "LLM TTFT", "transcript → first token", "course.llm_ttft"),
    ("tts_ttfb", "TTS TTFB", "text → first audio chunk", "course.tts_ttfb"),
    ("barge_in", "Barge-in", "speech → playback stopped", "course.barge_in"),
    ("wer", "WER", "ASR accuracy on test set", "course.wer"),
]
MARK_BASED = {"asr_final", "llm_ttft", "tts_ttfb", "wer"}


def _status(mean, p95, target) -> str:
    if mean is None or target is None:
        return "na"
    if mean > target:
        return "over"
    if p95 is not None and p95 > target:
        return "p95_over"
    return "met"


def build(out: dict) -> dict:
    """deck_summary from a results dict (metrics, session, resources)."""
    m = out.get("metrics", {})
    s = out.get("session", {}) or {}
    targets = {**DEFAULTS, **(s.get("targets") or {})}
    rows = []
    for key, metric, definition, mkey in ROWS:
        v = m.get(mkey) or {}
        if key == "wer":
            mean, p95, med, n = v.get("wer"), None, None, v.get("n", 0)
        else:
            mean, p95, med, n = v.get("mean"), v.get("p95"), v.get("median"), v.get("n", 0)
        note = None
        if key == "e2e" and v.get("source") and v.get("source") != "marks":
            note = ("from audio: no stage marks" if str(v.get("source")).startswith("audio (no")
                    else f"source: {v.get('source')}")
        if key == "barge_in" and v.get("kept_talking"):
            note = f"{v['kept_talking']} interruption(s) she talked through, not counted"
        if n == 0:
            note = ("not reported: the assistant sent no stage marks (docs/STAGES.md)" if key in MARK_BASED
                    else "no data in this run")
        rows.append({"key": key, "metric": metric, "definition": definition, "target": targets.get(key),
                     "mean": mean, "p95": p95, "median": med, "n": n, "unit": TARGETS[key][2],
                     "status": _status(mean, p95 if key != "wer" else None, targets.get(key)) if n else "na",
                     "note": note})
    phrases, runs, turns = m.get("course.phrases", 0), m.get("course.runs", 0), m.get("course.turns", 0)
    e2e = m.get("course.e2e") or {}
    seg = []
    for k, label in (("asr_final", "ASR"), ("llm_ttft", "LLM"), ("tts_ttfb", "TTS")):
        val = (m.get(f"course.{k}") or {}).get("mean")
        if val is not None:
            seg.append({"key": k, "label": label, "mean": val})
    if e2e.get("mean") is not None and seg:
        rest = e2e["mean"] - sum(x["mean"] for x in seg)
        if rest > 0.005:
            seg.append({"key": "other", "label": "hand-offs + playout", "mean": rest})
    res = out.get("resources") or {}
    hello = res.get("hello") or (s.get("machine_stats") or {}).get("hello") or {}
    mach = s.get("machine") or {}
    model = hello.get("pi_model") or mach.get("pi_model") or hello.get("os") or mach.get("os")
    ram_mb = hello.get("ram_mb")
    ram = f"{ram_mb / 1024:.0f} GB" if ram_mb else (f"{mach.get('ram_gb')} GB" if mach.get("ram_gb") else None)
    run = res.get("run") or {}
    link = s.get("link") or {}
    names = {"wifi": "Wi-Fi", "ethernet": "Ethernet"}
    cloud = hello.get("network") or mach.get("network")
    parts = [f"cloud: {names.get(cloud, cloud)}"] if cloud else []
    lnk = hello.get("link_network")
    rtt = f"{link['rtt_ms']:.0f} ms round trip" if link.get("rtt_ms") is not None else None
    if lnk or rtt:
        parts.append("benchmark link: " + ", ".join(x for x in (names.get(lnk, lnk), rtt) if x))
    network = " · ".join(parts) or None
    cooling = s.get("cooling") or "not stated"
    if run.get("temp_max") is not None:
        cooling += f" · peak {run['temp_max']:.0f} °C"
    if run.get("throttled_share") is not None:
        cooling += " · throttled " + (f"{run['throttled_share'] * 100:.0f} % of the time" if run["throttled_share"] else "never")
    date = (s.get("started") or out.get("scored_at") or "")[:10]
    ctx = m.get("course.context") or {}
    return {
        "title": "Full-Duplex Voice Agent on a Raspberry Pi",
        "student": s.get("student"), "run_id": s.get("run_id"), "date": date,
        "method": {"phrases": phrases, "runs": runs, "turns": turns,
                   "bullets": [
                       "One clock: the assistant machine's monotonic clock; every stage timestamped "
                       "(VAD end → ASR final → LLM 1st token → TTS 1st audio → speaker)",
                       "E2E = speaker − VAD end; barge-in = stop − speech start",
                       f"{phrases} phrases × {runs} runs; mean + p95; JSON log per run: stage_latency.json "
                       "(per turn: trials/<id>/stages.jsonl)",
                   ]},
        "targets": targets,
        "table": rows,
        "chart": {"segments": seg, "e2e_mean": e2e.get("mean"), "e2e_p95": e2e.get("p95"), "target": targets.get("e2e")},
        "run_card": {"device": " · ".join(x for x in (model, ram) if x) or None, "cooling": cooling,
                     "network": network, "date": date, "models": s.get("models") or {}},
        "context": ctx,
    }


# ------------------------------------------------------------------ text formats

def _v(row, key) -> str:
    val = row.get(key)
    if val is None:
        return "—"
    return f"{val * 100:.1f} %" if row["unit"] == "ratio" else f"{val:.2f} s"


STATUS_TEXT = {"met": "met", "p95_over": "p95 over", "over": "over", "na": "—"}


def markdown(d: dict) -> str:
    L = [f"# {d['title']}", "", f"Student {d.get('student')} · run {d.get('run_id')} · {d.get('date')}", "",
         "## 5 · Evaluation & Benchmark", "", "| Metric | Definition | Target | Mean | p95 | n |", "|---|---|---|---|---|---|"]
    for r in d["table"]:
        L.append(f"| {r['metric']} | {r['definition']} | < {fmt(r['key'], r['target'])} | {_v(r, 'mean')} | {_v(r, 'p95')} | {r['n']} |")
    L += [""] + [f"- {b}" for b in d["method"]["bullets"]]
    notes = [f"- {r['metric']}: {r['note']}" for r in d["table"] if r.get("note")]
    if notes:
        L += ["", "Notes:"] + notes
    rc = d["run_card"]
    L += ["", "## Results (deck 2)", "", "Latency chart: latency_chart.svg", "",
          f"- RPi model / RAM: {rc.get('device') or '—'}", f"- Cooling: {rc.get('cooling') or '—'}",
          f"- Network: {rc.get('network') or '—'}", f"- Date: {rc.get('date') or '—'}",
          "- Model versions: " + (", ".join(f"{k} {v}" for k, v in rc["models"].items()) or "not reported")]
    c = d.get("context") or {}
    L += ["", "## 3 · Context engineering: context size per run", ""]
    if c.get("n"):
        L += ["| Context size | Median | Max | First → last call |", "|---|---|---|---|",
              f"| Prompt tokens per LLM call ({', '.join(c.get('source') or [])}) | {c['tokens_median']:.0f} | {c['tokens_max']:.0f} | {c['tokens_first']:.0f} → {c['tokens_last']:.0f} |"]
        if c.get("messages_median") is not None:
            L.append(f"| Messages in context | {c['messages_median']:.0f} | {c['messages_max']:.0f} | {c['messages_first']:.0f} → {c['messages_last']:.0f} |")
    else:
        L.append("Not reported: the assistant sent no llm_request marks (docs/STAGES.md).")
    return "\n".join(L) + "\n"


def chart_svg(d: dict, standalone: bool = True) -> str:
    """Stacked mean stage breakdown with the p95 of E2E and the target line. Light colours, for slides."""
    ch = d["chart"]
    W, H, x0, x1 = 560, 150, 60, 540
    segs, e2e, p95, target = ch["segments"], ch.get("e2e_mean"), ch.get("e2e_p95"), ch.get("target")
    top = max([v for v in (e2e, p95, target) if v is not None] or [1.0]) * 1.1
    sc = lambda v: x0 + (x1 - x0) * min(v, top) / top  # noqa: E731
    colors = {"asr_final": "#1f7f86", "llm_ttft": "#e8742a", "tts_ttfb": "#16263f", "other": "#a0a8b5"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
             f'font-family="Helvetica, Arial, sans-serif" font-size="11">',
             f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    # axis and ticks
    step = 0.25 if top <= 1.5 else 0.5 if top <= 3 else 1.0
    t = 0.0
    while t <= top + 1e-9:
        x = sc(t)
        parts.append(f'<line x1="{x:.1f}" y1="34" x2="{x:.1f}" y2="96" stroke="#e3e7ee"/>'
                     f'<text x="{x:.1f}" y="110" text-anchor="middle" fill="#667085">{t:g}</text>')
        t += step
    parts.append(f'<text x="{x1}" y="124" text-anchor="end" fill="#667085">seconds</text>')
    parts.append('<text x="54" y="69" text-anchor="end" fill="#1b2230">E2E</text>')
    if e2e is None:
        parts.append(f'<text x="{x0}" y="69" fill="#667085">no E2E data in this run</text>')
    elif not segs:
        parts.append(f'<rect x="{x0}" y="54" width="{sc(e2e) - x0:.1f}" height="22" fill="#a0a8b5"/>')
    else:
        cum = 0.0
        for sgm in segs:
            xa, xb = sc(cum), sc(cum + sgm["mean"])
            parts.append(f'<rect x="{xa:.1f}" y="54" width="{max(0.0, xb - xa):.1f}" height="22" fill="{colors.get(sgm["key"], "#a0a8b5")}"/>')
            cum += sgm["mean"]
    if p95 is not None:
        xe, xp = sc(e2e or 0), sc(p95)
        parts.append(f'<line x1="{xe:.1f}" y1="65" x2="{xp:.1f}" y2="65" stroke="#1b2230"/>'
                     f'<line x1="{xp:.1f}" y1="58" x2="{xp:.1f}" y2="72" stroke="#1b2230"/>'
                     f'<text x="{xp:.1f}" y="50" text-anchor="middle" fill="#1b2230">p95 {p95:.2f}</text>')
    if target is not None:
        xt = sc(target)
        parts.append(f'<line x1="{xt:.1f}" y1="28" x2="{xt:.1f}" y2="96" stroke="#c0392b" stroke-dasharray="4 3"/>'
                     f'<text x="{xt:.1f}" y="22" text-anchor="middle" fill="#c0392b">target {target:g} s</text>')
    # legend
    lx = x0
    for sgm in segs:
        parts.append(f'<rect x="{lx}" y="132" width="10" height="10" fill="{colors.get(sgm["key"], "#a0a8b5")}"/>'
                     f'<text x="{lx + 14}" y="141" fill="#1b2230">{html.escape(sgm["label"])} {sgm["mean"]:.2f}</text>')
        lx += 30 + 7 * (len(sgm["label"]) + 5)
    if e2e is not None:
        parts.append(f'<text x="{x1}" y="141" text-anchor="end" fill="#1b2230">mean E2E {e2e:.2f} s</text>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ HTML (top of results.html)

CSS = """
.deck{--navy:#16263f;--accent:#e8742a;--teal:#1f7f86;--line:#d6dce5;--chip:#eef1f5;--muted:#667085;--ok:#2f7d4f;--warn:#b7791f;--bad:#c0392b;
 font-family:"Helvetica Neue",Arial,system-ui,sans-serif;color:#1b2230;margin:0 0 28px}
.deck .band{background:var(--navy);color:#fff;border-bottom:5px solid var(--accent);padding:16px 18px;border-radius:6px 6px 0 0;
 display:flex;flex-wrap:wrap;gap:6px 24px;justify-content:space-between;align-items:end}
.deck .kick{font-weight:700;font-size:12px;letter-spacing:.06em;color:var(--accent);text-transform:uppercase}
.deck .band h1{margin:2px 0 0;font-size:24px;line-height:1.2;color:#fff}
.deck .band .who{font:12px ui-monospace,Consolas,monospace;opacity:.85}
.deck .body{background:#f3f5f8;border:1px solid var(--line);border-top:0;border-radius:0 0 6px 6px;padding:14px;display:grid;gap:14px}
.deck .grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:14px}
@media (max-width:820px){.deck .grid{grid-template-columns:minmax(0,1fr)}}
.deck .blk{background:#fff;border:1px solid var(--line);border-radius:6px;padding:12px;display:grid;gap:9px;align-content:start;min-width:0}
.deck .hd{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.deck .num{background:var(--accent);color:#fff;font-weight:700;width:24px;height:24px;border-radius:4px;display:grid;place-items:center;font-size:13px}
.deck .hd h2{margin:0;font-size:15px;letter-spacing:.02em;text-transform:uppercase;color:var(--navy)}
.deck .slot{margin-left:auto;font:11px ui-monospace,Consolas,monospace;color:var(--teal);border:1px solid var(--teal);border-radius:3px;padding:1px 6px}
.deck .scroll{overflow-x:auto}
.deck table{border-collapse:collapse;width:100%;font-size:13px}
.deck th{background:var(--navy);color:#fff;text-align:left;font-size:12px;font-weight:700;text-transform:none;padding:6px 8px;white-space:nowrap;border:0}
.deck td{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.deck tr:nth-child(even) td{background:var(--chip)}
.deck td.n{font-variant-numeric:tabular-nums;font-family:ui-monospace,Consolas,monospace;white-space:nowrap}
.deck .pill{font:700 11px ui-monospace,Consolas,monospace;padding:1px 6px;border-radius:9px;white-space:nowrap}
.deck .met{color:var(--ok);border:1px solid var(--ok)}.deck .p95_over{color:var(--warn);border:1px solid var(--warn)}
.deck .over{color:var(--bad);border:1px solid var(--bad)}.deck .na{color:var(--muted);border:1px solid var(--line)}
.deck ul{margin:0;padding-left:18px;display:grid;gap:2px;font-size:12px;color:var(--muted)}
.deck .note{font-size:12px;color:var(--muted)}
.deck dl{margin:0;display:grid;grid-template-columns:max-content minmax(0,1fr);gap:3px 12px;font-size:13px}
.deck dt{color:var(--muted)}.deck dd{margin:0;font:12.5px ui-monospace,Consolas,monospace}
.deck .btns{display:flex;gap:8px;flex-wrap:wrap}
.deck button{font:600 12px "Helvetica Neue",Arial,sans-serif;background:#fff;color:#1b2230;border:1px solid var(--line);border-radius:4px;padding:4px 9px;cursor:pointer}
.deck button:hover{border-color:var(--accent)}.deck button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.deck svg{max-width:100%;height:auto}
.deck .foot{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:var(--muted)}.deck .foot a{color:var(--teal)}
"""

JS = r"""
<script>
function fdbCopy(id, btn){
  var el=document.getElementById(id); if(!el) return;
  var html=el.outerHTML, text=el.innerText;
  function done(ok){var o=btn.textContent;btn.textContent=ok?'Copied':'Select and copy';setTimeout(function(){btn.textContent=o},1500)}
  try{
    if(navigator.clipboard && window.ClipboardItem){
      navigator.clipboard.write([new ClipboardItem({'text/html':new Blob([html],{type:'text/html'}),'text/plain':new Blob([text],{type:'text/plain'})})]).then(function(){done(true)},function(){fallback()});
    } else fallback();
  }catch(e){fallback()}
  function fallback(){var r=document.createRange();r.selectNode(el);var s=window.getSelection();s.removeAllRanges();s.addRange(r);
    try{done(document.execCommand('copy'))}catch(e){done(false)}}
}
function fdbSave(kind){
  var svg=document.querySelector('#fdb-chart svg'); if(!svg) return;
  var src=new XMLSerializer().serializeToString(svg);
  if(kind==='svg'){fdbDownload(new Blob([src],{type:'image/svg+xml'}),'latency_chart.svg');return}
  var img=new Image(); var w=svg.viewBox.baseVal.width, h=svg.viewBox.baseVal.height;
  img.onload=function(){var c=document.createElement('canvas');c.width=w*3;c.height=h*3;var g=c.getContext('2d');
    g.fillStyle='#fff';g.fillRect(0,0,c.width,c.height);g.drawImage(img,0,0,c.width,c.height);
    c.toBlob(function(b){fdbDownload(b,'latency_chart.png')})};
  img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(src);
}
function fdbDownload(blob,name){var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;
  document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},500)}
</script>
"""


def _e(x) -> str:
    return html.escape(str(x)) if x is not None else "—"


def render_html(d: dict) -> str:
    rows = []
    for r in d["table"]:
        pill = f'<span class="pill {r["status"]}">{STATUS_TEXT[r["status"]]}</span>'
        note = f'<div class="note">{_e(r["note"])}</div>' if r.get("note") else ""
        rows.append(f'<tr><td>{_e(r["metric"])}</td><td>{_e(r["definition"])}{note}</td>'
                    f'<td class="n">&lt; {_e(fmt(r["key"], r["target"]))}</td><td class="n">{_v(r, "mean")}</td>'
                    f'<td class="n">{_v(r, "p95")}</td><td class="n">{r["n"]}</td><td>{pill}</td></tr>')
    bullets = "".join(f"<li>{_e(b)}</li>" for b in d["method"]["bullets"])
    tdefs = " · ".join(f"{TARGETS[k][0]} {fmt(k, v)}" for k, v in d["targets"].items())
    rc = d["run_card"]
    models = ", ".join(f"{k} {v}" for k, v in rc["models"].items()) or "not reported (describe() in the entry file, or --models)"
    c = d.get("context") or {}
    if c.get("n"):
        src = ", ".join(c.get("source") or [])
        ctx_rows = (f'<tr><td>Prompt tokens per LLM call <span class="note">({_e(src)})</span></td><td class="n">{c["tokens_median"]:.0f}</td>'
                    f'<td class="n">{c["tokens_max"]:.0f}</td><td class="n">{c["tokens_first"]:.0f} → {c["tokens_last"]:.0f}</td></tr>')
        if c.get("messages_median") is not None:
            ctx_rows += (f'<tr><td>Messages in context</td><td class="n">{c["messages_median"]:.0f}</td><td class="n">{c["messages_max"]:.0f}</td>'
                         f'<td class="n">{c["messages_first"]:.0f} → {c["messages_last"]:.0f}</td></tr>')
        ctx = (f'<div class="scroll"><table id="fdb-ctx"><tr><th>Context size</th><th>Median</th><th>Max</th><th>First → last call</th></tr>'
               f'{ctx_rows}</table></div><div class="btns"><button type="button" onclick="fdbCopy(\'fdb-ctx\',this)">Copy table</button></div>')
    else:
        ctx = '<p class="note">Not reported: the assistant sent no llm_request marks (docs/STAGES.md).</p>'
    m = d["method"]
    return f"""<style>{CSS}</style>{JS}
<div class="deck">
<div class="band"><div><div class="kick">fdbench · deck summary</div><h1>{_e(d['title'])}</h1></div>
<div class="who">student {_e(d.get('student'))} · run {_e(d.get('run_id'))} · {m['phrases']} phrases × {m['runs']} runs · {m['turns']} turns</div></div>
<div class="body">
<div class="grid">
<section class="blk">
<div class="hd"><span class="num">5</span><h2>Evaluation &amp; Benchmark</h2><span class="slot">template p.2 · §5 table</span></div>
<div class="scroll"><table id="fdb-s5"><tr><th>Metric</th><th>Definition</th><th>Target</th><th>Mean</th><th>p95</th><th>n</th><th></th></tr>{''.join(rows)}</table></div>
<ul id="fdb-method">{bullets}</ul>
<p class="note">Targets: {_e(tdefs)}. Change them in the wizard, with <code>--targets</code>, or in <code>targets.json</code>. "met" = mean and p95 under target.</p>
<div class="btns"><button type="button" onclick="fdbCopy('fdb-s5',this)">Copy table</button><button type="button" onclick="fdbCopy('fdb-method',this)">Copy method bullets</button></div>
</section>
<section class="blk">
<div class="hd"><span class="num">R</span><h2>Results</h2><span class="slot">deck 2 · Results box</span></div>
<div id="fdb-chart" class="scroll">{chart_svg(d)}</div>
<dl id="fdb-card"><dt>RPi model / RAM</dt><dd>{_e(rc.get('device'))}</dd><dt>Cooling</dt><dd>{_e(rc.get('cooling'))}</dd>
<dt>Network</dt><dd>{_e(rc.get('network'))}</dd><dt>Date</dt><dd>{_e(rc.get('date'))}</dd><dt>Model versions</dt><dd>{_e(models)}</dd></dl>
<div class="btns"><button type="button" onclick="fdbSave('svg')">Save chart SVG</button><button type="button" onclick="fdbSave('png')">Save chart PNG</button><button type="button" onclick="fdbCopy('fdb-card',this)">Copy run card</button></div>
</section>
</div>
<section class="blk">
<div class="hd"><span class="num">3</span><h2>Context Engineering</h2><span class="slot">template p.1 · §3 "log context size per run"</span></div>
{ctx}
</section>
<div class="foot"><b style="color:#1b2230">Details below</b><a href="#metrics">Metrics</a><a href="#stage-latency">Stage latency per turn</a><a href="#trials">Trials per group</a><a href="#resources">Machine resources</a></div>
</div></div>"""
