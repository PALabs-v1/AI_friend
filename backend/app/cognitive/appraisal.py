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

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..llm.model_roles import ModelRole
from .json_extract import extract_json_blocks

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

    def to_dict(self) -> dict[str, float]:
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

    def _parse_semantic_drift_json(self, json_str: str, response: str) -> dict | None:
        """Parse the appraisal JSON block, falling back through increasingly
        lenient strategies for a model that didn't emit valid JSON."""
        data = None
        try:
            data = json.loads(json_str)
        except Exception:
            try:
                import ast

                data = ast.literal_eval(json_str)
            except Exception:
                cleaned = json_str
                cleaned = re.sub(r"'\s*([a-zA-Z_0-9]+)\s*'\s*:", r'"\1":', cleaned)
                cleaned = re.sub(r":\s*'\s*([^']*)\s*'", r': "\1"', cleaned)
                cleaned = re.sub(r",\s*}", "}", cleaned)
                try:
                    data = json.loads(cleaned)
                except Exception as e2:
                    logger.error(
                        f"[System 2 Appraisal] Failed to sanitize and parse LLM response: {json_str}. Error: {e2}"
                    )

        # Sequential/float extraction fallback if dict structure was not successfully parsed
        if not data:
            try:
                blocks = re.findall(r"\{([^\}]+)\}", response) + re.findall(
                    r"\[([^\]]+)\]", response
                )
                for block in blocks:
                    floats = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", block)]
                    if len(floats) == 3:
                        data = {
                            "goal_congruence": floats[0],
                            "norm_alignment": floats[1],
                            "expectedness": floats[2],
                        }
                        logger.info(
                            f"[System 2 Appraisal] Extracted 3-float tuple from block: {data}"
                        )
                        break

                if not data:
                    all_floats = [
                        float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", response)
                    ]
                    if len(all_floats) >= 3:
                        floats_to_use = (
                            all_floats[-3:] if len(all_floats) > 3 else all_floats
                        )
                        data = {
                            "goal_congruence": floats_to_use[0],
                            "norm_alignment": floats_to_use[1],
                            "expectedness": floats_to_use[2],
                        }
                        logger.info(
                            f"[System 2 Appraisal] Extracted sequential floats from response: {data}"
                        )
            except Exception as seq_err:
                logger.error(
                    f"[System 2 Appraisal] Sequence extraction fallback failed: {seq_err}"
                )
        return data

    def _drift_pad_toward(
        self, data: dict, current_pad: dict[str, float]
    ) -> dict[str, float]:
        """Blend current PAD toward the target implied by one appraisal reading."""
        gc = float(data.get("goal_congruence", 0.0))
        na = float(data.get("norm_alignment", 1.0))
        exp = float(data.get("expectedness", 0.5))

        target_p = max(-1.0, min(1.0, gc))
        target_a = max(-1.0, min(1.0, -exp))
        target_d = max(-1.0, min(1.0, na))

        drift_factor = 0.2
        val = current_pad.get("valence", 0.0)
        aro = current_pad.get("arousal", 0.5)
        dom = current_pad.get("dominance", 0.5)

        new_p = val + drift_factor * (target_p - val)
        new_a = aro + drift_factor * (target_a - aro)
        new_d = dom + drift_factor * (target_d - dom)

        return {
            "valence": max(-1.0, min(1.0, new_p)),
            "arousal": max(0.0, min(1.0, new_a)),
            "dominance": max(0.0, min(1.0, new_d)),
        }

    async def appraise_semantic_drift(
        self, user_utterance: str, llm_client, current_pad: dict[str, float]
    ) -> dict[str, float]:
        """
        System 2 deliberative appraisal using LLM.
        Drifts target mood via primary/secondary appraisal analysis.
        """
        prompt = f"""
        Analyze the user's statement for deep appraisal dimensions:
        User statement: "{user_utterance}"

        Evaluate on a scale of -1.0 to 1.0:
        1. goal_congruence (Is this statement matching the friendly, helpful social goals? Positive if friendly, negative if hostile)
        2. norm_alignment (Does this align with social norms? 1.0 if perfectly polite, lower if offensive/toxic)
        3. expectedness (How expected is this statement? 1.0 if very standard, -1.0 if surprising)

        Output JSON ONLY:
        {{
          "goal_congruence": 0.0,
          "norm_alignment": 1.0,
          "expectedness": 0.5
        }}
        """.strip()

        try:
            from ..config import Config

            response = await llm_client.generate(
                prompt,
                model=Config.LLM_FAST_MODEL,
                options_override={"num_predict": 128},
                role=ModelRole.INTERPRETATION,
            )

            # Extract only the block that looks like our appraisal JSON.
            # Bracket-depth matched rather than regex-matched: a naive
            # `\{.*?\}` truncates at the first inner `}` of a nested object
            # (invalid JSON), and a naive `\{.*\}` spans to the LAST `}` in
            # the whole response, swallowing any second block or trailing
            # commentary (H1).
            candidate_blocks = extract_json_blocks(response, brackets="{")
            json_str = None
            for candidate in candidate_blocks:
                if "goal_congruence" in candidate or "norm_alignment" in candidate:
                    json_str = candidate
                    break

            if not json_str and candidate_blocks:
                json_str = candidate_blocks[0]

            if json_str:
                data = self._parse_semantic_drift_json(json_str, response)
                if data and isinstance(data, dict):
                    return self._drift_pad_toward(data, current_pad)
        except Exception as e:
            logger.error(f"[System 2 Appraisal] Semantic drift evaluation failed: {e}")
        return current_pad
