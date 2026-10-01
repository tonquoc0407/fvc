"""Terminal dashboard. Plain ANSI, no curses, so it also runs on Windows Terminal."""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional, Sequence, Tuple

from . import DEFAULT_BASE_URL, WINDOW_CHOICES
from .fmt import (
    ago,
    exact_int,
    human_int,
    limit_amount,
    local_clock,
    money,
    reset_label,
    truncate,
    until,
)
from .models import Limit, Usage
from .poller import Poller, Snapshot

WINDOWS = list(WINDOW_CHOICES)
VIEWS = ["log", "models", "keys"]

# Ask the terminal for mouse events, wheel included, in SGR encoding.
MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1006l\x1b[?1000l"

# Synchronized output (DEC 2026): the terminal buffers a whole frame and paints
# it in one go. Terminals without support ignore these silently.
SYNC_BEGIN = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"

# Braille cells are 2 columns by 4 rows of dots; these are the bit for each.
BRAILLE_DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
BRAILLE_BASE = 0x2800


class Palette:
    BORDER = "38;5;239"
    MUTED = "38;5;244"
    TEXT = "38;5;252"
    ACCENT = "38;5;44"
    BLUE = "38;5;75"
    GREEN = "38;5;78"
    YELLOW = "38;5;179"
    RED = "38;5;203"
    VIOLET = "38;5;141"
    # Green through amber to red, walked along a gauge from left to right.
    RAMP = ("38;5;78", "38;5;114", "38;5;149", "38;5;185", "38;5;215", "38;5;203")


class Style:
    def __init__(self, enabled: bool = True):
        self.on = enabled

    def paint(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.on else text

    def border(self, t):  return self.paint(Palette.BORDER, t)
    def muted(self, t):   return self.paint(Palette.MUTED, t)
    def text(self, t):    return self.paint(Palette.TEXT, t)
    def accent(self, t):  return self.paint(Palette.ACCENT, t)
    def blue(self, t):    return self.paint(Palette.BLUE, t)
    def green(self, t):   return self.paint(Palette.GREEN, t)
    def yellow(self, t):  return self.paint(Palette.YELLOW, t)
    def red(self, t):     return self.paint(Palette.RED, t)
    def violet(self, t):  return self.paint(Palette.VIOLET, t)
    def bold(self, t):    return self.paint("1", t)
    def invert(self, t):  return self.paint("7", t)
    def tag(self, code, t): return self.paint(f"{code};7", t)

    def level(self, ratio: Optional[float]) -> str:
        if ratio is None:
            return Palette.MUTED
        index = min(len(Palette.RAMP) - 1, int(ratio / 100 * len(Palette.RAMP)))
        return Palette.RAMP[index]

    def by_level(self, ratio: Optional[float], text: str) -> str:
        return self.paint(self.level(ratio), text)

    def gauge(self, ratio: Optional[float], width: int) -> str:
        """Bar whose filled cells walk the ramp, so the tail reddens as it fills."""
        if width <= 0:
            return ""
        if ratio is None:
            return self.border("░" * width)
        filled = int(round(max(0.0, min(100.0, ratio)) / 100 * width))
        out, run, run_code = [], 0, None
        for index in range(width):
            code = self.level((index + 1) / width * 100) if index < filled else Palette.BORDER
            if code != run_code:
                if run:
                    out.append(self.paint(run_code, ("█" if run_code != Palette.BORDER else "░") * run))
                run, run_code = 0, code
            run += 1
        out.append(self.paint(run_code, ("█" if run_code != Palette.BORDER else "░") * run))
        return "".join(out)


class Row:
    """A line built from spans, tracking visible width so ANSI never skews it."""

    def __init__(self) -> None:
        # (text, painter, visible width). Pre-coloured spans carry painter=None
        # and a width that is not len(text).
        self.items: List[Tuple[str, object, int]] = []
        self.width = 0

    def add(self, text: str, painter=None) -> "Row":
        text = str(text)
        self.items.append((text, painter, len(text)))
        self.width += len(text)
        return self

    def add_painted(self, painted: str, visible: int) -> "Row":
        self.items.append((painted, None, visible))
        self.width += visible
        return self

    def extend(self, other: "Row") -> "Row":
        self.items.extend(other.items)
        self.width += other.width
        return self

    def pad_to(self, column: int) -> "Row":
        if self.width < column:
            self.add(" " * (column - self.width))
        return self

    def render(self, limit: Optional[int] = None) -> str:
        out, used = [], 0
        for text, painter, visible in self.items:
            if limit is not None:
                if used >= limit:
                    break
                if used + visible > limit:
                    if visible != len(text):
                        break  # pre-coloured span cannot be cut safely
                    text, visible = text[: limit - used], limit - used
            out.append(painter(text) if painter else text)
            used += visible
        return "".join(out)


def braille_chart(values: Sequence[float], width: int, height: int) -> List[str]:
    """Filled area chart spanning the full width. Each cell holds 2x4 dots."""
    if width <= 0 or height <= 0:
        return []
    series = [max(0.0, float(v)) for v in values]
    if not series:
        return [chr(BRAILLE_BASE) * width] * height

    columns, dot_rows = width * 2, height * 4
    if len(series) >= columns:
        # Downsample on peaks: a spike that would vanish under averaging is the
        # thing worth seeing here.
        step = len(series) / columns
        sampled = [max(series[int(i * step):max(int(i * step) + 1, int((i + 1) * step))])
                   for i in range(columns)]
    else:
        sampled = [series[int(i * len(series) / columns)] for i in range(columns)]

    peak = max(sampled) or 1.0
    grid = [[0] * width for _ in range(height)]
    for x, value in enumerate(sampled):
        for step in range(max(1, int(round(value / peak * dot_rows)))):
            y = dot_rows - 1 - step
            grid[y // 4][x // 2] |= BRAILLE_DOTS[y % 4][x % 2]
    return ["".join(chr(BRAILLE_BASE + cell) for cell in line) for line in grid]


class KeyReader:
    """Non-blocking key reader.

    Reads the file descriptor with os.read rather than sys.stdin.read: the
    TextIOWrapper pulls a whole burst of bytes into Python's own buffer, so a
    following select() reports nothing available and escape sequences get split.
    """

    def __init__(self) -> None:
        self.is_windows = sys.platform.startswith("win")
        try:
            self.enabled = sys.stdin.isatty()
            self._fd = None if self.is_windows else sys.stdin.fileno()
        except (ValueError, OSError):
            self.enabled, self._fd = False, None
        self._saved = None
        self._pending = ""

    def __enter__(self) -> "KeyReader":
        if self.enabled and not self.is_windows:
            import termios
            import tty

            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)  # giu ISIG -> Ctrl-C van hoat dong
        return self

    def __exit__(self, *exc) -> None:
        if self._saved is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def poll(self, timeout: float) -> Optional[str]:
        if not self.enabled:
            time.sleep(timeout)
            return None
        if self.is_windows:
            return self._poll_windows(timeout)
        char = self._read_char(timeout)
        if char is None:
            return None
        return self._read_escape() if char == "\x1b" else char

    def _poll_windows(self, timeout: float) -> Optional[str]:
        import msvcrt

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ("\x00", "\xe0"):
                    return {"H": "up", "P": "down", "I": "pgup", "Q": "pgdn",
                            "G": "home", "O": "end"}.get(msvcrt.getwch())
                return char
            time.sleep(0.02)
        return None

    def _read_char(self, timeout: float = 0.05) -> Optional[str]:
        """One character, from the pending buffer if there is one, else from the fd."""
        if not self._pending:
            import select

            ready, _, _ = select.select([self._fd], [], [], timeout)
            if not ready:
                return None
            try:
                data = os.read(self._fd, 1024)
            except OSError:
                return None
            if not data:
                return None
            self._pending = data.decode("utf-8", "replace")
        char, self._pending = self._pending[0], self._pending[1:]
        return char

    def _read_escape(self) -> Optional[str]:
        """Read the rest of an escape sequence.

        Unrecognised sequences return None. They must never return "esc", which
        would quit the app the moment the wheel is rolled.
        """
        nxt = self._read_char()
        if nxt is None:
            return "esc"
        if nxt == "O":  # SS3 - che do application cursor: \x1bOA
            return CURSOR_CODES.get(self._read_char() or "")
        if nxt != "[":
            return None

        body = ""
        while True:
            char = self._read_char()
            if char is None:
                break
            body += char
            if body == "M":  # X10 mouse: exactly three raw bytes follow
                button = self._read_char()
                self._read_char()  # column; must be consumed or it reads back as a keypress
                self._read_char()  # row
                return _x10_mouse(button)
            if char.isalpha() or char == "~":
                break
        if body.startswith("<"):  # SGR mouse: \x1b[<64;12;30M
            return _sgr_mouse(body)
        return CURSOR_CODES.get(body)


CURSOR_CODES = {
    "A": "up", "B": "down", "C": "right", "D": "left",
    "H": "home", "F": "end", "1~": "home", "4~": "end",
    "5~": "pgup", "6~": "pgdn",
}


def _wheel(button: int) -> Optional[str]:
    return {64: "wheel_up", 65: "wheel_down"}.get(button)


def _x10_mouse(first: Optional[str]) -> Optional[str]:
    if first is None:
        return None
    return _wheel(ord(first) - 32)


def _sgr_mouse(body: str) -> Optional[str]:
    try:
        return _wheel(int(body[1:].split(";", 1)[0]))
    except (ValueError, IndexError):
        return None



# ---------------------------------------------------------------- dashboard
class Dashboard:
    def __init__(self, poller: Poller, base_url: str = DEFAULT_BASE_URL, color: bool = True):
        self.poller = poller
        self.host = base_url.replace("https://", "").replace("http://", "")
        self.s = Style(color)
        self.view = 0
        self.cursor = 0
        self.scroll = 0
        window = poller.snapshot().window
        self.window_index = WINDOWS.index(window) if window in WINDOWS else 3
        self.show_help = False
        self.running = True
        self.mouse = True
        self.message: Tuple[str, float] = ("", 0.0)
        self._page = 10
        self._rows_shown = 0

    # -- terminal ------------------------------------------------------
    def size(self) -> Tuple[int, int]:
        try:
            columns, rows = os.get_terminal_size()
        except OSError:
            columns, rows = 100, 30
        return max(64, columns), max(20, rows)

    def notify(self, text: str) -> None:
        self.message = (text, time.monotonic() + 2.5)

    def _message(self) -> str:
        text, expiry = self.message
        return text if time.monotonic() < expiry else ""

    # -- frame ---------------------------------------------------------
    def _rule(self, width: int, left: Row, right: Optional[Row], start: str, end: str) -> str:
        s = self.s
        fill = width - 2 - left.width - (right.width if right else 0)
        out = Row().add(start, s.border).extend(left)
        out.add("─" * max(0, fill), s.border)
        if right:
            out.extend(right)
        return out.add(end, s.border).render(width)

    def _line(self, row: Row, width: int) -> str:
        s = self.s
        inner = width - 4
        return (s.border("│ ") + row.render(inner)
                + " " * max(0, inner - min(row.width, inner)) + s.border(" │"))

    def _blank(self, width: int) -> str:
        return self._line(Row(), width)

    def _top(self, width: int, snap: Snapshot) -> str:
        s = self.s
        left = Row().add("─ ", s.border).add("Finnvnoi API Check", s.bold).add(" · ", s.border).add(self.host, s.muted).add(" ")
        countdown = f" ↻ {snap.seconds_to_next:.0f}s "
        return self._rule(width, left, Row().add(countdown, s.muted), "╭", "╮")

    def _section(self, width: int, title: str) -> str:
        s = self.s
        return self._rule(width, Row().add("─ ", s.border).add(title, s.muted).add(" "), None, "├", "┤")

    def _tabs(self, width: int) -> str:
        s = self.s
        left = Row().add("─", s.border)
        for index, name in enumerate(VIEWS):
            active = index == self.view
            left.add(f" {name} ", (lambda t: s.tag(Palette.ACCENT, t)) if active else s.muted)
            if index < len(VIEWS) - 1:
                left.add("│", s.border)
        return self._rule(width, left.add(" "), None, "├", "┤")

    def _bottom(self, width: int, snap: Snapshot) -> str:
        s = self.s
        message = self._message()
        if message:
            left = Row().add("─ ", s.border).add(truncate(message, width - 8), s.yellow).add(" ")
            return self._rule(width, left, None, "╰", "╯")

        if self.show_help:
            keys = "q quit · r refresh · Tab view · k/K key · w window · ↑↓ move · m mouse · +/- interval"
        else:
            keys = "q quit · r refresh · Tab view · ? help"
        left = Row().add("─ ", s.border).add(truncate(keys, max(10, width - 34)), s.muted).add(" ")

        if snap.error:
            state = f"retry {snap.consecutive_failures}"
            right = Row().add(" ", s.border).add(state, s.red).add(" ─", s.border)
        else:
            stamp = f"updated {ago(snap.fetched_at)}" if snap.fetched_at else "no data yet"
            right = Row().add(" ", s.border).add(stamp, s.muted).add(" ─", s.border)
        return self._rule(width, left, right, "╰", "╯")

    # -- sections ------------------------------------------------------
    def _header(self, snap: Snapshot, inner: int) -> Row:
        s = self.s
        row = Row().add(snap.active_label, s.bold).add("  ")
        row.add(snap.active_masked, s.muted).add("   ")
        row.add(f" {WINDOWS[self.window_index].upper()} ", lambda t: s.tag(Palette.VIOLET, t))
        row.add("   ")
        if snap.error:
            row.add("● ", s.red).add(truncate(snap.error, max(16, inner - row.width - 2)), s.red)
        elif not snap.has_data:
            row.add("◌ ", s.yellow).add("connecting", s.yellow)
        else:
            row.add("● ", s.green).add("live", s.green)
            if snap.latency_ms:
                row.add(f"  {snap.latency_ms:.0f}ms", s.muted)

        expires = (snap.usage.expires_dt if snap.usage else None)
        if expires:
            tail = f"key expires {until(expires)}"
            if row.width + 3 + len(tail) <= inner:
                row.pad_to(inner - len(tail))
                row.add(tail, s.muted)
        return row

    def _totals(self, snap: Snapshot, inner: int) -> List[Row]:
        s = self.s
        usage = snap.usage or Usage()
        cache_rate = usage.cache_hit_rate
        # Requests, tokens and cost always earn their place; the rest come back
        # as the terminal gets wider.
        columns = [
            ("REQUESTS", exact_int(usage.request_count), s.text),
            ("TOKENS", human_int(usage.total_tokens), s.accent),
        ]
        if inner >= 56:
            cached = human_int(usage.cached_input_tokens) + (f"  {cache_rate:.0f}%" if cache_rate else "")
            columns.append(("CACHED", cached, s.blue))
        columns.append(("COST", money(usage.total_cost_usd), s.green))

        aggregates = snap.activity.model_aggregates if snap.activity else []
        attempts = sum(a.total_count for a in aggregates)
        if attempts and inner >= 72:
            rate = sum(a.success_count for a in aggregates) / attempts * 100
            painter = s.green if rate >= 95 else (s.yellow if rate >= 80 else s.red)
            columns.append(("SUCCESS", f"{rate:.1f}%", painter))
        window = WINDOWS[self.window_index]
        if window != "lt" and snap.activity and inner >= 88:
            columns.append((f"{window.upper()} COST", money(snap.activity.totals.total_cost_usd), s.violet))

        step = inner // len(columns)
        labels, values = Row(), Row()
        for index, (label, value, painter) in enumerate(columns):
            labels.add(truncate(label, step - 2), s.muted).pad_to(step * (index + 1))
            values.add(truncate(value, step - 2), painter).pad_to(step * (index + 1))
        return [labels, values]

    def _chart_series(self, snap: Snapshot) -> List[float]:
        return [float(r.total_tokens) for r in reversed(snap.rows)]

    def _chart(self, series: List[float], inner: int, height: int) -> List[Row]:
        return [Row().add(line, self.s.accent) for line in braille_chart(series, inner, height)]

    def _limits(self, limits: List[Limit], inner: int) -> List[Row]:
        s = self.s
        upstream = any(l.source != "api_key_limit" for l in limits)
        long_tag = inner >= 80
        tag_width = (10 if long_tag else 4) if upstream else 0
        name_cap = 34 if inner >= 96 else (26 if inner >= 74 else 20)
        name_width = min(name_cap, max(12, max(len(l.label) for l in limits) + 1))
        amount_width, reset_width = 20, 18

        fixed = name_width + tag_width + 8  # gap plus the percentage
        show_amount = (inner - fixed - 5 >= amount_width)
        if show_amount:
            fixed += amount_width
        show_reset = (inner - fixed - 5 >= reset_width)
        if show_reset:
            fixed += reset_width
        bar_width = max(5, min(26, inner - fixed))

        rows = []
        for limit in limits:
            ratio = limit.percent
            row = Row().add(truncate(limit.label, name_width).ljust(name_width), s.text)
            if tag_width:
                if limit.source == "api_key_limit":
                    row.add(" " * tag_width)
                else:
                    row.add(("upstream" if long_tag else "up").ljust(tag_width), s.muted)
            row.add_painted(s.gauge(ratio, bar_width), bar_width)
            row.add("  ")
            row.add((f"{ratio:5.1f}%" if ratio is not None else "    -"), lambda t, r=ratio: s.by_level(r, t))
            if show_amount:
                amount = (f"{limit_amount(limit.limit_type, limit.current_value)}/"
                          f"{limit_amount(limit.limit_type, limit.max_value)}")
                row.add("  " + truncate(amount, amount_width - 2).ljust(amount_width - 2), s.muted)
            if show_reset and limit.reset_at:
                row.add("  " + truncate(reset_label(limit.reset_dt), reset_width - 2), s.muted)
            rows.append(row)
        return rows

    # -- table ---------------------------------------------------------
    def _table(self, snap: Snapshot, inner: int, height: int) -> List[Row]:
        builder = (self._table_log, self._table_models, self._table_keys)[self.view]
        header, rows = builder(snap, inner)
        self._rows_shown = len(rows)
        visible = max(1, height - 1)
        self._page = visible

        if not rows:
            empty = ("nothing logged for this key yet", "no model data yet",
                     "one key configured · add more with: fvc key add <label>")[self.view]
            return [header, Row().add(truncate(empty, inner), self.s.muted)]

        self.cursor = max(0, min(self.cursor, len(rows) - 1))
        self.scroll = max(0, min(self.scroll, max(0, len(rows) - visible)))
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + visible:
            self.scroll = self.cursor - visible + 1

        out = [header]
        for index in range(self.scroll, min(len(rows), self.scroll + visible)):
            row = rows[index]
            if index == self.cursor:
                marked = Row().add("▸", self.s.accent)
                marked.items.extend(row.items[1:])
                marked.width = row.width
                row = marked
            out.append(row)
        return out

    def _table_log(self, snap: Snapshot, inner: int) -> Tuple[Row, List[Row]]:
        s = self.s
        cached = inner >= 72
        model_width = max(10, min(30, inner - (55 if cached else 45)))
        header = Row().add(" ").add("TIME".ljust(10), s.muted).add("MODEL".ljust(model_width + 2), s.muted)
        header.add("STATUS".ljust(12), s.muted).add("TOKENS".rjust(9), s.muted)
        if cached:
            header.add("CACHED".rjust(10), s.muted)
        header.add("COST".rjust(11), s.muted)

        rows = []
        for entry in snap.rows:
            ok = entry.ok
            row = Row().add(" ")
            row.add(local_clock(entry.when).ljust(10), s.muted)
            row.add(truncate(entry.model, model_width).ljust(model_width + 2), s.accent)
            status = entry.error_code or entry.status or ("ok" if ok else "error")
            row.add(truncate(status, 11).ljust(12), s.green if ok else s.red)
            row.add(human_int(entry.total_tokens).rjust(9), s.text)
            if cached:
                row.add(human_int(entry.cached_input_tokens).rjust(10), s.muted)
            row.add(money(entry.cost_usd).rjust(11), s.green)
            rows.append(row)
        return header, rows

    def _table_models(self, snap: Snapshot, inner: int) -> Tuple[Row, List[Row]]:
        s = self.s
        aggregates = sorted(
            snap.activity.model_aggregates if snap.activity else [],
            key=lambda a: a.total_cost_usd,
            reverse=True,
        )
        share_column = inner >= 78
        model_width = max(12, min(32, inner - (60 if share_column else 38)))
        header = Row().add(" ").add("MODEL".ljust(model_width + 2), s.muted)
        header.add("OK".rjust(7), s.muted).add("FAIL".rjust(7), s.muted)
        header.add("TOKENS".rjust(10), s.muted).add("COST".rjust(11), s.muted)
        if share_column:
            header.add("   SHARE", s.muted)

        grand = sum(a.total_cost_usd for a in aggregates) or 1.0
        rows = []
        for aggregate in aggregates:
            share = aggregate.total_cost_usd / grand * 100
            row = Row().add(" ")
            row.add(truncate(aggregate.model, model_width).ljust(model_width + 2), s.accent)
            row.add(exact_int(aggregate.success_count).rjust(7), s.green)
            row.add(exact_int(aggregate.failed_count).rjust(7), s.red if aggregate.failed_count else s.muted)
            row.add(human_int(aggregate.total_tokens).rjust(10), s.text)
            row.add(money(aggregate.total_cost_usd).rjust(11), s.green)
            if share_column:
                row.add("   ").add_painted(s.gauge(share, 12), 12)
                row.add(f" {share:5.1f}%", s.muted)
            rows.append(row)
        return header, rows

    def _table_keys(self, snap: Snapshot, inner: int) -> Tuple[Row, List[Row]]:
        s = self.s
        key_column = inner >= 78
        limit_column = inner >= 104
        header = Row().add(" ").add("  ").add("LABEL".ljust(14), s.muted)
        if key_column:
            header.add("KEY".ljust(22), s.muted)
        header.add("REQ".rjust(8), s.muted).add("TOKENS".rjust(10), s.muted)
        header.add("COST".rjust(11), s.muted)
        if limit_column:
            header.add("   TIGHTEST LIMIT", s.muted)

        rows = []
        for index, status in enumerate(snap.keys):
            active = index == self.poller.active_index
            row = Row().add(" ")
            row.add("● " if active else "  ", s.accent)
            row.add(truncate(status.label, 13).ljust(14), s.bold if active else s.text)
            if key_column:
                row.add(truncate(status.masked_key, 21).ljust(22), s.muted)
            if status.ok and status.usage:
                usage = status.usage
                row.add(exact_int(usage.request_count).rjust(8), s.text)
                row.add(human_int(usage.total_tokens).rjust(10), s.accent)
                row.add(money(usage.total_cost_usd).rjust(11), s.green)
                worst = usage.worst_limit
                if limit_column and worst and worst.percent is not None:
                    row.add("   ").add_painted(s.gauge(worst.percent, 10), 10)
                    room = max(6, inner - row.width - 7)
                    row.add(f" {worst.percent:3.0f}%  {truncate(worst.label, room)}", s.muted)
            else:
                detail = status.error or "could not read usage"
                if status.status_code:
                    detail = f"HTTP {status.status_code} · {detail}"
                row.add("  " + truncate(detail, max(20, inner - 42)), s.red)
            rows.append(row)
        return header, rows

    # -- assembly ------------------------------------------------------
    def render(self) -> str:
        width, height = self.size()
        inner = width - 4
        snap = self.poller.snapshot()
        usage = snap.usage or Usage()
        limits = usage.limits + usage.upstream_limits

        # top, header, totals rule, two totals rows, tab rule, bottom
        overhead = 6
        if limits:
            overhead += 1 + len(limits)
        series = self._chart_series(snap)
        chart_height = max(0, min(3, height - overhead - 4)) if series else 0
        if chart_height:
            overhead += 1 + chart_height
        table_height = max(2, height - overhead)

        lines = [self._top(width, snap), self._line(self._header(snap, inner), width)]
        lines.append(self._section(width, "totals"))
        lines += [self._line(row, width) for row in self._totals(snap, inner)]
        if chart_height:
            lines.append(self._section(width, f"tokens per request · peak {human_int(max(series))}"))
            lines += [self._line(row, width) for row in self._chart(series, inner, chart_height)]
        if limits:
            pool = []
            if usage.account_pool_primary is not None:
                pool.append(f"{usage.account_pool_primary:.0f}%")
            if usage.account_pool_secondary is not None:
                pool.append(f"{usage.account_pool_secondary:.0f}%")
            title = "limits" + (f" · account pool {' / '.join(pool)}" if pool else "")
            lines.append(self._section(width, title))
            lines += [self._line(row, width) for row in self._limits(limits, inner)]

        lines.append(self._tabs(width))
        lines += [self._line(row, width) for row in self._table(snap, inner, table_height)]

        while len(lines) < height - 1:
            lines.append(self._blank(width))
        lines = lines[: height - 1]
        lines.append(self._bottom(width, snap))

        out = [SYNC_BEGIN]
        for index, line in enumerate(lines):
            # Absolute positioning per line. Ending lines with "\r\n" makes the
            # last one scroll the terminal by a row on every frame, which flickers.
            out.append(f"\x1b[{index + 1};1H{line}\x1b[K")
        out.append("\x1b[J" + SYNC_END)
        return "".join(out)

    # -- input ---------------------------------------------------------
    def handle(self, key: Optional[str]) -> None:
        if key is None:
            return
        if key in ("q", "Q", "\x03"):
            self.running = False
        elif key == "esc":
            self.show_help = False  # Esc closes help; it must not quit
        elif key in ("r", "R"):
            self.poller.refresh_now()
            self.notify("Refreshing")
        elif key == "\t" or key in ("v", "V"):
            self.view = (self.view + 1) % len(VIEWS)
            self.cursor = self.scroll = 0
        elif key == "k":
            self.poller.cycle_active(1)
            self.cursor = self.scroll = 0
            self.notify(f"Key: {self.poller.entries[self.poller.active_index][0]}")
        elif key == "K":
            self.poller.cycle_active(-1)
            self.cursor = self.scroll = 0
            self.notify(f"Key: {self.poller.entries[self.poller.active_index][0]}")
        elif key in ("w", "W"):
            self.window_index = (self.window_index + 1) % len(WINDOWS)
            self.poller.set_window(WINDOWS[self.window_index])
            self.notify(f"Window {WINDOWS[self.window_index].upper()}")
        elif key == "up":
            self.cursor = max(0, self.cursor - 1)
        elif key == "down":
            self.cursor += 1
        elif key == "wheel_up":
            self.cursor = max(0, self.cursor - 3)
        elif key == "wheel_down":
            self.cursor += 3
        elif key == "pgup":
            self.cursor = max(0, self.cursor - self._page)
        elif key == "pgdn":
            self.cursor += self._page
        elif key in ("home", "g"):
            self.cursor = 0
        elif key in ("end", "G"):
            self.cursor = max(0, self._rows_shown - 1)
        elif key in ("+", "="):
            self.poller.set_interval(self.poller.interval + 5)
            self.notify(f"Interval {self.poller.interval:.0f}s")
        elif key in ("-", "_"):
            self.poller.set_interval(self.poller.interval - 5)
            self.notify(f"Interval {self.poller.interval:.0f}s")
        elif key in ("m", "M"):
            self.mouse = not self.mouse
            sys.stdout.write(MOUSE_ON if self.mouse else MOUSE_OFF)
            sys.stdout.flush()
            self.notify("Mouse on · wheel scrolls" if self.mouse else "Mouse off · text selection works")
        elif key == "?":
            self.show_help = not self.show_help

    # -- loop ----------------------------------------------------------
    def run(self) -> int:
        out = sys.stdout
        enable_vt()
        out.write("\x1b[?1049h\x1b[?25l" + MOUSE_ON)
        out.flush()
        try:
            with KeyReader() as reader:
                last_frame = None
                while self.running:
                    frame = self.render()
                    if frame != last_frame:  # identical content, nothing to repaint
                        out.write(frame)
                        out.flush()
                        last_frame = frame
                    self.handle(reader.poll(0.25))
        except KeyboardInterrupt:
            pass
        finally:
            out.write(MOUSE_OFF + "\x1b[?25h\x1b[?1049l")
            out.flush()
        return 0


def enable_vt() -> None:
    """Enable ANSI escapes on older Windows consoles."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR_FORCE"):
        return True
    return sys.stdout.isatty()


def run_tui(poller: Poller, base_url: str = DEFAULT_BASE_URL, alerts: bool = True) -> int:
    dashboard = Dashboard(poller, base_url=base_url, color=use_color())
    if alerts:
        from .alerts import AlertWatcher
        from .notify import Notifier

        watcher, notifier = AlertWatcher(), Notifier()

        def announce(snap):
            # Runs on the poller thread. Both calls only assign, so no lock.
            for alert in watcher.check(snap):
                dashboard.notify(alert.body)
                notifier.send_alert(alert)

        poller.set_on_update(announce)
    poller.start()
    try:
        return dashboard.run()
    finally:
        poller.stop()
