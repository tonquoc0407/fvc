"""Usage threshold alerts."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set

from .fmt import limit_amount, until
from .models import Limit
from .poller import Snapshot

LIMIT_THRESHOLDS = (80.0, 95.0)
EXPIRY_DAYS = (7, 1)
HYSTERESIS = 3.0


@dataclass(frozen=True)
class Alert:
    ident: str
    title: str
    body: str
    level: str  # "warning" or "critical"


class AlertWatcher:
    """Turns successive snapshots into the alerts that have newly become true."""

    def __init__(
        self,
        thresholds: Sequence[float] = LIMIT_THRESHOLDS,
        expiry_days: Sequence[int] = EXPIRY_DAYS,
    ):
        self.thresholds = tuple(sorted(thresholds))
        self.expiry_days = tuple(sorted(expiry_days, reverse=True))
        self._fired: Dict[str, Set[float]] = {}
        self._cycle: Dict[str, str] = {}
        self._expiry_fired: Dict[str, Set[int]] = {}

    def reset(self) -> None:
        self._fired.clear()
        self._cycle.clear()
        self._expiry_fired.clear()

    def check(self, snap: Snapshot, now: Optional[_dt.datetime] = None) -> List[Alert]:
        if snap.usage is None:
            return []
        now = now or _dt.datetime.now(_dt.timezone.utc)
        key = snap.active_label or "key"
        alerts: List[Alert] = []
        for limit in snap.usage.limits + snap.usage.upstream_limits:
            alerts.extend(self._check_limit(key, limit))
        alerts.extend(self._check_expiry(key, snap, now))
        return alerts

    # -- limits --------------------------------------------------------
    def _check_limit(self, key: str, limit: Limit) -> List[Alert]:
        ratio = limit.percent
        if ratio is None:
            return []
        ident = f"{key}::{limit.label}"

        # A new reset window is a clean slate.
        cycle = limit.reset_at or ""
        if self._cycle.get(ident) != cycle:
            self._cycle[ident] = cycle
            self._fired[ident] = set()

        fired = self._fired.setdefault(ident, set())
        for threshold in self.thresholds:
            if ratio < threshold - HYSTERESIS:
                fired.discard(threshold)

        alerts = []
        for threshold in self.thresholds:
            if ratio >= threshold and threshold not in fired:
                fired.add(threshold)
                alerts = [self._limit_alert(key, limit, threshold, ratio)]  # keep only the highest
        return alerts

    @staticmethod
    def _limit_alert(key: str, limit: Limit, threshold: float, ratio: float) -> Alert:
        remaining = f"{limit_amount(limit.limit_type, limit.remaining_value)} left"
        reset = f", resets {until(limit.reset_dt)}" if limit.reset_at else ""
        return Alert(
            ident=f"{key}::{limit.label}::{threshold:.0f}",
            title=f"Finnvnoi API Check · {key}",
            body=f"{limit.label} at {ratio:.0f}% — {remaining}{reset}",
            level="critical" if threshold >= 95 else "warning",
        )

    # -- key expiry ----------------------------------------------------
    def _check_expiry(self, key: str, snap: Snapshot, now: _dt.datetime) -> List[Alert]:
        expires = snap.usage.expires_dt if snap.usage else None
        if expires is None:
            return []
        remaining_days = (expires - now).total_seconds() / 86400
        fired = self._expiry_fired.setdefault(key, set())
        for days in self.expiry_days:
            if remaining_days > days:
                fired.discard(days)

        for days in self.expiry_days:
            if remaining_days <= days and days not in fired:
                fired.add(days)
                return [Alert(
                    ident=f"{key}::expiry::{days}",
                    title=f"Finnvnoi API Check · {key}",
                    body=f"API key expires {until(expires, now)}",
                    level="critical" if days <= 1 else "warning",
                )]
        return []
