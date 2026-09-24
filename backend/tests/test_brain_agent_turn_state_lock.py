"""P2-14/M1-A14 -- `last_audio_progress`/`last_assistant_response` are
written from three independent NATS subscription tasks (chat.input's turn
flow, audio.playback.progress's tracker, audio.stop's truncation handler)
and read-then-written together by truncation. `_generation_lock` only
guards which task owns `_active_generation_task`; it never protected this
data. `_turn_state_lock` now does.

This test forces the exact race the finding described: a new turn's reset
(the same unguarded write `_process_chat_input_flow` used to do) landing
while `_truncate_interrupted_reply` is mid-flight, inside its own real
await point (the conversation-store DB write) -- not a contrived stall,
the code's actual suspension point.
"""

import asyncio
import uuid

import pytest

from app.agents.brain_agent import BrainAgent
from app.state import ConversationHistoryStore


@pytest.mark.asyncio
async def test_turn_state_lock_blocks_a_concurrent_reset_during_truncation(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    store = ConversationHistoryStore()
    await store.initialize()
    await store.start_session()

    original_text = "I was planning to buy a coffee, but I forgot my wallet."
    row_id = uuid.uuid4()
    await store.log_message("assistant", original_text, message_id=row_id)

    agent = BrainAgent(
        ollama_url="http://dummy",
        graph_db=mock_graph_db,
        memory_store=mock_memory_store,
        conversation_store=store,
    )
    agent.last_assistant_response = original_text
    # As `_process_chat_input_flow` leaves a stored reply: owned by the
    # active turn and addressed by its row id.
    agent._reply_turn_id = agent._active_response_turn_id = "t1"
    agent._reply_message_id = row_id
    agent.last_audio_progress = None  # forces the "no progress" branch below

    db_write_started = asyncio.Event()
    db_write_may_finish = asyncio.Event()
    real_update = store.update_last_assistant_message

    async def slow_update(text, **kwargs):
        # _truncate_interrupted_reply calls this from inside its locked
        # critical section on the progress-known branch. Stalling here
        # forces a real suspension point mid-lock, the exact window the
        # finding described.
        db_write_started.set()
        await db_write_may_finish.wait()
        return await real_update(text, **kwargs)

    store.update_last_assistant_message = slow_update

    # Progress-known branch: the one that performs the awaited DB write.
    class _Progress:
        completed = False
        character_offset = 30

    agent.last_audio_progress = _Progress()

    reset_started = asyncio.Event()
    reset_finished = asyncio.Event()

    async def concurrent_new_turn_reset():
        # Mirrors _process_chat_input_flow's reset of the same two fields.
        reset_started.set()
        async with agent._turn_state_lock:
            agent.last_assistant_response = None
            agent.last_audio_progress = None
        reset_finished.set()

    truncate_task = asyncio.create_task(agent._truncate_interrupted_reply())
    await db_write_started.wait()  # truncation is now holding the lock, mid-DB-write

    reset_task = asyncio.create_task(concurrent_new_turn_reset())
    await reset_started.wait()
    # Give the reset task every chance to run if nothing were blocking it.
    for _ in range(5):
        await asyncio.sleep(0)

    assert agent.last_assistant_response == original_text, (
        "a concurrent turn reset must not be able to clear "
        "last_assistant_response while truncation is still mid-flight "
        "inside its own critical section"
    )
    assert not reset_finished.is_set(), (
        "the reset task must be blocked on _turn_state_lock until "
        "truncation releases it, not racing ahead"
    )

    db_write_may_finish.set()
    await truncate_task
    await reset_task

    assert reset_finished.is_set()
    assert agent.last_assistant_response is None, (
        "the reset must finally apply once truncation's critical section is done"
    )

    new_brief = await store.get_last_interaction_brief()
    assert new_brief == "I was planning to buy a coffee", (
        "truncation must have used the ORIGINAL text, unaffected by the "
        "concurrent reset that was blocked out"
    )

    await store.close()
