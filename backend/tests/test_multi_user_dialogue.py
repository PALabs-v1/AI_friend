from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.subconscious_agent import SubconsciousAgent
from app.cognitive.learning import ReflectionService
from app.config import Config
from app.state.memory_store import MemoryStore


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.acquire = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, conn


@pytest.mark.asyncio
async def test_multi_user_message_logging_and_retrieval(mock_pool):
    """
    Verify that get_recent_unconsolidated_episodes fetches custom roles (like Raj and Priya)
    when role != 'system'.
    """
    pool, conn = mock_pool

    # Mock data representing unconsolidated messages from dynamic speakers
    mock_rows = [
        {
            "id": "msg-1",
            "role": "Raj",
            "content": "Is Priya going to the park?",
            "timestamp": datetime.now(UTC),
        },
        {
            "id": "msg-2",
            "role": "assistant",
            "content": "Yes, Priya is planning to walk there.",
            "timestamp": datetime.now(UTC),
        },
        {
            "id": "msg-3",
            "role": "Priya",
            "content": "Actually, I'm heading to the workspace.",
            "timestamp": datetime.now(UTC),
        },
    ]
    conn.fetch.return_value = mock_rows

    mock_graph = MagicMock()
    store = MemoryStore(pool, mock_graph)
    store.qdrant_store.client = None

    # 1. Fetch episodes
    episodes = await store.get_recent_unconsolidated_episodes(limit=5)
    assert len(episodes) == 3
    assert episodes[0]["role"] == "Raj"
    assert episodes[1]["role"] == "assistant"
    assert episodes[2]["role"] == "Priya"

    # Verify query had role != 'system'
    conn.fetch.assert_called()
    query_str = conn.fetch.call_args[0][0]
    assert "role != 'system'" in query_str


@pytest.mark.asyncio
async def test_subconscious_agent_multi_user_pairing():
    """
    Verify SubconsciousAgent correctly pairs custom roles (non-assistant messages)
    and populates the 'speaker' property.
    """
    mock_mem_store = AsyncMock()

    # Chronological messages: reversed from query order (Raj, then assistant, then Priya)
    mock_episodes = [
        {
            "id": "msg-3",
            "role": "Priya",
            "content": "Actually, I'm heading to the workspace.",
            "timestamp": datetime.now(UTC),
        },
        {
            "id": "msg-2",
            "role": "assistant",
            "content": "Yes, Priya is planning to walk there.",
            "timestamp": datetime.now(UTC),
        },
        {
            "id": "msg-1",
            "role": "Raj",
            "content": "Is Priya going to the park?",
            "timestamp": datetime.now(UTC),
        },
    ]
    mock_mem_store.get_recent_unconsolidated_episodes.return_value = mock_episodes

    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.get_context_snapshot.return_value = {
        "emotion": "neutral",
        "energy": 0.5,
        "fatigue": 0.0,
        "last_user_interaction": 0.0,
    }
    mock_state.check_proactive_eligibility.return_value = False
    mock_state.current_state.last_user_interaction = 0.0

    mock_reflection = AsyncMock()

    agent = SubconsciousAgent(
        graph_db=mock_graph,
        state_service=mock_state,
        memory_store=mock_mem_store,
        reflection_service=mock_reflection,
    )
    agent.state_service.current_state.last_user_interaction = 0.0

    # Mock dynamic config consolidation silence bypass
    with patch.object(Config, "TESTING_CONSOLIDATION_BYPASS_SILENCE", True):
        await agent._on_system_tick({})
        # P1-1: consolidation is dispatched, not awaited inline - await the
        # retained task, still inside the patch context, before asserting.
        await agent._consolidation_task

    # Verify trigger_reflection was called with paired episodes
    mock_reflection.trigger_reflection.assert_called_once()
    paired_episodes = mock_reflection.trigger_reflection.call_args[0][0]

    # msg-1 (Raj) paired with msg-2 (assistant)
    assert len(paired_episodes) == 2

    ep1 = paired_episodes[0]
    assert ep1["event"] == "Is Priya going to the park?"
    assert ep1["speaker"] == "Raj"
    assert ep1["response"] == "Yes, Priya is planning to walk there."

    # msg-3 (Priya) is unpaired (no assistant response after it in history)
    ep2 = paired_episodes[1]
    assert ep2["event"] == "Actually, I'm heading to the workspace."
    assert ep2["speaker"] == "Priya"
    assert ep2["response"] == ""


@pytest.mark.asyncio
async def test_reflection_dynamic_speaker_consolidation():
    """
    Verify ReflectionService._consolidate formats LLM prompt dynamically
    with custom speaker names instead of the static 'User:' prefix.
    """
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = "We talked about park and workspace."
    mock_graph = AsyncMock()
    mock_vector = AsyncMock()

    service = ReflectionService(
        llm_service=mock_llm, graph_store=mock_graph, pg_vector=mock_vector
    )

    episodes = [
        {
            "id": "1",
            "speaker": "Raj",
            "content": "Is Priya going to the park?",
            "response": "Yes.",
            "context": "context_data",
        },
        {
            "id": "2",
            "speaker": "Priya",
            "content": "I'm going to workspace.",
            "response": "",
            "context": "context_data",
        },
    ]

    with patch.object(service, "_extract_json", return_value=[]):
        await service._consolidate(episodes)

    # Speaker labels and dialogue stay separated as untrusted prompt fields.
    mock_llm.generate.assert_called()
    prompts = [call[0][0] for call in mock_llm.generate.call_args_list]

    # Verify the consolidation prompt contains speaker-specific dialogues
    consol_prompt = next(
        p for p in prompts if "Consolidate the following recent interaction" in p
    )
    assert (
        "[RETRIEVED-CONTENT]Raj[/RETRIEVED-CONTENT]: "
        "[RETRIEVED-CONTENT]Is Priya going to the park?[/RETRIEVED-CONTENT]"
        in consol_prompt
    )
    assert (
        "[RETRIEVED-CONTENT]Priya[/RETRIEVED-CONTENT]: "
        "[RETRIEVED-CONTENT]I'm going to workspace.[/RETRIEVED-CONTENT]"
        in consol_prompt
    )
    assert "User:" not in consol_prompt
