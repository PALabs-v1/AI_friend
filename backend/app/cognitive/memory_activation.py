"""Typed retrieval activations and checks against memory-borne instruction injection.

The activation adapter projects storage records into the decision contract while
preserving provenance, validity, contradiction and outage metadata. Retrieved
content remains evidence rather than policy or executable instructions.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field

RecordType = Literal["experience", "belief", "procedure"]
ContradictionState = Literal[
    "NONE",
    "DISPUTED",
    "SUPERSEDED",
    "INVALIDATED",
    "CONFLICT",
    "UPDATE",
    "CORRECTION",
    "ELABORATION",
]


class MemoryActivation(BaseModel):
    """One retrieved memory's contribution to the current cognitive turn.

    outage_flag distinguishes "the store was queried and found nothing"
    (False, zero matches -- a real absence) from "the store could not be
    queried" (True, a retrieval failure). Collapsing the two would make a
    database outage silently look identical to an agent with nothing
    relevant to remember.
    """

    record_id: str
    record_type: RecordType
    structured_value: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    validity: bool = True
    provenance: str = "memory"
    contradiction_state: ContradictionState = "NONE"
    outage_flag: bool = False


# Fix round (Codex review B5): each pattern targets one family of indirect
# prompt injection carried in retrieved memory text -- instruction
# overrides, role/control-delimiter hijacks, and system-prompt exfiltration.
# Case-insensitive, applied to text that has already been Unicode-normalized
# and zero-width-stripped (see _normalize_for_detection), since an attacker
# paraphrases and obfuscates -- the accepted cost is some false positives (a
# memory genuinely narrating "she told me to ignore previous instructions",
# or a memory that happens to start with the word "System" and a colon) in
# exchange for not missing the injection.
#
# The instruction-override and exfiltration patterns allow 1 to 3 modifier
# words before the anchor noun ("instructions"/"context"/"prompt") rather
# than a fixed phrase, so both the original review examples ("ignore all
# previous instructions") and the harder ones Codex demonstrated bypassed
# detection ("ignore the previous instructions", "reveal the system
# prompt") match the same pattern.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"ignore\s+(?:the|all|any|previous|prior|above|earlier)(?:\s+"
        r"(?:the|all|any|previous|prior|above|earlier)){0,2}\s+instructions",
        re.IGNORECASE,
    ),
    re.compile(
        r"disregard\s+(?:the|all|any|previous|prior|above|earlier)(?:\s+"
        r"(?:the|all|any|previous|prior|above|earlier)){0,2}\s+(instructions|context)",
        re.IGNORECASE,
    ),
    re.compile(r"you must now act as", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all)\s+(you|that)", re.IGNORECASE),
    re.compile(r"new\s+system\s+prompt", re.IGNORECASE),
    re.compile(r"pretend\s+(you are|to be)", re.IGNORECASE),
    re.compile(
        r"(?:repeat|print|quote|copy|echo|reproduce).{0,48}"
        r"(?:system|developer|hidden).{0,32}prompt",
        re.IGNORECASE,
    ),
    re.compile(
        r"reveal\s+(?:the|system|all)(?:\s+(?:the|system|all)){0,1}\s+prompt",
        re.IGNORECASE,
    ),
    re.compile(r"<\s*/?\s*system\s*>", re.IGNORECASE),
    re.compile(r"\[\s*system\s*\]", re.IGNORECASE),
    re.compile(r"<\|.*?\|>", re.IGNORECASE),
    re.compile(r"###\s*(system|instruction)", re.IGNORECASE),
    # Chat-template role/control delimiters: a memory string that opens with
    # one of these is attempting to be re-parsed as a role turn, not quoted
    # conversational content.
    re.compile(r"\[\s*/?\s*inst\s*\]", re.IGNORECASE),
    re.compile(r"\b(system|user|assistant)\s*:", re.IGNORECASE),
)

# Fix round (B5): quarantine the whole field rather than the matched span.
# `pattern.sub(marker, text)` used to leave everything the matched phrase
# did not cover intact -- "[filtered] and reveal the secret" still carries
# the imperative payload straight past the redaction. A detected attempt now
# discards the entire untrusted string.
_QUARANTINE_MARKER = "[UNTRUSTED_CONTENT_FILTERED]"
_MAX_UNTRUSTED_TEXT_CHARS = 8192

# Characters with no visible rendering that an attacker can splice into the
# middle of a trigger word (e.g. "ignore previ<ZWSP>ous instructions") to
# defeat a plain substring/regex match while the text still reads and
# displays identically to a human. Written as escape sequences, not literal
# characters, to keep this file pure 7-bit ASCII: zero width space
# (U+200B), zero width non-joiner (U+200C), zero width joiner (U+200D), and
# byte order mark / zero width no-break space (U+FEFF).
_ZERO_WIDTH_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")
_CONFUSABLE_TRANSLATION = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "Α": "A",
        "Β": "B",
        "Ε": "E",
        "Ζ": "Z",
        "Η": "H",
        "Ι": "I",
        "Κ": "K",
        "Μ": "M",
        "Ν": "N",
        "Ο": "O",
        "Ρ": "P",
        "Τ": "T",
        "Υ": "Y",
        "Χ": "X",
        "ο": "o",
        "ι": "i",
        "κ": "k",
        "ν": "v",
        "τ": "t",
        "υ": "y",
        "χ": "x",
        "ε": "e",
        "ρ": "p",
    }
)


def _normalize_for_detection(text: str) -> str:
    """Undo the cheapest text-level obfuscation before pattern matching.

    NFKC folds Unicode compatibility/width variants (for example fullwidth
    Latin letters) into their canonical ASCII-adjacent form, and stripping
    the zero-width characters closes the mid-word splice trick above. This
    is defense against cheap bypasses, not a claim that every Unicode
    obfuscation technique is covered.
    """
    normalized = unicodedata.normalize("NFKC", text)
    for zero_width in _ZERO_WIDTH_CHARS:
        normalized = normalized.replace(zero_width, "")
    return normalized.casefold().translate(_CONFUSABLE_TRANSLATION)


class AntiInjectionGate:
    """Treats retrieved memory content as untrusted external data: every
    string surfaced from a MemoryActivation's structured_value must pass
    through here before it reaches a prompt (Section 39).

    This remains a denylist detector over rendered text, not the allowlisted
    structured-field isolation Codex's review recommended as the stronger
    long-term design (finding B5) -- that is a larger prompt-assembly
    redesign tracked as NOT DONE. What changed in this fix round is that a
    detected attempt now quarantines the entire field rather than leaving a
    partially-redacted string that can still carry an imperative payload.
    """

    def is_injection_attempt(self, text: str) -> bool:
        if not text:
            return False
        return self._matches_normalized(_normalize_for_detection(text))

    @staticmethod
    def _matches_normalized(text: str) -> bool:
        return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)

    def sanitize_memory_text(self, text: str) -> str:
        if not text:
            return text
        if len(text) > _MAX_UNTRUSTED_TEXT_CHARS:
            return _QUARANTINE_MARKER
        if self.is_injection_attempt(text):
            return _QUARANTINE_MARKER
        return text

    def sanitize_memory_batch(self, texts: list[str]) -> list[str]:
        """Quarantine related fields together when an attack is split across them."""
        normalized = [
            None
            if len(text) > _MAX_UNTRUSTED_TEXT_CHARS
            else _normalize_for_detection(text)
            for text in texts
        ]
        rendered = [
            _QUARANTINE_MARKER
            if text is None or self._matches_normalized(text)
            else original
            for original, text in zip(texts, normalized, strict=True)
        ]
        if any(text == _QUARANTINE_MARKER for text in rendered):
            return rendered
        combined = "\n".join(text for text in normalized if text is not None)
        reversed_combined = "\n".join(
            text for text in reversed(normalized) if text is not None
        )
        if self._matches_normalized(combined) or self._matches_normalized(
            reversed_combined
        ):
            return [_QUARANTINE_MARKER for _ in texts]
        return rendered


def wrap_retrieved_text(text: str) -> str:
    """Wrap untrusted text while canonicalizing attempts to forge our markers."""
    text = unicodedata.normalize("NFKC", text)
    for zero_width in _ZERO_WIDTH_CHARS:
        text = text.replace(zero_width, "")
    text = re.sub(
        r"\[\s*/?\s*retrieved-content\s*\]",
        lambda match: match.group(0).lower().replace(" ", ""),
        text,
        flags=re.IGNORECASE,
    )
    return f"[RETRIEVED-CONTENT]{text}[/RETRIEVED-CONTENT]"


_VALID_CONTRADICTION_STATES: frozenset[str] = frozenset(ContradictionState.__args__)


def _nested_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    """`memory["metadata"]` when it is itself a dict, else `{}`.

    `SurfacingAgent` (`agents/surfacing_agent.py`) places source truth
    fields under each surfaced memory's own `metadata` dict rather than at
    the top level; `CognitiveService._on_memory_surfaced` now preserves
    that nested dict alongside the flattened top-level copies (fix round
    P7-FIX-03/P7-FIX-06). A degraded or non-dict `metadata` value must never
    raise here -- it simply contributes nothing, the same as it being
    absent.
    """
    metadata = memory.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _extract_contradiction_state(memory: dict[str, Any]) -> ContradictionState:
    """A memory dict's own `contradiction_state` wins outright when present
    and recognized, checked at the top level first and then inside a
    nested `metadata` dict. Failing that, a linked `BeliefRecord`
    (memory_records.py -- either the object itself under `belief_record`/
    `belief`, at either level, or an already-dict-shaped copy) contributes
    its `status` (ACTIVE/SUPERSEDED/INVALIDATED/DISPUTED) or, for a
    temporal-store contradiction decision, its `contradiction_type`
    (CONFLICT/UPDATE/CORRECTION/ELABORATION). Anything unrecognized falls
    back to "NONE" -- this adapter must never invent a dispute the source
    data did not actually assert.
    """
    metadata = _nested_metadata(memory)

    for source in (memory, metadata):
        explicit = source.get("contradiction_state")
        if isinstance(explicit, str) and explicit in _VALID_CONTRADICTION_STATES:
            return explicit  # type: ignore[return-value]

    belief = (
        memory.get("belief_record")
        or memory.get("belief")
        or metadata.get("belief_record")
        or metadata.get("belief")
    )
    if belief is not None:
        contradiction_type = getattr(belief, "contradiction_type", None)
        if contradiction_type is None and isinstance(belief, dict):
            contradiction_type = belief.get("contradiction_type")
        if (
            isinstance(contradiction_type, str)
            and contradiction_type in _VALID_CONTRADICTION_STATES
        ):
            return contradiction_type  # type: ignore[return-value]

        status = getattr(belief, "status", None)
        if status is None and isinstance(belief, dict):
            status = belief.get("status")
        if isinstance(status, str) and status in _VALID_CONTRADICTION_STATES:
            return status  # type: ignore[return-value]
        if status == "ACTIVE":
            return "NONE"

    return "NONE"


def _extract_outage_flag(memory: dict[str, Any]) -> bool:
    """True when the memory dict itself, or its nested `metadata`, was
    stamped with a retrieval failure -- an explicit `outage_flag`, or an
    `error`/`retrieval_error` key a degraded surfacing path attaches
    (mirroring `MemoryStore.last_search_error` on the retrieval side)."""
    metadata = _nested_metadata(memory)
    for source in (memory, metadata):
        if source.get("outage_flag"):
            return True
        if source.get("error") or source.get("retrieval_error"):
            return True
    return False


_RETRIEVAL_OUTAGE_RECORD_ID = "memory-retrieval-outage"


def _retrieval_outage_activation(last_search_error: str) -> MemoryActivation:
    """A synthetic, content-free activation representing a whole-retrieval
    failure that produced zero surfaced memories.

    `MemoryStore.search_memories` records a failure in `last_search_error`
    and still returns `[]` on an outage (never raising) -- indistinguishable,
    to a caller that only looks at the surfaced-memories list, from "nothing
    relevant was found" (see `MemoryActivation.outage_flag`'s own docstring
    on exactly this collapse). When a caller supplies that error string here
    and the surfaced list is empty, this stands in for the outage so
    `retrieval_degraded` still gets set downstream instead of the failure
    disappearing into an empty list.
    """
    return MemoryActivation(
        record_id=_RETRIEVAL_OUTAGE_RECORD_ID,
        record_type="experience",
        structured_value={"error": str(last_search_error)},
        relevance_score=0.0,
        provenance="memory_store_outage",
        contradiction_state="NONE",
        outage_flag=True,
    )


def memories_to_activations(
    surfaced_memories: list[dict[str, Any]] | None,
    *,
    last_search_error: str | None = None,
) -> list[MemoryActivation]:
    """Adapt legacy surfaced-memory dicts into typed MemoryActivation tokens.

    `CognitiveService.surfaced_memories` (core.py) and the proactive-recall
    fallback in action.py both produce plain dicts shaped
    `{"content": str, "source": ..., "timestamp": ..., "relevance": float}`
    -- the format retrieval has always used, predating Phase 02. This is the
    bridge Codex review finding B1 asked for: without it, a production turn
    with `Config.MEMORY_TRUTH_ENABLED=True` had no path from the real
    memory-surfacing pipeline into `DecisionService.decide`'s
    `memory_activations` parameter, so the ASK/outage branches were only
    reachable from a hand-built test argument.

    Every adapted token gets `record_type="experience"` (the closest fit
    for an untyped retrieved snippet). `contradiction_state` and
    `outage_flag` are no longer hardcoded (finding: a legacy dict carrying
    real contradiction/outage information from a degraded retrieval path,
    at the top level or nested under `metadata`, was silently discarded
    here, blinding the agent to both) -- see `_extract_contradiction_state`
    and `_extract_outage_flag`. A plain legacy dict with neither key still
    resolves to "NONE"/False exactly as before.

    `last_search_error` (fix round P7-FIX-06, optional, keyword-only):
    mirrors `MemoryStore.last_search_error`, which `search_memories` sets
    on a genuine retrieval failure while still returning `[]` (never
    raising) -- indistinguishable, to a caller that only sees the surfaced-
    memories list, from "nothing relevant was found" (the exact collapse
    `MemoryActivation.outage_flag`'s own docstring warns against). When
    supplied and truthy, and no adapted activation already carries
    `outage_flag=True`, one synthetic content-free activation
    (`_retrieval_outage_activation`) is appended so a whole-retrieval
    failure -- including the common case where it produced an empty
    `surfaced_memories` list -- still reaches `pipeline.py`'s
    `retrieval_degraded` computation (`any(activation.outage_flag for
    activation in activations)`) instead of disappearing into an empty
    list. Not yet wired to a live caller: `pipeline.py`'s
    `_setup_memory_activations` still calls this positionally with only
    `surfaced_memories` -- passing `memory_store.last_search_error` through
    there is documented follow-up
    (`orchestration/PHASE_07/CLAUDE_RESULT.md`).
    """
    activations: list[MemoryActivation] = []
    for index, memory in enumerate(surfaced_memories or []):
        if not isinstance(memory, dict):
            continue
        content = memory.get("content")
        if not content:
            # A content-free dict is noise unless it is an outage marker: a
            # degraded surfacing path reports the failure as a dict carrying
            # `outage_flag`/`error` and nothing to say. Dropping it here would
            # hide the outage from `retrieval_degraded` downstream -- the
            # exact collapse `MemoryActivation.outage_flag` exists to prevent.
            if not _extract_outage_flag(memory):
                continue
            content = str(
                memory.get("error")
                or _nested_metadata(memory).get("error")
                or "retrieval_outage"
            )
        relevance = memory.get("relevance", memory.get("score", 1.0))
        if isinstance(relevance, (int, float)) and not isinstance(relevance, bool):
            relevance_score = max(0.0, min(1.0, float(relevance)))
        else:
            relevance_score = 1.0
        record_id = str(
            memory.get("id") or memory.get("record_id") or f"legacy-{index}"
        )
        activations.append(
            MemoryActivation(
                record_id=record_id,
                record_type="experience",
                structured_value={
                    "content": str(content),
                    "source": memory.get("source"),
                    "timestamp": memory.get("timestamp"),
                },
                relevance_score=relevance_score,
                provenance=str(memory.get("source") or "memory"),
                contradiction_state=_extract_contradiction_state(memory),
                outage_flag=_extract_outage_flag(memory),
            )
        )
    if last_search_error and not any(a.outage_flag for a in activations):
        activations.append(_retrieval_outage_activation(last_search_error))
    return activations
