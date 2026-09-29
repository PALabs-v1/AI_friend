"""The one parser for "is this query asking about the past?" (W1, DR-005).

`TemporalMemoryStore.search_beliefs` and `MemoryStore`'s candidate filter
used to parse temporal intent separately, and the filter's list lacked
"what did I": a plain past-tense question then hid every superseded fact,
making history unreachable (W1 critic r2 #2).

The costs are asymmetric. Missing a history question hides history
outright, which DR-005 forbids. A false positive only lets superseded
values appear next to the current one, where the ranker and the typed
status still mark them as past. So past-tense questions count, unless the
query also asks about the present ("now", "currently", "these days").
"""

from __future__ import annotations

import re

# Always historical, whatever else the query says.
_EXPLICIT_PAST = re.compile(
    r"\b(?:used to|back (?:when|then)|previously|formerly|originally|at first|"
    r"in the past|historically|before|earlier|ago|last (?:week|month|year)|"
    r"(?:my|the) (?:old|former|previous) )",
    re.IGNORECASE,
)
# Past-tense questions ("what did I drink?", "where was I working?").
_PAST_QUESTION = re.compile(
    r"\b(?:what|where|who|which|how|when)\b[^?]*?\b(?:did|was|were|had)\b"
    r"|\b(?:did|was|were|had)\s+(?:i|you|we|he|she|they|my)\b",
    re.IGNORECASE,
)
_PRESENT = re.compile(
    r"\b(?:now|currently|these days|nowadays|at the moment|right now|today|still)\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(before|after|during|in)\s+((?:19|20)\d{2})\b", re.IGNORECASE)


def query_year(query: str) -> tuple[str, int] | None:
    """The year window a query names, as ("before"|"after"|"during", year)."""
    match = _YEAR.search(query)
    if match is None:
        return None
    cue = match.group(1).casefold()
    return ("during" if cue == "in" else cue), int(match.group(2))


def historical_intent(query: str) -> bool:
    """Whether retrieval should also reach superseded (past) facts."""
    if _EXPLICIT_PAST.search(query) or query_year(query) is not None:
        return True
    return bool(_PAST_QUESTION.search(query)) and not _PRESENT.search(query)
