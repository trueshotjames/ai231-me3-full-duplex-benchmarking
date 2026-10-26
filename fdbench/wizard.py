"""The three questions: student number, rounds, judge. Lettered/numbered menus, remembered defaults."""
from __future__ import annotations

import os
from typing import Optional

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

from .score.judge import PROVIDERS, PROVIDER_ORDER, JudgeConfig
from .settings import load_last, save_last
from .util import fmt_dur

console = Console()
ROUND_OPTIONS = [(1, "check run"), (3, ""), (5, "quick"), (10, "standard"), (15, "extended")]


def ask_student(default: Optional[str]) -> str:
    while True:
        s = Prompt.ask("Student number", default=default or None)
        if s and s.strip():
            return s.strip()


def ask_rounds(round_seconds: float, default: Optional[int]) -> int:
    console.print("\nHow many rounds? (each round plays every test once; stop any time with \\[q])")
    default_choice = None
    for i, (n, label) in enumerate(ROUND_OPTIONS, 1):
        est = fmt_dur(n * round_seconds)
        tag = f"  ({label})" if label else ""
        console.print(f"  \\[{i}] {n:>2} round{'s' if n > 1 else ' '}   ~{est}{tag}")
        if default == n:
            default_choice = str(i)
    console.print(f"  \\[{len(ROUND_OPTIONS)+1}] custom")
    choice = Prompt.ask(">", default=default_choice or "1")
    try:
        k = int(choice)
    except ValueError:
        k = 1
    if 1 <= k <= len(ROUND_OPTIONS):
        return ROUND_OPTIONS[k - 1][0]
    while True:
        n = Prompt.ask("rounds (1–30)", default=str(default or 2))
        if n.isdigit() and 1 <= int(n) <= 30:
            return int(n)


def ask_judge(default_provider: Optional[str], default_model: Optional[str]) -> Optional[JudgeConfig]:
    letters = "abcdefgh"
    console.print("\nLLM judge? (needed for the interruption score and the v1.5 behaviour labels; can be done later)")
    line = []
    for L, p in zip(letters, PROVIDER_ORDER):
        line.append(f"\\[{L}] {PROVIDERS[p]['label']}")
    console.print("  " + "   ".join(line[:4]))
    console.print("  " + "   ".join(line[4:]) + "   \\[n] none, score later")
    default_letter = None
    if default_provider in PROVIDER_ORDER:
        default_letter = letters[PROVIDER_ORDER.index(default_provider)]
    ch = Prompt.ask(">", default=default_letter or "n").strip().lower()
    if ch not in letters or letters.index(ch) >= len(PROVIDER_ORDER):
        return None
    provider = PROVIDER_ORDER[letters.index(ch)]
    p = PROVIDERS[provider]
    base_url = None
    if provider == "custom":
        base_url = Prompt.ask("base URL (OpenAI-compatible, e.g. http://host:8000/v1)",
                              default=os.environ.get("JUDGE_BASE_URL") or None)
        os.environ["JUDGE_BASE_URL"] = base_url
    # model
    models = p["models"]
    if models:
        console.print("  model:")
        default_idx = None
        for i, mname in enumerate(models, 1):
            tag = "  (paper)" if mname == p.get("paper") else ""
            console.print(f"    \\[{i}] {mname}{tag}")
            if default_model == mname:
                default_idx = str(i)
        console.print(f"    \\[{len(models)+1}] other")
        mch = Prompt.ask("  >", default=default_idx or "1")
        if mch.isdigit() and 1 <= int(mch) <= len(models):
            model = models[int(mch) - 1]
        else:
            model = Prompt.ask("  model name", default=default_model or None)
    else:
        model = Prompt.ask("  model name", default=default_model or None)
    # key
    api_key = None
    if p["key_env"]:
        if os.environ.get(p["key_env"]):
            console.print(f"  [dim]{p['key_env']} found in .env / environment[/dim]")
        else:
            api_key = Prompt.ask(f"  {p['key_env']} (not written to any file; put it in .env to skip this)", password=True)
            if api_key:
                os.environ[p["key_env"]] = api_key
    return JudgeConfig(provider=provider, model=model, api_key=api_key, base_url=base_url)


def ask_connection(default: Optional[str]) -> str:
    console.print("\nHow does fdbench reach her?")
    console.print("  \\[a] direct feed: she runs in benchmark mode (FDBENCH_URL), no audio devices")
    console.print("  \\[b] virtual audio cables (VB-CABLE / PipeWire), assistant unchanged")
    ch = Prompt.ask(">", default="b" if default == "cables" else "a").strip().lower()
    return "cables" if ch == "b" else "direct"


def ask_where(default: Optional[str]) -> str:
    console.print("\nWhere does she run?")
    console.print("  \\[a] on this machine: fdbench starts her itself (one terminal)")
    console.print("  \\[b] on another machine (Raspberry Pi): you start her there with launch_assistant.py --url ws://THIS-IP:8791")
    ch = Prompt.ask(">", default="b" if default == "remote" else "a").strip().lower()
    return "remote" if ch == "b" else "local"


def run_wizard(round_seconds: float, ask_connect: bool = True, ask_where_q: bool = True, **kw) -> dict:
    ask_where_q = kw.get("ask_where", ask_where_q)
    last = load_last()
    console.print("[bold]fdbench[/bold] · full-duplex voice assistant benchmark\n")
    student = ask_student(last.get("student"))
    rounds = ask_rounds(round_seconds, last.get("rounds"))
    connect = ask_connection(last.get("connect")) if ask_connect else None
    where = ask_where(last.get("where")) if (ask_where_q and connect != "cables") else None
    judge = ask_judge(last.get("judge_provider"), last.get("judge_model"))
    save_last(student=student, rounds=rounds, connect=connect, where=where, judge_provider=(judge.provider if judge else "none"),
              judge_model=(judge.model if judge else None))
    return {"student": student, "rounds": rounds, "connect": connect, "where": where, "judge": judge}


def confirm_proceed(n_warnings: int) -> bool:
    msg = (f"{n_warnings} warning{'s' if n_warnings != 1 else ''}. " if n_warnings else "All checks passed. ") + "\\[P]roceed / \\[Q]uit"
    ch = Prompt.ask(msg, default="p").strip().lower()
    return ch != "q"
