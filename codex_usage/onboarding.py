"""First-run API key collection for terminal and desktop front ends."""

from __future__ import annotations

import getpass
import sys
from typing import List, Tuple

from . import config


def _save(value: str) -> List[Tuple[str, str]]:
    value = value.strip()
    if not value:
        return []
    config.add_key("default", value)
    return config.resolve_keys()


def terminal_key() -> List[Tuple[str, str]]:
    """Prompt without echo before starting the TUI."""
    if not sys.stdin.isatty():
        return []
    try:
        print("No API key configured. Add one to start Finnvnoi API Check.")
        return _save(getpass.getpass("API key: "))
    except (EOFError, KeyboardInterrupt):
        print()
        return []


def desktop_key() -> List[Tuple[str, str]]:
    """Ask for a key using the native available desktop dialog."""
    if sys.platform == "darwin":
        try:
            import rumps

            result = rumps.Window(
                title="Finnvnoi API Check",
                message="Enter API key to start.",
                default_text="",
                ok="Save",
                cancel="Cancel",
                secure=True,
            ).run()
            return _save(result.text) if result.clicked else []
        except (ImportError, Exception):
            return []
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        value = simpledialog.askstring("Finnvnoi API Check", "Enter API key to start:",
                                       show="*", parent=root)
        root.destroy()
        return _save(value or "")
    except Exception:
        return []
