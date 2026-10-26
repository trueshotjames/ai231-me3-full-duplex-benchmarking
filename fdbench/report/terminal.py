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
    lo, hi = r.get("ci95", [None, None])
    ci = f"  [{lo*100:.0f}–{hi*100:.0f}]" if lo is not None else ""
    return f"{r['value']*100:.0f}%  ({r['k']}/{r['n']}){ci}"


def _lat(r: dict) -> str:
    if not r or r.get("median") is None:
        return "—"
    s = f"med {r['median']:.2f} s · p95 {r['p95']:.2f} s · mean {r['mean']:.2f} s · n={r['n']}"
    return s


def print_results(summary: dict, session: dict) -> None:
    m = summary["metrics"]
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
    console.print(t)
    if summary.get("notes"):
        for n in summary["notes"]:
            console.print(f"[yellow]note:[/yellow] {n}")
    console.print(f"[dim]judge: {summary.get('judge') or 'none'} · asr: {summary.get('asr') or 'none'}[/dim]")
