"""Shared colors and fonts."""

from __future__ import annotations

import sys
from typing import Tuple

# Terminal palette, carried over by index: 239, 244, 252, 44, 75, 78, 179, 203, 141
BORDER = "#4e4e4e"
MUTED = "#808080"
TEXT = "#d0d0d0"
ACCENT = "#00d7d7"
BLUE = "#5fafff"
GREEN = "#5fd787"
YELLOW = "#d7af5f"
RED = "#ff5f5f"
VIOLET = "#af87ff"

# Panel grounds. The terminal borrows the user's background; a window has to
# bring its own, so these sit just under the palette in lightness.
INK = "#12141a"
SURFACE = "#191c24"
LINE = "#262a33"

# Gauge ramp, same six stops as the terminal: 78, 114, 149, 185, 215, 203
RAMP = ("#5fd787", "#87d787", "#afd75f", "#d7d75f", "#ffaf5f", "#ff5f5f")
RAMP_RGB = ((95, 215, 135), (135, 215, 135), (175, 215, 95),
            (215, 215, 95), (255, 175, 95), (255, 95, 95))
IDLE_RGB = (78, 78, 78)
ERROR_RGB = (255, 95, 95)

# A 4px rhythm. Every gap in the panel is one of these.
SPACE = (4, 8, 12, 16, 24)
PANEL_WIDTH = 340


def ramp_index(percent) -> int:
    if percent is None:
        return -1
    return min(len(RAMP) - 1, int(max(0.0, min(100.0, percent)) / 100 * len(RAMP)))


def level_color(percent) -> str:
    index = ramp_index(percent)
    return MUTED if index < 0 else RAMP[index]


def level_rgb(percent, error: bool = False) -> Tuple[int, int, int]:
    if error:
        return ERROR_RGB
    index = ramp_index(percent)
    return IDLE_RGB if index < 0 else RAMP_RGB[index]


def ui_family() -> str:
    if sys.platform.startswith("win"):
        return "Segoe UI"
    if sys.platform == "darwin":
        return "SF Pro Text"
    return "DejaVu Sans"


def mono_family() -> str:
    """Numbers are set in mono so columns line up down the panel."""
    if sys.platform.startswith("win"):
        return "Consolas"
    if sys.platform == "darwin":
        return "SF Mono"
    return "DejaVu Sans Mono"
