"""Tests for dashboard geometry.

Run: python3 tests/test_layout.py

Two invariants, checked across terminal sizes, views and data states:

  1. A frame is exactly `height` lines, and every line is exactly `width` cells.
  2. No row is wider than the space inside the frame, so nothing gets clipped.

The second one matters because rows fail silently: `_line` pads and truncates, so
an over-wide row just loses its tail. Sizing bugs stay invisible without this.
"""

import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CLICOLOR_FORCE"] = "1"

from codex_usage.models import Activity, KeyStatus, Usage
from codex_usage.poller import Poller, Snapshot
from codex_usage.tui import Dashboard

SIZES = [(64, 20), (66, 21), (70, 22), (74, 23), (80, 24), (84, 25),
         (90, 26), (104, 30), (112, 32), (120, 40), (200, 50)]

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
POSITION = re.compile(r"\x1b\[\d+;1H")


def _plain(text: str) -> str:
    return ANSI.sub("", text)


def _states(now):
    usage = Usage.parse({
        "request_count": 1204, "total_tokens": 4823117, "cached_input_tokens": 2101884,
        "total_cost_usd": 18.42,
        "limits": [
            {"limit_type": "tokens", "limit_window": "5h", "max_value": 1000000,
             "current_value": 624000, "remaining_value": 376000,
             "reset_at": (now + dt.timedelta(hours=2)).isoformat()},
            {"limit_type": "total_tokens", "limit_window": "lifetime", "max_value": 52600000,
             "current_value": 9370000, "remaining_value": 43230000, "reset_at": "9999-12-31T23:59:59Z"},
        ],
        "upstream_limits": [
            {"limit_type": "weekly", "limit_window": "7d", "max_value": 100, "current_value": 41,
             "remaining_value": 59, "reset_at": (now + dt.timedelta(days=3)).isoformat(),
             "source": "upstream"},
        ],
    })
    rows = [{"id": 500 - i, "requested_at": (now - dt.timedelta(minutes=i)).isoformat(),
             "model": "gpt-5.6-sol", "status": "success", "total_tokens": 40000 + i * 137,
             "cached_input_tokens": 12000, "cost_usd": 0.09} for i in range(60)]
    activity = Activity.parse({
        "window": "lt", "max_rows": 482, "latest_id": 500,
        "totals": {"request_count": 1204, "total_tokens": 4823117,
                   "cached_input_tokens": 2101884, "total_cost_usd": 18.42},
        "model_aggregates": [{"model": "gpt-5.6-sol", "success_count": 880, "failed_count": 25,
                              "total_tokens": 3900000, "total_cost_usd": 15.2}],
        "requests": rows,
    })
    # A limit whose every field is far longer than the columns that hold it.
    stretched = Usage.parse({
        "request_count": 9, "total_tokens": 9, "cached_input_tokens": 0, "total_cost_usd": 0.0,
        "limits": [{"limit_type": "an_extremely_long_limit_type_name",
                    "limit_window": "rolling_30_days", "max_value": 100, "current_value": 1,
                    "remaining_value": 99, "model_filter": "gpt-5.6-sol-preview-long",
                    "reset_at": (now + dt.timedelta(days=3600)).isoformat()}],
    })
    return {
        "populated": Snapshot(usage=usage, activity=activity, rows=activity.requests,
                              keys=[KeyStatus(label="work", masked_key="sk-amin-CP...DPtM", ok=True,
                                              status_code=200, usage=usage),
                                    KeyStatus(label="personal", masked_key="sk-clb-zzz...11bb",
                                              ok=False, status_code=401, error="Invalid API key")],
                              active_label="work", active_masked="sk-amin-CP...DPtM", window="lt",
                              fetched_at=now, latency_ms=312.0, poll_interval=10),
        "no data": Snapshot(active_label="work", active_masked="sk-x", window="lt", poll_interval=10),
        "error": Snapshot(active_label="work", active_masked="sk-x", window="lt", poll_interval=10,
                          error="HTTP 500: upstream unavailable and the message runs on",
                          consecutive_failures=3),
        "overlong fields": Snapshot(usage=stretched, active_label="work", active_masked="sk-x",
                                    window="lt", poll_interval=10, fetched_at=now),
    }


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    poller = Poller([("work", "sk-a"), ("personal", "sk-b")], base_url="https://example", interval=10)

    overflows = []
    original = Dashboard._line

    def watched(self, row, width):
        if row.width > width - 4:
            overflows.append((width, self.view, row.width - (width - 4), _plain(row.render())[:48]))
        return original(self, row, width)

    Dashboard._line = watched

    failures = 0
    checks = 0
    print(f"{'state':<17} {'size':<9} {'view':<8} result")
    print("-" * 60)
    for name, snapshot in _states(now).items():
        poller._snapshot = snapshot
        for width, height in SIZES:
            for view, view_name in enumerate(("log", "models", "keys")):
                for window in range(4):
                    dashboard = Dashboard(poller, base_url="https://example", color=True)
                    dashboard.size = lambda w=width, h=height: (w, h)
                    dashboard.view, dashboard.cursor, dashboard.window_index = view, 2, window
                    lines = [_plain(part) for part in POSITION.split(dashboard.render())[1:]]
                    widths = {len(line) for line in lines}
                    checks += 1
                    if len(lines) != height or widths != {width}:
                        failures += 1
                        print(f"{name:<17} {f'{width}x{height}':<9} {view_name:<8} "
                              f"FAIL {len(lines)} lines, widths {sorted(widths)}")
        print(f"{name:<17} {'all':<9} {'all':<8} {len(SIZES) * 12} frames exact")

    print("-" * 60)
    if overflows:
        failures += 1
        print(f"{len(overflows)} rows wider than the frame:")
        for entry in overflows[:8]:
            print(f"   width {entry[0]} view {entry[1]} over by {entry[2]}: {entry[3]}")
    else:
        print(f"{checks} frames checked, no row overflows its frame")
    print(f"{failures} failed" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
