"""Parse Finnvnoi API responses into dataclasses."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .fmt import parse_time, pct


def _int(source: Dict[str, Any], *names: str, default: int = 0) -> int:
    for name in names:
        value = source.get(name)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _float(source: Dict[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        value = source.get(name)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _str(source: Dict[str, Any], *names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = source.get(name)
        if value not in (None, ""):
            return str(value)
    return default


@dataclass
class Limit:
    limit_type: str = ""
    limit_window: str = ""
    max_value: int = 0
    current_value: int = 0
    remaining_value: int = 0
    model_filter: Optional[str] = None
    reset_at: Optional[str] = None
    source: str = "api_key_limit"

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "Limit":
        raw = raw or {}
        filter_val = raw.get("model_filter")
        if isinstance(filter_val, list):
            filter_str = ", ".join(str(x) for x in filter_val) if filter_val else None
        elif filter_val not in (None, ""):
            filter_str = str(filter_val)
        else:
            filter_str = None
        return cls(
            limit_type=_str(raw, "limit_type", default="") or "",
            limit_window=_str(raw, "limit_window", default="") or "",
            max_value=_int(raw, "max_value"),
            current_value=_int(raw, "current_value"),
            remaining_value=_int(raw, "remaining_value"),
            model_filter=filter_str,
            reset_at=_str(raw, "reset_at"),
            source=_str(raw, "source", default="api_key_limit") or "api_key_limit",
        )

    @property
    def percent(self) -> Optional[float]:
        return pct(self.current_value, self.max_value)

    @property
    def reset_dt(self) -> Optional[_dt.datetime]:
        return parse_time(self.reset_at)

    @property
    def label(self) -> str:
        parts = [p for p in (self.limit_type, self.limit_window) if p]
        text = " / ".join(parts) if parts else "limit"
        if self.model_filter:
            clean_filter = self.model_filter.strip()
            if clean_filter.startswith("[") and clean_filter.endswith("]"):
                clean_filter = clean_filter[1:-1].strip()
            text += f" [{clean_filter}]"
        return text


@dataclass
class Usage:
    request_count: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    total_cost_usd: float = 0.0
    expires_at: Optional[str] = None
    limits: List[Limit] = field(default_factory=list)
    upstream_limits: List[Limit] = field(default_factory=list)
    account_pool_primary: Optional[float] = None
    account_pool_secondary: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "Usage":
        raw = raw or {}
        pool = raw.get("account_pool_usage") or {}
        if not isinstance(pool, dict):
            pool = {}
        return cls(
            request_count=_int(raw, "request_count"),
            total_tokens=_int(raw, "total_tokens"),
            cached_input_tokens=_int(raw, "cached_input_tokens"),
            total_cost_usd=_float(raw, "total_cost_usd"),
            expires_at=_str(raw, "expires_at"),
            limits=[Limit.parse(x) for x in (raw.get("limits") or []) if isinstance(x, dict)],
            upstream_limits=[Limit.parse(x) for x in (raw.get("upstream_limits") or []) if isinstance(x, dict)],
            account_pool_primary=pool.get("primary"),
            account_pool_secondary=pool.get("secondary"),
            raw=raw,
        )

    @property
    def expires_dt(self) -> Optional[_dt.datetime]:
        return parse_time(self.expires_at)

    @property
    def cache_hit_rate(self) -> Optional[float]:
        if self.total_tokens <= 0:
            return None
        return self.cached_input_tokens / self.total_tokens * 100.0

    @property
    def worst_limit(self) -> Optional[Limit]:
        """The limit closest to its ceiling. Drives the tray icon colour."""
        scored = [(l.percent, l) for l in self.limits + self.upstream_limits if l.percent is not None]
        if not scored:
            return None
        return max(scored, key=lambda item: item[0])[1]

    @property
    def worst_percent(self) -> Optional[float]:
        limit = self.worst_limit
        return limit.percent if limit else None


@dataclass
class Totals:
    request_count: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    total_cost_usd: float = 0.0

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "Totals":
        raw = raw or {}
        return cls(
            request_count=_int(raw, "request_count"),
            total_tokens=_int(raw, "total_tokens"),
            cached_input_tokens=_int(raw, "cached_input_tokens"),
            total_cost_usd=_float(raw, "total_cost_usd"),
        )


@dataclass
class ModelAggregate:
    model: str = ""
    success_count: int = 0
    failed_count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "ModelAggregate":
        raw = raw or {}
        return cls(
            model=_str(raw, "model", default="?") or "?",
            success_count=_int(raw, "success_count"),
            failed_count=_int(raw, "failed_count"),
            total_tokens=_int(raw, "total_tokens"),
            total_cost_usd=_float(raw, "total_cost_usd"),
        )

    @property
    def total_count(self) -> int:
        return self.success_count + self.failed_count


@dataclass
class RequestRow:
    id: int = 0
    requested_at: Optional[str] = None
    model: str = ""
    status: str = ""
    error_code: Optional[str] = None
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "RequestRow":
        raw = raw or {}
        return cls(
            id=_int(raw, "id"),
            # The server sends requested_at; accept created_at in case it is renamed.
            requested_at=_str(raw, "requested_at", "created_at", "timestamp"),
            model=_str(raw, "model", default="?") or "?",
            status=_str(raw, "status", default="") or "",
            error_code=_str(raw, "error_code", "error"),
            total_tokens=_int(raw, "total_tokens"),
            cached_input_tokens=_int(raw, "cached_input_tokens"),
            cost_usd=_float(raw, "cost_usd", "total_cost_usd"),
        )

    @property
    def when(self) -> Optional[_dt.datetime]:
        return parse_time(self.requested_at)

    @property
    def ok(self) -> bool:
        status = (self.status or "").lower()
        if self.error_code:
            return False
        if status.isdigit():
            return int(status) < 400
        return status in ("success", "ok", "completed", "succeeded", "200")


@dataclass
class Activity:
    window: str = "lt"
    page: int = 1
    page_size: int = 0
    total_pages: int = 0
    max_rows: int = 0
    latest_id: Optional[int] = None
    removed_ids: List[int] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)
    model_aggregates: List[ModelAggregate] = field(default_factory=list)
    requests: List[RequestRow] = field(default_factory=list)

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "Activity":
        raw = raw or {}
        latest = raw.get("latest_id")
        try:
            latest = int(latest) if latest is not None else None
        except (TypeError, ValueError):
            latest = None
        removed = []
        for value in raw.get("removed_ids") or []:
            try:
                removed.append(int(value))
            except (TypeError, ValueError):
                continue
        return cls(
            window=_str(raw, "window", default="lt") or "lt",
            page=_int(raw, "page", default=1),
            page_size=_int(raw, "page_size"),
            total_pages=_int(raw, "total_pages"),
            max_rows=_int(raw, "max_rows"),
            latest_id=latest,
            removed_ids=removed,
            totals=Totals.parse(raw.get("totals") or {}),
            model_aggregates=[ModelAggregate.parse(x) for x in (raw.get("model_aggregates") or []) if isinstance(x, dict)],
            requests=[RequestRow.parse(x) for x in (raw.get("requests") or []) if isinstance(x, dict)],
        )


@dataclass
class KeyStatus:
    """Summary row for one key in the multi-key table."""

    label: str = ""
    masked_key: str = ""
    ok: bool = False
    status_code: int = 0
    usage: Optional[Usage] = None
    error: Optional[str] = None
    expires_at: Optional[str] = None
    plan_type: Optional[str] = None

    @property
    def expires_dt(self) -> Optional[_dt.datetime]:
        return parse_time(self.expires_at)
