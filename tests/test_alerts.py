"""Tests for the threshold watcher.

Run: python3 tests/test_alerts.py

An alert must fire once when a limit crosses a threshold, stay quiet while it
sits there, and become eligible again only after a real drop or a new reset
window. Getting that wrong means a notification every poll, which is worse than
no notification at all.
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codex_usage.alerts import AlertWatcher
from codex_usage.models import Usage
from codex_usage.poller import Snapshot

NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)
RESET = (NOW + dt.timedelta(hours=5)).isoformat()


def snapshot(percent, reset_at=RESET, expires_at=None, label="work"):
    used = int(percent * 100)
    usage = Usage.parse({
        "request_count": 1, "total_tokens": 1, "cached_input_tokens": 0, "total_cost_usd": 0.0,
        "expires_at": expires_at,
        "limits": [{"limit_type": "requests", "limit_window": "1d", "max_value": 10000,
                    "current_value": used, "remaining_value": 10000 - used, "reset_at": reset_at}],
    })
    return Snapshot(usage=usage, active_label=label, active_masked="sk-x", window="lt")


def main() -> int:
    checks = []

    def expect(description, got, want):
        checks.append((description, got, want, got == want))

    # A single crossing fires once and then goes quiet.
    watcher = AlertWatcher()
    expect("50% is quiet", len(watcher.check(snapshot(50), NOW)), 0)
    expect("crossing 80% fires", len(watcher.check(snapshot(81), NOW)), 1)
    expect("still at 81% stays quiet", len(watcher.check(snapshot(81), NOW)), 0)
    expect("rising to 90% stays quiet", len(watcher.check(snapshot(90), NOW)), 0)
    expect("crossing 95% fires", len(watcher.check(snapshot(96), NOW)), 1)
    expect("still at 96% stays quiet", len(watcher.check(snapshot(96), NOW)), 0)

    # Jumping straight past both thresholds reports the higher one only.
    watcher = AlertWatcher()
    alerts = watcher.check(snapshot(99), NOW)
    expect("jump to 99% fires once", len(alerts), 1)
    expect("jump to 99% is critical", alerts[0].level if alerts else None, "critical")

    # Hysteresis: a dip has to be real before the alert may fire again.
    watcher = AlertWatcher()
    watcher.check(snapshot(81), NOW)
    expect("dip to 79% is within hysteresis", len(watcher.check(snapshot(79), NOW)), 0)
    expect("back to 82% does not re-fire", len(watcher.check(snapshot(82), NOW)), 0)
    expect("dip to 70% clears the latch", len(watcher.check(snapshot(70), NOW)), 0)
    expect("82% after a real dip fires again", len(watcher.check(snapshot(82), NOW)), 1)

    # A new reset window is a clean slate even without a dip.
    watcher = AlertWatcher()
    watcher.check(snapshot(96), NOW)
    later = (NOW + dt.timedelta(days=1)).isoformat()
    expect("same window stays quiet", len(watcher.check(snapshot(96), NOW)), 0)
    expect("new reset window fires again", len(watcher.check(snapshot(96, reset_at=later), NOW)), 1)

    # Keys are tracked separately, so switching key does not silence the other.
    watcher = AlertWatcher()
    watcher.check(snapshot(96, label="work"), NOW)
    expect("a second key fires on its own", len(watcher.check(snapshot(96, label="spare"), NOW)), 1)
    expect("first key still quiet", len(watcher.check(snapshot(96, label="work"), NOW)), 0)

    # Key expiry warnings.
    watcher = AlertWatcher()
    far = (NOW + dt.timedelta(days=20)).isoformat()
    soon = (NOW + dt.timedelta(days=5)).isoformat()
    imminent = (NOW + dt.timedelta(hours=12)).isoformat()
    expect("20 days out is quiet", len(watcher.check(snapshot(10, expires_at=far), NOW)), 0)
    expect("5 days out warns", len(watcher.check(snapshot(10, expires_at=soon), NOW)), 1)
    expect("5 days out warns only once", len(watcher.check(snapshot(10, expires_at=soon), NOW)), 0)
    expect("12 hours out warns again", len(watcher.check(snapshot(10, expires_at=imminent), NOW)), 1)

    # A limit with no ceiling cannot have a percentage, so it cannot alert.
    watcher = AlertWatcher()
    usage = Usage.parse({"limits": [{"limit_type": "x", "limit_window": "y", "max_value": 0,
                                     "current_value": 5, "remaining_value": 0, "reset_at": None}]})
    unbounded = Snapshot(usage=usage, active_label="work", active_masked="sk-x", window="lt")
    expect("no ceiling means no alert", len(watcher.check(unbounded, NOW)), 0)
    expect("no usage means no alert", len(watcher.check(Snapshot(active_label="w"), NOW)), 0)

    # The message has to carry the numbers a person acts on.
    watcher = AlertWatcher()
    alert = watcher.check(snapshot(96), NOW)[0]
    expect("body names the limit", "requests / 1d" in alert.body, True)
    expect("body carries the percentage", "96%" in alert.body, True)
    expect("body says what is left", "left" in alert.body, True)
    expect("body says when it resets", "resets in" in alert.body, True)
    expect("title names the key", alert.title, "Finnvnoi API Check · work")

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
