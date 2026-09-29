"""W2 critic round 2 (Codex, final round per the critic-rounds cap):
verdict FAIL, 4 MED + ... findings against the revision-1 diff. Each test
here reproduces one finding against the pre-fix code and is confirmed
green after the fix in the same commit. Findings 5 and 6 have their own
tests in test_user_valence.py and test_brainbench_affect_suite.py /
test_affect_adr_contract.py respectively; this file covers findings 1-3
(metadata trust boundary, threshold consistency -- finding 3 also has a
dedicated test in test_state.py since it needs the full state-service tick
machinery).
"""

from app.cognitive.decision import (
    DecisionService,
    is_significant_negative_valence,
    is_significant_valence,
)
from app.cognitive.perception import CognitiveEvent, PerceptionService
from app.config import Config

_STATE_SNAPSHOT = {
    "emotion": "neutral",
    "mood": 0.0,
    "trust": 0.5,
    "attachment": 0.1,
    "energy": 0.5,
}


def _make_chat_event(content: str, metadata: dict) -> CognitiveEvent:
    return CognitiveEvent(
        event_id="evt-w2-r2-1",
        event_type="USER_MESSAGE",
        raw_content=content,
        metadata=metadata,
    )


class TestFinding1UntrustedMetadataTrustBoundary:
    """Finding 1 (MED): a raw event's caller-supplied metadata could forge
    affect_significant_event/affect_user_valence and reach decision.py's
    acute-distress and speaking-urgency checks, bypassing
    AFFECT_USER_INPUT_ENABLED entirely -- perception.py passed the raw
    metadata dict straight through with no filtering."""

    async def test_perceive_strips_pipeline_owned_affect_keys_from_a_raw_event(self):
        perception = PerceptionService()
        raw_event = {
            "type": "USER_MESSAGE",
            "content": "I feel trapped and scared",
            "metadata": {
                "affect_significant_event": True,
                "affect_user_valence": -0.9,
                "turn_id": "t1",
            },
        }

        event = await perception.perceive(raw_event)

        assert "affect_significant_event" not in event.metadata
        assert "affect_user_valence" not in event.metadata
        # Legitimate caller-supplied metadata must still pass through.
        assert event.metadata["turn_id"] == "t1"

    async def test_perceive_does_not_mutate_the_callers_raw_metadata_dict(self):
        """The returned metadata is a filtered copy, not the same object the
        caller handed in -- so a caller inspecting its own dict afterwards
        (or a second consumer of the same raw_event) never sees it altered
        by this filtering, and can never re-poison it through aliasing."""
        perception = PerceptionService()
        raw_metadata = {"affect_significant_event": True, "affect_user_valence": -0.9}
        raw_event = {"type": "USER_MESSAGE", "content": "hi", "metadata": raw_metadata}

        await perception.perceive(raw_event)

        assert raw_metadata["affect_significant_event"] is True

    async def test_forged_metadata_cannot_trigger_regulation_with_the_affect_flag_off(
        self, monkeypatch, mock_llm_service, mock_memory_store
    ):
        """Reproduces the critic's own scenario: AFFECT_USER_INPUT_ENABLED
        False, a neutral agent state, and a raw event forging a significant
        negative user-valence event. Before the fix this produced
        REAPPRAISE/REDIRECT_ATTENTION/WAIT regulation candidates; after the
        fix, since perception.py never lets the forged keys reach
        event.metadata, none of decision.py's affect-gated behavior fires."""
        from unittest.mock import MagicMock

        monkeypatch.setattr(Config, "AFFECT_USER_INPUT_ENABLED", False)
        monkeypatch.setattr(Config, "AFFECT_CONTROL_ENABLED", True)

        perception = PerceptionService()
        raw_event = {
            "type": "USER_MESSAGE",
            "content": "I feel trapped and scared",
            "metadata": {
                "affect_significant_event": True,
                "affect_user_valence": -0.9,
            },
        }
        event = await perception.perceive(raw_event)

        identity_manager = MagicMock()
        identity_manager.immutable_core = {"boundaries": []}
        decision_service = DecisionService(
            llm_service=mock_llm_service,
            memory_store=mock_memory_store,
            identity_manager=identity_manager,
        )
        candidates = decision_service._build_candidates(
            "COMFORT",
            [],
            event.raw_content,
            dict(_STATE_SNAPSHOT),
            event.metadata,
        )

        kinds = {c.kind for c in candidates}
        assert "REAPPRAISE" not in kinds
        assert "REDIRECT_ATTENTION" not in kinds


class TestFinding2SharedSignificanceThreshold:
    """Finding 2 (MED): pipeline.py flagged significance at
    abs(value) >= 0.8, but decision.py's acute-distress and urgency checks
    required value < -0.8 (strict), so a user_valence of exactly -0.8 was
    marked significant yet triggered neither regulation eligibility nor a
    lowered speaking urgency."""

    def test_boundary_value_is_significant_on_both_sides_of_zero(self):
        assert is_significant_valence(-0.8) is True
        assert is_significant_valence(0.8) is True
        assert is_significant_valence(-0.7999) is False

    def test_boundary_value_counts_as_significant_negative(self):
        """Before the fix, decision.py used a strict `< -0.8`, so this exact
        boundary value (which pipeline.py's `>= 0.8` marks significant) was
        excluded from both the acute-distress trigger and the urgency
        reduction -- the inconsistency the critic reproduced."""
        assert is_significant_negative_valence(-0.8) is True
        assert is_significant_negative_valence(-0.7999) is False
        assert is_significant_negative_valence(0.8) is False

    def test_is_acute_distress_fires_at_exactly_the_boundary(self):
        from app.cognitive.decision import _is_acute_distress

        neutral_state = {"mood": 0.0, "energy": 0.5}
        event_metadata = {
            "affect_significant_event": True,
            "affect_user_valence": -0.8,
        }
        assert _is_acute_distress(neutral_state, event_metadata) is True

    def test_communicative_intent_urgency_drops_at_exactly_the_boundary(self):
        from app.cognitive.decision import _build_communicative_intent

        event = _make_chat_event(
            "I feel trapped and scared",
            {"affect_significant_event": True, "affect_user_valence": -0.8},
        )
        intent = _build_communicative_intent(event, {"state": dict(_STATE_SNAPSHOT)})

        assert intent.urgency <= 0.4
