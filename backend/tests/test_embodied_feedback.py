import asyncio
import uuid

import pytest

from app.agents.brain_agent import BrainAgent
from app.state import ConversationHistoryStore


@pytest.mark.asyncio
async def test_dialogue_truncation_on_interruption(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    # Initialize ConversationHistoryStore which falls back to our mock asyncpg (SQLite)
    store = ConversationHistoryStore()
    await store.initialize()

    # Start session
    await store.start_session()

    # Log an assistant message
    original_text = "I was planning to buy a coffee, but I forgot my wallet."
    row_id = uuid.uuid4()
    await store.log_message("assistant", original_text, message_id=row_id)

    # Verify the message is logged
    brief = await store.get_last_interaction_brief()
    assert brief == original_text

    # Instantiate BrainAgent with injected dependencies
    agent = BrainAgent(
        ollama_url="http://dummy",
        graph_db=mock_graph_db,
        memory_store=mock_memory_store,
        conversation_store=store,
    )

    # Set agent's state as the turn flow leaves a stored reply: owned by the
    # playing turn and addressed by its history row id (ADR-003).
    agent.last_assistant_response = original_text
    agent._reply_turn_id = agent._active_response_turn_id = "utt-1"
    agent._reply_message_id = row_id

    # Simulate receiving audio.playback.progress
    # Slice at "I was planning to buy a coffee" (length 30)
    progress_data = {
        "utterance_id": "utt-1",
        "character_offset": 30,
        "word_index": 6,
        "completed": False,
    }
    await agent._on_audio_playback_progress(progress_data)
    assert agent.last_audio_progress is not None
    assert agent.last_audio_progress.character_offset == 30

    # Simulate receiving confirmed AUDIO_STOP
    stop_data = {
        "interrupt": True,
        "speculative": False,
        "reason": "confirmed_stop",
        "intent_type": "VOICE_INTERRUPTION",
    }
    await agent._on_audio_stop(stop_data)

    # Verify database has the truncated text
    new_brief = await store.get_last_interaction_brief()
    assert new_brief == "I was planning to buy a coffee"

    # Verify last_audio_progress has been cleared
    assert agent.last_audio_progress is None

    await store.close()


@pytest.mark.asyncio
async def test_dialogue_is_not_rewritten_when_playback_progress_is_unknown(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    """Renamed and inverted from `test_dialogue_truncation_via_estimation_fallback`.

    That test asserted the old behaviour: with no playback progress, estimate
    the spoken length at 15 characters/second and rewrite the stored reply at
    that offset. Here it cut "…buy a coffee, but I forgot my wallet." down to
    "…buy a coffee" purely because 2.0 seconds had passed.

    The estimate has been deliberately removed rather than tuned. The rate was
    invented, real speech rate varies with prosody and pauses, and the
    timestamp it measured against was set on only one of the two streaming
    paths — so the cut point could be derived from an entirely different turn.
    Since the stored message is what memory and the persona prompt read back,
    a wrong cut makes the agent believe it said something it did not.

    The assertion is therefore inverted on purpose: this documents a behaviour
    change, not a weakened test. The accurate path — a real `character_offset`
    from playback progress — is still covered above and still truncates.
    """
    store = ConversationHistoryStore()
    await store.initialize()

    await store.start_session()
    original_text = "I was planning to buy a coffee, but I forgot my wallet."
    await store.log_message("assistant", original_text)

    agent = BrainAgent(
        ollama_url="http://dummy",
        graph_db=mock_graph_db,
        memory_store=mock_memory_store,
        conversation_store=store,
    )

    agent.last_assistant_response = original_text
    assert agent.last_audio_progress is None

    stop_data = {
        "interrupt": True,
        "speculative": False,
        "reason": "confirmed_stop",
        "intent_type": "VOICE_INTERRUPTION",
    }
    await agent._on_audio_stop(stop_data)

    new_brief = await store.get_last_interaction_brief()
    assert new_brief == original_text, (
        "the reply was rewritten despite nothing knowing how much was heard"
    )

    await store.close()


@pytest.mark.asyncio
async def test_cancel_active_generation_waits_for_task_to_fully_unwind(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    """A4 regression: cancelling the active generation task must wait for the
    task to actually stop touching shared turn state, not just request
    cancellation and move on. Otherwise a new turn (or a semantic interrupt)
    can reset last_assistant_response while the old, "cancelled" task is still
    mid-flight and about to write stale data on top of it.
    """
    store = ConversationHistoryStore()
    await store.initialize()
    await store.start_session()

    agent = BrainAgent(
        ollama_url="http://dummy",
        graph_db=mock_graph_db,
        memory_store=mock_memory_store,
        conversation_store=store,
    )

    cleanup_done = asyncio.Event()

    async def slow_old_turn():
        try:
            agent.last_assistant_response = "partial-old-turn-text"
            await asyncio.sleep(10)
        finally:
            # Simulates a still-unwinding _stream_to_speech/_process_chat_input_flow
            # writing turn state after being cancelled.
            agent.last_assistant_response = (
                agent.last_assistant_response or ""
            ) + "-cleanup-write"
            cleanup_done.set()

    task = asyncio.create_task(slow_old_turn())
    agent._active_generation_task = task

    # Let the task actually start and set its initial state.
    await asyncio.sleep(0)
    assert agent.last_assistant_response == "partial-old-turn-text"

    await agent._cancel_active_generation("test cancellation")

    # By the time _cancel_active_generation returns, the old task's cleanup
    # must have already run (not still pending on the event loop).
    assert cleanup_done.is_set()
    assert agent.last_assistant_response == "partial-old-turn-text-cleanup-write"
    assert agent._active_generation_task is None

    await store.close()
