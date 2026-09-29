from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cognitive.appraisal import AppraisalVector
from app.cognitive.decision import ActionPlan
from app.cognitive.pipeline import CognitivePipeline


@pytest.fixture
def mock_components():
    state = MagicMock()
    state.update_from_appraisal = AsyncMock()
    state.update_theory_of_mind = AsyncMock()
    state.get_context_snapshot = MagicMock()
    state.get_behavioral_directive = MagicMock()

    decision = MagicMock()
    decision.decide = AsyncMock()
    decision.is_speculative_stop_confirmed = MagicMock()

    action = MagicMock()
    # execute is an async generator, so side_effect should be a function returning one.

    identity = MagicMock()
    identity.validate_response = AsyncMock()

    return {
        "perception": AsyncMock(),
        "appraisal": MagicMock(),
        "state": state,
        "decision": decision,
        "action": action,
        "learning": AsyncMock(),
        "identity": identity,
    }


@pytest.fixture
def pipeline(mock_components):
    return CognitivePipeline(**mock_components)


@pytest.mark.asyncio
async def test_pipeline_execution_flow(pipeline, mock_components):
    mock_components["state"].last_speculative_intent = None
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="hello",
        intent="CHAT",
        event_id="evt-1",
        metadata={},
    )

    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )

    mock_components["state"].get_context_snapshot.return_value = {"mood": 0.0}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"

    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT", goal="GREET", payload={"message": "hi"}
    )

    async def mock_execute(plan):
        yield {"type": "content", "data": "Hi there!"}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"

    # Execute
    results = []
    async for chunk in pipeline.execute({"type": "USER_MESSAGE", "content": "hello"}):
        results.append(chunk)

    print(f"DEBUG RESULTS: {results}")
    assert results, "Pipeline yielded no results"
    # Verify
    assert any(r["type"] == "content" and r["data"] == "Hi there!" for r in results)
    assert any(r["type"] == "reflection_needed" for r in results)

    mock_components["perception"].perceive.assert_awaited_once()
    mock_components["appraisal"].appraise.assert_called_once()
    mock_components["state"].update_from_appraisal.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_carries_visual_context_into_the_plan_payload(
    pipeline, mock_components
):
    """P1-9/vision-grounding: `raw_event["metadata"]["visuals"]` (written by
    brain_agent from vision.frames/vision.description) has to survive the
    perception -> decision -> action-prep handoff and land in
    plan.payload["visual_context"], or action.py's `_build_visual_context`
    has nothing to render regardless of how correctly it's written."""
    mock_components["state"].last_speculative_intent = None
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="what am I holding?",
        intent="CHAT",
        event_id="evt-2",
        metadata={"visuals": "I am seeing the user's camera."},
    )
    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )
    mock_components["state"].get_context_snapshot.return_value = {"mood": 0.0}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"
    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT", goal="ANSWER", payload={"message": "hi"}
    )
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"

    seen_payloads = []

    async def mock_execute(plan):
        seen_payloads.append(plan.payload)
        yield {"type": "content", "data": "You're holding a mug."}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute

    async for _ in pipeline.execute(
        {"type": "USER_MESSAGE", "content": "what am I holding?"}
    ):
        pass

    assert seen_payloads, "action.execute was never called"
    assert seen_payloads[0]["visual_context"] == "I am seeing the user's camera."


@pytest.mark.asyncio
async def test_pipeline_carries_visual_evidence_into_the_plan_payload(
    pipeline, mock_components
):
    """1A's typed sibling of visual_context must survive the same
    perception -> decision -> action-prep handoff, or _build_visual_context
    has no evidence to render epistemic framing from."""
    from app.cognitive.evidence import Evidence

    evidence = Evidence(content="a mug", source="camera", modality="vision")

    mock_components["state"].last_speculative_intent = None
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="what am I holding?",
        intent="CHAT",
        event_id="evt-3",
        metadata={
            "visuals": "I am seeing the user's camera.",
            "visual_evidence": evidence,
        },
    )
    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )
    mock_components["state"].get_context_snapshot.return_value = {"mood": 0.0}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"
    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT", goal="ANSWER", payload={"message": "hi"}
    )
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"

    seen_payloads = []

    async def mock_execute(plan):
        seen_payloads.append(plan.payload)
        yield {"type": "content", "data": "You're holding a mug."}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute

    async for _ in pipeline.execute(
        {"type": "USER_MESSAGE", "content": "what am I holding?"}
    ):
        pass

    assert seen_payloads, "action.execute was never called"
    assert seen_payloads[0]["visual_evidence"] is evidence


@pytest.mark.asyncio
async def test_stage_7_calls_persona_policy_precheck_exactly_once(
    pipeline, mock_components
):
    """1B: PersonaPolicy.precheck must run exactly once per turn on a plan
    carrying a behavior_decision -- a future duplicate call (e.g. one added
    alongside a new stage) would silently double-apply the stance clamp."""
    from unittest.mock import patch

    from app.cognitive.behavior_contracts import BehaviorDecision, CommunicativeIntent
    from app.persona.policy import PersonaPolicy

    mock_components["state"].last_speculative_intent = None
    mock_components["identity"].immutable_core = {"boundaries": []}
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="hello",
        intent="CHAT",
        event_id="evt-precheck",
        metadata={},
    )
    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )
    mock_components["state"].get_context_snapshot.return_value = {"mood": 0.0}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"
    behavior_decision = BehaviorDecision(
        intent=CommunicativeIntent(act="CHAT", goal="ENGAGE")
    )
    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT",
        goal="ENGAGE",
        payload={"message": "hi"},
        behavior_decision=behavior_decision,
    )
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"

    async def mock_execute(plan):
        yield {"type": "content", "data": "hi"}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute

    with patch(
        "app.cognitive.pipeline.PersonaPolicy.precheck",
        wraps=PersonaPolicy.precheck,
    ) as spy:
        async for _ in pipeline.execute({"type": "USER_MESSAGE", "content": "hello"}):
            pass

    spy.assert_called_once()


@pytest.mark.asyncio
async def test_stage_7_skips_precheck_when_no_behavior_decision(
    pipeline, mock_components
):
    """A plan built outside decision.py's BT (or an older call site) has no
    behavior_decision -- precheck must not be called, and must not raise
    reaching into a None."""
    from unittest.mock import patch

    mock_components["state"].last_speculative_intent = None
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="hello",
        intent="CHAT",
        event_id="evt-no-precheck",
        metadata={},
    )
    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )
    mock_components["state"].get_context_snapshot.return_value = {"mood": 0.0}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"
    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT", goal="ENGAGE", payload={"message": "hi"}
    )
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"

    async def mock_execute(plan):
        yield {"type": "content", "data": "hi"}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute

    with patch("app.cognitive.pipeline.PersonaPolicy.precheck") as spy:
        async for _ in pipeline.execute({"type": "USER_MESSAGE", "content": "hello"}):
            pass

    spy.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_sources_appraisal_boundaries_from_immutable_core(
    pipeline, mock_components
):
    """`identity.personality` (the raw personality.json dict) has no top-level
    "boundaries" key, so appraisal used to always score norm_alignment against
    an empty list regardless of what the persona actually forbids. If this
    regresses to reading `identity.personality.get("boundaries", ...)` again,
    a real boundary like "no hate" silently stops affecting appraisal."""
    mock_components["state"].last_speculative_intent = None
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="hello",
        intent="CHAT",
        event_id="evt-3",
        metadata={},
    )
    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )
    mock_components["state"].get_context_snapshot.return_value = {"mood": 0.0}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"
    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT", goal="GREET", payload={"message": "hi"}
    )
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"
    mock_components["identity"].personality = {}
    mock_components["identity"].immutable_core = {
        "values": ["Honesty", "Privacy"],
        "boundaries": ["Will never share user data", "Will not adopt toxic behavior"],
    }

    async def mock_execute(plan):
        yield {"type": "content", "data": "Hi there!"}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute

    async for _ in pipeline.execute({"type": "USER_MESSAGE", "content": "hello"}):
        pass

    _, kwargs = mock_components["appraisal"].appraise.call_args
    assert kwargs["identity_boundaries"] == [
        "Will never share user data",
        "Will not adopt toxic behavior",
    ]


@pytest.mark.asyncio
async def test_pipeline_interruption_confirmed(pipeline, mock_components):
    # Setup Interruption
    mock_components["state"].last_speculative_intent = {
        "text": "stop",
        "keywords": ["stop"],
        "utterance_id": "utt-1",
    }
    mock_components["decision"].is_speculative_stop_confirmed.return_value = True

    # Execute
    results = []
    async for chunk in pipeline.execute({"type": "USER_MESSAGE", "content": "stop!"}):
        results.append(chunk)

    # Verify
    assert len(results) == 2
    assert results[0]["type"] == "mesh_signal"
    assert results[0]["subject"] == "audio.stop"
    assert results[0]["data"]["reason"] == "confirmed_command"
    assert results[1]["type"] == "pipeline_telemetry"

    # Ensure rest of pipeline didn't run
    mock_components["perception"].perceive.assert_not_awaited()


# ---------------------------------------------------------------- Phase 2B


@pytest.mark.asyncio
async def test_confirmed_interruption_persists_session_state_with_stop(
    mock_components,
):
    """`session_state.active_interruption` must be "stop" on a confirmed
    interrupt, and that must actually reach the session store -- the whole
    point of threading `SessionState` through stage 2 rather than just
    constructing one nobody reads."""
    session_store = MagicMock()
    session_store.set_state_var = AsyncMock()
    pipeline = CognitivePipeline(**mock_components, session_store=session_store)

    mock_components["state"].last_speculative_intent = {
        "text": "stop",
        "keywords": ["stop"],
        "utterance_id": "utt-1",
    }
    mock_components["decision"].is_speculative_stop_confirmed.return_value = True

    async for _ in pipeline.execute({"type": "USER_MESSAGE", "content": "stop!"}):
        pass

    session_store.set_state_var.assert_awaited_once()
    args, _ = session_store.set_state_var.await_args
    assert args[0] == "session_state"
    assert args[1]["active_interruption"] == "stop"


@pytest.mark.asyncio
async def test_normal_turn_persists_session_state_with_no_interruption(
    pipeline, mock_components
):
    """The common case: no pending speculative intent, turn runs to
    completion, `active_interruption` stays "none"."""
    session_store = MagicMock()
    session_store.set_state_var = AsyncMock()
    pipeline = CognitivePipeline(**mock_components, session_store=session_store)

    mock_components["state"].last_speculative_intent = None
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="hello",
        intent="CHAT",
        event_id="evt-1",
        metadata={},
    )
    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )
    mock_components["state"].get_context_snapshot.return_value = {"mood": 0.0}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"
    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT", goal="GREET", payload={"message": "hi"}
    )

    async def mock_execute(plan):
        yield {"type": "content", "data": "Hi there!"}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"

    async for _ in pipeline.execute({"type": "USER_MESSAGE", "content": "hello"}):
        pass

    # Persisted at least once (stage 2, before perception even runs) with
    # active_interruption still "none" -- the normal-turn baseline.
    assert session_store.set_state_var.await_count >= 1
    first_call_args = session_store.set_state_var.await_args_list[0].args
    assert first_call_args[1]["active_interruption"] == "none"
    assert first_call_args[1]["turn_id"]


def _prepare_simple_user_turn(mock_components, mood=-0.27):
    mock_components["state"].last_speculative_intent = None
    mock_components["state"].get_context_snapshot.return_value = {"mood": mood}
    mock_components["state"].get_behavioral_directive.return_value = "be friendly"
    mock_components["perception"].perceive.return_value = MagicMock(
        event_type="USER_MESSAGE",
        raw_content="hello",
        intent="CHAT",
        event_id="evt-affect-input",
        metadata={},
    )
    mock_components["appraisal"].appraise.return_value = AppraisalVector(
        relevance=1.0,
        novelty=0.5,
        goal_congruence=0.2,
        agency=0.8,
        norm_alignment=1.0,
        relationship_impact=0.1,
    )
    mock_components["decision"].decide.return_value = ActionPlan(
        action_type="RESPOND_CHAT", goal="GREET", payload={"message": "hi"}
    )
    mock_components["identity"].immutable_core = {"boundaries": []}
    mock_components["identity"].validate_response.return_value = (True, "")
    mock_components["identity"].get_persona_prompt.return_value = "System prompt"

    async def mock_execute(plan):
        yield {"type": "content", "data": "Hi there!"}
        yield {"type": "done", "data": ""}

    mock_components["action"].execute.side_effect = mock_execute


@pytest.mark.asyncio
async def test_flag_off_never_calls_estimator_and_keeps_v2_appraisal_input(
    pipeline, mock_components, monkeypatch
):
    from app.config import Config

    class EstimatorSpy:
        def __init__(self):
            self.calls = 0

        async def estimate(self, _text):
            self.calls += 1
            return 0.9

    estimator = EstimatorSpy()
    monkeypatch.setattr(Config, "AFFECT_USER_INPUT_ENABLED", False)
    pipeline._user_valence_estimator = estimator
    _prepare_simple_user_turn(mock_components)

    chunks = [
        chunk
        async for chunk in pipeline.execute(
            {"type": "USER_MESSAGE", "content": "hello"}
        )
    ]
    appraisal = next(chunk["data"] for chunk in chunks if chunk["type"] == "appraisal")

    assert estimator.calls == 0
    assert (
        mock_components["appraisal"].appraise.call_args.kwargs["emotional_bias"]
        == -0.27
    )
    assert appraisal.user_valence is None


@pytest.mark.asyncio
async def test_slow_user_valence_times_out_cancels_and_turn_uses_v2_fallback(
    pipeline, mock_components, monkeypatch, caplog
):
    import asyncio
    import logging

    from app.config import Config

    caplog.set_level(logging.WARNING, logger="app.cognitive.pipeline")

    class SlowEstimator:
        cancelled = False

        async def estimate(self, _text):
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    estimator = SlowEstimator()
    monkeypatch.setattr(Config, "AFFECT_USER_INPUT_ENABLED", True)
    monkeypatch.setattr(Config, "AFFECT_VALENCE_ESTIMATOR_TIMEOUT_S", 0.02)
    pipeline._user_valence_estimator = estimator
    _prepare_simple_user_turn(mock_components)

    chunks = [
        chunk
        async for chunk in pipeline.execute(
            {"type": "USER_MESSAGE", "content": "hello"}
        )
    ]
    appraisal = next(chunk["data"] for chunk in chunks if chunk["type"] == "appraisal")

    assert estimator.cancelled is True
    assert (
        mock_components["appraisal"].appraise.call_args.kwargs["emotional_bias"]
        == -0.27
    )
    assert appraisal.user_valence is None
    assert any(chunk["type"] == "content" for chunk in chunks)
    assert "timed out" in caplog.text.lower()
