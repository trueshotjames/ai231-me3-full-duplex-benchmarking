"""Results tables in the terminal."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..suites import GROUPS, KIND_LABELS
from ..score.scorer import VOICE_FEATURES

console = Console()


def _pct(r: dict) -> str:
    if not r or r.get("value") is None:
        return "—"
    return f"{r['value']*100:.0f}%  ({r['k']}/{r['n']})"


def _lat(r: dict) -> str:
    if not r or r.get("median") is None:
        return "—"
    s = f"med {r['median']:.2f} s · p95 {r['p95']:.2f} s · mean {r['mean']:.2f} s · n={r['n']}"
    return s


def print_deck(summary: dict, session: dict) -> None:
    """The course template's table first: what goes on the slides."""
    from .deck import STATUS_TEXT, _v, build
    from ..targets import fmt
    d = summary.get("deck_summary") or build({**summary, "session": session})
    t = Table(show_header=True, header_style="bold", title="Deck summary · template §5 (Evaluation & Benchmark)",
              title_justify="left", expand=True)
    for col in ("metric", "definition", "target", "mean", "p95", "n", ""):
        t.add_column(col)
    style = {"met": "green", "p95_over": "yellow", "over": "red", "na": "dim"}
    for r in d["table"]:
        st = r["status"]
        t.add_row(r["metric"], r["definition"] + (f"\n[dim]{r['note']}[/dim]" if r.get("note") else ""),
                  f"< {fmt(r['key'], r['target'])}", _v(r, "mean"), _v(r, "p95"), str(r["n"]),
                  f"[{style[st]}]{STATUS_TEXT[st]}[/{style[st]}]")
    console.print(t)
    rc = d["run_card"]
    console.print(f"[dim]{d['method']['phrases']} phrases × {d['method']['runs']} runs · {rc.get('device') or '?'} · "
                  f"cooling: {rc.get('cooling')} · network: {rc.get('network') or '?'} · models: "
                  f"{', '.join(f'{k} {v}' for k, v in rc['models'].items()) or 'not reported'}[/dim]")
    c = d.get("context") or {}
    if c.get("n"):
        console.print(f"[dim]context size per LLM call: median {c['tokens_median']:.0f} tokens, max {c['tokens_max']:.0f}, "
                      f"first → last {c['tokens_first']:.0f} → {c['tokens_last']:.0f}[/dim]")


def print_results(summary: dict, session: dict) -> None:
    m = summary["metrics"]
    print_deck(summary, session)
    console.rule(f"[bold]Results · {session.get('student', '?')} · {summary['trials']} trials")
    t = Table(show_header=True, header_style="bold dim", expand=True)
    t.add_column("metric", ratio=3)
    t.add_column("value", ratio=4)
    t.add_column("better", ratio=1)

    def row(name, val, better=""):
        t.add_row(name, val, better)

    def sec(title):
        t.add_section()
        t.add_row(f"[bold]{title}[/bold]", "", "")

    sec("Full-Duplex-Bench v1 (2025)")
    row("TOR, pause handling (synthetic)", _pct(m["v1_pause_synth.tor"]), "lower")
    row("TOR, pause handling (Candor)", _pct(m["v1_pause_candor.tor"]), "lower")
    row("TOR, turn-taking (col 6)", _pct(m["v1_turntaking.tor"]), "higher")
    row("Missed turn-taking rate", _pct(m["v1_turntaking.missed_rate"]), "lower")
    row("False turn-taking rate", _pct(m["false_tt.rate"]), "lower")
    for kind in GROUPS["false_tt"].kinds:
        row(f"   · {KIND_LABELS[kind]}", _pct(m[f"false_tt.rate.{kind}"]), "lower")
    row("Latency, turn-taking", _lat(m["v1_turntaking.latency"]), "lower")
    row("TOR, interruption", _pct(m["v1_interrupt.tor"]), "higher")
    r = m["v1_interrupt.rating"]
    row("Judge score, interruption (0–5)", (f"{r['mean']:.2f}  (n={r['n']})" if r["mean"] is not None else "— (not judged)"), "higher")
    row("Latency, interruption", _lat(m["v1_interrupt.latency"]), "lower")
    if m.get("v1_interrupt.not_played"):
        row("   · trials where she never answered the first turn", str(m["v1_interrupt.not_played"]), "")

    sec("Full-Duplex-Bench v1.5 (2026) · overlap handling")
    for g in ("v15_interruption", "v15_backchannel", "v15_talking_to_other", "v15_background"):
        title = GROUPS[g].title.replace("v1.5 ", "")
        want = "Respond" if g == "v15_interruption" else "Resume"
        parts = []
        for lab in ("respond", "resume", "uncertain", "unknown"):
            v = m[f"{g}.{lab}"]
            parts.append(f"{lab.capitalize()} {v['value']*100:.0f}%" if v.get("value") is not None else f"{lab.capitalize()} —")
        judged = m.get(f"{g}.judged", 0)
        if judged:
            row(f"{title} · behaviour (n={m[f'{g}.n']}, judged {judged})", " · ".join(parts), f"{want} higher")
        else:
            row(f"{title} · behaviour (n={m[f'{g}.n']})", "— needs ASR + judge (content labels)", f"{want} higher")
        cl = [m.get(f"{g}.class.{c}", {}) for c in ("through", "paused", "stopped")]
        if any(v.get("value") is not None for v in cl):
            txt = " · ".join(f"{lab} {v['value']*100:.0f}%" for lab, v in zip(("kept talking", "paused & came back", "stopped, silent"), cl)
                             if v.get("value") is not None)
            row("   · timing (no content)", txt, "")
        row(f"   · stop latency", _lat(m[f"{g}.t_stop"]), "lower")
        row(f"   · response latency", _lat(m[f"{g}.t_resp"]), "lower")

    sec(f"v1.5 voice change after an interruption · {m.get('voice.basis', '')}")
    for f in VOICE_FEATURES:
        v = m[f"voice.{f}"]
        if v["n"] == 0:
            val = "— (not scored)"
        else:
            p = f"p={v['p']:.3f}" if v["p"] is not None else "p=—"
            sig = " [green]significant[/green]" if v["significant"] else ""
            val = f"Δ {v['mean_delta']:+.2f}  ({p}, n={v['n']}){sig}"
        row(f"Δ {f}", val, v["expected"])
    r = summary.get("resources") or {}
    run = r.get("run") if r.get("available") else None
    if run:
        hello = r.get("hello") or {}
        sec(f"Machine resources · {hello.get('pi_model') or hello.get('os') or '?'}")
        if run.get("temp_max") is not None:
            row("Peak temperature", f"{run['temp_max']:.1f} °C", "lower")
        if run.get("throttled_share") is not None:
            sh = run["throttled_share"]
            n_thr = sum(1 for p in r.get("per_trial", []) if p.get("throttled"))
            row("Time throttled or capped", (f"[red]{sh*100:.1f}%[/red]" if sh else "0%") + f"  ({n_thr} trials)", "lower")
        if run.get("cpu_median") is not None:
            row("CPU, all cores", f"median {run['cpu_median']:.0f}% · p95 {run['cpu_p95']:.0f}% · busiest core p95 "
                f"{run['busiest_core_p95']:.0f}%" if run.get("busiest_core_p95") is not None else
                f"median {run['cpu_median']:.0f}% · p95 {run['cpu_p95']:.0f}%", "lower")
        for lab, p in (run.get("procs") or {}).items():
            row(f"   · {p['label']}", f"CPU mean {p['cpu_mean'] or 0:.1f}% · RAM peak {p['rss_peak_mb'] or 0:.0f} MB", "")
        if run.get("mem_avail_min_mb") is not None:
            row("Lowest free RAM", f"{run['mem_avail_min_mb']:.0f} MB", "higher")
        if run.get("mhz_min") is not None:
            row("Lowest ARM clock", f"{run['mhz_min']:.0f} MHz", "higher")
        for w in (r.get("start_warnings") or []) + [f"{k} during the run" for k in r.get("new_flags_during_run") or []]:
            row("   [yellow]![/yellow]", w, "")
    console.print(t)
    if summary.get("notes"):
        for n in summary["notes"]:
            console.print(f"[yellow]note:[/yellow] {n}")
    console.print(f"[dim]judge: {summary.get('judge') or 'none'} · asr: {summary.get('asr') or 'none'}[/dim]")
