"""Windows and Linux tray front end."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import webbrowser
from typing import Optional, Tuple

from . import DEFAULT_BASE_URL, INTERVAL_CHOICES, WINDOW_CHOICES, autostart
from .alerts import AlertWatcher
from .fmt import ago, exact_int, human_int, limit_amount, local_clock, money, reset_label, truncate
from .models import Usage
from .notify import Notifier
from .poller import Poller, Snapshot
from .theme import (
    ACCENT, BLUE, GREEN, INK, LINE, MUTED, PANEL_WIDTH, RAMP, SPACE, TEXT,
    level_color, level_rgb, mono_family, ui_family,
)

SUPERSAMPLE = 4
DESIGN_DPI = 96.0


class MissingDependency(RuntimeError):
    pass


class _Unlocked:

    def close(self) -> None:
        pass


def claim_single_instance():
    """Lock the per-user tray instance."""
    from . import config as cfg_mod

    try:
        directory = cfg_mod.config_dir()
        os.makedirs(directory, exist_ok=True)
        handle = open(os.path.join(directory, "tray.lock"), "a+")
    except OSError:
        return _Unlocked()

    try:
        if sys.platform.startswith("win"):
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    except ImportError:
        return _Unlocked()
    return handle


def _require_tray():
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise MissingDependency(
            "Tray mode needs two extra packages:\n"
            "    pip install pystray pillow\n"
            f"({exc})"
        ) from None
    return pystray, Image, ImageDraw


# --------------------------------------------------------------------- icon
def make_icon(percent: Optional[float], error: bool = False, size: int = 64, mono: bool = False):
    """Create the tray status icon."""
    _, Image, ImageDraw = _require_tray()
    scale = size * SUPERSAMPLE
    image = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    stroke = int(scale * 0.17)
    inset = stroke // 2 + int(scale * 0.06)
    box = [inset, inset, scale - inset, scale - inset]
    colour = (0, 0, 0) if mono else level_rgb(percent, error)

    if error:
        draw.ellipse(box, fill=colour + (255,))
        return image.resize((size, size), Image.LANCZOS)

    if mono:
        draw.ellipse(box, outline=(0, 0, 0, 90), width=stroke)
    else:
        draw.ellipse(box, outline=(78, 78, 78, 255), width=stroke)

    if percent is not None:
        sweep = max(8.0, min(360.0, percent * 3.6))
        draw.arc(box, start=-90, end=-90 + sweep, fill=colour + (255,), width=stroke)

    return image.resize((size, size), Image.LANCZOS)


def tooltip(snap: Snapshot) -> str:
    if snap.error:
        return f"Finnvnoi API Check · {snap.active_label}\n{truncate(snap.error, 60)}"
    usage = snap.usage or Usage()
    lines = [f"Finnvnoi API Check · {snap.active_label}"]
    worst = usage.worst_limit
    if worst and worst.percent is not None:
        lines.append(f"{worst.label}  {worst.percent:.0f}%  {reset_label(worst.reset_dt)}")
    lines.append(f"{exact_int(usage.request_count)} requests · {human_int(usage.total_tokens)} tokens "
                 f"· {money(usage.total_cost_usd)}")
    return "\n".join(lines)


# ------------------------------------------------------------------ display
def work_area(root) -> Tuple[int, int, int, int]:
    """The usable screen as (left, top, right, bottom).

    Tk only knows the whole screen, so on Windows the taskbar has to be asked
    for separately; the old code guessed 72 pixels for it, which is wrong at
    every display scale but 100% and wrong again when the bar is on the side.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            SPI_GETWORKAREA = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def corner(area: Tuple[int, int, int, int], size: Tuple[int, int], margin: int) -> Tuple[int, int]:
    """Bottom-right of the work area, clamped so no edge falls off the screen."""
    left, top, right, bottom = area
    return (max(left, right - size[0] - margin), max(top, bottom - size[1] - margin))


def declare_dpi_awareness() -> None:
    """Ask Windows not to stretch the window.

    A DPI-unaware process gets its bitmap scaled up by the compositor, which is
    what turns a crisp panel blurry on a HiDPI display. CPython's own launchers
    carry a manifest that declares this already; a PyInstaller build does not,
    so declare it before Tk reads the screen.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError, NameError):
            pass


# -------------------------------------------------------------------- panel
class Panel:
    """The flyout. Fixed width, one 4px rhythm, numbers set in mono.

    Every pixel value from theme.py is drawn for a 96 dpi display and scaled
    here. Tk converts font point sizes for the display on its own, so at 200%
    an unscaled panel puts 21px text inside 4px padding, which is the cramped,
    overlapping look. Scaling the two together keeps the proportions.
    """

    def __init__(self, app: "TrayApp"):
        declare_dpi_awareness()  # before Tk reads the screen

        import tkinter as tk
        import tkinter.font as tkfont

        self.tk = tk
        self.tkfont = tkfont
        self.app = app
        self.ui, self.mono = ui_family(), mono_family()
        self.root = tk.Tk()
        self.root.withdraw()
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / DESIGN_DPI)
        self.space = tuple(self.px(value) for value in SPACE)
        self.width = self.px(PANEL_WIDTH)
        self.root.title("Finnvnoi API Check")
        self.root.configure(bg=INK)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        # A flyout has no title bar and no taskbar button. Without this the panel
        # arrives with minimise and maximise buttons, which it has no use for.
        try:
            self.root.overrideredirect(True)
        except self.tk.TclError:
            pass
        self.root.bind("<Escape>", lambda _event: self.hide())
        self.root.bind("<FocusOut>", self._focus_out)
        self.visible = False
        self._shown_at = 0.0
        self._drag = (0, 0)
        self._build()
        self.root.after(500, self._pump)

    def px(self, value: float) -> int:
        """A design pixel in this display's pixels."""
        return max(1, int(round(value * self.scale)))

    # -- construction --------------------------------------------------
    def _text(self, parent, content="", *, size=9, colour=TEXT, weight="normal",
              mono=False, anchor="w", spacing=0):
        font = (self.mono if mono else self.ui, size, weight)
        return self.tk.Label(parent, text=content, bg=INK, fg=colour, anchor=anchor,
                             font=font, justify="left")

    def _caption(self, parent, content):
        """Section captions: small, muted, letterspaced by hand."""
        return self._text(parent, " ".join(content), size=7, colour=MUTED)

    def _divider(self, parent, pad):
        self.tk.Frame(parent, bg=LINE, height=1).pack(fill="x", pady=pad)

    def _build(self) -> None:
        tk = self.tk
        # Without decorations the window needs its own edge, or it dissolves into
        # whatever is behind it.
        border = tk.Frame(self.root, bg=LINE, padx=1, pady=1)
        border.pack(fill="both", expand=True)
        outer = tk.Frame(border, bg=INK, padx=self.space[3], pady=self.space[2])
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=INK)
        head.pack(fill="x")
        title = self._text(head, "Finnvnoi API Check", size=11, weight="bold")
        title.pack(side="left")
        self.key_label = self._text(head, "", colour=MUTED, anchor="e")
        self.key_label.pack(side="right")
        # The title row is the drag handle, standing in for the title bar.
        for widget in (head, title, self.key_label):
            widget.bind("<Button-1>", self._grab)
            widget.bind("<B1-Motion>", self._drag_to)

        stats = tk.Frame(outer, bg=INK)
        stats.pack(fill="x", pady=(self.space[2], 0))
        self.stats = {}
        cells = (("requests", "REQUESTS", TEXT), ("tokens", "TOKENS", ACCENT),
                 ("cached", "CACHED", BLUE), ("cost", "COST", GREEN))
        for column in (0, 1):
            stats.columnconfigure(column, weight=1, uniform="stat")
        for index, (field, caption, colour) in enumerate(cells):
            cell = tk.Frame(stats, bg=INK)
            cell.grid(row=index // 2, column=index % 2, sticky="w",
                      pady=(0 if index < 2 else self.space[2], 0))
            self._caption(cell, caption).pack(anchor="w")
            value = self._text(cell, "\u2014", size=12, colour=colour, mono=True)
            value.pack(anchor="w", pady=(self.space[0], 0))
            self.stats[field] = value

        self._divider(outer, (self.space[2], self.space[1]))
        self._caption(outer, "LIMITS").pack(anchor="w")
        self.limits = tk.Canvas(outer, bg=INK, height=self.px(4),
                                width=self.width - 2 * self.space[3],
                                highlightthickness=0, bd=0)
        self.limits.pack(fill="x", pady=(self.space[1], 0))

        self._divider(outer, (self.space[2], self.space[1]))
        self._caption(outer, "RECENT").pack(anchor="w")
        self.recent = self._text(outer, "", size=8, colour=MUTED, mono=True)
        self.recent.pack(fill="x", pady=(self.space[1], 0))

        self._divider(outer, (self.space[2], self.space[1]))
        self.status = self._text(outer, "", size=8, colour=MUTED)
        self.status.pack(fill="x")

        actions = tk.Frame(outer, bg=INK)
        actions.pack(fill="x", pady=(self.space[2], 0))
        for label, command in (("Refresh", self.app.refresh_now),
                               ("Switch key", self.app.next_key),
                               ("Hide", self.hide)):
            tk.Button(actions, text=label, command=command, bg=LINE, fg=TEXT,
                      activebackground=ACCENT, activeforeground=INK, relief="flat",
                      font=(self.ui, 8), padx=self.space[2], pady=self.space[0], borderwidth=0,
                      highlightthickness=0, cursor="hand2", takefocus=0
                      ).pack(side="left", padx=(0, self.space[1]))

    # -- lifecycle -----------------------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                action = self.app.ui_queue.get_nowait()
                if action == "show":
                    self._show()
                elif action == "toggle":
                    self.hide() if self.visible else self._show()
                elif action == "hide":
                    self.hide()
                elif action == "quit":
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        if self.visible:
            self.update(self.app.poller.snapshot())
        self.root.after(500, self._pump)

    def _show(self) -> None:
        self.update(self.app.poller.snapshot())
        self.root.update_idletasks()
        # winfo_width() is 1 until the window has been mapped, and 1 is truthy,
        # so `or PANEL_WIDTH` never fired and the first open placed the panel one
        # pixel in from the corner -- all but off screen. The requested size is
        # known before mapping and is what Tk will use anyway.
        size = (self.root.winfo_reqwidth(), self.root.winfo_reqheight())
        x, y = corner(work_area(self.root), size, self.space[2])
        self.root.geometry(f"+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.visible = True
        self._shown_at = time.monotonic()

    def _focus_out(self, _event) -> None:
        if not self.visible or time.monotonic() - self._shown_at < 0.4:
            return
        try:
            if self.root.focus_displayof() is not None:
                return  # focus stayed inside the panel
        except self.tk.TclError:
            return
        self.hide()

    def _grab(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_to(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def hide(self) -> None:
        self.root.withdraw()
        self.visible = False

    # -- painting ------------------------------------------------------
    def update(self, snap: Snapshot) -> None:
        usage = snap.usage or Usage()
        self.key_label.config(text=f"{snap.active_label}   {snap.window.upper()}")

        rate = usage.cache_hit_rate
        self.stats["requests"].config(text=exact_int(usage.request_count))
        self.stats["tokens"].config(text=human_int(usage.total_tokens))
        self.stats["cached"].config(text=human_int(usage.cached_input_tokens)
                                    + (f"  {rate:.0f}%" if rate else ""))
        self.stats["cost"].config(text=money(usage.total_cost_usd))

        self._paint_limits(usage)

        rows = snap.rows[:4]
        self.recent.config(text="\n".join(
            f"{local_clock(r.when)}  {truncate(r.model, 18):<18}{human_int(r.total_tokens):>7}"
            f"{money(r.cost_usd):>10}" for r in rows) or "no requests yet")

        if snap.error:
            self.status.config(text=truncate(snap.error, 46), fg=RAMP[-1])
        else:
            self.status.config(text=f"updated {ago(snap.fetched_at)}   ·   "
                                    f"next in {snap.seconds_to_next:.0f}s", fg=MUTED)

    def _paint_limits(self, usage: Usage) -> None:
        canvas = self.limits
        canvas.delete("all")
        width = canvas.winfo_width()
        if width <= 1:
            width = self.width - 2 * self.space[3]

        # Font sizes are in points, so display scaling decides how tall a line
        # actually is: Segoe UI at 8pt is 11px at 100% and 21px at 200%. Measure
        # it, or the bar gets drawn straight through the label.
        label_font = self.tkfont.Font(family=self.ui, size=8)
        value_font = self.tkfont.Font(family=self.mono, size=8)
        detail_font = self.tkfont.Font(family=self.ui, size=7)
        label_h = label_font.metrics("linespace")
        detail_h = detail_font.metrics("linespace")
        gap = max(3, label_h // 4)
        bar_h = max(4, label_h // 3)
        block = label_h + gap + bar_h + gap + detail_h + gap * 2

        limits = (usage.limits + usage.upstream_limits)[:3]
        if not limits:
            canvas.configure(height=label_h)
            canvas.create_text(0, 0, anchor="nw", text="no limits reported",
                               fill=MUTED, font=label_font)
            return

        canvas.configure(height=block * len(limits) - gap * 2)
        for index, limit in enumerate(limits):
            y = index * block
            ratio = limit.percent
            canvas.create_text(0, y, anchor="nw", text=truncate(limit.label, 30),
                               fill=TEXT, font=label_font)
            canvas.create_text(width, y, anchor="ne",
                               text=f"{ratio:.1f}%" if ratio is not None else "-",
                               fill=level_color(ratio), font=value_font)
            y += label_h + gap
            self._gauge(canvas, 0, y, width, bar_h, ratio)
            y += bar_h + gap
            detail = (f"{limit_amount(limit.limit_type, limit.current_value)} of "
                      f"{limit_amount(limit.limit_type, limit.max_value)}")
            if limit.reset_at:
                detail += f"   \u00b7   {reset_label(limit.reset_dt)}"
            canvas.create_text(0, y, anchor="nw", text=detail, fill=MUTED, font=detail_font)

    @staticmethod
    def _gauge(canvas, x, y, width, height, percent) -> None:
        """Same six-stop ramp the terminal gauge walks."""
        canvas.create_rectangle(x, y, x + width, y + height, fill=LINE, outline="")
        if not percent:
            return
        filled = width * min(1.0, percent / 100)
        for index, colour in enumerate(RAMP):
            start = width * index / len(RAMP)
            if start >= filled:
                break
            canvas.create_rectangle(x + start, y, x + min(width * (index + 1) / len(RAMP), filled),
                                    y + height, fill=colour, outline="")


# ------------------------------------------------------------------ tray app
class TrayApp:
    def __init__(self, poller: Poller, base_url: str = DEFAULT_BASE_URL, alerts: bool = True):
        self.poller = poller
        self.base_url = base_url
        self.ui_queue: "queue.Queue[str]" = queue.Queue()
        self.icon = None
        self.panel: Optional[Panel] = None
        self.watcher = AlertWatcher()
        self.notifier = Notifier(enabled=alerts)
        self._icon_state = None

    # -- actions -------------------------------------------------------
    def refresh_now(self, *_):
        self.poller.refresh_now()

    def next_key(self, *_):
        self.poller.cycle_active(1)
        self.watcher.reset()

    def toggle_panel(self, *_):
        self.ui_queue.put("toggle")

    def open_web(self, *_):
        webbrowser.open(self.base_url)

    def toggle_alerts(self, *_):
        self.notifier.enabled = not self.notifier.enabled

    def quit(self, *_):
        self.poller.stop()
        self.ui_queue.put("quit")
        if self.icon:
            self.icon.stop()

    # -- menu ----------------------------------------------------------
    def _menu(self):
        pystray, _, _ = _require_tray()
        Item, Menu = pystray.MenuItem, pystray.Menu

        def totals(_=None) -> str:
            snap = self.poller.snapshot()
            if snap.error:
                return truncate(snap.error, 46)
            usage = snap.usage or Usage()
            return (f"{exact_int(usage.request_count)} requests   "
                    f"{human_int(usage.total_tokens)} tokens   {money(usage.total_cost_usd)}")

        def tightest(_=None) -> str:
            worst = (self.poller.snapshot().usage or Usage()).worst_limit
            if not worst or worst.percent is None:
                return "no limits reported"
            return f"{truncate(worst.label, 24)}   {worst.percent:.0f}%   {reset_label(worst.reset_dt)}"

        def keys():
            for index, (label, _key) in enumerate(self.poller.entries):
                yield Item(label,
                           (lambda i: lambda *_: (self.poller.set_active(i), self.watcher.reset()))(index),
                           checked=(lambda i: lambda _item: self.poller.active_index == i)(index),
                           radio=True)

        def intervals():
            for seconds in INTERVAL_CHOICES:
                yield Item(f"{seconds} seconds",
                           (lambda s: lambda *_: self.poller.set_interval(s))(seconds),
                           checked=(lambda s: lambda _item: abs(self.poller.interval - s) < 0.5)(seconds),
                           radio=True)

        def windows():
            for name in WINDOW_CHOICES:
                yield Item(name.upper(),
                           (lambda w: lambda *_: self.poller.set_window(w))(name),
                           checked=(lambda w: lambda _item: self.poller.window == w)(name),
                           radio=True)

        items = [
            Item(lambda _: f"Finnvnoi API Check · {self.poller.snapshot().active_label}", None, enabled=False),
            Item(totals, None, enabled=False),
            Item(tightest, None, enabled=False),
            Menu.SEPARATOR,
        ]
        if self.panel is not None:
            items.append(Item("Dashboard", self.toggle_panel, default=True))
        items += [
            Item("Refresh now", self.refresh_now, default=self.panel is None),
            Item("Switch key", Menu(*keys())),
            Item("Totals window", Menu(*windows())),
            Item("Update interval", Menu(*intervals())),
            Item("Alert at 80% and 95%", self.toggle_alerts,
                 checked=lambda _item: self.notifier.enabled),
            Menu.SEPARATOR,
            Item("Open web dashboard", self.open_web),
        ]
        if autostart.supported():
            items.append(Item(autostart.label(), lambda *_: autostart.toggle(),
                              checked=lambda _item: autostart.enabled()))
        items += [Menu.SEPARATOR, Item("Quit", self.quit)]
        return Menu(*items)

    # -- poll callback -------------------------------------------------
    def on_update(self, snap: Snapshot) -> None:
        if not self.icon:
            return
        percent = snap.usage.worst_percent if snap.usage else None
        state = (round(percent) if percent is not None else None, bool(snap.error))
        if state != self._icon_state:
            self._icon_state = state
            try:
                self.icon.icon = make_icon(percent, error=bool(snap.error))
            except Exception:
                pass
        try:
            self.icon.title = tooltip(snap)
        except Exception:
            pass
        for alert in self.watcher.check(snap):
            self.notifier.send_alert(alert)

    # -- run -----------------------------------------------------------
    def run(self) -> int:
        pystray, _, _ = _require_tray()
        if sys.platform == "darwin":
            # pystray drives AppKit, which runs its event loop on the main
            # thread and will not share it with Tk. Building the panel here
            # would deadlock or abort, so the menu carries the detail instead.
            self.panel = None
        else:
            try:
                self.panel = Panel(self)
            except Exception as exc:  # no tkinter, or no display
                print(f"[warning] Could not open the panel ({exc}); using the tray menu only.",
                      file=sys.stderr)
                self.panel = None

        self.icon = pystray.Icon("finnvnoi-api-check", make_icon(None), "Finnvnoi API Check", self._menu())
        if hasattr(self.icon, "notify"):
            self.notifier.balloon = lambda message, title: self.icon.notify(message, title)
        self.poller.set_on_update(self.on_update)
        self.poller.start()

        if self.panel is None:
            self.icon.run()
            self.poller.stop()
            return 0

        threading.Thread(target=self.icon.run, name="finnvnoi-api-check-tray", daemon=True).start()
        try:
            self.panel.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.poller.stop()
            try:
                self.icon.stop()
            except Exception:
                pass
        return 0


def run_tray(poller: Poller, base_url: str = DEFAULT_BASE_URL, alerts: bool = True) -> int:
    lock = claim_single_instance()
    if lock is None:
        print("Finnvnoi API Check is already running in the tray.", file=sys.stderr)
        return 0
    try:
        return TrayApp(poller, base_url=base_url, alerts=alerts).run()
    finally:
        lock.close()
