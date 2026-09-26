"""Regression coverage for the Phase 07 production composition root."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.brain_agent import BrainAgent
from app.cognitive.action import ActionService
from app.cognitive.background_scheduler import BackgroundScheduler
from app.cognitive.core import CognitiveService
from app.cognitive.decision import ActionPlan, DecisionService
from app.cognitive.external_action import ExternalActionDispatcher
from app.cognitive.learning_governance import LearningGovernor
from app.cognitive.perception import CognitiveEvent
from app.cognitive.pipeline import CognitivePipeline
from app.cognitive.planning import (
    DeterministicPlanExecutor,
    DeterministicPlanVerifier,
)
from app.cognitive.simulation import EpisodicSimulator
from app.config import Config
from app.llm.adapter_gate import OfflineAdapterGate
from app.llm.model_roles import ProviderCapabilityNegotiator
from app.state.session_state import SessionState
from app.state.temporal_store import TemporalMemoryStore
from app.state.workspace_store import SQLiteWorkspaceStore


@pytest.fixture
def cognitive_service(tmp_path):
    service = CognitiveService(
        llm_service=MagicMock(),
        memory_store=MagicMock(),
        graph_db=MagicMock(),
        base_path=str(tmp_path),
    )
    yield service


def test_cognitive_service_composes_phase_services(cognitive_service):
    """The live root must own every Phase 01-06 service it exposes."""
    expected_types = {
        "workspace_store": SQLiteWorkspaceStore,
        "temporal_memory_store": TemporalMemoryStore,
        "scheduler": BackgroundScheduler,
        "plan_verifier": DeterministicPlanVerifier,
        "plan_executor": DeterministicPlanExecutor,
        "episodic_simulator": EpisodicSimulator,
        "learning_governor": LearningGovernor,
        "offline_adapter_gate": OfflineAdapterGate,
        "provider_capability_negotiator": ProviderCapabilityNegotiator,
        "external_action_dispatcher": ExternalActionDispatcher,
    }

    for attribute, expected_type in expected_types.items():
        assert isinstance(getattr(cognitive_service, attribute), expected_type)
        assert getattr(cognitive_service.pipeline, attribute) is getattr(
            cognitive_service, attribute
        )
    assert cognitive_service.learning.governor is cognitive_service.learning_governor


def test_pipeline_composition_has_scheduler_and_session_store(cognitive_service):
    """Foreground turns need both scheduler preemption and session persistence."""
    assert cognitive_service.pipeline.scheduler is cognitive_service.scheduler
    assert cognitive_service.pipeline.scheduler is not None
    assert cognitive_service.pipeline.session_store is cognitive_service.session_store


@pytest.mark.asyncio
async def test_brain_agent_passes_workspace_to_process_event(tmp_path):
    """A chat turn must pass the store snapshot so intent provenance is causal."""
    workspace_store = SQLiteWorkspaceStore(tmp_path / "workspace.db")
    snapshot = await workspace_store.get_snapshot("authoritative-user")
    captured = {}

    async def process_event(*args, **kwargs):
        captured.update(kwargs)
        if False:
            yield None

    runtime = MagicMock()
    runtime.calculate_pacing_parameters.return_value = {"silence_duration_ms": 0}
    runtime.monitor_stream_and_fill.side_effect = lambda generator, **kwargs: generator
    state = MagicMock()
    state.get_context_snapshot.return_value = {"mood": 0.0}
    core = SimpleNamespace(
        state=state,
        workspace_store=workspace_store,
        process_event=process_event,
    )
    agent = BrainAgent.__new__(BrainAgent)
    agent.cognitive_core = core
    agent.conversational_runtime = runtime
    agent.conversation_store = None
    agent.last_percept = MagicMock()
    agent.last_visual_context = "No visual data available."
    agent.last_visual_evidence = None
    agent.last_user_distance = 1.0
    agent.last_user_voice_properties = None
    agent.last_playback_backlog = 0
    agent.last_assistant_response = None
    agent.last_audio_progress = None
    agent._active_response_turn_id = None
    agent._active_action_intent = None
    agent._reply_contexts = {}
    agent._turn_state_lock = asyncio.Lock()

    async def consume_stream(generator, **kwargs):
        async for _ in generator:
            pass
        return ""

    agent._stream_to_speech = consume_stream

    from app.contracts import ChatInput

    await BrainAgent._process_chat_input_flow(
        agent,
        ChatInput(text="hello", turn_id="turn-1", utterance_id="utterance-1"),
        False,
        {"user_id": "authoritative-user"},
    )

    passed_workspace = captured["workspace"]
    assert (passed_workspace.epoch, passed_workspace.revision) == (
        snapshot.epoch,
        snapshot.revision,
    )
    assert (passed_workspace.epoch, passed_workspace.revision) != (0, 0)

    plan = ActionPlan(action_type="RESPOND_CHAT", goal="ENGAGE", payload={})
    pipeline = CognitivePipeline(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    intent = pipeline._commit_action_intent(
        plan,
        SessionState.start_turn("turn-1"),
        passed_workspace,
        agent.last_percept,
    )
    assert (intent.workspace_epoch, intent.workspace_revision) != (0, 0)
    await workspace_store.close()


@pytest.mark.asyncio
async def test_wait_candidate_maps_to_silent_action(monkeypatch):
    """Selecting WAIT must produce a WAIT plan and no language output."""
    decision = DecisionService.__new__(DecisionService)

    def choose_wait(behavior_decision, *args, **kwargs):
        behavior_decision.selected_candidate = {"kind": "WAIT"}
        return behavior_decision

    decision._select_action_candidate = MagicMock(side_effect=choose_wait)
    event = CognitiveEvent(
        event_id="wait-event",
        event_type="USER_MESSAGE",
        raw_content="hello",
        metadata={},
    )
    blackboard = {
        "event": event,
        "state": {
            "emotion": "neutral",
            "mood": 0.0,
            "trust": 0.5,
            "attachment": 0.1,
        },
        "memory_activations": [],
    }

    monkeypatch.setattr(Config, "MEMORY_TRUTH_ENABLED", True)
    await decision._plan_social_response(blackboard)

    assert blackboard["plan"].action_type == "WAIT"
    chunks = [
        chunk
        async for chunk in ActionService(llm_service=MagicMock()).execute(
            blackboard["plan"]
        )
    ]
    assert chunks == [{"type": "done", "data": ""}]


@pytest.mark.asyncio
async def test_external_action_is_fail_closed():
    """An external action without a typed authorization path must not run."""
    plan = ActionPlan(action_type="EXTERNAL_ACT", goal="ACT", payload={})
    chunks = [chunk async for chunk in ActionService().execute(plan)]

    assert chunks == [
        {"type": "error", "data": "External action blocked."},
        {"type": "done", "data": ""},
    ]


@pytest.mark.asyncio
async def test_external_action_dispatcher_receives_typed_intent():
    """A configured dispatcher must receive and complete the typed action."""
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = {"status": "COMPLETED", "executed": True}
    plan = ActionPlan(
        action_type="EXTERNAL_ACT",
        goal="ACT",
        payload={
            "action_id": "action-1",
            "turn_id": "turn-1",
            "tool_or_actuator": "lamp.dim",
            "parameters": {"level": 0.3},
        },
    )

    chunks = [
        chunk
        async for chunk in ActionService(external_action_dispatcher=dispatcher).execute(
            plan
        )
    ]

    assert chunks == [{"type": "done", "data": ""}]
    dispatcher.dispatch.assert_called_once()
    intent = dispatcher.dispatch.call_args.args[0]
    assert intent.action_id == "action-1"
    assert intent.turn_id == "turn-1"
    assert intent.tool_or_actuator == "lamp.dim"
    assert intent.parameters == {"level": 0.3}


@pytest.mark.asyncio
async def test_external_action_dispatch_failure_emits_error_and_done():
    """A dispatcher exception must terminate safely without leaking execution."""
    dispatcher = MagicMock()
    dispatcher.dispatch.side_effect = RuntimeError("executor unavailable")
    plan = ActionPlan(
        action_type="EXTERNAL_ACT",
        goal="ACT",
        payload={"tool_or_actuator": "lamp.dim"},
    )

    chunks = [
        chunk
        async for chunk in ActionService(external_action_dispatcher=dispatcher).execute(
            plan
        )
    ]

    assert chunks == [
        {"type": "error", "data": "executor unavailable"},
        {"type": "done", "data": ""},
    ]


@pytest.mark.asyncio
async def test_memory_surfacing_preserves_epistemic_metadata(cognitive_service):
    """Surfacing must retain truth fields for downstream activation adaptation."""
    memory = {
        "content": "The appointment moved.",
        "score": 0.9,
        "source": "conversation",
        "created_at": 123.0,
        "contradiction_state": "DISPUTED",
        "outage_flag": True,
        "metadata": {"record_type": "belief"},
        "belief_record": {"claim": "appointment moved"},
    }

    await cognitive_service._on_memory_surfaced({"memories": [memory]})

    stored = cognitive_service.surfaced_memories[-1]
    assert stored["contradiction_state"] == "DISPUTED"
    assert stored["outage_flag"] is True
    assert stored["metadata"] == {"record_type": "belief"}
    assert stored["belief_record"] == {"claim": "appointment moved"}
