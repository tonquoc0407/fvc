"""macOS menu bar front end."""

from __future__ import annotations

import webbrowser

from . import DEFAULT_BASE_URL, INTERVAL_CHOICES, WINDOW_CHOICES, autostart
from .alerts import AlertWatcher
from .fmt import ago, exact_int, human_int, local_clock, money, reset_label, truncate
from .models import Usage
from .notify import Notifier
from .poller import Poller, Snapshot

RECENT_ROWS = 5


class MissingDependency(RuntimeError):
    pass


def _require_rumps():
    try:
        import rumps
    except ImportError as exc:
        raise MissingDependency(
            "The menu bar app needs rumps:\n"
            "    pip install rumps\n"
            f"({exc})"
        ) from None
    return rumps


def menu_title(snap: Snapshot) -> str:
    """What sits in the menu bar. Short, and stable enough not to jitter."""
    usage = snap.usage
    if usage is None:
        return "Finnvnoi API Check" if not snap.error else "Finnvnoi API Check (offline)"
    worst = usage.worst_limit
    parts = []
    if worst and worst.percent is not None:
        parts.append(f"{worst.percent:.0f}%")
    parts.append(money(usage.total_cost_usd))
    text = "  ".join(parts)
    return f"({text})" if snap.error else text


def slot(title: str, index: int) -> str:
    """Make duplicate menu labels unique to rumps."""
    return title + " " * index


class MenuBarApp:
    def __init__(self, poller: Poller, base_url: str = DEFAULT_BASE_URL, alerts: bool = True):
        self.rumps = _require_rumps()
        self.poller = poller
        self.base_url = base_url
        self.watcher = AlertWatcher()
        self.notifier = Notifier(balloon=self._balloon, enabled=alerts)
        self.app = self.rumps.App("Finnvnoi API Check", title="Finnvnoi API Check", quit_button=None)
        self._timer = None
        self._build()

    # -- menu ----------------------------------------------------------
    def _item(self, title, callback=None, key=None):
        return self.rumps.MenuItem(title, callback=callback, key=key)

    def _submenu(self, title, rows):
        """A submenu built from (title, callback) pairs, one row per slot."""
        parent = self._item(title)
        items = []
        for index, (label, callback) in enumerate(rows):
            item = self._item(slot(label, index), callback)
            items.append(item)
            parent.add(item)
        return parent, items

    def _build(self) -> None:
        self.header = self._item("Finnvnoi API Check")
        self.totals = self._item("connecting")
        self.tightest = self._item("—")

        self.keys_menu, self.key_items = self._submenu("Switch key", [
            (label, (lambda i: lambda _s: self._pick_key(i))(index))
            for index, (label, _key) in enumerate(self.poller.entries)
        ])
        self.window_menu, self.window_items = self._submenu("Totals window", [
            (name.upper(), (lambda w: lambda _s: self._pick_window(w))(name))
            for name in WINDOW_CHOICES
        ])
        self.interval_menu, self.interval_items = self._submenu("Update interval", [
            (f"{seconds} seconds", (lambda s: lambda _i: self._pick_interval(s))(seconds))
            for seconds in INTERVAL_CHOICES
        ])
        self.recent_menu, self.recent_items = self._submenu("Recent requests", [
            ("no requests yet", None) for _ in range(RECENT_ROWS)
        ])

        self.alerts_item = self._item("Alert at 80% and 95%", self._toggle_alerts)
        self.alerts_item.state = 1 if self.notifier.enabled else 0

        menu = [
            self.header, self.totals, self.tightest, None,
            self._item("Refresh now", lambda _i: self.poller.refresh_now(), key="r"),
            self.keys_menu, self.window_menu, self.interval_menu, self.alerts_item, None,
            self.recent_menu,
            self._item("Open web dashboard", lambda _i: self._open_web()),
        ]
        if autostart.supported():
            self.login_item = self._item(autostart.label(), self._toggle_login)
            menu.append(self.login_item)
        else:
            self.login_item = None
        # quit_button=None dropped rumps' own Quit, so this one has to carry the
        # shortcut as well, or Command-Q does nothing.
        menu += [None, self._item("Quit", lambda _i: self._quit(), key="q")]

        self.app.menu = menu
        self._sync_marks()

    # -- actions -------------------------------------------------------
    def _pick_key(self, index: int) -> None:
        self.poller.set_active(index)
        self.watcher.reset()
        self._sync_marks()

    def _pick_window(self, window: str) -> None:
        self.poller.set_window(window)
        self._sync_marks()

    def _pick_interval(self, seconds: int) -> None:
        self.poller.set_interval(seconds)
        self._sync_marks()

    def _toggle_alerts(self, _item) -> None:
        self.notifier.enabled = not self.notifier.enabled
        self.alerts_item.state = 1 if self.notifier.enabled else 0

    def _toggle_login(self, _item) -> None:
        self.login_item.state = 1 if autostart.toggle() else 0

    def _open_web(self) -> None:
        webbrowser.open(self.base_url)

    def _quit(self) -> None:
        self.poller.stop()
        self.rumps.quit_application()

    def _balloon(self, message: str, title: str) -> None:
        self.rumps.notification(title, None, message)

    def _sync_marks(self) -> None:
        for index, item in enumerate(self.key_items):
            item.state = 1 if index == self.poller.active_index else 0
        for name, item in zip(WINDOW_CHOICES, self.window_items):
            item.state = 1 if self.poller.window == name else 0
        for seconds, item in zip(INTERVAL_CHOICES, self.interval_items):
            item.state = 1 if abs(self.poller.interval - seconds) < 0.5 else 0
        if self.login_item is not None:
            self.login_item.state = 1 if autostart.enabled() else 0

    # -- tick ----------------------------------------------------------
    @staticmethod
    def _set(item, title: str) -> None:
        """Update an item only when its text changes."""
        if item.title != title:
            item.title = title

    def tick(self, _timer=None) -> None:
        snap = self.poller.snapshot()
        title = menu_title(snap)
        if self.app.title != title:
            self.app.title = title
        self._set(self.header, f"Finnvnoi API Check · {snap.active_label}   {snap.window.upper()}")

        usage = snap.usage or Usage()
        if snap.error:
            self._set(self.totals, truncate(snap.error, 52))
        else:
            self._set(self.totals, f"{exact_int(usage.request_count)} requests   "
                                   f"{human_int(usage.total_tokens)} tokens   "
                                   f"{money(usage.total_cost_usd)}")

        worst = usage.worst_limit
        if worst and worst.percent is not None:
            self._set(self.tightest, f"{truncate(worst.label, 26)}   {worst.percent:.0f}%   "
                                     f"{reset_label(worst.reset_dt)}")
        else:
            self._set(self.tightest,
                      f"updated {ago(snap.fetched_at)}" if snap.fetched_at else "no data yet")

        rows = snap.rows[:RECENT_ROWS]
        for index, item in enumerate(self.recent_items):
            if index < len(rows):
                row = rows[index]
                self._set(item, f"{local_clock(row.when)}   {truncate(row.model, 20)}   "
                                f"{human_int(row.total_tokens)}   {money(row.cost_usd)}")
            else:
                self._set(item, slot("—", index))

        for alert in self.watcher.check(snap):
            self.notifier.send_alert(alert)

    # -- run -----------------------------------------------------------
    def run(self) -> int:
        # Without a bundle the process shows a Dock icon; a menu bar app should
        # not. Accessory activation policy is what LSUIElement does in a plist.
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

            NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception:
            pass

        self.poller.start()
        self._timer = self.rumps.Timer(self.tick, 1)
        self._timer.start()
        try:
            self.app.run()
        finally:
            if self._timer is not None:
                self._timer.stop()
            self.poller.stop()
        return 0


def run_menubar(poller: Poller, base_url: str = DEFAULT_BASE_URL, alerts: bool = True) -> int:
    return MenuBarApp(poller, base_url=base_url, alerts=alerts).run()
