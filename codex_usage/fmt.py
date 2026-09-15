"""Shared formatting for both front ends."""

from __future__ import annotations

import datetime as _dt
import math
import sys
from typing import Optional

_UNITS = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))

# Legacy Windows code pages cannot encode these, so they are resolved at runtime
# rather than baked into signatures. adopt_output_encoding() swaps them.
BLOCK_FULL, BLOCK_EMPTY, ELLIPSIS = "\u2588", "\u2591", "\u2026"


def adopt_output_encoding(stream=None) -> bool:
    """Fall back to ASCII bar glyphs when the output encoding cannot carry them.

    Returns True if the Unicode glyphs survived.
    """
    global BLOCK_FULL, BLOCK_EMPTY, ELLIPSIS
    encoding = getattr(stream or sys.stdout, "encoding", None) or "ascii"
    try:
        (BLOCK_FULL + BLOCK_EMPTY + ELLIPSIS + "\u00b7").encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        BLOCK_FULL, BLOCK_EMPTY, ELLIPSIS = "#", "-", "..."
        return False

# Lifetime limits come back with a reset date in the far future (year 9999).
# Rendering that as "in 2912195d" is noise, so treat it as no reset at all.
FOREVER_SECONDS = 10 * 365 * 86400


def human_int(n: Optional[float]) -> str:
    if n is None:
        return "-"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    for size, suffix in _UNITS:
        if n >= size:
            value = n / size
            digits = 2 if value < 10 else (1 if value < 100 else 0)
            return f"{sign}{value:.{digits}f}{suffix}"
    return f"{sign}{int(n):,}"


def exact_int(n: Optional[float]) -> str:
    if n is None:
        return "-"
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def money(x: Optional[float]) -> str:
    if x is None:
        return "-"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if x == 0:
        return "$0.00"
    if abs(x) < 0.01:
        return f"${x:.6f}".rstrip("0").rstrip(".")
    if abs(x) < 1:
        text = f"{x:.4f}".rstrip("0")
        whole, _, frac = text.partition(".")
        return f"${whole}.{frac.ljust(2, '0')}"
    return f"${x:,.2f}"


def limit_amount(limit_type: str, value: Optional[float]) -> str:
    """Format a limit value in the unit reported by the API."""
    if value is None:
        return "-"
    # Cost limits are reported as integer millionths of a US dollar. Some API
    # versions call the dimension cost_usd, newer ones call it micro_usd.
    if limit_type in {"cost_usd", "micro_usd"}:
        return money(float(value) / 1_000_000)
    return human_int(value)


def pct(current: Optional[float], maximum: Optional[float]) -> Optional[float]:
    """Usage ratio 0..100, or None when the maximum is unknown."""
    try:
        current = float(current)
        maximum = float(maximum)
    except (TypeError, ValueError):
        return None
    if maximum <= 0:
        return None
    return max(0.0, min(100.0, current / maximum * 100.0))


def parse_time(value) -> Optional[_dt.datetime]:
    """Accept ISO-8601 (with or without 'Z') or epoch seconds. Returns UTC-aware."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        try:
            return _dt.datetime.fromtimestamp(int(text), tz=_dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


def duration(seconds: Optional[float], short: bool = False) -> str:
    if seconds is None:
        return "-"
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "-"
    seconds = int(max(0, math.floor(seconds)))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    gap = "" if short else " "
    if days:
        return f"{days}d{gap}{hours}h"
    if hours:
        return f"{hours}h{gap}{minutes}m"
    if minutes:
        return f"{minutes}m{gap}{secs}s"
    return f"{secs}s"


def until(target: Optional[_dt.datetime], now: Optional[_dt.datetime] = None) -> str:
    if target is None:
        return "-"
    now = now or _dt.datetime.now(_dt.timezone.utc)
    delta = (target - now).total_seconds()
    if delta <= 0:
        return "due"
    if delta > FOREVER_SECONDS:
        return "never"
    return "in " + duration(delta)


def reset_label(target: Optional[_dt.datetime], now: Optional[_dt.datetime] = None) -> str:
    """Full label for a reset column: 'resets in 2h 5m' / 'never resets'."""
    text = until(target, now)
    return {"-": "-", "never": "never resets", "due": "resetting"}.get(text, "resets " + text)


def ago(target: Optional[_dt.datetime], now: Optional[_dt.datetime] = None) -> str:
    if target is None:
        return "-"
    now = now or _dt.datetime.now(_dt.timezone.utc)
    delta = (now - target).total_seconds()
    return "just now" if delta < 1 else duration(delta) + " ago"


def local_clock(target: Optional[_dt.datetime], with_date: bool = False) -> str:
    if target is None:
        return "-"
    local = target.astimezone()
    return local.strftime("%d/%m %H:%M:%S" if with_date else "%H:%M:%S")


def mask_key(key: Optional[str]) -> str:
    if not key:
        return "-"
    key = key.strip()
    if len(key) <= 12:
        return key[:3] + "***"
    return f"{key[:10]}...{key[-4:]}"


def bar(ratio: Optional[float], width: int = 20,
        filled: Optional[str] = None, empty: Optional[str] = None) -> str:
    filled = BLOCK_FULL if filled is None else filled
    empty = BLOCK_EMPTY if empty is None else empty
    if width <= 0:
        return ""
    if ratio is None:
        return empty * width
    n = int(round(max(0.0, min(100.0, ratio)) / 100.0 * width))
    return filled * n + empty * (width - n)


def truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = str(text)
    if len(text) <= width:
        return text
    if width <= len(ELLIPSIS):
        return text[:width]
    return text[: width - len(ELLIPSIS)] + ELLIPSIS
