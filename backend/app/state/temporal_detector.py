"""Small deterministic relation detector for same-slot temporal claims."""

from __future__ import annotations

import math
import re

from .memory_records import BeliefRecord, ContradictionType, _values_equivalent

_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|quit|stopped|ceased)\b", re.IGNORECASE
)
_CORRECTION_RE = re.compile(
    r"\b(?:i meant|i mean|correction|rather than|sorry)\b", re.IGNORECASE
)
_TEMPORAL_RE = re.compile(
    r"\b(?:moved|switched|changed|no longer|anymore)\b",
    re.IGNORECASE,
)
# The fact held, or may still hold: the deterministic arm abstains and the
# top-three classifier decides (W1 critic r2 #1).
_CONTINUITY_RE = re.compile(
    r"\b(?:still|stayed|remains?|remained|never (?:left|moved|switched|changed)"
    r"|(?:not|never|\w+n't)\s+(?:\w+\s+)?(?:move[ds]?|left|leave|switch(?:ed)?"
    r"|change[ds]?|quit))\b",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(
    r"\b(?:maybe|might|perhaps|probably|possibly|i think|i guess|not (?:sure|certain)"
    r"|unsure|unclear)\b",
    re.IGNORECASE,
)
_CLAUSE_RE = re.compile(r"[.;!?]|,|\bbut\b|\band\b", re.IGNORECASE)


def _change_cue_for(value: str, context: str) -> bool:
    """A change word counts only in the clause that names the new value.

    "I moved house last month, I work at Globex" moved a house, not the
    job; the whole-context search closed the job on it. An extractor's
    one-clause reason ("switched drinks recently") is about this fact, so
    its cue counts without restating the value.
    """
    if _TEMPORAL_RE.search(value):
        return True
    clauses = [c for c in _CLAUSE_RE.split(context) if c and c.strip()]
    if len(clauses) == 1:
        return bool(_TEMPORAL_RE.search(clauses[0]))
    needle = value.casefold().strip()
    return bool(needle) and any(
        needle in clause.casefold() and _TEMPORAL_RE.search(clause)
        for clause in clauses
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Compute finite cosine similarity for precomputed embedding vectors."""
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def classify_e8_temporal_relation(
    existing: BeliefRecord,
    incoming: BeliefRecord,
    *,
    similarity: float,
    context: str = "",
    threshold: float = 0.72,
) -> ContradictionType | None:
    """Return a safe relation or ``None`` when top-three LLM review is needed.

    High semantic similarity only opens a candidate pair. A relation is
    decided only when negation/polarity or explicit temporal language supports
    it; otherwise this detector deliberately abstains.
    """
    if existing.subject != incoming.subject or existing.predicate != incoming.predicate:
        raise ValueError("E8 comparison requires matching semantic slots")
    if _values_equivalent(existing.object, incoming.object):
        return None
    if similarity < threshold:
        return None

    lowered = context.casefold()
    if _CORRECTION_RE.search(lowered):
        return "CORRECTION"
    evidence = f"{incoming.object} {context}"
    if _HEDGE_RE.search(evidence) or _CONTINUITY_RE.search(evidence):
        return None
    old_negative = bool(_NEGATION_RE.search(existing.object))
    new_negative = bool(_NEGATION_RE.search(incoming.object))
    temporal_change = _change_cue_for(incoming.object, context)
    if temporal_change and incoming.confidence >= existing.confidence:
        return "UPDATE"
    if old_negative != new_negative and incoming.valid_from > existing.valid_from:
        return "UPDATE"
    return None
