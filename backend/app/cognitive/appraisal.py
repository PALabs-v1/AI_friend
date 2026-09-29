"""
Appraisal Engine -- OCC/Lazarus/EMA (psychological_layer.md section1).

Computes the 6-variable appraisal vector on every user event.
Uses heuristic computation on the hot path; the ReappraisalEngine
refines weights in the background.

Sources:
  - OCC (Ortony, Clore & Collins, 1988) for emotion categorization
  - Lazarus (1991) for primary/secondary appraisal
  - EMA (Gratch & Marsella, 2004) for computational implementation
"""

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_NORM_SKIP_WORDS = frozenset({"not", "no", "don't", "never", "without", "isn't"})


class AppraisalRecord(BaseModel):
    """Structured valuation of one event against goals and expectation.

    This record is intentionally separate from the legacy ``AppraisalVector``:
    it is a pure event reducer output and does not own factual state, evidence,
    identity, or safety constraints.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    goal_congruence: float = Field(default=0.0, ge=-1.0, le=1.0)
    expectation: float = Field(default=0.0, ge=-1.0, le=1.0)
    agency: float = Field(default=0.0, ge=-1.0, le=1.0)
    controllability: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    affect_delta: Mapping[str, float] = Field(default_factory=dict)

    def model_post_init(self, __context: Any, /) -> None:
        """Freeze the nested affect mapping as well as the record itself."""
        object.__setattr__(
            self, "affect_delta", MappingProxyType(dict(self.affect_delta))
        )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _metadata_number(metadata: dict, key: str, default: float) -> float:
    """Read a finite numeric metadata value without mutating the event."""
    value = metadata.get(key, default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _event_goal_ids(event_metadata: dict) -> set[str]:
    """Collect optional goal identifiers from a permissive event envelope."""
    values: list[object] = []
    for key in ("goal_id", "goal_ids", "goals", "active_goal"):
        value = event_metadata.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            values.append(value)
        elif isinstance(value, (list, tuple, set)):
            values.extend(value)
    return {str(value) for value in values if isinstance(value, (str, int, float))}


def _goal_congruence(event_metadata: dict, active_goals: list[str]) -> float:
    """Resolve explicit congruence first, then infer it from goal overlap."""
    raw_congruence = event_metadata.get("goal_congruence")
    if isinstance(raw_congruence, (int, float)) and not isinstance(
        raw_congruence, bool
    ):
        return _clamp(raw_congruence, -1.0, 1.0)
    if event_metadata.get("goal_congruent") is True:
        return 1.0
    if event_metadata.get("goal_incongruent") is True:
        return -1.0
    if _event_goal_ids(event_metadata) & {str(goal) for goal in active_goals}:
        return 1.0
    return 0.0


def appraise_event(
    event_metadata: dict,
    active_goals: list[str],
    expectation: float = 0.0,
) -> AppraisalRecord:
    """Purely reduce event metadata and active goals into an appraisal record.

    Affects are directional control deltas only. The reducer neither receives
    nor mutates beliefs, evidence, identity, provenance, or safety policy.
    """
    goal_congruence = _goal_congruence(event_metadata, active_goals)
    novelty = _clamp(_metadata_number(event_metadata, "novelty", 0.0), 0.0, 1.0)
    agency = _clamp(_metadata_number(event_metadata, "agency", 0.0), -1.0, 1.0)
    controllability = _clamp(
        _metadata_number(event_metadata, "controllability", 0.5), 0.0, 1.0
    )
    input_expectation = _clamp(
        _metadata_number(event_metadata, "expectation", expectation), -1.0, 1.0
    )
    unexpectedness = _clamp(
        _metadata_number(
            event_metadata, "unexpectedness", (1.0 - input_expectation) / 2.0
        ),
        0.0,
        1.0,
    )
    reduced_expectation = _clamp(
        input_expectation - 0.50 * novelty - 0.25 * unexpectedness, -1.0, 1.0
    )

    affect_delta = {
        "pleasure": _clamp(0.40 * goal_congruence, -1.0, 1.0),
        "arousal": _clamp(0.30 * novelty + 0.30 * unexpectedness, -1.0, 1.0),
        "dominance": _clamp(0.40 * (controllability - 0.50) + 0.10 * agency, -1.0, 1.0),
    }
    event_id = str(event_metadata.get("event_id", event_metadata.get("id", "event")))
    return AppraisalRecord(
        event_id=event_id,
        goal_congruence=goal_congruence,
        expectation=reduced_expectation,
        agency=agency,
        controllability=controllability,
        novelty=novelty,
        affect_delta=affect_delta,
    )


def _word_set(content: str) -> set[str]:
    return set(content.lower().split())


def _compute_novelty_fallback(content: str, recent_contents: list[str]) -> float:
    """Mirrors `compute_novelty` in cognitive-rust/src/lib.rs word-for-word."""
    if not recent_contents:
        return 0.8
    content_words = _word_set(content)
    if not content_words:
        return 0.5
    max_overlap = 0.0
    for recent in recent_contents:
        recent_words = _word_set(recent)
        if not recent_words:
            continue
        intersection = len(content_words & recent_words)
        union = len(content_words) + len(recent_words) - intersection
        if union > 0:
            max_overlap = max(max_overlap, intersection / union)
    return min(max(1.0 - max_overlap, 0.0), 1.0)


def _check_norm_alignment_fallback(content: str, boundaries: list[str]) -> float:
    """Mirrors `check_norm_alignment` in cognitive-rust/src/lib.rs word-for-word."""
    if not boundaries:
        return 1.0
    content_lower = content.lower()
    violations = 0
    for boundary in boundaries:
        for keyword in boundary.lower().split():
            if keyword in _NORM_SKIP_WORDS:
                continue
            if len(keyword) > 3 and keyword in content_lower:
                violations += 1
    return min(max(1.0 - violations * 0.2, 0.0), 1.0)


@dataclass(slots=True)
class AppraisalVector:
    """
    6-variable appraisal vector (section1.3).

    Primary appraisal (Lazarus):
        R  -- Relevance          [0, 1]
        N  -- Novelty            [0, 1]
        G  -- Goal Congruence    [-1, 1]

    Secondary appraisal (Lazarus/OCC/EMA):
        A  -- Agency             [0, 1]
        NA -- Norm Alignment     [0, 1]
        RI -- Relationship Impact [-1, 1]  (our extension)
    """

    relevance: float = 0.5
    novelty: float = 0.3
    goal_congruence: float = 0.0
    agency: float = 0.5
    norm_alignment: float = 1.0
    relationship_impact: float = 0.0
    user_valence: float | None = None
    significant_event: bool = False

    def to_dict(self) -> dict[str, float | bool | None]:
        return asdict(self)


def _compute_appraisal_fallback(
    event_content: str,
    event_type: str,
    emotional_bias: float,
    trust: float,
    recent_contents: list[str],
    identity_boundaries: list[str],
    pitch_f0: float | None,
    energy_rms: float | None,
) -> AppraisalVector:
    """Pure-Python mirror of `cognitive_rust::compute_appraisal`.

    Used only when the compiled extension isn't installed (e.g. not built for
    the host target). Kept in lockstep with cognitive-rust/src/lib.rs by hand;
    `test_appraisal_fallback_matches_rust_extension` in tests/ pins both
    implementations against the same inputs whenever the extension is present.
    """
    relevance = {"USER_MESSAGE": 1.0, "SYSTEM_TICK": 0.1}.get(event_type, 0.5)
    novelty = _compute_novelty_fallback(event_content, recent_contents)
    goal_congruence = min(max(emotional_bias, -1.0), 1.0)
    agency = 0.8 if event_type == "USER_MESSAGE" else 0.3
    norm_alignment = _check_norm_alignment_fallback(event_content, identity_boundaries)
    relationship_impact = emotional_bias * 0.5
    if trust < 0.3:
        relationship_impact *= 0.5

    pitch = pitch_f0 if pitch_f0 is not None else 150.0
    energy = energy_rms if energy_rms is not None else 0.0
    if energy > 0.15 or pitch > 250.0:
        goal_congruence = min(max(goal_congruence - 0.3, -1.0), 1.0)
        relationship_impact = min(max(relationship_impact - 0.2, -1.0), 1.0)

    return AppraisalVector(
        relevance=relevance,
        novelty=novelty,
        goal_congruence=goal_congruence,
        agency=agency,
        norm_alignment=norm_alignment,
        relationship_impact=relationship_impact,
    )


class AppraisalEngine:
    """
    Computes appraisal vectors from cognitive events.

    Uses heuristic computation on the hot path to avoid LLM latency.
    R and N use lightweight text similarity; G, A, NA, RI are derived
    from available state signals (acoustic perception, identity boundaries).
    """

    def __init__(self, identity_core_values: list[str] | None = None):
        self.identity_values = identity_core_values or []
        self._recent_contents: list[str] = []
        self._max_recent = 20

    def appraise(
        self,
        event_content: str,
        event_type: str,
        emotional_bias: float,
        state_snapshot: dict[str, Any],
        identity_boundaries: list[str] | None = None,
        user_voice_properties: dict[str, Any] | None = None,
    ) -> AppraisalVector:
        """
        Heuristic appraisal for the real-time cognitive loop.
        Returns an AppraisalVector without requiring LLM or embedding calls.
        """
        pitch = None
        energy = None
        if user_voice_properties:
            pitch_raw = user_voice_properties.get("pitch_f0")
            energy_raw = user_voice_properties.get("energy_rms")
            try:
                pitch = float(pitch_raw) if pitch_raw is not None else 150.0
            except (ValueError, TypeError):
                pitch = 150.0
            try:
                energy = float(energy_raw) if energy_raw is not None else 0.0
            except (ValueError, TypeError):
                energy = 0.0

            # High energy yells (energy > 0.15) or extremely high pitch (F0 > 250Hz) shifts appraisal
            if energy > 0.15 or pitch > 250.0:
                logger.info(
                    f"[Audio] [Appraisal] High arousal user vocal cues detected (energy={energy:.3f}, pitch={pitch:.1f}Hz). Raising threat level."
                )

        # Delegate to Rust; fall back to the pure-Python mirror if the
        # compiled extension isn't installed (e.g. not built for this host).
        try:
            import cognitive_rust

            rust_vector = cognitive_rust.compute_appraisal(
                event_content,
                event_type,
                emotional_bias,
                state_snapshot.get("trust", 0.5),
                self._recent_contents,
                identity_boundaries or [],
                pitch,
                energy,
            )
            vector = AppraisalVector(
                relevance=rust_vector.relevance,
                novelty=rust_vector.novelty,
                goal_congruence=rust_vector.goal_congruence,
                agency=rust_vector.agency,
                norm_alignment=rust_vector.norm_alignment,
                relationship_impact=rust_vector.relationship_impact,
            )
        except ImportError:
            logger.warning(
                "cognitive_rust extension not installed; using pure-Python "
                "appraisal fallback. Build it with `maturin build --manifest-path "
                "crates/cognitive-rust/Cargo.toml --out target/wheels` for the "
                "native implementation."
            )
            vector = _compute_appraisal_fallback(
                event_content,
                event_type,
                emotional_bias,
                state_snapshot.get("trust", 0.5),
                self._recent_contents,
                identity_boundaries or [],
                pitch,
                energy,
            )

        # Track content for novelty computation
        self._recent_contents.append(event_content[:100])
        if len(self._recent_contents) > self._max_recent:
            self._recent_contents = self._recent_contents[-self._max_recent :]

        logger.debug(
            "[Appraisal] R=%.2f N=%.2f G=%.2f A=%.2f NA=%.2f RI=%.2f",
            vector.relevance,
            vector.novelty,
            vector.goal_congruence,
            vector.agency,
            vector.norm_alignment,
            vector.relationship_impact,
        )
        return vector
