"""Tests for the Windows and macOS front ends.

Run: python3 tests/test_platform.py

Neither front end opens under the Linux interpreter, so what is testable there
is the part that decides things: the command a login launch runs, where the flyout lands, and the
menu the macOS app builds. The last one is checked against a stand-in for rumps
that copies the one behaviour that bit us -- a menu is a dict keyed by each
item's title, so items built with the same text collapse into one row.
"""

import datetime as dt
import os
import plistlib
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codex_usage import INTERVAL_CHOICES, WINDOW_CHOICES, autostart
from codex_usage.models import RequestRow, Usage
from codex_usage.poller import Poller, Snapshot


# ------------------------------------------------------- a stand-in for rumps
class FakeMenuItem:
    """rumps keys a menu off the item title, and the first title wins."""

    def __init__(self, title, callback=None, key=None, **_kwargs):
        self.title = title
        self.callback = callback
        self.key = key
        self.state = 0
        self.menu = {}

    def add(self, item):
        self.menu.setdefault(item.title, item)


class FakeApp:
    def __init__(self, name, title=None, quit_button="Quit", **_kwargs):
        self.name = name
        self.title = title
        self.quit_button = quit_button
        self._menu = {}
        self.ran = False

    @property
    def menu(self):
        return self._menu

    @menu.setter
    def menu(self, rows):
        self._menu = {}
        for row in rows:
            if row is None:
                continue
            self._menu.setdefault(row.title, row)

    def run(self):
        self.ran = True


class FakeRumps:
    App = FakeApp
    MenuItem = FakeMenuItem
    notifications = []

    class Timer:
        def __init__(self, callback, interval):
            self.callback, self.interval = callback, interval

        def start(self):
            pass

        def stop(self):
            pass

    @staticmethod
    def notification(title, subtitle, message):
        FakeRumps.notifications.append((title, subtitle, message))

    @staticmethod
    def quit_application():
        pass


def build_menubar():
    sys.modules["rumps"] = FakeRumps
    from codex_usage.menubar import MenuBarApp

    poller = Poller(entries=[("work", "sk-clb-aaaa1111"), ("spare", "sk-clb-bbbb2222")],
                    base_url="https://example.invalid", window="7d")
    return MenuBarApp(poller, alerts=True)


def snapshot(rows=0):
    usage = Usage.parse({
        "request_count": 12, "total_tokens": 34567, "cached_input_tokens": 1000,
        "total_cost_usd": 20.93,
        "limits": [{"limit_type": "requests", "limit_window": "1d", "max_value": 100,
                    "current_value": 62, "remaining_value": 38,
                    "reset_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)).isoformat()}],
    })
    # Identical rows on purpose: two requests can look the same, and a menu that
    # keys off the text would drop one of them.
    requests = [RequestRow(id=i, model="gpt-5", total_tokens=100, cost_usd=0.01)
                for i in range(rows)]
    return Snapshot(usage=usage, rows=requests, active_label="work", window="7d",
                    fetched_at=dt.datetime.now(dt.timezone.utc))


def main() -> int:
    checks = []

    def expect(description, got, want):
        checks.append((description, got, want, got == want))

    # -- the command a login launch runs ---------------------------------
    frozen = autostart.launch_command(executable="/Apps/FinnvnoiApiCheck", frozen=True)
    expect("a frozen build launches itself", frozen, ["/Apps/FinnvnoiApiCheck", "tray"])

    installed = autostart.launch_command(executable="/venv/bin/python3", frozen=False, installed=True)
    expect("an installed package uses -m", installed,
           ["/venv/bin/python3", "-m", "codex_usage", "tray"])

    # Run from a clone the package is not on sys.path at login, so the command
    # has to carry the source directory.
    source = autostart.launch_command(executable="/venv/bin/python3", frozen=False,
                                      installed=False, root="/home/a b/src")
    expect("a source checkout carries its path", source[:2], ["/venv/bin/python3", "-c"])
    expect("the path is in the command", "/home/a b/src" in source[2], True)
    expect("the command compiles", bool(compile(source[2], "<autostart>", "exec")) or True, True)

    # A quote in a user's home must not break the line either.
    awkward = autostart.BOOTSTRAP.format(root="/Users/tôn's mac/src")
    expect("an apostrophe in the path still compiles",
           bool(compile(awkward, "<autostart>", "exec")) or True, True)
    expect("the whole command survives quoting",
           subprocess.list2cmdline(["py.exe", "-c", awkward]).count('"') >= 2, True)

    with tempfile.TemporaryDirectory() as directory:
        console = os.path.join(directory, "python.exe")
        open(console, "w").close()
        expect("no pythonw means no swap", autostart.windowed_python(console), console)
        windowed = os.path.join(directory, "pythonw.exe")
        open(windowed, "w").close()
        expect("pythonw is preferred when present", autostart.windowed_python(console), windowed)
        expect("pythonw is left alone", autostart.windowed_python(windowed), windowed)
        other = os.path.join(directory, "FinnvnoiApiCheck.exe")
        expect("a frozen exe is not renamed", autostart.windowed_python(other), other)

    plist = plistlib.loads(autostart.agent_plist(["/venv/bin/python3", "-m", "codex_usage", "tray"]))
    expect("the agent runs at load", plist["RunAtLoad"], True)
    expect("the agent does not respawn", plist["KeepAlive"], False)
    expect("the agent label matches the installer", plist["Label"], autostart.AGENT_LABEL)
    expect("the agent runs the tray", plist["ProgramArguments"][-1], "tray")
    expect("the agent path is under LaunchAgents",
           autostart.agent_path().endswith(f"/LaunchAgents/{autostart.AGENT_LABEL}.plist"), True)

    # -- where the flyout lands ------------------------------------------
    from codex_usage.tray import Panel, corner

    # px() unbound, so the scaling can be checked without a display.
    at = lambda scale: types.SimpleNamespace(scale=scale)
    expect("a 100% display draws the design pixels", Panel.px(at(1.0), 12), 12)
    expect("a 150% display scales them", Panel.px(at(1.5), 12), 18)
    expect("a 200% display scales them", Panel.px(at(2.0), 4), 8)
    expect("a hairline never rounds away", Panel.px(at(1.0), 0), 1)

    expect("the panel sits inside the work area",
           corner((0, 0, 1920, 1040), (340, 480), 12), (1568, 548))
    expect("a taskbar on the left is respected",
           corner((80, 0, 1920, 1080), (340, 480), 12), (1568, 588))
    expect("a panel taller than the screen is not pushed off the top",
           corner((0, 0, 800, 400), (340, 600), 12), (448, 0))
    expect("a second monitor's offset is kept",
           corner((1920, 0, 3840, 1040), (340, 480), 12), (3488, 548))

    # -- the macOS menu ---------------------------------------------------
    app = build_menubar()
    menu = app.app.menu
    expect("the menu bar reads before a poll", app.app.title, "Finnvnoi API Check")
    expect("quit is present", "Quit" in menu, True)
    expect("command-Q still quits", menu["Quit"].key, "q")
    expect("the web dashboard is one click away", "Open web dashboard" in menu, True)

    expect("every stored key gets a row", len(app.keys_menu.menu), 2)
    expect("the active key is ticked", app.key_items[0].state, 1)
    expect("the other key is not", app.key_items[1].state, 0)

    expect("every window is offered", len(app.window_menu.menu), len(WINDOW_CHOICES))
    expect("the poller's window is ticked",
           [item.state for item in app.window_items], [0, 1, 0, 0])
    expect("every interval is offered", len(app.interval_menu.menu), len(INTERVAL_CHOICES))
    expect("the default interval is ticked",
           [item.state for item in app.interval_items], [0, 1, 0, 0])

    # The regression: five rows built from one string used to collapse into one.
    expect("recent keeps five separate rows", len(app.recent_menu.menu), 5)
    expect("and five separate items", len({id(i) for i in app.recent_items}), 5)

    expect("an unpolled log says so",
           app.recent_items[0].title.strip(), "no requests yet")

    app.poller._snapshot = snapshot(rows=0)
    app.tick()
    expect("the menu bar carries the tightest limit and the spend",
           app.app.title, "62%  $20.93")
    expect("an empty log falls back to dashes",
           [item.title.strip() for item in app.recent_items], ["—"] * 5)

    app.poller._snapshot = snapshot(rows=2)
    app.tick()
    filled = [item.title for item in app.recent_items]
    expect("two rows fill two slots", sum("gpt-5" in title for title in filled), 2)
    expect("identical requests both survive", filled[0], filled[1])
    expect("the rest fall back to a dash", filled[2].strip(), "—")
    expect("the header names the key and window",
           app.header.title, "Finnvnoi API Check · work   7D")

    stale = snapshot(rows=1)
    stale.error = "timed out"
    app.poller._snapshot = stale
    app.tick()
    expect("a stale reading is bracketed", app.app.title, "(62%  $20.93)")
    expect("the error replaces the totals", app.totals.title, "timed out")

    # -- the choices are shared, not copied -------------------------------
    from codex_usage import cli, tui
    from codex_usage.tray import INTERVAL_CHOICES as tray_intervals

    expect("the flag and the dashboard offer the same windows", cli.WINDOWS, tui.WINDOWS)
    expect("so does the tray", list(WINDOW_CHOICES), cli.WINDOWS)
    expect("both desktop menus offer the same intervals", tray_intervals, INTERVAL_CHOICES)

    poller = app.poller
    poller.set_window("30d")
    expect("the poller reports its window", poller.window, "30d")

    width = max(len(c[0]) for c in checks)
    failures = 0
    for description, got, want, ok in checks:
        failures += not ok
        detail = "" if ok else f"   got {got!r}, wanted {want!r}"
        print(f"  {'ok  ' if ok else 'FAIL'}  {description.ljust(width)}{detail}")
    print(f"\n{failures} failed" if failures else f"\nall {len(checks)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
