"""Tests for the TUI key reader.

Run: python3 tests/test_input.py

These guard a real bug: every unrecognised escape sequence used to come back as
"esc", and "esc" quit the app, so one roll of the mouse wheel killed the TUI.
On top of that sys.stdin.read(1) pulled a whole burst of bytes into Python's own
buffer, leaving the following select() with nothing and splitting the sequence,
which broke plain arrow keys too.
"""

import os
import sys
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codex_usage.poller import Poller
from codex_usage.tui import Dashboard, KeyReader

# (bytes written to the terminal, expected key, should it quit, description)
CASES = [
    (b"\x1bOA", "up", False, "SS3 arrow up"),
    (b"\x1bOB", "down", False, "SS3 arrow down"),
    (b"\x1b[A", "up", False, "CSI arrow up"),
    (b"\x1b[B", "down", False, "CSI arrow down"),
    (b"\x1b[5~", "pgup", False, "PageUp"),
    (b"\x1b[6~", "pgdn", False, "PageDown"),
    (b"\x1b[M" + bytes([64 + 32, 0x71, 0x30]), "wheel_up", False, "X10 wheel up, coordinate byte is 'q'"),
    (b"\x1b[M" + bytes([65 + 32, 0x51, 0x30]), "wheel_down", False, "X10 wheel down, coordinate byte is 'Q'"),
    (b"\x1b[<64;12;30M", "wheel_up", False, "SGR wheel up"),
    (b"\x1b[<65;12;30M", "wheel_down", False, "SGR wheel down"),
    (b"\x1b[<0;12;30M", None, False, "SGR left click, ignored"),
    (b"\x1b[200~", None, False, "bracketed paste, ignored"),
    (b"\t", "\t", False, "Tab switches view, must not quit"),
    (b"q", "q", True, "q must quit"),
]


def main() -> int:
    master, slave = os.openpty()
    tty.setraw(slave)  # a pty starts in canonical mode

    reader = KeyReader.__new__(KeyReader)
    reader.is_windows = False
    reader.enabled = True
    reader._fd = slave
    reader._saved = None
    reader._pending = ""

    dashboard = Dashboard(Poller([("t", "sk-x")], base_url="https://x", interval=10), color=False)
    dashboard.size = lambda: (100, 30)

    failures = 0
    print(f"{'input':<34} {'key':<12} {'quits':<7} description")
    print("-" * 88)
    for raw, expected, should_quit, description in CASES:
        os.write(master, raw)
        time.sleep(0.05)
        key = reader.poll(0.3)

        leftovers = []
        while True:
            extra = reader.poll(0.05)
            if extra is None:
                break
            leftovers.append(extra)  # leftover bytes mean a bad parse and can act as keys

        dashboard.running = True
        dashboard.handle(key)
        for extra in leftovers:
            dashboard.handle(extra)
        quit_flag = not dashboard.running

        ok = key == expected and quit_flag == should_quit and not leftovers
        failures += not ok
        note = description + (f"  ⚠ byte thua: {leftovers}" if leftovers else "")
        print(f"{str(raw):<34} {str(key):<12} {'yes' if quit_flag else 'no':<7} {'' if ok else 'FAIL '}{note}")

    print("-" * 88)
    print(f"{failures} failed" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
