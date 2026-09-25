"""Context-local, privacy-constrained traces for cognitive decisions."""

from __future__ import annotations

import logging
import math
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.clock import time as clock_time

_STRING = re.compile(r"^[A-Za-z0-9_.:/\-]{1,80}$")
_SCHEMAS: dict[str, frozenset[str]] = {
    "memory.search": frozenset(
        {
            "policy",
            "source",
            "pool",
            "archived_candidates",
            "skipped_dimension",
            "error",
            "error_code",
            "ms",
            "results",
            "cache_hit",
            "score_terms",
        }
    ),
    "affect.update": frozenset({"cause", "inputs", "before", "after", "delta"}),
    "trust.update": frozenset(
        {"person_id", "cause", "inputs", "before", "after", "delta"}
    ),
    "proactive.decision": frozenset(
        {
            "fired",
            "reason",
            "idle_s",
            "idle_threshold_s",
            "cooldown_remaining_s",
            "cooldown_s",
            "energy",
            "energy_min",
            "probability",
            "probability_min",
            "benchmark_elapsed_s",
            "benchmark_threshold_s",
        }
    ),
    "arbitration.decision": frozenset({"chosen", "candidates"}),
}


class TraceSchemaError(ValueError):
    """A trace event contains an undeclared field or unsafe value."""


@dataclass
class _Sink:
    events: list[dict[str, Any]]
    strict: bool


_sinks: ContextVar[tuple[_Sink, ...]] = ContextVar("cognitive_trace_sinks", default=())
_dropped = 0
_warned_kinds: set[str] = set()
_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TraceSchemaError("non-finite number")
        return round(value, 6)
    if isinstance(value, str):
        if not _STRING.fullmatch(value):
            raise TraceSchemaError("unsafe string")
        return value
    if depth >= 3:
        raise TraceSchemaError("value nesting exceeds three levels")
    if isinstance(value, list):
        if len(value) > 200:
            raise TraceSchemaError("list exceeds 200 items")
        return [_safe_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not _STRING.fullmatch(key):
                raise TraceSchemaError("unsafe mapping key")
            copied[key] = _safe_value(item, depth=depth + 1)
        return copied
    raise TraceSchemaError("unsupported value type")


def _validated(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(kind, str):
        raise TraceSchemaError("invalid trace kind")
    allowed = _SCHEMAS.get(kind)
    if allowed is None:
        raise TraceSchemaError("unknown trace kind")
    unknown = fields.keys() - allowed
    if unknown:
        raise TraceSchemaError("unknown trace field")
    # Schemas are allowlists; fields may be absent when their value is not
    # available at the decision point.
    return {key: _safe_value(value) for key, value in fields.items()}


def validate_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an event at a persistence boundary and return a safe copy."""
    kind = event.get("kind")
    timestamp = event.get("t")
    if not isinstance(kind, str):
        raise TraceSchemaError("invalid trace kind")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise TraceSchemaError("invalid trace timestamp")
    safe_timestamp = _safe_value(timestamp)
    fields = {key: value for key, value in event.items() if key not in {"kind", "t"}}
    return {"kind": kind, "t": safe_timestamp, **_validated(kind, fields)}


def emit(kind: str, **fields: Any) -> None:
    """Send one safe event to every active collection scope."""
    sinks = _sinks.get()
    if not sinks:
        return
    try:
        safe_fields = _validated(kind, fields)
    except TraceSchemaError:
        if any(sink.strict for sink in sinks):
            raise
        global _dropped
        warning_key = kind if isinstance(kind, str) else type(kind).__name__
        with _lock:
            _dropped += 1
            if warning_key not in _warned_kinds:
                _warned_kinds.add(warning_key)
                kind_label = (
                    kind if isinstance(kind, str) and kind in _SCHEMAS else "unknown"
                )
                _logger.warning("Dropped invalid cognitive trace kind=%s", kind_label)
        return
    event = {"kind": kind, "t": round(clock_time(), 6), **safe_fields}
    for sink in sinks:
        sink.events.append(event)


def enabled() -> bool:
    """Return whether this context currently has an active trace sink."""
    return bool(_sinks.get())


@contextmanager
def collect(*, strict: bool = True) -> Iterator[list[dict[str, Any]]]:
    """Collect events; nested scopes each receive their own event copy."""
    events: list[dict[str, Any]] = []
    sink = _Sink(events=events, strict=strict)
    token = _sinks.set((*_sinks.get(), sink))
    try:
        yield events
    finally:
        _sinks.reset(token)


def dropped_count() -> int:
    """Return the process-wide count of events rejected by non-strict scopes."""
    with _lock:
        return _dropped
