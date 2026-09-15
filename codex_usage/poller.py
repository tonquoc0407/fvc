"""Background poller: calls /v1/usage and /v1/usage/activity on an interval, publishes a snapshot."""

from __future__ import annotations

import datetime as _dt
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .api import ApiError, Client
from .fmt import mask_key
from .models import Activity, KeyStatus, RequestRow, Usage

MAX_ROWS = 500
MAX_BACKOFF = 60.0


@dataclass
class Snapshot:
    """An immutable view of the latest poll, safe to read from another thread."""

    usage: Optional[Usage] = None
    activity: Optional[Activity] = None
    rows: List[RequestRow] = field(default_factory=list)
    keys: List[KeyStatus] = field(default_factory=list)
    active_label: str = ""
    active_masked: str = ""
    window: str = "lt"
    error: Optional[str] = None
    error_is_auth: bool = False
    fetched_at: Optional[_dt.datetime] = None
    latency_ms: Optional[float] = None
    consecutive_failures: int = 0
    next_poll_at: float = 0.0
    poll_interval: float = 10.0

    @property
    def has_data(self) -> bool:
        return self.usage is not None or self.activity is not None

    @property
    def seconds_to_next(self) -> float:
        return max(0.0, self.next_poll_at - time.monotonic())


class Poller:
    """Owns a worker thread. Front ends read `snapshot()` or subscribe with `set_on_update`."""

    def __init__(
        self,
        entries: List[Tuple[str, str]],
        base_url: str,
        interval: float = 10.0,
        active_index: int = 0,
        window: str = "lt",
        include_logs: bool = True,
        timeout: float = 15.0,
        on_update: Optional[Callable[[Snapshot], None]] = None,
    ):
        if not entries:
            raise ValueError("No API key. Run: fvc key add <label>")
        self._entries = list(entries)
        self._active = max(0, min(active_index, len(self._entries) - 1))
        self._interval = max(2.0, float(interval))
        self._window = window
        self._include_logs = include_logs
        self._client = Client(base_url=base_url, timeout=timeout)
        self._on_update = on_update

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._rows: Dict[int, RequestRow] = {}
        self._last_id: Optional[int] = None
        self._failures = 0
        self._snapshot = Snapshot(
            active_label=self._entries[self._active][0],
            active_masked=mask_key(self._entries[self._active][1]),
            window=window,
            poll_interval=self._interval,
        )

    # -- lifecycle -----------------------------------------------------
    def start(self) -> "Poller":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fvc-poller", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # -- controls ------------------------------------------------------
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def refresh_now(self) -> None:
        self._wake.set()

    def set_on_update(self, callback) -> None:
        """Register a callback fired after each poll, on the worker thread."""
        self._on_update = callback

    @property
    def entries(self) -> List[Tuple[str, str]]:
        return list(self._entries)

    @property
    def active_index(self) -> int:
        return self._active

    @property
    def interval(self) -> float:
        return self._interval

    @property
    def window(self) -> str:
        return self._window

    def set_interval(self, seconds: float) -> None:
        with self._lock:
            self._interval = max(2.0, float(seconds))
        self.refresh_now()

    def set_active(self, index: int) -> None:
        with self._lock:
            index = index % len(self._entries)
            if index == self._active:
                return
            self._active = index
            self._rows.clear()
            self._last_id = None
            self._failures = 0
            self._snapshot = Snapshot(
                keys=self._snapshot.keys,
                active_label=self._entries[index][0],
                active_masked=mask_key(self._entries[index][1]),
                window=self._window,
                poll_interval=self._interval,
            )
        self.refresh_now()

    def cycle_active(self, step: int = 1) -> None:
        self.set_active(self._active + step)

    def set_window(self, window: str) -> None:
        with self._lock:
            if window == self._window:
                return
            self._window = window
        self.refresh_now()

    # -- worker --------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            with self._lock:
                delay = self._interval
                if self._failures:
                    delay = min(MAX_BACKOFF, self._interval * (2 ** min(self._failures, 5)))
                self._snapshot.next_poll_at = time.monotonic() + delay
            self._wake.wait(delay)
            self._wake.clear()

    def _tick(self) -> None:
        with self._lock:
            label, key = self._entries[self._active]
            window = self._window
            last_id = self._last_id
            include_logs = self._include_logs

        error: Optional[str] = None
        is_auth = False
        usage: Optional[Usage] = None
        activity: Optional[Activity] = None

        try:
            usage = Usage.parse(self._client.usage(api_key=key))
        except ApiError as exc:
            error, is_auth = str(exc), exc.is_auth

        if error is None or not is_auth:
            try:
                raw = self._client.activity(
                    window=window,
                    include_logs=include_logs,
                    after_id=last_id if include_logs else None,
                    api_key=key,
                )
                activity = Activity.parse(raw)
            except ApiError as exc:
                if error is None:
                    error, is_auth = str(exc), exc.is_auth

        keys_status = self._refresh_key_table(key) if len(self._entries) > 1 else []

        with self._lock:
            if activity is not None:
                for removed in activity.removed_ids:
                    self._rows.pop(removed, None)
                for row in activity.requests:
                    self._rows[row.id] = row
                if len(self._rows) > MAX_ROWS:
                    for stale in sorted(self._rows)[: len(self._rows) - MAX_ROWS]:
                        self._rows.pop(stale, None)
                if activity.latest_id is not None:
                    self._last_id = activity.latest_id
                elif self._rows:
                    self._last_id = max(self._rows)

            if error:
                self._failures += 1
            else:
                self._failures = 0

            previous = self._snapshot
            self._snapshot = Snapshot(
                usage=usage or (previous.usage if error else None),
                activity=activity or (previous.activity if error else None),
                rows=[self._rows[i] for i in sorted(self._rows, reverse=True)],
                keys=keys_status or previous.keys,
                active_label=label,
                active_masked=mask_key(key),
                window=window,
                error=error,
                error_is_auth=is_auth,
                fetched_at=_dt.datetime.now(_dt.timezone.utc) if not error else previous.fetched_at,
                latency_ms=self._client.last_latency_ms,
                consecutive_failures=self._failures,
                next_poll_at=previous.next_poll_at,
                poll_interval=self._interval,
            )
            snapshot = self._snapshot

        if self._on_update:
            try:
                self._on_update(snapshot)
            except Exception:
                pass

    def _refresh_key_table(self, auth_key: str) -> List[KeyStatus]:
        """Summarise every configured key in a single bulk call."""
        labels = [label for label, _ in self._entries]
        keys = [key for _, key in self._entries]
        try:
            payload = self._client.bulk(keys, api_key=auth_key)
        except ApiError as exc:
            return [
                KeyStatus(label=label, masked_key=mask_key(key), ok=False, error=str(exc))
                for label, key in self._entries
            ]

        out: List[KeyStatus] = []
        items = payload.get("data") if isinstance(payload, dict) else None
        for position, item in enumerate(items or []):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index", position))
            except (TypeError, ValueError):
                index = position
            label = labels[index] if 0 <= index < len(labels) else f"#{index}"
            fallback_mask = mask_key(keys[index]) if 0 <= index < len(keys) else "-"
            err = item.get("error") if isinstance(item.get("error"), dict) else None
            codex = item.get("codex_usage") if isinstance(item.get("codex_usage"), dict) else None
            out.append(
                KeyStatus(
                    label=label,
                    masked_key=item.get("masked_key") or fallback_mask,
                    ok=bool(item.get("ok")),
                    status_code=int(item.get("status_code") or 0),
                    usage=Usage.parse(item["usage"]) if isinstance(item.get("usage"), dict) else None,
                    error=(err or {}).get("message"),
                    expires_at=item.get("expires_at"),
                    plan_type=(codex or {}).get("plan_type"),
                )
            )
        return out
