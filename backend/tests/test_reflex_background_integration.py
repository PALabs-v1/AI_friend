import asyncio
from unittest.mock import AsyncMock, MagicMock

import cognitive_rust
import pytest

from app.agents.subconscious_agent import SubconsciousAgent
from app.cognitive.action import ActionService
from app.cognitive.decision import ActionPlan


@pytest.mark.asyncio
async def test_acoustic_reflex_rust():
    # Asserts that evaluate_acoustic_reflex properly registers loud signals and outputs startle interrupts.
    # Signature in cognitive_rust: evaluate_acoustic_reflex(rms: float, _zcr: float, threshold: float) -> bool
    assert cognitive_rust.evaluate_acoustic_reflex(95.0, 0.0, 80.0) is True
    assert cognitive_rust.evaluate_acoustic_reflex(45.0, 0.0, 80.0) is False


@pytest.mark.asyncio
async def test_subconscious_loop_abort():
    # Simulates NATS activity during a background monologue generation and verifies that the task is immediately canceled.
    mock_state_service = MagicMock()
    mock_state_service.get_context_snapshot.return_value = {
        "emotion": "curious",
        "energy": 0.8,
    }
    mock_state_service.check_proactive_eligibility.return_value = True

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="I should do something.")

    agent = SubconsciousAgent(
        state_service=mock_state_service, graph_db=MagicMock(), memory_store=MagicMock()
    )
    agent.llm = mock_llm

    # Mock NATS connection
    agent.publish = AsyncMock()

    # Create a long running monologue task
    async def long_running_task():
        await asyncio.sleep(5)
        # This shouldn't be reached if cancelled

    agent._current_monologue_task = asyncio.create_task(long_running_task())
    agent._current_dream_task = asyncio.create_task(long_running_task())

    # Simulate a user message trigger via _on_chat_input
    await agent._on_chat_input({"text": "Hello there!", "metadata": {"source": "user"}})

    # Allow the event loop to yield and process cancellation
    await asyncio.sleep(0.1)

    # Verify that tasks were cancelled
    assert agent._current_monologue_task.cancelled() is True, (
        "Monologue task should be cancelled on user input"
    )
    assert agent._current_dream_task.cancelled() is True, (
        "Dream task should be cancelled on user input"
    )


@pytest.mark.asyncio
async def test_paralinguistic_tag_injection():
    # Validates that emotional states correctly trigger breathing/sigh tags in responses.
    llm = MagicMock()

    # Mock LLM streaming
    async def mock_stream(*args, **kwargs):
        yield "Hello."

    llm.generate_stream = MagicMock(side_effect=mock_stream)

    action_service = ActionService(llm_service=llm, memory_store=MagicMock())

    # Test case 1: arousal > 0.6 and valence < -0.3 -> prepend <breath_fast>
    plan_breath = ActionPlan(
        action_type="RESPOND_CHAT",
        goal="ENGAGE",
        payload={
            "message": "hello",
            "valence": -0.5,
            "arousal": 0.8,
            "dominance": 0.5,
        },
    )

    chunks = []
    async for chunk in action_service.execute(plan_breath):
        chunks.append(chunk)

    assert len(chunks) > 0
    content_chunks = [c["data"] for c in chunks if c["type"] == "content"]
    assert any("<breath_fast>" in c for c in content_chunks)

    # Test case 2: arousal < 0.4 and valence < 0.0 -> prepend <sigh_soft>
    plan_sigh = ActionPlan(
        action_type="RESPOND_CHAT",
        goal="ENGAGE",
        payload={
            "message": "hello",
            "valence": -0.2,
            "arousal": 0.2,
            "dominance": 0.5,
        },
    )

    chunks = []
    async for chunk in action_service.execute(plan_sigh):
        chunks.append(chunk)

    content_chunks = [c["data"] for c in chunks if c["type"] == "content"]
    assert any("<sigh_soft>" in c for c in content_chunks)


@pytest.mark.asyncio
async def test_metacognitive_self_correction():
    # Simulates an identity drift detection, verifies that audio.stop is sent
    # to interrupt playback, and asserts that a correction phrase is injected.
    # control.interrupt used to be published alongside audio.stop here, but
    # had zero subscribers anywhere (P1-8) and was redundant with audio.stop,
    # which already carries the same {"interrupt": True, "reason": ...}
    # payload and is the one subscribers actually act on -- removed rather
    # than given a consumer it never needed.
    llm = MagicMock()

    async def mock_stream_violating(*args, **kwargs):
        # Genuine contempt aimed at the user, not just the word "hate" --
        # Phase 3.2's friction audit found (and fixed) this check rejecting
        # ordinary non-hostile phrases like "I hate this", which is no longer
        # a violation and would no longer exercise the self-correction path
        # this test is actually about.
        yield "You're so pathetic."

    async def mock_stream_corrected(*args, **kwargs):
        yield "Let's be positive."

    streams = [mock_stream_violating, mock_stream_corrected]
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        res = streams[call_count]
        call_count += 1
        return res(*args, **kwargs)

    llm.generate_stream = MagicMock(side_effect=side_effect)

    action_service = ActionService(llm_service=llm, memory_store=MagicMock())
    publish_mock = AsyncMock()
    action_service.publish_cb = publish_mock

    plan = ActionPlan(
        action_type="RESPOND_CHAT",
        goal="ENGAGE",
        payload={
            "message": "hello",
            "valence": 0.0,
            "arousal": 0.5,
            "dominance": 0.5,
        },
    )

    chunks = []
    async for chunk in action_service.execute(plan):
        chunks.append(chunk)

    publish_mock.assert_awaited()
    assert any(call.args[0] == "audio.stop" for call in publish_mock.call_args_list)
    assert not any(
        call.args[0] == "control.interrupt" for call in publish_mock.call_args_list
    )

    content_chunks = [c["data"] for c in chunks if c["type"] == "content"]
    assert any("Wait, let me rephrase that..." in c for c in content_chunks)
    assert any("Let's be positive." in c for c in content_chunks)


@pytest.mark.asyncio
async def test_silent_reasoning_stripping():
    # Ensures that <thought> blocks are parsed out of final vocalizations but saved to telemetry.
    llm = MagicMock()

    async def mock_stream(*args, **kwargs):
        yield "<thought>Analyzing the request...</thought>Hello user!"

    llm.generate_stream = MagicMock(side_effect=mock_stream)

    action_service = ActionService(llm_service=llm, memory_store=MagicMock())

    plan = ActionPlan(
        action_type="RESPOND_CHAT",
        goal="ENGAGE",
        payload={
            "message": "hello",
            "valence": 0.0,
            "arousal": 0.5,
            "dominance": 0.5,
        },
    )

    chunks = []
    async for chunk in action_service.execute(plan):
        chunks.append(chunk)

    content_chunks = [c["data"] for c in chunks if c["type"] == "content"]
    full_vocalization = "".join(content_chunks)

    assert "<thought>" not in full_vocalization
    assert "Analyzing the request..." not in full_vocalization
    assert "Hello user!" in full_vocalization


@pytest.mark.asyncio
async def test_sleep_dreaming_neo4j():
    # Mocks Neo4j entity retrieval and verifies that generated dreams are
    # NEVER written into autobiographical memory (epistemic quarantine,
    # Sections 19/37, Invariant 12: generated background content cannot
    # self-promote to truth) while the graph query itself still runs.
    mock_state_service = MagicMock()
    mock_state_service.get_context_snapshot.return_value = {"fatigue": 0.9}

    mock_graph_db = MagicMock()
    mock_graph_db.execute_query = AsyncMock(
        return_value=[
            {"name": "Concept A"},
            {"name": "Concept B"},
            {"name": "Concept C"},
        ]
    )

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="I had a dream about A, B, and C.")

    mock_memory_store = MagicMock()
    mock_memory_store.add_memory = AsyncMock()

    agent = SubconsciousAgent(
        state_service=mock_state_service,
        graph_db=mock_graph_db,
        memory_store=mock_memory_store,
    )
    agent.llm = mock_llm

    await agent._run_dream_sequence()

    mock_graph_db.execute_query.assert_awaited()
    mock_memory_store.add_memory.assert_not_called()
    assert not any(
        call.kwargs.get("source") == "subconscious_dream"
        for call in mock_memory_store.add_memory.await_args_list
    ), "dream text must never be added to autobiographical memory (quarantine)"

    dream_query = mock_graph_db.execute_query.await_args.args[0]
    assert "rand()" not in dream_query.lower(), (
        "Regression for the O(N log N) `ORDER BY rand()` full-graph-scan-and-"
        "sort fix - the query must use apoc.coll.randomItems (O(N), no sort) "
        "instead of evaluating and sorting a random value for every node."
    )
    assert "apoc.coll.randomitems" in dream_query.lower()


@pytest.mark.usefixtures("actr_v1_ranking")
@pytest.mark.asyncio
async def test_mrl_dimension_gating():
    # Test that MRL dimension gating handles stress/arousal/fatigue scaling
    from app.state.memory_store import MemoryStore

    # Initialize MemoryStore with mock parameters
    store = MemoryStore(MagicMock(), MagicMock())
    store.get_embedding = AsyncMock(return_value=[0.1] * 768)
    store.qdrant_store = MagicMock()
    store.qdrant_store.search_vector_memories = MagicMock(return_value=[])
    store.graph_db = MagicMock()
    store.graph_db.execute_query = AsyncMock(return_value=[])

    # Case 1: High stress/arousal/fatigue -> mrl_dim = 256
    results = await store.search_memories(
        "hello", current_arousal=0.9, current_cortisol=0.9
    )
    assert results == []
    # Extract the query vector sent to qdrant
    called_args = store.qdrant_store.search_vector_memories.call_args[1]
    q_vec = called_args["query_vector"]
    assert q_vec[255] == 0.1
    assert q_vec[256] == 0.0
    assert len(q_vec) == 768

    # Case 2: Relaxed -> mrl_dim = 768
    results_relaxed = await store.search_memories(
        "hello", current_arousal=0.2, current_cortisol=0.2
    )
    assert results_relaxed == []
    called_args_relaxed = store.qdrant_store.search_vector_memories.call_args[1]
    q_vec_relaxed = called_args_relaxed["query_vector"]
    assert q_vec_relaxed[256] == 0.1
    assert q_vec_relaxed[767] == 0.1


@pytest.mark.asyncio
async def test_actr_goal_utility_rl():
    # Test ACT-R Goal Utility Reinforcement Learning updates in DecisionService
    from app.cognitive.decision import DecisionService
    from app.cognitive.perception import CognitiveEvent

    decision_service = DecisionService()
    event = CognitiveEvent(
        "ev-1",
        "USER_MESSAGE",
        "hello",
        {"gaze": 0.8, "appraisal": {"relevance": 0.5}},
        "CHAT",
    )

    # Simulate selecting ENGAGE goal first
    decision_service._previous_goal = "ENGAGE"
    # Execute decide (which triggers _score_goals_maut and TD-learning updates)
    state = {
        "mood": 0.5,
        "energy": 0.5,
        "trust": 0.5,
        "inferred_valence": 0.8,
        "emotion": "neutral",
    }
    await decision_service.decide(event, state)

    # The utility of the previous goal "ENGAGE" should have been updated from 1.0
    # Reward = 0.7 * norm_valence + 0.3 * gaze = 0.7 * 0.9 + 0.3 * 0.8 = 0.63 + 0.24 = 0.87
    # U = 1.0 + 0.1 * (0.87 - 1.0) = 0.987
    assert decision_service.goal_utilities["ENGAGE"] == pytest.approx(0.987, abs=1e-3)


@pytest.mark.asyncio
async def test_emotionally_gated_consolidation():
    # Test that ReflectionService filters unconsolidated episodes based on the saliency index
    from app.cognitive.learning import ReflectionService

    reflection_service = ReflectionService(
        llm_service=MagicMock(), graph_store=MagicMock()
    )

    # Mock LLM and Graph
    reflection_service.llm.generate = AsyncMock(return_value="[]")
    reflection_service.graph.decay_relationships = AsyncMock()

    # 3 episodes: low, medium, high saliency
    episodes = [
        {
            "event": "EventApple",
            "emotion_vector": {"Ar": 0.1, "cortisol": 0.1},
        },  # ESI = 0.1
        {
            "event": "EventBanana",
            "emotion_vector": {"Ar": 0.5, "cortisol": 0.4},
        },  # ESI = 0.46
        {
            "event": "EventCherry",
            "emotion_vector": {"Ar": 0.9, "cortisol": 0.8},
        },  # ESI = 0.86
    ]

    # Run consolidate
    await reflection_service._consolidate(episodes)
    # The llm.generate call should receive a summary consisting only of prioritized events (B & C)
    for call in reflection_service.llm.generate.call_args_list:
        called_prompt = call[0][0]
        assert "EventBanana" in called_prompt
        assert "EventCherry" in called_prompt
        assert (
            "EventApple" not in called_prompt
        )  # EventApple should have been filtered out (low saliency)


@pytest.mark.asyncio
async def test_tom_belief_tracking_and_neo4j():
    # Test UserMentalModel user_beliefs tracking and extract_belief_discrepancies helper
    from app.cognitive.tom import UserMentalModel, extract_belief_discrepancies

    model = UserMentalModel(
        inferred_valence=0.5, user_beliefs={"sky": "green", "grass": "green"}
    )

    ground_truth = {"sky": "blue", "grass": "green"}
    discrepancies = extract_belief_discrepancies(model.user_beliefs, ground_truth)

    assert "sky" in discrepancies
    assert discrepancies["sky"]["user_belief"] == "green"
    assert discrepancies["sky"]["ground_truth"] == "blue"
    assert "grass" not in discrepancies


@pytest.mark.asyncio
async def test_vap_predictive_pre_generation():
    # Test that CognitivePipeline handles VAP turn projection and triggers speculative pre-generation
    from app.cognitive.pipeline import CognitivePipeline

    mock_perception = MagicMock()
    # Mock perceive returning a mock CognitiveEvent
    mock_event = MagicMock()
    mock_event.event_type = "USER_MESSAGE"
    mock_event.raw_content = "hello"
    mock_event.metadata = {"speculative": False}
    mock_perception.perceive = AsyncMock(return_value=mock_event)

    mock_appraisal = MagicMock()
    mock_appraisal_vector = MagicMock()
    mock_appraisal_vector.to_dict.return_value = {}
    mock_appraisal_vector.relationship_impact = 0.0
    mock_appraisal.appraise.return_value = mock_appraisal_vector

    mock_state = MagicMock()
    mock_state.get_context_snapshot.return_value = {"mood": 0.5, "energy": 0.5}
    mock_state.get_behavioral_directive.return_value = "neutral"
    # Mock async methods
    mock_state.update_theory_of_mind = AsyncMock()
    mock_state.update_from_appraisal = AsyncMock()

    mock_decision = MagicMock()
    mock_plan = MagicMock()
    mock_plan.goal = "ENGAGE"
    mock_plan.payload = {}
    mock_decision.decide = AsyncMock(return_value=mock_plan)

    mock_action = MagicMock()
    mock_action.execute = MagicMock(return_value=MagicMock())

    # Create an async generator mock
    async def mock_action_generator(*args, **kwargs):
        yield {"type": "content", "data": "Hi"}
        yield {"type": "done", "data": "finished"}

    mock_action.execute.side_effect = mock_action_generator

    mock_identity = MagicMock()
    mock_identity.validate_response = AsyncMock(return_value=(True, ""))

    pipeline = CognitivePipeline(
        perception=mock_perception,
        appraisal=mock_appraisal,
        state=mock_state,
        decision=mock_decision,
        action=mock_action,
        learning=MagicMock(),
        identity=mock_identity,
    )

    # Case 1: Partial event, low VAP -> should discard early and return nothing
    partial_low = {
        "event_type": "USER_MESSAGE",
        "is_partial": True,
        "vap_probability": 0.3,
    }
    outputs_low = [out async for out in pipeline.execute(partial_low)]
    assert len(outputs_low) == 0

    # Case 2: Partial event, high VAP -> should pre-generate speculatively
    partial_high = {
        "event_type": "USER_MESSAGE",
        "is_partial": True,
        "vap_probability": 0.85,
    }
    outputs_high = [out async for out in pipeline.execute(partial_high)]

    # Check that audio.pre_generate signal was yielded
    pre_gen_signals = [
        o
        for o in outputs_high
        if o.get("type") == "mesh_signal" and o.get("subject") == "audio.pre_generate"
    ]
    assert len(pre_gen_signals) == 1
    assert pre_gen_signals[0]["data"]["speculative"] is True

    # Check that content chunks have speculative flag set to True
    content_chunks = [o for o in outputs_high if o.get("type") == "content"]
    assert len(content_chunks) > 0
    assert all(c.get("speculative") is True for c in content_chunks)
