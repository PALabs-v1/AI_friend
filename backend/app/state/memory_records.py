"""Typed records and deterministic contradiction decisions for durable memory."""

from __future__ import annotations

import re
import time
from typing import Literal, overload

from pydantic import BaseModel, ConfigDict, Field


class MemoryStoreError(Exception):
    """Base exception raised by the temporal memory persistence boundary."""


class DuplicateRecordError(MemoryStoreError):
    """Raised when a record identifier already exists."""


class RecordNotFoundError(MemoryStoreError):
    """Raised when a requested temporal record does not exist."""


class InvalidIntervalError(MemoryStoreError, ValueError):
    """Raised when a temporal transition would create an invalid interval."""


class ExperienceRecord(BaseModel):
    """An immutable, timestamped account of one observed experience."""

    model_config = ConfigDict(frozen=True)

    record_id: str
    session_id: str
    participants: list[str]
    interval_start: float
    interval_end: float
    source_evidence_ids: list[str] = Field(default_factory=list)
    appraisal_snapshot: dict[str, float] = Field(default_factory=dict)
    action_id: str | None = None
    outcome_id: str | None = None
    summary: str
    recorded_at: float = Field(default_factory=time.time)


class BeliefRecord(BaseModel):
    """A semantic assertion with valid-time and recorded-time provenance."""

    record_id: str
    subject: str
    predicate: str
    object: str
    valid_from: float
    valid_until: float | None = None
    recorded_at: float = Field(default_factory=time.time)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: Literal["ACTIVE", "SUPERSEDED", "INVALIDATED", "DISPUTED"] = "ACTIVE"
    superseded_by: str | None = None
    contradicts_id: str | None = None
    provenance: str = "conversation"


class ProcedureRecord(BaseModel):
    """A learned procedure and its observed success or failure history."""

    procedure_id: str
    name: str
    steps: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    status: str = "ACTIVE"


ContradictionType = Literal["ELABORATION", "UPDATE", "CORRECTION", "CONFLICT"]


class ContradictionDecision(BaseModel):
    """An auditable decision for a relationship between two belief records."""

    contradiction_type: ContradictionType
    existing_record_id: str
    new_record_id: str
    action_taken: str
    reason: str


_TEMPORAL_PROGRESSION_MARKERS = (
    "after ",
    "as of ",
    "currently ",
    "from now ",
    "now ",
    "since ",
    "today ",
)


@overload
def classify_contradiction(
    existing: BeliefRecord,
    incoming: BeliefRecord,
    new_predicate: None = None,
    new_object: None = None,
    explicit_correction: bool = False,
) -> ContradictionType: ...


@overload
def classify_contradiction(
    existing: BeliefRecord,
    incoming: str,
    new_predicate: str,
    new_object: str,
    explicit_correction: bool = False,
) -> ContradictionType: ...


def classify_contradiction(
    existing: BeliefRecord,
    incoming: BeliefRecord | str,
    new_predicate: str | None = None,
    new_object: str | None = None,
    explicit_correction: bool = False,
) -> ContradictionType:
    """Classify an assertion against a belief with the same semantic slot.

    A newer, equally confident assertion of a different value is normally a
    slot update. Simultaneous assertions without temporal precedence remain a
    conflict. The legacy scalar arguments remain supported for callers that
    have not yet constructed an incoming record.
    """
    if isinstance(incoming, BeliefRecord):
        new_subject = incoming.subject
        predicate = incoming.predicate
        object_value = incoming.object
        # Recorded time says when the system learned a claim, not when it
        # became true. A later write alone must not turn simultaneous,
        # incompatible evidence into an update.
        has_precedence = (
            incoming.valid_from > existing.valid_from
            and incoming.valid_from != incoming.recorded_at
        )
        can_update = incoming.confidence >= existing.confidence and has_precedence
    else:
        if new_predicate is None or new_object is None:
            raise TypeError("Legacy classification requires predicate and object")
        new_subject = incoming
        predicate = new_predicate
        object_value = new_object
        normalized_object = object_value.lower().strip()
        can_update = existing.valid_until is not None or normalized_object.startswith(
            _TEMPORAL_PROGRESSION_MARKERS
        )

    if existing.subject != new_subject or existing.predicate != predicate:
        raise ValueError(
            "Contradiction classification requires matching subject and predicate"
        )
    if _values_equivalent(existing.object, object_value):
        return "ELABORATION"
    if explicit_correction:
        return "CORRECTION"
    if can_update:
        return "UPDATE"
    return "CONFLICT"


_LEGAL_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "ltd",
    "llc",
    "plc",
}


def _normalized_words(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return words


def _values_equivalent(left: str, right: str) -> bool:
    """Compare values after formatting, legal suffix, and acronym normalization."""
    left_words = _normalized_words(left)
    right_words = _normalized_words(right)
    if left_words == right_words:
        return True
    if len(left_words) > 1 and len(right_words) == 1:
        return "".join(word[0] for word in left_words) == right_words[0]
    if len(right_words) > 1 and len(left_words) == 1:
        return "".join(word[0] for word in right_words) == left_words[0]
    return False
