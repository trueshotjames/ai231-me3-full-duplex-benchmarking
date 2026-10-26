"""Live terminal table during the run (rich). One row per trial; rows update in place."""
from __future__ import annotations

import threading
from typing import Optional

from rich.console import Console, Group as RGroup
from rich.live import Live
from rich.table import Table
from rich.text import Text

from .util import fmt_dur

import sys
console = Console(width=None if sys.stdout.isatty() else 150)


class LiveTable:
    """Finished trials are printed once, as one row each, and scroll up like a log. Only a small block at the
    bottom (header, the trial in progress, status, keys) redraws in place, so the terminal is never flooded."""

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
        self._printed_header = False

    # ---------------------------------------------------------------- rows
    def _table(self, header: bool) -> Table:
        t = Table(expand=True, show_header=header, show_edge=False, box=None, pad_edge=False,
                  header_style="bold dim")
        t.add_column("#", justify="right", width=4, no_wrap=True)
        t.add_column("test", width=24, no_wrap=True, overflow="ellipsis")
        t.add_column("clip", ratio=3, no_wrap=True, overflow="ellipsis")
        t.add_column("expected", ratio=2, no_wrap=True, overflow="ellipsis")
        t.add_column("actual", ratio=3, no_wrap=True, overflow="ellipsis")
        t.add_column("verdict", width=26, no_wrap=True, overflow="ellipsis")
        return t

    def _add_row(self, t: Table, r: dict) -> None:
        v = Text(str(r["verdict"]))
        if r["ok"] is True:
            v.stylize("green")
        elif r["ok"] is False:
            v.stylize("red")
        else:
            v.stylize("yellow")
        actual = Text(str(r["actual"]), style="yellow" if r.get("live") else "")
        t.add_row(str(r["n"]), r["test"], r["clip"], r["expected"], actual, v)

    def _print(self, renderable) -> None:
        if self._live is not None:
            self._live.console.print(renderable)
        else:
            console.print(renderable)

    def add(self, index: int, test: str, clip: str, expected: str) -> dict:
        if not self._printed_header:
            self._printed_header = True
            self._print(self._table(header=True))  # column titles once
        row = {"n": index, "test": test, "clip": clip, "expected": expected, "actual": "▶ playing…",
               "verdict": "", "ok": None, "live": True}
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
            t = self._table(header=False)
            self._add_row(t, row)
            self._print(t)  # printed once, scrolls up with the rest
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
            t = self._table(header=False)
            self._add_row(t, cur)
            parts.append(t)
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
