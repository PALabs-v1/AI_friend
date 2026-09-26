"""
Test suite for the Subconscious Engine.
Validates idle checking, internal thought generation, and routing to the BrainAgent.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cognitive.subconscious import SubconsciousEngine
from app.contracts import ChatInput, Topics


@pytest.fixture
def mock_state_service():
    service = MagicMock()
    service.check_proactive_eligibility.return_value = True
    service.proactive_candidate_eligible.return_value = True
    service.record_proactive_thought.return_value = ("stable-goal", 1.0)
    service.persist_state = AsyncMock()
    service.get_context_snapshot.return_value = {"emotion": "curious", "energy": 0.8}
    service.current_state.last_user_interaction = 0.0
    return service


@pytest.fixture
def mock_llm_service():
    llm = MagicMock()
    llm.generate = AsyncMock(return_value='"I should ask them about their day."')
    return llm


class TestSubconsciousAgent:
    @pytest.mark.asyncio
    async def test_generation_prompt_contains_bounded_goal_and_ignore_context(self):
        llm = MagicMock()
        llm.generate = AsyncMock(
            return_value='{"thought":"Check the grant plan","importance":0.8,"goal_id":"grant"}'
        )
        engine = SubconsciousEngine(llm)
        snapshot = {
            "timestamp": 1000.0,
            "active_goals": ["finish application"],
            "proactive_goals": [
                {
                    "goal_id": "grant",
                    "description": "finish the grant application",
                    "deadline": 4600.0,
                }
            ],
            "unresolved_thoughts": [
                {"goal_id": "grant", "description": "check the budget section"}
            ],
        }

        result = await engine.evaluate_and_think(snapshot, True)

        assert result is not None and result.goal_id == "grant"
        prompt = llm.generate.await_args.args[0]
        assert "finish application" in prompt
        assert "deadline in 1.0 hours" in prompt
        assert "check the budget section" in prompt
        assert "do not invent commitments" in prompt

    @pytest.mark.asyncio
    async def test_subconscious_agent_ignores_tick_when_ineligible(
        self, mock_state_service, mock_llm_service
    ):
        from app.agents.subconscious_agent import SubconsciousAgent

        mock_state_service.check_proactive_eligibility.return_value = False

        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=MagicMock()
        )
        agent.llm = mock_llm_service
        agent.publish = AsyncMock()

        await agent._on_system_tick({"timestamp": 1234567890})

        # Should not generate thought or publish anything
        agent.llm.generate.assert_not_awaited()
        agent.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subconscious_agent_generates_and_publishes_thought(
        self, mock_state_service, mock_llm_service
    ):
        from app.agents.subconscious_agent import SubconsciousAgent

        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=MagicMock()
        )
        agent.llm = mock_llm_service
        agent.publish = AsyncMock()
        # Phase 3.1: a thought only publishes live when someone is actually
        # connected -- see TestProactiveQueueing below for the alternative.
        agent._someone_connected = True

        await agent._on_system_tick({"timestamp": 1234567890})

        # Ensure LLM was prompted
        agent.llm.generate.assert_awaited_once()
        prompt = agent.llm.generate.await_args.args[0]
        assert "curious" in prompt
        assert "0.8" in prompt

        # Ensure it published to CHAT_INPUT
        agent.publish.assert_awaited_once()
        topic, payload = agent.publish.await_args.args
        assert topic == Topics.CHAT_INPUT

        # Verify the payload structure
        msg = ChatInput.model_validate(payload)
        assert msg.text == "I should ask them about their day."
        assert msg.metadata.source == "subconscious"
        assert msg.metadata.importance == 0.2
        assert msg.metadata.category == "useful_to_user"

        # Verify attempt was marked
        mock_state_service.mark_proactive_attempt.assert_called_once()


class TestProactiveQueueing:
    """Phase 3.1: a thought generated while nobody is connected must queue
    instead of publishing into a room with no one to hear it, and must
    replay on the next 0 -> 1 reconnect."""

    @pytest.mark.asyncio
    async def test_a_thought_queues_instead_of_publishing_when_disconnected(
        self, mock_state_service, mock_llm_service, tmp_path
    ):
        from app.agents.subconscious_agent import SubconsciousAgent
        from app.state import proactive_queue

        mock_state_service.db_path = str(tmp_path / "state.db")
        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=MagicMock()
        )
        agent.llm = mock_llm_service
        agent.publish = AsyncMock()
        assert agent._someone_connected is False  # the default

        await agent._on_system_tick({"timestamp": 1234567890})

        agent.publish.assert_not_awaited()
        queued = proactive_queue.pop_all(mock_state_service.db_path)
        assert len(queued) == 1
        assert json.loads(queued[0])["text"] == "I should ask them about their day."
        # The cooldown must still be consumed, or every tick while still
        # disconnected would generate (and queue) another duplicate thought.
        mock_state_service.mark_proactive_attempt.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnecting_replays_a_queued_thought(
        self, mock_state_service, tmp_path
    ):
        from app.agents.subconscious_agent import SubconsciousAgent
        from app.state import proactive_queue

        mock_state_service.db_path = str(tmp_path / "state.db")
        proactive_queue.enqueue(mock_state_service.db_path, "queued while away")
        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=MagicMock()
        )
        agent.publish = AsyncMock()

        await agent._on_session_presence({"connected": True})

        agent.publish.assert_awaited_once()
        topic, payload = agent.publish.await_args.args
        assert topic == Topics.CHAT_INPUT
        assert ChatInput.model_validate(payload).text == "queued while away"
        # Delivered once -- the queue must actually be drained, not just read.
        assert proactive_queue.pop_all(mock_state_service.db_path) == []

    @pytest.mark.asyncio
    async def test_presence_with_an_empty_queue_publishes_nothing(
        self, mock_state_service, tmp_path
    ):
        from app.agents.subconscious_agent import SubconsciousAgent

        mock_state_service.db_path = str(tmp_path / "state.db")
        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=MagicMock()
        )
        agent.publish = AsyncMock()

        await agent._on_session_presence({"connected": True})

        agent.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_repeated_connected_signal_does_not_replay_again(
        self, mock_state_service, tmp_path
    ):
        """Only the 0 -> 1 edge should replay. A redundant connected=True
        (e.g. a redelivered NATS message for an event already processed)
        must not drain and deliver whatever happens to be in the queue at
        that moment -- from this service's perspective it is already known
        to be connected, so only a genuine reconnect should trigger delivery.

        Queues a second thought directly (bypassing the tick handler, which
        would not queue at all once already connected) between the two
        signals specifically so this test cannot pass merely because the
        queue was already empty on the second call."""
        from app.agents.subconscious_agent import SubconsciousAgent
        from app.state import proactive_queue

        mock_state_service.db_path = str(tmp_path / "state.db")
        proactive_queue.enqueue(mock_state_service.db_path, "delivered on the edge")
        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=MagicMock()
        )
        agent.publish = AsyncMock()

        await agent._on_session_presence({"connected": True})
        agent.publish.reset_mock()

        proactive_queue.enqueue(mock_state_service.db_path, "must not be delivered")
        await agent._on_session_presence({"connected": True})

        agent.publish.assert_not_awaited()
        # And the item must still be sitting there, untouched by the
        # redundant signal -- not silently dropped either.
        assert proactive_queue.pop_all(mock_state_service.db_path) == [
            "must not be delivered"
        ]


class TestBrainAgentSubconsciousRouting:
    @pytest.mark.asyncio
    async def test_brain_agent_routes_subconscious_thought_to_proactive_generator(self):
        from app.agents.brain_agent import BrainAgent

        agent = BrainAgent(graph_db=None, memory_store=None, conversation_store=None)
        agent.cognitive_core.state = MagicMock()
        agent.cognitive_core.state.get_context_snapshot.return_value = {
            "valence": 0.0,
            "arousal": 0.5,
            "dominance": 0.5,
            "fatigue": 0.0,
        }
        agent.cognitive_core.state.record_proactive_thought.return_value = (
            "stable-goal",
            1.0,
        )
        agent.cognitive_core.state.persist_state = AsyncMock()
        agent.cognitive_core.generate_proactive_response = MagicMock()
        agent.cognitive_core.process_event = MagicMock()
        agent._stream_to_speech = AsyncMock(return_value="Hello there.")

        # Simulate a subconscious thought arriving on chat.input
        thought_msg = {
            "text": "I should say hi.",
            "metadata": {"source": "subconscious"},
        }

        await agent._on_chat_input(thought_msg)

        # 1. Ensure user interaction was NOT recorded
        agent.cognitive_core.state.record_user_interaction.assert_not_called()

        # 2. Ensure it routed to generate_proactive_response, NOT process_event
        agent.cognitive_core.process_event.assert_not_called()
        agent.cognitive_core.generate_proactive_response.assert_called_once_with(
            thought_prompt="I should say hi."
        )

        # 3. Ensure stream_to_speech was called with is_proactive=True
        agent._stream_to_speech.assert_awaited_once()
        assert agent._stream_to_speech.await_args.kwargs["is_proactive"] is True


class TestSubconsciousAgentStateSharing:
    """P1-5: subconscious_agent's AgentState was never hydrated and never
    updated - an independent copy diverging from the brain's real state
    from the moment each process started. Every silence gate and the dream
    path read that same never-synced state."""

    @pytest.mark.asyncio
    async def test_state_broadcast_is_applied_to_this_process_own_state(self):
        from app.agents.subconscious_agent import SubconsciousAgent

        mock_state_service = MagicMock()
        mock_state_service.apply_external_state = AsyncMock()
        mock_graph_db = MagicMock()
        mock_graph_db.execute_query = AsyncMock(return_value=[{"name": "my friend"}])
        mock_graph_db.invalidate_cache = AsyncMock()

        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=mock_graph_db
        )

        broadcast = {"agent_name": "my friend", "mood": 0.42, "energy": 0.8}
        await agent._on_state_broadcast(broadcast)

        mock_state_service.apply_external_state.assert_awaited_once_with(broadcast)

    @pytest.mark.asyncio
    async def test_state_broadcast_with_invalid_agent_name_does_not_touch_state(self):
        """The existing agent_name validation must still short-circuit
        before either sync path runs - applying a malformed broadcast is
        worse than dropping it."""
        from app.agents.subconscious_agent import SubconsciousAgent

        mock_state_service = MagicMock()
        mock_state_service.apply_external_state = AsyncMock()

        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=MagicMock()
        )

        await agent._on_state_broadcast({"agent_name": None, "mood": 0.9})

        mock_state_service.apply_external_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_hydrates_state_before_serving(self, monkeypatch):
        """A one-time catch-up on startup, so this process isn't running on
        persona defaults until the first broadcast happens to arrive -
        mirrors what CognitiveService.initialize() does on the brain side."""
        from app.agents.subconscious_agent import SubconsciousAgent

        mock_state_service = MagicMock()
        mock_state_service.hydrate_state = AsyncMock()
        mock_graph_db = MagicMock()
        mock_graph_db.initialize = AsyncMock()

        agent = SubconsciousAgent(
            state_service=mock_state_service, graph_db=mock_graph_db
        )
        agent.connect = AsyncMock()
        agent.subscribe = AsyncMock()
        agent.memory_store = MagicMock()  # pre-set: skip the init-on-start block
        agent.reflection_service = MagicMock()  # pre-set: skip the init-on-start block
        agent._continuous_monologue_loop = AsyncMock()

        await agent.start()

        mock_state_service.hydrate_state.assert_awaited_once()
        # Hydration must happen before the mesh is serving traffic, not
        # racing the first tick/broadcast to decide which wins.
        assert agent.connect.await_count >= 1
