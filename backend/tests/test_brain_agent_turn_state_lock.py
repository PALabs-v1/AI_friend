"""P2-14/M1-A14 -- a cut must write what was heard of *its own* reply,
whatever the other subscription tasks do while it waits on the database.

Before W5 the cut read `last_audio_progress`/`last_assistant_response`,
fields a new turn's flow resets, so it had to hold `_turn_state_lock`
across its DB write to keep that reset out. The reply ledger (ADR-W5 §3)
gives each reply its own text, progress and row id, which no other turn
writes. This test forces the original race -- a new turn beginning while
the cut is suspended inside its real await point, the conversation-store
DB write -- and checks the row still gets the cut reply's heard text.
"""

import asyncio
import uuid

import pytest

from app.agents.brain_agent import BrainAgent, _ReplyLedgerEntry
from app.contracts import AudioPlaybackProgress
from app.state import ConversationHistoryStore


@pytest.mark.asyncio
async def test_a_new_turn_during_a_cuts_db_write_cannot_change_what_it_writes(
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
    progress = AudioPlaybackProgress(
        utterance_id="t1", character_offset=30, word_index=6, completed=False
    )
    # As the turn flow leaves a stored, playing reply.
    agent._active_response_turn_id = "t1"
    agent.last_assistant_response = original_text
    agent.last_audio_progress = progress
    entry = _ReplyLedgerEntry(
        turn_id="t1",
        source="user",
        text=original_text,
        progress=progress,
        message_id=row_id,
        started=True,
        speaking=True,
    )
    agent._reply_ledger["t1"] = entry

    db_write_started = asyncio.Event()
    db_write_may_finish = asyncio.Event()
    real_update = store.rewrite_assistant_message

    async def slow_update(text, **kwargs):
        # The cut's real suspension point: stall it mid-write.
        db_write_started.set()
        await db_write_may_finish.wait()
        return await real_update(text, **kwargs)

    store.rewrite_assistant_message = slow_update

    # The transport's INTERRUPTED at the heard offset resolves the cut.
    cut_task = asyncio.create_task(
        agent._resolve_reply(entry, "TRUNCATED", offset=30, reason="confirmed_command")
    )
    await db_write_started.wait()

    # A new user turn begins while the write is suspended: the same reset
    # and turn start `_process_chat_input_flow` performs.
    await agent._begin_turn("t2")
    async with agent._turn_state_lock:
        agent.last_assistant_response = None
        agent.last_audio_progress = None
    agent._reply_ledger["t2"].text = "A different reply entirely."

    db_write_may_finish.set()
    await cut_task

    assert await store.get_last_interaction_brief() == "I was planning to buy a coffee"
    assert [(r.turn_id, r.status, r.heard_text) for r in agent.reply_resolutions] == [
        ("t1", "TRUNCATED", "I was planning to buy a coffee")
    ]
    assert "t2" in agent._reply_ledger  # the new turn is untouched

    await store.close()
