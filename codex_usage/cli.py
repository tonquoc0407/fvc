"""Command line entry point: pick a front end, manage keys, or print usage once."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import List, Optional, Tuple

from . import DEFAULT_BASE_URL, WINDOW_CHOICES, __version__
from . import autostart
from . import config as cfg_mod
from .api import ApiError, Client
from .fmt import (adopt_output_encoding, bar, exact_int, human_int, limit_amount,
                  local_clock, mask_key, money, reset_label, until)
from .models import Activity, Usage
from .poller import Poller

WINDOWS = list(WINDOW_CHOICES)


def _add_common(parser: argparse.ArgumentParser, sub: bool = False) -> None:
    """Add the shared flags.

    Subparser copies default to SUPPRESS: when a flag is absent argparse leaves
    the attribute unset, so a value already parsed by the main parser survives.
    That makes both `fvc -i 5 tui` and `fvc tui -i 5` work.
    """

    def value(default):
        return argparse.SUPPRESS if sub else default

    parser.add_argument("--key", default=value(None), help="Use this API key directly; takes precedence over everything else.")
    parser.add_argument("--base-url", default=value(None), help=f"Defaults to {DEFAULT_BASE_URL}")
    parser.add_argument("-i", "--interval", type=float, default=value(None),
                        help="Refresh interval in seconds (default 10).")
    parser.add_argument("-w", "--window", choices=WINDOWS, default=value(None),
                        help="Window for the totals (default lt).")
    parser.add_argument("--timeout", type=float, default=value(15.0), help="Per-request HTTP timeout in seconds.")
    parser.add_argument("--no-logs", action="store_true", default=value(False),
                        help="Skip the request log for a lighter poll.")
    parser.add_argument("--no-alerts", action="store_true", default=value(False),
                        help="Do not send desktop notifications at 80%% and 95%% of a limit.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fvc",
        description="Check API key usage on Finnvnoi API.",
        epilog="Shared flags work before or after the subcommand. Avoid passing an API key "
               "on the command line; use `key add` or the FINNVNOI_API_KEY variable.",
    )
    parser.add_argument("--version", action="version", version=f"fvc {__version__}")
    _add_common(parser)

    sub = parser.add_subparsers(dest="command")
    for name, description in (
        ("tui", "Terminal dashboard."),
        ("tray", "Desktop app: tray icon on Windows, menu bar on macOS."),
    ):
        _add_common(sub.add_parser(name, help=description), sub=True)

    once = sub.add_parser("once", help="Print usage once and exit.")
    _add_common(once, sub=True)
    once.add_argument("--json", action="store_true", help="Print the raw JSON.")
    once.add_argument("-n", "--logs", type=int, default=10, help="How many log rows to show (0 for none).")

    key = sub.add_parser("key", help="Manage stored API keys.")
    key_sub = key.add_subparsers(dest="key_command", required=True)
    add = key_sub.add_parser("add", help="Add or replace a key. Prompts without echo.")
    add.add_argument("label", help="Short name, e.g. work")
    add.add_argument("--key", dest="key_value", help="Pass the key inline. Not recommended: it lands in shell history.")
    remove = key_sub.add_parser("rm", help="Remove a key by label.")
    remove.add_argument("label")
    use = key_sub.add_parser("use", help="Set the default key.")
    use.add_argument("label")
    key_sub.add_parser("list", help="List stored keys. Keys are always masked.")

    sub.add_parser("config", help="Show the config path and current settings.")
    return parser


# ------------------------------------------------------------------ helpers
def _resolve(args) -> Tuple[List[Tuple[str, str]], dict]:
    cfg = cfg_mod.load()
    if args.base_url:
        cfg["base_url"] = args.base_url.rstrip("/")
    if args.interval:
        cfg["poll_interval"] = max(2.0, args.interval)
    entries = cfg_mod.resolve_keys(args.key)
    return entries, cfg


def _no_keys_message() -> int:
    print(
        "No API key configured.\n"
        "  store one     fvc key add work\n"
        "  or export     export FINNVNOI_API_KEY=sk-...\n"
        "  or one-off    fvc --key sk-... tui",
        file=sys.stderr,
    )
    return 2


def _make_poller(args, entries, cfg) -> Poller:
    return Poller(
        entries=entries,
        base_url=cfg["base_url"],
        interval=cfg["poll_interval"],
        active_index=cfg_mod.active_index(entries, cfg),
        window=args.window or "lt",
        include_logs=not args.no_logs,
        timeout=args.timeout,
    )


# ----------------------------------------------------------------- commands
def cmd_key(args) -> int:
    if args.key_command == "add":
        value = args.key_value
        if not value:
            try:
                value = getpass.getpass(f"API key for '{args.label}' (not echoed): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled.", file=sys.stderr)
                return 1
        if not value:
            print("Empty key, nothing saved.", file=sys.stderr)
            return 1
        cfg_mod.add_key(args.label, value)
        print(f"Saved '{args.label}' = {mask_key(value)} to {cfg_mod.config_path()} (mode 0600).")
        return 0

    if args.key_command == "rm":
        ok = cfg_mod.remove_key(args.label)
        print(f"Removed '{args.label}'." if ok else f"No key labelled '{args.label}'.")
        return 0 if ok else 1

    if args.key_command == "use":
        ok = cfg_mod.set_active(args.label)
        print(f"Default key is now '{args.label}'." if ok else f"No key labelled '{args.label}'.")
        return 0 if ok else 1

    cfg = cfg_mod.load()
    entries = cfg_mod.resolve_keys()
    if not entries:
        return _no_keys_message()
    for label, key in entries:
        marker = "*" if label == cfg.get("active") else " "
        origin = " (from environment)" if label == "env" else ""
        print(f" {marker} {label:<16} {mask_key(key)}{origin}")
    return 0


def cmd_config(args) -> int:
    cfg = cfg_mod.load()
    print(f"config      : {cfg_mod.config_path()}")
    print(f"base url    : {cfg['base_url']}")
    print(f"interval    : {cfg['poll_interval']:.0f}s")
    print(f"default key : {cfg.get('active') or '-'}")
    print(f"stored keys : {len(cfg['keys'])}")
    print(f"at login    : {autostart.describe()}")
    print(f"endpoints   : GET {cfg['base_url']}/v1/usage")
    print(f"             GET {cfg['base_url']}/v1/usage/activity?window=lt&include_logs=true")
    print(f"             POST {cfg['base_url']}/v1/usage/bulk")
    return 0


def cmd_once(args) -> int:
    entries, cfg = _resolve(args)
    if not entries:
        return _no_keys_message()
    index = cfg_mod.active_index(entries, cfg)
    label, key = entries[index]
    client = Client(base_url=cfg["base_url"], timeout=args.timeout)
    window = args.window or "lt"

    try:
        usage_raw = client.usage(api_key=key)
        activity_raw = client.activity(
            window=window, include_logs=args.logs > 0, api_key=key
        )
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        json.dump({"usage": usage_raw, "activity": activity_raw}, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    usage = Usage.parse(usage_raw)
    activity = Activity.parse(activity_raw)
    print(f"Key      : {label} ({mask_key(key)})   window {window.upper()}")
    print(f"Requests : {exact_int(usage.request_count)}")
    print(f"Tokens   : {exact_int(usage.total_tokens)}  (cached {exact_int(usage.cached_input_tokens)}"
          + (f", {usage.cache_hit_rate:.1f}%" if usage.cache_hit_rate else "") + ")")
    print(f"Cost     : {money(usage.total_cost_usd)}")
    if usage.expires_dt:
        print(f"Expires  : {local_clock(usage.expires_dt, True)} ({until(usage.expires_dt)})")

    for heading, limits in (("LIMITS", usage.limits), ("UPSTREAM LIMITS", usage.upstream_limits)):
        if not limits:
            continue
        print(f"\n{heading}")
        for limit in limits:
            ratio = limit.percent
            percent = f"{ratio:5.1f}%" if ratio is not None else "    -"
            reset = f"  {reset_label(limit.reset_dt)}" if limit.reset_at else ""
            print(f"  {limit.label:<28} {bar(ratio, 20)} {percent}  "
                  f"{limit_amount(limit.limit_type, limit.current_value)}/"
                  f"{limit_amount(limit.limit_type, limit.max_value)}{reset}")

    if activity.model_aggregates:
        print(f"\nBY MODEL ({window.upper()})")
        for aggregate in sorted(activity.model_aggregates, key=lambda a: a.total_cost_usd, reverse=True):
            print(f"  {aggregate.model:<30} ok {aggregate.success_count:>6}  fail {aggregate.failed_count:>5}  "
                  f"{human_int(aggregate.total_tokens):>9}  {money(aggregate.total_cost_usd):>12}")

    if args.logs > 0 and activity.requests:
        print(f"\nLAST {min(args.logs, len(activity.requests))} REQUESTS ({exact_int(activity.max_rows)} logged)")
        for row in sorted(activity.requests, key=lambda r: r.id, reverse=True)[: args.logs]:
            status = row.error_code or row.status or ("ok" if row.ok else "err")
            print(f"  {local_clock(row.when):<9} {row.model:<26} {status:<12} "
                  f"{human_int(row.total_tokens):>8}  {money(row.cost_usd):>12}")
    return 0


def cmd_tui(args) -> int:
    from .tui import run_tui

    entries, cfg = _resolve(args)
    if not entries:
        from .onboarding import terminal_key

        entries = terminal_key()
        if not entries:
            return _no_keys_message()
    poller = _make_poller(args, entries, cfg)
    return run_tui(poller, base_url=cfg["base_url"], alerts=not args.no_alerts)


def cmd_tray(args) -> int:
    """macOS gets the menu bar app; everywhere else gets the tray icon."""
    entries, cfg = _resolve(args)
    if not entries:
        from .onboarding import desktop_key

        entries = desktop_key()
        if not entries:
            return _no_keys_message()
    poller = _make_poller(args, entries, cfg)
    alerts = not args.no_alerts
    problems = []

    if sys.platform == "darwin":
        from .menubar import MissingDependency as MenuBarMissing, run_menubar

        try:
            return run_menubar(poller, base_url=cfg["base_url"], alerts=alerts)
        except MenuBarMissing as exc:
            problems.append(str(exc))

    from .tray import MissingDependency as TrayMissing, run_tray

    try:
        return run_tray(poller, base_url=cfg["base_url"], alerts=alerts)
    except TrayMissing as exc:
        problems.append(str(exc))

    for problem in problems:
        print(problem, file=sys.stderr)
    print("\nFalling back to the terminal dashboard.\n", file=sys.stderr)
    from .tui import run_tui

    return run_tui(poller, base_url=cfg["base_url"], alerts=alerts)


def pick_default_mode() -> str:
    """The desktop front end where its packages are installed, otherwise the TUI."""
    if sys.platform == "darwin":
        try:
            import rumps  # noqa: F401

            return "tray"
        except ImportError:
            pass
    if sys.platform.startswith("win") or sys.platform == "darwin":
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401

            return "tray"
        except ImportError:
            return "tui"
    return "tui"


def configure_console() -> None:
    """Windows consoles still default to a legacy code page; this output is Unicode.

    Switching the code page fixes modern terminals; errors="replace" keeps the
    rest from raising, and the ASCII glyph fallback keeps them readable.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    adopt_output_encoding()


def main(argv: Optional[List[str]] = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    command = args.command or pick_default_mode()
    return {
        "tui": cmd_tui,
        "tray": cmd_tray,
        "once": cmd_once,
        "key": cmd_key,
        "config": cmd_config,
    }[command](args)
