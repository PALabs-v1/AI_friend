"""Tests for Phase 05 Package B: foundation model roles, provider
capability negotiation, and the structured vision boundary.

Covers: `ModelRole` taxonomy and request/result contract validation;
`ProviderCapabilityNegotiator` across Scenarios A/B/C and the invariant that
its fallbacks can never bypass identity/safety or mutate authoritative
state; `StructuredVisionPercept` field constraints; the anti-emotion-fact
invariant on `FacialObservable`, at both construction time and via
`validate_vision_invariants`; `VLMCaptionVisionAdapter` and
`SpatialTrackingVisionAdapter` conformance to `VisionAdapterProtocol`;
`PerceptEnvelope` normalization via `to_percept_envelope`; and pure 7-bit
ASCII compliance for every file this package owns.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.cognitive.percept import PerceptEnvelope
from app.cognitive.vision_percept import (
    DetectedObject,
    FacialObservable,
    IdentityEstimate,
    SpatialRelation,
    StructuredVisionPercept,
    contains_emotional_language,
    validate_vision_invariants,
)
from app.llm.model_manifest import ModelCapability
from app.llm.model_roles import (
    ROLE_REQUIREMENTS,
    FallbackStrategy,
    ModelRole,
    ProviderCapabilityNegotiator,
    ProviderScenario,
    RoleExecutionRequest,
    RoleExecutionResult,
    RoleResultRejected,
    classify_scenario,
    ensure_committable,
    validate_execution_result,
)
from app.vision.adapters import (
    SpatialTrackingVisionAdapter,
    VisionAdapterProtocol,
    VLMCaptionVisionAdapter,
    to_percept_envelope,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]

PACKAGE_B_FILES = [
    BACKEND_ROOT / "app/llm/model_roles.py",
    BACKEND_ROOT / "app/cognitive/vision_percept.py",
    BACKEND_ROOT / "app/vision/adapters.py",
    BACKEND_ROOT / "tests/test_model_roles_vision.py",
]


# ---------------------------------------------------------------------------
# ModelRole taxonomy and request/result contracts
# ---------------------------------------------------------------------------


def test_model_role_taxonomy_is_exactly_six_roles():
    assert {member.value for member in ModelRole} == {
        "INTERPRETATION",
        "CANDIDATE_GENERATION",
        "PLANNING",
        "EVALUATION",
        "COMPRESSION",
        "REALIZATION",
    }


def test_every_role_has_a_requirement_entry():
    assert set(ROLE_REQUIREMENTS.keys()) == set(ModelRole)


def test_role_execution_request_requires_role_and_prompt():
    with pytest.raises(ValidationError):
        RoleExecutionRequest()  # type: ignore[call-arg]


def test_role_execution_request_defaults():
    request = RoleExecutionRequest(role=ModelRole.PLANNING, prompt="plan the turn")

    assert request.system_prompt is None
    assert request.schema_definition is None
    assert request.evidence_ids == []
    assert request.allowed_claims == []
    assert request.budget_tokens == 512
    assert request.budget_time_s == 10.0
    assert request.model_tag is None


def test_role_execution_result_defaults_are_fail_closed():
    """`validated` must default to False: an unvalidated result reporting
    itself trustworthy by default is exactly the gap
    CODEX_REVIEW_OF_CLAUDE.md's P1 finding demonstrated (an arbitrary
    RoleExecutionResult(raw_output="unsafe claim") read back validated=True
    with zero checks run)."""
    result = RoleExecutionResult(role=ModelRole.EVALUATION, raw_output="ok")

    assert result.parsed_output is None
    assert result.validated is False
    assert result.fallback_strategy == FallbackStrategy.NATIVE
    assert result.fallback_applied is False
    assert result.validation_errors == []
    assert result.tokens_used == 0
    assert result.latency_ms == 0.0
    assert result.error is None


def test_role_execution_result_can_carry_a_fallback_and_error():
    result = RoleExecutionResult(
        role=ModelRole.COMPRESSION,
        raw_output="",
        validated=False,
        fallback_applied=True,
        error="context window exceeded",
    )

    assert result.fallback_applied is True
    assert result.validated is False
    assert result.error == "context window exceeded"


# ---------------------------------------------------------------------------
# validate_execution_result / ensure_committable: the enforceable gate a
# role result must pass before commitment, regardless of fallback strategy
# ---------------------------------------------------------------------------


def test_ensure_committable_rejects_the_reviewers_exact_probe():
    """CODEX_REVIEW_OF_CLAUDE.md's P1 finding demonstrated
    RoleExecutionResult(raw_output="unsafe claim") reporting validated=True
    by default. It now defaults to False, and this is the hard gate: an
    un-validated result of any shape must never reach commitment."""
    result = RoleExecutionResult(role=ModelRole.PLANNING, raw_output="unsafe claim")

    with pytest.raises(RoleResultRejected):
        ensure_committable(result)


def test_ensure_committable_accepts_a_gate_approved_result():
    request = RoleExecutionRequest(role=ModelRole.PLANNING, prompt="plan")
    result = RoleExecutionResult(role=ModelRole.PLANNING, raw_output="ok")

    approved = validate_execution_result(request, result)

    assert ensure_committable(approved) is approved


def test_validate_execution_result_passes_with_no_constraints_configured():
    request = RoleExecutionRequest(role=ModelRole.REALIZATION, prompt="say hi")
    result = RoleExecutionResult(role=ModelRole.REALIZATION, raw_output="hi there")

    validated = validate_execution_result(request, result)

    assert validated.validated is True
    assert validated.validation_errors == []


def test_validate_execution_result_accepts_claims_within_allowed_set():
    request = RoleExecutionRequest(
        role=ModelRole.EVALUATION, prompt="x", allowed_claims=["fact-1", "fact-2"]
    )
    result = RoleExecutionResult(
        role=ModelRole.EVALUATION, raw_output="ok", parsed_output=["fact-1"]
    )

    validated = validate_execution_result(request, result)

    assert validated.validated is True
    assert validated.validation_errors == []


def test_validate_execution_result_rejects_claim_outside_allowed_set():
    request = RoleExecutionRequest(
        role=ModelRole.EVALUATION, prompt="x", allowed_claims=["fact-1", "fact-2"]
    )
    result = RoleExecutionResult(
        role=ModelRole.EVALUATION, raw_output="ok", parsed_output=["fact-1", "fact-99"]
    )

    validated = validate_execution_result(request, result)

    assert validated.validated is False
    assert any("fact-99" in error for error in validated.validation_errors)
    with pytest.raises(RoleResultRejected):
        ensure_committable(validated)


def test_validate_execution_result_accepts_claims_dict_shape():
    """parsed_output may also be a dict carrying a "claims" list -- the
    other natural shape a structured role output would take."""
    request = RoleExecutionRequest(
        role=ModelRole.EVALUATION, prompt="x", allowed_claims=["fact-1"]
    )
    result = RoleExecutionResult(
        role=ModelRole.EVALUATION,
        raw_output="ok",
        parsed_output={"claims": ["fact-1"], "summary": "..."},
    )

    validated = validate_execution_result(request, result)

    assert validated.validated is True


def test_validate_execution_result_fails_closed_when_claims_configured_but_unparseable():
    """allowed_claims restricts the request, but parsed_output gives
    nothing checkable -- the caller's restriction must not be silently
    exempted just because the result is unstructured."""
    request = RoleExecutionRequest(
        role=ModelRole.EVALUATION, prompt="x", allowed_claims=["fact-1"]
    )
    result = RoleExecutionResult(role=ModelRole.EVALUATION, raw_output="ok")

    validated = validate_execution_result(request, result)

    assert validated.validated is False
    assert validated.validation_errors


def test_validate_execution_result_schema_check_rejects_missing_required_field():
    request = RoleExecutionRequest(
        role=ModelRole.PLANNING,
        prompt="x",
        schema_definition={
            "type": "object",
            "required": ["plan_id", "steps"],
        },
    )
    result = RoleExecutionResult(
        role=ModelRole.PLANNING, raw_output="ok", parsed_output={"plan_id": "p1"}
    )

    validated = validate_execution_result(request, result)

    assert validated.validated is False
    assert any("schema validation failed" in error for error in validated.validation_errors)


def test_validate_execution_result_schema_check_accepts_valid_shape():
    request = RoleExecutionRequest(
        role=ModelRole.PLANNING,
        prompt="x",
        schema_definition={
            "type": "object",
            "required": ["plan_id", "steps"],
            "properties": {"steps": {"type": "array"}},
        },
    )
    result = RoleExecutionResult(
        role=ModelRole.PLANNING,
        raw_output="ok",
        parsed_output={"plan_id": "p1", "steps": ["look", "speak"]},
    )

    validated = validate_execution_result(request, result)

    assert validated.validated is True
    assert validated.validation_errors == []


def test_validate_execution_result_custom_validator_can_reject():
    request = RoleExecutionRequest(role=ModelRole.INTERPRETATION, prompt="x")
    result = RoleExecutionResult(
        role=ModelRole.INTERPRETATION, raw_output="ok", parsed_output={"intent": "unknown"}
    )

    validated = validate_execution_result(
        request, result, custom_validator=lambda parsed: parsed.get("intent") != "unknown"
    )

    assert validated.validated is False
    assert "custom_validator rejected parsed_output" in validated.validation_errors


def test_validate_execution_result_custom_validator_exception_is_recorded_not_raised():
    """A broken custom_validator must not crash the gate it is supposed to
    strengthen -- it should be recorded as a validation failure instead."""
    request = RoleExecutionRequest(role=ModelRole.INTERPRETATION, prompt="x")
    result = RoleExecutionResult(role=ModelRole.INTERPRETATION, raw_output="ok")

    def broken_validator(_parsed):
        raise RuntimeError("boom")

    validated = validate_execution_result(request, result, custom_validator=broken_validator)

    assert validated.validated is False
    assert any("boom" in error for error in validated.validation_errors)


def test_validate_execution_result_is_pure_and_returns_a_new_object():
    request = RoleExecutionRequest(role=ModelRole.PLANNING, prompt="x")
    result = RoleExecutionResult(role=ModelRole.PLANNING, raw_output="ok")

    validated = validate_execution_result(request, result)

    assert result.validated is False  # the original is untouched
    assert validated is not result
    assert validated.validated is True


@pytest.mark.parametrize(
    "strategy", [FallbackStrategy.TEMPLATE_PROCEDURE, FallbackStrategy.ROLE_DEGRADATION]
)
def test_fallback_results_are_rejected_until_they_pass_the_same_gate_as_native(strategy):
    """The fix's core requirement: TEMPLATE_PROCEDURE and ROLE_DEGRADATION
    results are not exempt from validation just because a fallback was
    already chosen by negotiation -- they must pass through
    validate_execution_result exactly like a NATIVE result before
    ensure_committable will accept them."""
    request = RoleExecutionRequest(role=ModelRole.COMPRESSION, prompt="x")
    unvalidated = RoleExecutionResult(
        role=ModelRole.COMPRESSION,
        raw_output="template output",
        fallback_strategy=strategy,
        fallback_applied=True,
    )

    with pytest.raises(RoleResultRejected):
        ensure_committable(unvalidated)

    approved = validate_execution_result(request, unvalidated)
    assert approved.fallback_strategy == strategy
    assert ensure_committable(approved) is approved


def test_fallback_result_with_rejected_claims_still_cannot_commit():
    """A fallback strategy must not grant an exemption from the
    allowed_claims check either -- a degraded role can still assert an
    out-of-scope claim, and the gate must catch it exactly as it would for
    a NATIVE result."""
    request = RoleExecutionRequest(
        role=ModelRole.EVALUATION, prompt="x", allowed_claims=["fact-1"]
    )
    result = RoleExecutionResult(
        role=ModelRole.EVALUATION,
        raw_output="template",
        parsed_output=["fact-1", "unauthorized-claim"],
        fallback_strategy=FallbackStrategy.TEMPLATE_PROCEDURE,
        fallback_applied=True,
    )

    validated = validate_execution_result(request, result)

    assert validated.validated is False
    with pytest.raises(RoleResultRejected):
        ensure_committable(validated)


# ---------------------------------------------------------------------------
# ProviderCapabilityNegotiator: Scenario B (local compact, real manifest)
# ---------------------------------------------------------------------------


def test_scenario_b_local_compact_native_fit():
    negotiator = ProviderCapabilityNegotiator()

    ok, strategy, details = negotiator.negotiate_role(ModelRole.PLANNING, "llama3.2:3b")

    assert ok is True
    assert strategy == FallbackStrategy.NATIVE.value
    assert details["scenario"] == ProviderScenario.SCENARIO_B_LOCAL_COMPACT.value
    assert details["model_tag"] == "llama3.2:3b"


def test_scenario_b_local_compact_context_window_triggers_role_degradation():
    negotiator = ProviderCapabilityNegotiator()

    # qwen2.5:3b's manifest context_window (32768) is below COMPRESSION's
    # floor (65536): this is the real, already-catalogued model the repo
    # runs, not a synthetic capability.
    ok, strategy, details = negotiator.negotiate_role(ModelRole.COMPRESSION, "qwen2.5:3b")

    assert ok is False
    assert strategy == FallbackStrategy.ROLE_DEGRADATION.value
    assert details["reason"] == "insufficient_context_window"
    assert details["available_context_window"] == 32768
    assert details["required_context_window"] == 65536
    assert details["scenario"] == ProviderScenario.SCENARIO_B_LOCAL_COMPACT.value


@pytest.mark.parametrize(
    "model_tag", ["llama3.2:3b", "qwen2.5:3b", "phi4-mini", "llama3.2:1b"]
)
def test_scenario_b_native_fit_for_low_bar_roles(model_tag):
    negotiator = ProviderCapabilityNegotiator()

    ok, strategy, _details = negotiator.negotiate_role(ModelRole.REALIZATION, model_tag)

    assert ok is True
    assert strategy == FallbackStrategy.NATIVE.value


# ---------------------------------------------------------------------------
# ProviderCapabilityNegotiator: Scenario A / C (unregistered tags) and
# classify_scenario labeling
# ---------------------------------------------------------------------------


def test_classify_scenario_labels_registered_tag_as_local_compact():
    assert classify_scenario("llama3.2:3b") == ProviderScenario.SCENARIO_B_LOCAL_COMPACT


def test_classify_scenario_labels_unregistered_frontier_style_tag():
    assert classify_scenario("claude-opus-5") == ProviderScenario.SCENARIO_A_FRONTIER
    assert classify_scenario("gpt-5.1") == ProviderScenario.SCENARIO_A_FRONTIER


def test_classify_scenario_labels_unknown_tag_as_alternative_provider():
    assert (
        classify_scenario("some-vendor-model-v1")
        == ProviderScenario.SCENARIO_C_ALTERNATIVE_PROVIDER
    )


def test_scenario_a_and_c_unregistered_tags_abstain_rather_than_assume_capability():
    """An unregistered tag carries no verified capability data, regardless
    of whether its name looks like a frontier offering (Scenario A) or an
    unfamiliar alternative provider (Scenario C). The negotiator must not
    grant a role based on a name pattern alone -- failing closed here is
    what the "never bypass safety" invariant looks like in practice for an
    unverified provider."""
    negotiator = ProviderCapabilityNegotiator()

    for tag, scenario in [
        ("claude-opus-5", ProviderScenario.SCENARIO_A_FRONTIER),
        ("some-vendor-model-v1", ProviderScenario.SCENARIO_C_ALTERNATIVE_PROVIDER),
    ]:
        ok, strategy, details = negotiator.negotiate_role(ModelRole.PLANNING, tag)

        assert ok is False
        assert strategy == FallbackStrategy.ABSTAIN.value
        assert details["reason"] == "capability_unknown"
        assert details["scenario"] == scenario.value


# ---------------------------------------------------------------------------
# ProviderCapabilityNegotiator: evaluate_capability with synthetic
# capabilities (covers structured-output gaps the seeded manifest has none
# of, and a fully-capable frontier-shaped capability)
# ---------------------------------------------------------------------------


def _capability(**overrides) -> ModelCapability:
    base = {
        "context_window": 200_000,
        "supports_thinking_tokens": True,
        "streaming": True,
        "structured_output": True,
        "language": ["en"],
    }
    base.update(overrides)
    return ModelCapability(**base)


def test_missing_structured_output_triggers_template_procedure():
    negotiator = ProviderCapabilityNegotiator()
    incapable = _capability(structured_output=False)

    ok, strategy, details = negotiator.evaluate_capability(ModelRole.PLANNING, incapable)

    assert ok is False
    assert strategy == FallbackStrategy.TEMPLATE_PROCEDURE.value
    assert details["reason"] == "missing_structured_output"


def test_missing_streaming_triggers_role_degradation():
    negotiator = ProviderCapabilityNegotiator()
    incapable = _capability(streaming=False, structured_output=False)
    # CANDIDATE_GENERATION has no structured_output requirement, so only the
    # streaming gap should be surfaced.

    ok, strategy, details = negotiator.evaluate_capability(
        ModelRole.CANDIDATE_GENERATION, incapable
    )

    assert ok is False
    assert strategy == FallbackStrategy.ROLE_DEGRADATION.value
    assert details["reason"] == "missing_streaming"


@pytest.mark.parametrize("role", list(ModelRole))
def test_fully_capable_synthetic_provider_natively_fits_every_role(role):
    negotiator = ProviderCapabilityNegotiator()
    frontier_like = _capability()

    ok, strategy, _details = negotiator.evaluate_capability(role, frontier_like)

    assert ok is True
    assert strategy == FallbackStrategy.NATIVE.value


def test_none_capability_always_abstains_regardless_of_role():
    negotiator = ProviderCapabilityNegotiator()

    for role in ModelRole:
        ok, strategy, details = negotiator.evaluate_capability(role, None)
        assert ok is False
        assert strategy == FallbackStrategy.ABSTAIN.value
        assert details["reason"] == "capability_unknown"


# ---------------------------------------------------------------------------
# Invariant: fallbacks are a closed, advisory set that cannot bypass
# identity/safety or mutate authoritative state
# ---------------------------------------------------------------------------


def test_negotiator_strategy_is_always_a_closed_fallback_value():
    negotiator = ProviderCapabilityNegotiator()
    allowed = {member.value for member in FallbackStrategy}

    capability_variants = [
        None,
        _capability(),
        _capability(structured_output=False),
        _capability(streaming=False),
        _capability(context_window=1024),
    ]

    for role, capability in itertools.product(ModelRole, capability_variants):
        ok, strategy, _details = negotiator.evaluate_capability(role, capability)
        assert strategy in allowed
        # ok is True if and only if the strategy is NATIVE: a fallback is
        # never silently treated as a pass.
        assert ok == (strategy == FallbackStrategy.NATIVE.value)


def test_negotiator_is_pure_and_does_not_mutate_manifest_capability():
    negotiator = ProviderCapabilityNegotiator()

    first = negotiator.negotiate_role(ModelRole.PLANNING, "llama3.2:3b")
    second = negotiator.negotiate_role(ModelRole.PLANNING, "llama3.2:3b")

    assert first == second


def test_model_roles_module_has_no_authoritative_state_or_identity_coupling():
    """Structural check for the "fallbacks cannot bypass identity
    constraints or mutate authoritative state" invariant: the negotiator
    must not import the very modules that own state mutation and identity
    enforcement, since importing them would be the first step toward
    reaching into them."""
    source = (BACKEND_ROOT / "app/llm/model_roles.py").read_text(encoding="ascii")

    forbidden_symbols = ["StateService", "AgentState", "IdentityManager", "PersonaProfile"]
    for symbol in forbidden_symbols:
        assert symbol not in source, f"model_roles.py must not reference {symbol}"


def test_provider_capability_negotiator_exposes_no_mutating_methods():
    """The negotiator's public surface is read-only: both public methods
    return advisory tuples, neither takes nor stores a reference to agent
    state. A future method named like an action (`apply_*`, `commit_*`,
    `mutate_*`) would be the regression this test is meant to catch."""
    public_methods = [
        name
        for name in dir(ProviderCapabilityNegotiator)
        if not name.startswith("_") and callable(getattr(ProviderCapabilityNegotiator, name))
    ]

    assert set(public_methods) == {"evaluate_capability", "negotiate_role"}


# ---------------------------------------------------------------------------
# StructuredVisionPercept and field constraints
# ---------------------------------------------------------------------------


def test_structured_vision_percept_defaults():
    percept = StructuredVisionPercept()

    assert percept.track_ids == []
    assert percept.identity_estimates == []
    assert percept.objects == []
    assert percept.actions_events == []
    assert percept.gaze_pose is None
    assert percept.facial_observables == []
    assert percept.scene_deltas == []
    assert percept.spatial_relations == []
    assert percept.staleness_ms == 0.0
    assert percept.confidence == 1.0
    assert percept.provenance == "structured_vision"


def test_structured_vision_percept_confidence_bounds():
    with pytest.raises(ValidationError):
        StructuredVisionPercept(confidence=1.5)
    with pytest.raises(ValidationError):
        StructuredVisionPercept(confidence=-0.1)


def test_identity_estimate_and_detected_object_confidence_bounds():
    with pytest.raises(ValidationError):
        IdentityEstimate(person_id="p1", confidence=2.0)
    with pytest.raises(ValidationError):
        DetectedObject(label="mug", confidence=-1.0)


def test_spatial_relation_requires_all_three_fields():
    relation = SpatialRelation(subject="mug", relation="on", object="table")
    assert relation.subject == "mug"
    assert relation.relation == "on"
    assert relation.object == "table"

    with pytest.raises(ValidationError):
        SpatialRelation(subject="mug", relation="on")  # type: ignore[call-arg]


def test_structured_vision_percept_composes_nested_models():
    percept = StructuredVisionPercept(
        track_ids=["t1"],
        identity_estimates=[IdentityEstimate(person_id="p1", confidence=0.8)],
        objects=[DetectedObject(label="mug", confidence=0.9)],
        spatial_relations=[SpatialRelation(subject="mug", relation="on", object="table")],
    )

    assert percept.identity_estimates[0].person_id == "p1"
    assert percept.objects[0].label == "mug"
    assert percept.spatial_relations[0].relation == "on"


# ---------------------------------------------------------------------------
# Anti-emotion-fact invariant on FacialObservable
# ---------------------------------------------------------------------------


def test_facial_observable_accepts_muscle_movement_descriptors():
    observable = FacialObservable(
        action_units=["AU12", "lip_corner_pull"],
        confidence=0.7,
        muscle_movement="brow lowered, lip corners raised",
    )

    assert observable.action_units == ["AU12", "lip_corner_pull"]
    assert observable.muscle_movement == "brow lowered, lip corners raised"


@pytest.mark.parametrize("emotion_word", ["happy", "angry", "sad", "surprised"])
def test_facial_observable_rejects_emotion_label_in_action_units(emotion_word):
    with pytest.raises(ValidationError):
        FacialObservable(action_units=[emotion_word])


def test_facial_observable_rejects_emotion_label_embedded_in_muscle_movement():
    with pytest.raises(ValidationError):
        FacialObservable(muscle_movement="the user looks angry")


def test_validate_vision_invariants_passes_for_clean_percept():
    percept = StructuredVisionPercept(
        facial_observables=[
            FacialObservable(action_units=["AU12"], muscle_movement="lip corner pull")
        ]
    )

    validate_vision_invariants(percept)  # must not raise


def test_validate_vision_invariants_catches_post_construction_mutation():
    """FacialObservable's field validator only runs at construction time;
    appending to the list afterward bypasses it entirely (Pydantic does not
    re-validate in-place list mutation). validate_vision_invariants is the
    defense-in-depth re-check for exactly this case."""
    observable = FacialObservable(action_units=["AU12"], muscle_movement="lip pull")
    percept = StructuredVisionPercept(facial_observables=[observable])

    observable.action_units.append("happy")

    with pytest.raises(ValueError):
        validate_vision_invariants(percept)


# ---------------------------------------------------------------------------
# Anti-emotion-fact invariant: expanded lexicon, predicate phrases, and
# scene_deltas (the gap CODEX_REVIEW_OF_CLAUDE.md's P1 finding demonstrated
# was bypassable through both non-lexicon words and an unguarded field)
# ---------------------------------------------------------------------------

# The reviewer's own probes: "the user appears furious", "the user looks
# ecstatic", and "the user is depressed" were previously accepted by both
# FacialObservable and (because scene_deltas was never checked at all) a
# VLM caption. "she responded angrily" exercises the adverb form the
# original module's own comment admitted it did not catch.
_REVIEWER_PROBE_PHRASES = [
    "the user appears furious",
    "the user looks ecstatic",
    "the user is depressed",
    "she responded angrily",
]


@pytest.mark.parametrize("phrase", _REVIEWER_PROBE_PHRASES)
def test_contains_emotional_language_catches_reviewer_probe_phrases(phrase):
    assert contains_emotional_language(phrase) is True


@pytest.mark.parametrize("phrase", _REVIEWER_PROBE_PHRASES)
def test_facial_observable_rejects_reviewer_probe_phrases_in_muscle_movement(phrase):
    with pytest.raises(ValidationError):
        FacialObservable(muscle_movement=phrase)


def test_emotion_lexicon_does_not_flag_the_common_nonemotional_sense_of_content():
    """"content" was removed from the lexicon specifically because, once
    applied to free-form scene_deltas text, it collides constantly with its
    ordinary sense ("the content of the frame") rather than an emotional
    claim. A regression here would mean benign captions get spuriously
    redacted."""
    assert contains_emotional_language("scene content: a mug on the table") is False


@pytest.mark.parametrize("phrase", _REVIEWER_PROBE_PHRASES)
def test_validate_vision_invariants_rejects_reviewer_probe_phrases_in_scene_deltas(phrase):
    """scene_deltas has no per-item constructor-time validator (it is
    free-form text), so this is the first line of defense for it --
    exactly the gap the review demonstrated with a plain
    scene_deltas=["the user is angry"] percept."""
    percept = StructuredVisionPercept(scene_deltas=[phrase])

    with pytest.raises(ValueError):
        validate_vision_invariants(percept)


def test_validate_vision_invariants_passes_for_benign_scene_deltas():
    percept = StructuredVisionPercept(
        scene_deltas=["a mug is on the table", "the door is open"]
    )

    validate_vision_invariants(percept)  # must not raise


def test_to_percept_envelope_rejects_direct_emotion_claim_in_scene_deltas():
    """The full boundary, end to end: a hand-built percept (bypassing both
    adapters entirely) that asserts an emotion fact in scene_deltas must
    still be caught before it reaches PerceptEnvelope."""
    percept = StructuredVisionPercept(scene_deltas=["the user is angry"])

    with pytest.raises(ValueError):
        to_percept_envelope(percept)


# ---------------------------------------------------------------------------
# VLMCaptionVisionAdapter: caption sanitization against the anti-emotion-
# fact invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caption",
    [
        "the user is angry and pacing around the room",
        "the user appears furious",
        "the user looks ecstatic",
        "the user is depressed",
        "she responded angrily to the phone call",
    ],
)
def test_vlm_caption_adapter_strictly_rejects_emotion_claims_from_captions(caption):
    """The caption is untrusted external VLM output; an emotional claim in
    it must never be promoted into an authoritative scene delta. The
    adapter sanitizes it away rather than raising, so the frame is reported
    as "no observation" instead of crashing vision for that tick."""
    percept = VLMCaptionVisionAdapter().process(caption)

    assert percept.scene_deltas == []
    validate_vision_invariants(percept)  # the backstop must also see nothing wrong


def test_vlm_caption_adapter_sanitizes_emotion_claims_in_dict_payload():
    percept = VLMCaptionVisionAdapter().process(
        {"description": "the user is depressed", "confidence": 0.6}
    )

    assert percept.scene_deltas == []


def test_vlm_caption_adapter_preserves_benign_captions_unchanged():
    """Sanitization must not be so aggressive that it silently discards
    ordinary, non-emotional observations -- only captions that actually
    trip the lexicon should be dropped."""
    percept = VLMCaptionVisionAdapter().process("a person is typing at a desk")

    assert percept.scene_deltas == ["a person is typing at a desk"]


def test_vlm_caption_adapter_output_never_fails_the_invariant_backstop():
    """Belt-and-suspenders check: whatever the sanitizer does or does not
    catch, the percept it returns must always satisfy
    validate_vision_invariants -- process() calls it internally, so a
    caller never sees an adapter output that would fail it."""
    for caption in [*_REVIEWER_PROBE_PHRASES, "a benign caption", ""]:
        percept = VLMCaptionVisionAdapter().process(caption)
        validate_vision_invariants(percept)  # must not raise for any case


# ---------------------------------------------------------------------------
# Adapter conformance
# ---------------------------------------------------------------------------


def test_vlm_caption_adapter_satisfies_protocol():
    assert isinstance(VLMCaptionVisionAdapter(), VisionAdapterProtocol)


def test_spatial_tracking_adapter_satisfies_protocol():
    assert isinstance(SpatialTrackingVisionAdapter(), VisionAdapterProtocol)


def test_vlm_caption_adapter_produces_low_confidence_scene_delta():
    adapter = VLMCaptionVisionAdapter()

    percept = adapter.process("a person is sitting at a desk")

    assert percept.scene_deltas == ["a person is sitting at a desk"]
    assert percept.provenance == "vlm_caption"
    assert 0.0 <= percept.confidence < 0.5
    assert percept.objects == []
    assert percept.facial_observables == []


def test_vlm_caption_adapter_accepts_dict_payload_and_clamps_confidence():
    adapter = VLMCaptionVisionAdapter()

    percept = adapter.process({"description": "a dog runs by", "confidence": 5.0})

    assert percept.scene_deltas == ["a dog runs by"]
    assert percept.confidence == 1.0


def test_vlm_caption_adapter_empty_input_yields_no_scene_delta():
    adapter = VLMCaptionVisionAdapter()

    percept = adapter.process("")

    assert percept.scene_deltas == []


def test_spatial_tracking_adapter_builds_structured_percept():
    adapter = SpatialTrackingVisionAdapter()

    raw = {
        "track_ids": ["t1", "t2"],
        "objects": [{"label": "mug", "confidence": 0.9, "spatial_relation": "on_table"}],
        "identity_estimates": [{"person_id": "p1", "confidence": 0.6}],
        "facial_observables": [
            {"action_units": ["AU12", "AU6"], "confidence": 0.75, "muscle_movement": "cheek raise"}
        ],
        "spatial_relations": [{"subject": "mug", "relation": "on", "object": "table"}],
        "actions_events": ["hand_raised"],
        "gaze_pose": {"yaw": 0.1, "pitch": -0.05},
        "confidence": 0.95,
        "staleness_ms": 12.5,
    }

    percept = adapter.process(raw)

    assert percept.track_ids == ["t1", "t2"]
    assert percept.objects[0].label == "mug"
    assert percept.identity_estimates[0].person_id == "p1"
    assert percept.facial_observables[0].action_units == ["AU12", "AU6"]
    assert percept.spatial_relations[0].object == "table"
    assert percept.actions_events == ["hand_raised"]
    assert percept.gaze_pose == {"yaw": 0.1, "pitch": -0.05}
    assert percept.provenance == "spatial_tracking"
    assert percept.confidence == 0.95
    assert percept.staleness_ms == 12.5


def test_spatial_tracking_adapter_rejects_emotional_label_in_facial_observable():
    adapter = SpatialTrackingVisionAdapter()

    raw = {
        "facial_observables": [{"action_units": ["happy"], "confidence": 0.5}],
    }

    with pytest.raises(ValidationError):
        adapter.process(raw)


def test_spatial_tracking_adapter_handles_empty_payload():
    adapter = SpatialTrackingVisionAdapter()

    percept = adapter.process({})

    assert percept.track_ids == []
    assert percept.objects == []
    assert percept.provenance == "spatial_tracking"


# ---------------------------------------------------------------------------
# SpatialTrackingVisionAdapter: malformed-input coercion (P2 fix). A raw
# tracker payload comes from an upstream process outside cognition's typed
# boundary, so None, wrong types, and out-of-range staleness must degrade
# safely rather than crash process() with a raw TypeError/ValueError.
# ---------------------------------------------------------------------------


def test_spatial_tracking_adapter_handles_none_raw_data():
    percept = SpatialTrackingVisionAdapter().process(None)

    assert percept.track_ids == []
    assert percept.objects == []
    assert percept.provenance == "spatial_tracking"


@pytest.mark.parametrize(
    "list_field", ["track_ids", "objects", "identity_estimates", "facial_observables", "spatial_relations", "actions_events", "scene_deltas"]
)
def test_spatial_tracking_adapter_coerces_none_list_field_to_empty(list_field):
    """A key present with an explicit None value is the sharp edge here:
    dict.get(key, []) does NOT fall back to the default when the key
    exists, so a naive `data.get(key, [])` still receives None."""
    percept = SpatialTrackingVisionAdapter().process({list_field: None})

    assert getattr(percept, list_field) == []


def test_spatial_tracking_adapter_coerces_string_track_ids_to_empty_instead_of_splitting_characters():
    """The exact bug the peer review demonstrated: a string track_ids value
    silently iterated into one bogus "track" per character."""
    percept = SpatialTrackingVisionAdapter().process({"track_ids": "abc"})

    assert percept.track_ids == []


def test_spatial_tracking_adapter_drops_malformed_nested_entries():
    """A non-dict entry inside an otherwise-valid list (a string, a number)
    must not crash the ** unpacking used to build the nested Pydantic
    models -- it is dropped, and well-formed entries are still kept."""
    percept = SpatialTrackingVisionAdapter().process(
        {
            "objects": ["not-a-dict", {"label": "mug", "confidence": 0.9}, 42],
            "identity_estimates": [None, {"person_id": "p1"}],
        }
    )

    assert [obj.label for obj in percept.objects] == ["mug"]
    assert [ident.person_id for ident in percept.identity_estimates] == ["p1"]


@pytest.mark.parametrize(
    "raw_data",
    [
        {"track_ids": "abc", "objects": "nope", "staleness_ms": float("nan")},
        {"track_ids": 12345},
        {"objects": {"label": "mug"}},  # a dict, not a list of dicts
        "just a string, not a dict at all",
        42,
    ],
)
def test_spatial_tracking_adapter_never_raises_on_malformed_raw_data(raw_data):
    percept = SpatialTrackingVisionAdapter().process(raw_data)

    assert isinstance(percept, StructuredVisionPercept)


@pytest.mark.parametrize("bad_staleness", [float("nan"), float("inf"), float("-inf"), -50.0, "not-a-number", None])
def test_spatial_tracking_adapter_defaults_invalid_staleness_to_zero(bad_staleness):
    percept = SpatialTrackingVisionAdapter().process({"staleness_ms": bad_staleness})

    assert percept.staleness_ms == 0.0


def test_spatial_tracking_adapter_preserves_valid_positive_staleness():
    percept = SpatialTrackingVisionAdapter().process({"staleness_ms": 42.5})

    assert percept.staleness_ms == 42.5


@pytest.mark.parametrize("bad_staleness", [float("nan"), -10.0, "garbage"])
def test_vlm_caption_adapter_also_defaults_invalid_staleness_to_zero(bad_staleness):
    """The original peer review flagged this gap in "either adapter" --
    VLMCaptionVisionAdapter shares the same staleness_ms coercion helper."""
    percept = VLMCaptionVisionAdapter().process(
        {"description": "a mug on the table", "staleness_ms": bad_staleness}
    )

    assert percept.staleness_ms == 0.0


# ---------------------------------------------------------------------------
# PerceptEnvelope conversion and normalization
# ---------------------------------------------------------------------------


def test_to_percept_envelope_normalizes_structured_percept():
    percept = StructuredVisionPercept(
        scene_deltas=["a mug appeared"],
        actions_events=["hand_raised"],
        confidence=0.8,
        provenance="spatial_tracking",
    )

    envelope = to_percept_envelope(percept)

    assert isinstance(envelope, PerceptEnvelope)
    assert envelope.modality == "vision"
    assert envelope.source == "spatial_tracking"
    assert envelope.provenance == "spatial_tracking"
    assert envelope.confidence == 0.8
    assert envelope.text_content == "a mug appeared; hand_raised"
    assert envelope.raw_payload["scene_deltas"] == ["a mug appeared"]


def test_to_percept_envelope_empty_text_fields_yield_none_text_content():
    percept = StructuredVisionPercept()

    envelope = to_percept_envelope(percept)

    assert envelope.text_content is None


def test_to_percept_envelope_percept_ids_are_unique_across_calls():
    percept = StructuredVisionPercept(scene_deltas=["same content"])

    first = to_percept_envelope(percept)
    second = to_percept_envelope(percept)

    assert first.percept_id != second.percept_id


def test_to_percept_envelope_raises_on_tampered_percept():
    """The envelope boundary is the last chance to catch a percept that
    slipped past FacialObservable's own construction-time validator via
    post-construction mutation -- to_percept_envelope must not silently
    forward an emotional fact into cognition."""
    observable = FacialObservable(action_units=["AU12"])
    percept = StructuredVisionPercept(facial_observables=[observable])
    observable.action_units.append("angry")

    with pytest.raises(ValueError):
        to_percept_envelope(percept)


def test_to_percept_envelope_confidence_always_within_unit_interval():
    for confidence in (0.0, 0.5, 1.0):
        percept = StructuredVisionPercept(confidence=confidence)
        envelope = to_percept_envelope(percept)
        assert 0.0 <= envelope.confidence <= 1.0


# ---------------------------------------------------------------------------
# Pure 7-bit ASCII compliance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relative_path", PACKAGE_B_FILES)
def test_package_b_files_are_pure_ascii(relative_path):
    raw_bytes = relative_path.read_bytes()

    non_ascii = [byte for byte in raw_bytes if byte > 0x7F]
    assert not non_ascii, f"{relative_path} contains non-ASCII byte(s): {non_ascii[:10]}"

    # Decoding under strict ascii is the authoritative check; the byte scan
    # above exists only to surface which bytes failed when this assertion
    # trips.
    raw_bytes.decode("ascii")


def test_package_b_files_contain_no_smart_punctuation():
    """A narrower, human-readable companion to the byte-level ASCII check:
    catches the specific non-ASCII characters (em/en dashes, curly quotes)
    most likely to be pasted in by an editor's autocorrect."""
    smart_punctuation = re.compile("[\u2013\u2014\u2018\u2019\u201c\u201d]")

    for relative_path in PACKAGE_B_FILES:
        text = relative_path.read_text(encoding="utf-8")
        assert not smart_punctuation.search(text), (
            f"{relative_path} contains smart punctuation (em/en dash or curly quote)"
        )
