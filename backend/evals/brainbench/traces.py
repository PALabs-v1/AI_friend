"""JSONL persistence and compact summaries for cognitive trace events."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.cognitive.trace import dropped_count, validate_event


def write_jsonl(events: Iterable[Mapping[str, Any]], path: str | Path) -> None:
    """Write one already-validated trace event per line."""
    with Path(path).open("w", encoding="utf-8") as stream:
        for event in events:
            safe_event = validate_event(event)
            stream.write(json.dumps(safe_event, separators=(",", ":"), sort_keys=True))
            stream.write("\n")


def summarize(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Count event kinds and their cause/reason codes."""
    by_kind: Counter[str] = Counter()
    by_cause: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    for event in events:
        kind = event.get("kind")
        if isinstance(kind, str):
            by_kind[kind] += 1
        cause = event.get("cause")
        if isinstance(cause, str):
            by_cause[cause] += 1
        reason = event.get("reason")
        if isinstance(reason, str):
            by_reason[reason] += 1
    return {
        "counts_by_kind": dict(sorted(by_kind.items())),
        "counts_by_cause": dict(sorted(by_cause.items())),
        "counts_by_reason": dict(sorted(by_reason.items())),
        "dropped_events": dropped_count(),
    }
