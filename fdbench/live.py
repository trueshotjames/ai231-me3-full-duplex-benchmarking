"""Live terminal display during the run (rich): one multi-line block per trial, printed once."""
from __future__ import annotations

import threading
from typing import Optional

from rich.console import Console, Group as RGroup
from rich.live import Live
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from .util import fmt_dur

import sys
console = Console(width=None if sys.stdout.isatty() else 150)


class LiveTable:
    """Each finished trial is printed once, as a short block (header line, full transcripts, expected, actual,
    verdict), and scrolls up like a log. Only a small region at the bottom (progress, the trial in progress,
    status, keys) redraws in place, so the terminal is never flooded."""

    def __init__(self, student: str, total: int, rounds: int, keep_rows: int = 14, enabled: bool = True):
        self.student = student
        self.total = total
        self.rounds = rounds
        self.current: Optional[dict] = None
        self.header = ""
        self.footer = "[dim]\\[space] pause between trials   \\[s] skip clip   \\[q] stop and score[/dim]"
        self.enabled = enabled
        self._lock = threading.Lock()
        self._live: Optional[Live] = None
        self.status_line = ""

    # -------------------------------------------------------------- blocks
    def _block(self, r: dict):
        """One trial as a few lines: title, specs, transcripts (wrapped, never cut), expected, actual, verdict."""
        title = Text()
        title.append(f"{r['n']:>3}/{self.total}  ", style="bold")
        title.append(r["test"], style="bold cyan")
        title.append(f"  ·  {r['clip']}", style="dim")
        g = Table.grid(padding=(0, 2), expand=True)
        g.add_column(width=12, style="dim", no_wrap=True)
        g.add_column(ratio=1, overflow="fold")
        if r.get("specs"):
            g.add_row("clip", r["specs"])
        for label, text in r.get("lines") or []:
            g.add_row(label, Text(text, style="italic"))
        g.add_row("expected", r["expected"])
        g.add_row("actual", Text(str(r["actual"]), style="yellow" if r.get("live") else ""))
        if r["verdict"]:
            style = {True: "bold green", False: "bold red"}.get(r["ok"], "bold yellow")
            g.add_row("verdict", Text(str(r["verdict"]), style=style))
        return RGroup(Text(""), title, Padding(g, (0, 0, 0, 5)))

    def _print(self, renderable) -> None:
        if self._live is not None:
            self._live.console.print(renderable)
        else:
            console.print(renderable)

    def add(self, index: int, test: str, clip: str, expected: str, details: Optional[tuple] = None) -> dict:
        specs, lines = details if details else ("", [])
        row = {"n": index, "test": test, "clip": clip, "expected": expected, "specs": specs, "lines": lines,
               "actual": "▶ playing…", "verdict": "", "ok": None, "live": True}
        with self._lock:
            self.current = row
        self.refresh()
        return row

    def update(self, row: dict, **kw) -> None:
        with self._lock:
            row.update(kw)
            finished = not row.get("live", True)
            if finished and self.current is row:
                self.current = None
        if finished:
            self._print(self._block(row))  # printed once, scrolls up with the rest
        self.refresh()

    def set_header(self, done: int, round_no: int, elapsed: float, remaining: Optional[float]) -> None:
        rem = f" · est. remaining {fmt_dur(remaining)}" if remaining is not None else ""
        self.header = (f"[bold]Round {round_no}/{self.rounds}[/bold] · trial {done}/{self.total} · "
                       f"elapsed {fmt_dur(elapsed)}{rem} · {self.student}")
        self.refresh()

    def set_status(self, text: str) -> None:
        self.status_line = text
        self.refresh()

    # ------------------------------------------------------------- render
    def render(self):
        parts = [Text("─" * 20, style="dim")]
        if self.header:
            parts.append(Text.from_markup(self.header))
        with self._lock:
            cur = dict(self.current) if self.current else None
        if cur:
            parts.append(self._block(cur))
        if self.status_line:
            parts.append(Text.from_markup(self.status_line))
        parts.append(Text.from_markup(self.footer))
        return RGroup(*parts)

    def refresh(self):
        if self._live is not None:
            self._live.update(self.render())

    def __enter__(self):
        if self.enabled:
            self._live = Live(self.render(), console=console, refresh_per_second=4, transient=True,
                              vertical_overflow="crop")
            self._live.__enter__()
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            self._live.__exit__(*exc)
            self._live = None
        if self.header:
            console.print(Text.from_markup(self.header))


class KeyWatcher:
    """Non-blocking single-key reader: space = pause, s = skip, q = quit. Works on Windows and POSIX ttys."""

    def __init__(self):
        self.paused = False
        self.skip = False
        self.quit = False
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        import sys
        if not sys.stdin.isatty():
            return self
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        import sys
        try:
            if sys.platform == "win32":
                import msvcrt
                while not self._stop.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        self._handle(ch)
                    else:
                        self._stop.wait(0.1)
            else:
                import select
                import termios
                import tty
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                    while not self._stop.is_set():
                        r, _, _ = select.select([sys.stdin], [], [], 0.1)
                        if r:
                            self._handle(sys.stdin.read(1))
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:  # noqa: BLE001  (no tty, odd terminals) - keys simply do nothing
            return

    def _handle(self, ch: str):
        if ch == " ":
            self.paused = not self.paused
        elif ch in ("s", "S"):
            self.skip = True
        elif ch in ("q", "Q", "\x03"):
            self.quit = True
