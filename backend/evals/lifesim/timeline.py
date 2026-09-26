"""Event-sourced ground truth: what was true about the world, and when.

Every fact about the simulated human (and the people, pets, commitments and
robot relationship around them) is an `Assertion` on one ``(entity,
attribute)`` slot with a validity window. A genuine change closes the old
assertion and opens a successor that points back at it (``supersedes``), so
history is kept. A misstatement or correction is an observation-layer event
and never enters this timeline: per DR-006 a corrected value was never true.

Projections answer the two questions every probe reduces to:

* ``value_at(entity, attribute, t)`` -- what was true at time t.
* ``history(entity, attribute)`` -- every value the slot ever held, in order.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime

KINDS = (
    "stable",
    "changing",
    "temporary",
    "historical",
    "relation",
    "commitment",
    "status",
)


@dataclass
class Assertion:
    assertion_id: str
    entity: str
    attribute: str
    value: str
    valid_from: datetime
    valid_to: datetime | None
    kind: str
    source_event: str | None
    supersedes: str | None = None
    certainty: float = 1.0
    importance: float = 0.5

    def active_at(self, t: datetime) -> bool:
        return self.valid_from <= t and (self.valid_to is None or t < self.valid_to)

    def to_json(self) -> dict:
        d = asdict(self)
        d["valid_from"] = self.valid_from.isoformat()
        d["valid_to"] = self.valid_to.isoformat() if self.valid_to else None
        return d


class TimelineError(ValueError):
    pass


class Timeline:
    def __init__(self) -> None:
        self._by_id: dict[str, Assertion] = {}
        self._slots: dict[tuple[str, str], list[Assertion]] = defaultdict(list)
        self._next = 0

    def _new_id(self) -> str:
        self._next += 1
        return f"a{self._next:06d}"

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self):
        return iter(
            sorted(self._by_id.values(), key=lambda a: (a.valid_from, a.assertion_id))
        )

    def get(self, assertion_id: str) -> Assertion:
        return self._by_id[assertion_id]

    def slots(self) -> list[tuple[str, str]]:
        return sorted(self._slots)

    def history(self, entity: str, attribute: str) -> list[Assertion]:
        return list(self._slots.get((entity, attribute), ()))

    def can_assert(self, entity: str, attribute: str, t: datetime) -> bool:
        """True if a new value at ``t`` would move the slot strictly forward in time."""
        hist = self._slots.get((entity, attribute))
        return not hist or hist[-1].valid_from < t

    def value_at(self, entity: str, attribute: str, t: datetime) -> Assertion | None:
        for a in reversed(self._slots.get((entity, attribute), ())):
            if a.active_at(t):
                return a
        return None

    def current(self, entity: str, attribute: str, t: datetime) -> str | None:
        a = self.value_at(entity, attribute, t)
        return a.value if a else None

    def truth_at(self, t: datetime) -> dict[tuple[str, str], str]:
        out = {}
        for key in self._slots:
            a = self.value_at(*key, t)
            if a is not None:
                out[key] = a.value
        return out

    def assert_(
        self,
        entity: str,
        attribute: str,
        value: str,
        t: datetime,
        *,
        kind: str,
        source_event: str | None,
        until: datetime | None = None,
        certainty: float = 1.0,
        importance: float = 0.5,
    ) -> Assertion:
        """Make ``value`` true from ``t``. Closes and supersedes any value active at ``t``.

        A slot may only move forward in time: asserting before the start of the
        latest assertion is rejected, because it would rewrite history rather
        than record a change.
        """
        if kind not in KINDS:
            raise TimelineError(f"unknown kind {kind!r}")
        hist = self._slots[(entity, attribute)]
        prev = hist[-1] if hist else None
        if prev is not None and t < prev.valid_from:
            raise TimelineError(
                f"{entity}.{attribute}: new value at {t} precedes latest at {prev.valid_from}"
            )
        supersedes = None
        if prev is not None and (prev.valid_to is None or prev.valid_to > t):
            # Same value at a new certainty is a confirmation: kept as its own
            # assertion so "when did it become definite" has an answer.
            if prev.value == value and prev.certainty == certainty:
                return prev
            prev.valid_to = t
            supersedes = prev.assertion_id
        a = Assertion(
            assertion_id=self._new_id(),
            entity=entity,
            attribute=attribute,
            value=value,
            valid_from=t,
            valid_to=until,
            kind=kind,
            source_event=source_event,
            supersedes=supersedes,
            certainty=certainty,
            importance=importance,
        )
        hist.append(a)
        self._by_id[a.assertion_id] = a
        return a

    def end(self, entity: str, attribute: str, t: datetime) -> Assertion | None:
        """Close the active value at ``t`` without a successor (a temporary state ending)."""
        a = self.value_at(entity, attribute, t)
        if a is not None:
            a.valid_to = t
        return a

    def validate(self) -> None:
        """Raise `TimelineError` on any broken invariant (R3)."""
        for key, hist in self._slots.items():
            for prev, cur in itertools.pairwise(hist):
                if prev.valid_to is None or prev.valid_to > cur.valid_from:
                    raise TimelineError(
                        f"{key}: overlap between {prev.assertion_id} and {cur.assertion_id}"
                    )
                if cur.supersedes is not None and cur.supersedes != prev.assertion_id:
                    raise TimelineError(
                        f"{key}: {cur.assertion_id} supersedes a non-predecessor"
                    )
                if (
                    cur.supersedes == prev.assertion_id
                    and prev.valid_to != cur.valid_from
                ):
                    raise TimelineError(
                        f"{key}: supersession gap at {cur.assertion_id}"
                    )
            for a in hist:
                if a.valid_to is not None and a.valid_to < a.valid_from:
                    raise TimelineError(
                        f"{key}: {a.assertion_id} ends before it starts"
                    )
                if a.supersedes is not None:
                    target = self._by_id.get(a.supersedes)
                    if target is None or target.valid_from > a.valid_from:
                        raise TimelineError(
                            f"{key}: {a.assertion_id} supersedes forward in time"
                        )
