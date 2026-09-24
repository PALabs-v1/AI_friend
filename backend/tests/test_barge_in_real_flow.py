"""Barge-in through the real turn flow (ADR-003; reviews R2-2, R3-1..R3-4).

Each test drives `BrainAgent._on_chat_input` -> `_begin_turn` -> Stage 2's
`audio.stop` (looped back into `_on_audio_stop`, as NATS delivers the brain's
own publish) with a scripted cognitive core, and checks the one thing that
outlives the turn: what conversation history says the agent said. History is
what memory consolidation and the persona prompt read back, so a wrong row is
a false memory, not a cosmetic bug.

The history double behaves like `ConversationHistoryStore`: rows in insertion
order, `log_message` swallows its own failures, and
`update_last_assistant_message(..., expected=...)` rewrites the newest
assistant row only if it still holds `expected`. The guard itself is tested
against the real store at the bottom.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.brain_agent import BrainAgent
from app.cognitive.action_intent import build_action_intent
from app.config import Config

REPLY_A = "I went to the market and bought apples and pears"
PIECES_A = ["I went to the market ", "and bought apples ", "and pears and more"]


class History:
    def __init__(self):
        self.rows: list[list[str]] = []  # [role, content]
        self.fail_inserts = False

    async def log_message(self, role, content):
        await asyncio.sleep(0.001)
        if self.fail_inserts and role == "assistant":
            return  # the real store logs and swallows insert failures
        self.rows.append([role, content])

    async def update_last_assistant_message(self, content, expected=None):
        await asyncio.sleep(0.001)
        for row in reversed(self.rows):
            if row[0] == "assistant":
                if expected is None or row[1] == expected:
                    row[1] = content
                return


class _Runtime:
    def calculate_pacing_parameters(self, _snapshot):
        return {"silence_duration_ms": 0.0}

    def monitor_stream_and_fill(self, generator, **_):
        return generator


@pytest.fixture(autouse=True)
def _no_onset_grace(monkeypatch):
    monkeypatch.setattr(Config, "BARGE_IN_ONSET_GRACE_S", 0.0)


def _agent(history, scripts):
    agent = BrainAgent(
        ollama_url="http://dummy",
        graph_db=MagicMock(),
        memory_store=MagicMock(),
        conversation_store=history,
    )

    async def publish(subject, data):
        if subject == "audio.stop":

            async def deliver():
                await asyncio.sleep(0.002)
                await agent._on_audio_stop(data)

            agent.spawn(deliver())

    agent.publish = publish
    agent.set_state = AsyncMock()
    agent.conversational_runtime = _Runtime()
    core = MagicMock()
    core.state.last_speculative_intent = None
    core.state.get_context_snapshot = MagicMock(return_value={})
    core.state.release_adrenaline = AsyncMock()
    core.workspace_store.get_snapshot = AsyncMock(return_value=None)

    async def process_event(raw_event, **_):
        md = raw_event["metadata"]
        script = scripts[raw_event["content"]]
        if script.get("stop"):  # Stage 2 confirmed the stop: pipeline ends here
            await publish(
                "audio.stop",
                {
                    "interrupt": True,
                    "speculative": False,
                    "reason": "confirmed_command",
                    "turn_id": md.get("interrupted_turn_id") or md["turn_id"],
                },
            )
            return
        intent = build_action_intent(
            turn_id=md["turn_id"],
            workspace_epoch=0,
            workspace_revision=0,
            kind="SPEAK",
            behavior_decision={},
        )
        yield {"type": "action_intent", "data": intent.model_dump()}
        for piece in script["pieces"]:
            yield {"type": "content", "data": piece}
            await asyncio.sleep(script.get("delay", 0))
        yield {"type": "done"}

    async def proactive(thought_prompt):
        for piece in scripts[thought_prompt]["pieces"]:
            yield {"type": "content", "data": piece}
        yield {"type": "done"}

    core.process_event = process_event
    core.generate_proactive_response = proactive
    agent.cognitive_core = core
    return agent


async def _say(agent, text, turn_id, *, subconscious=False):
    msg = {
        "text": text,
        "turn_id": turn_id,
        "utterance_id": turn_id,
        "metadata": {"source": "subconscious" if subconscious else "whisper"},
    }
    return asyncio.create_task(agent._on_chat_input(msg))


async def _progress(agent, turn_id, offset, completed=False):
    await agent._on_audio_playback_progress(
        {
            "utterance_id": turn_id,
            "character_offset": offset,
            "word_index": 1,
            "completed": completed,
        }
    )


async def _startle(agent, turn_id):
    await agent._on_audio_stop(
        {
            "interrupt": True,
            "speculative": False,
            "reason": "facial_reflex_startle",
            "turn_id": turn_id,
        }
    )


async def _settle(agent, seconds=0.05):
    await asyncio.sleep(seconds)
    while agent._background_tasks:
        await asyncio.gather(*list(agent._background_tasks), return_exceptions=True)
    await asyncio.sleep(0.01)


def _outcomes(agent):
    return [
        (r.turn_id, r.status, r.actual_delivered_text) for r in agent._outcome_history
    ]


async def _finished_reply_a(agent):
    turn = await _say(agent, "tell me", "A")
    await turn
    await _settle(agent)
    await _progress(agent, "A", 6)


@pytest.mark.asyncio
async def test_stop_while_a_finished_reply_plays_cuts_it_to_what_was_heard():
    """R2-2: the stop is addressed to the playing reply A, arrives after the
    "stop" turn B has reset the live reply state, and must cut A (not B)."""
    history = History()
    agent = _agent(history, {"tell me": {"pieces": [REPLY_A]}, "stop": {"stop": True}})
    await _finished_reply_a(agent)

    turn_b = await _say(agent, "stop", "B")
    await _progress(agent, "A", 22)  # A keeps playing while B runs Stages 1-2
    await turn_b
    await _settle(agent)

    assert history.rows == [
        ["User", "tell me"],
        ["assistant", REPLY_A[:22].strip()],
        ["User", "stop"],
    ]
    assert _outcomes(agent) == [("A", "TRUNCATED", REPLY_A[:22].strip())]
    assert not turn_b.cancelled()


@pytest.mark.asyncio
async def test_stop_mid_generation_never_writes_after_the_users_stop():
    """R3-2: A was cancelled before it was stored. Its heard part used to be
    appended after the user's "stop", reading as the answer to "stop"."""
    history = History()
    agent = _agent(
        history,
        {"tell me": {"pieces": PIECES_A, "delay": 0.1}, "stop": {"stop": True}},
    )
    await _say(agent, "tell me", "A")
    await asyncio.sleep(0.15)  # two pieces streamed
    await _progress(agent, "A", 14)
    turn_b = await _say(agent, "stop", "B")
    await turn_b
    await _settle(agent, 0.2)

    assert history.rows == [["User", "tell me"], ["User", "stop"]]
    assert _outcomes(agent) == [("A", "CANCELLED", None)]  # exactly one record


@pytest.mark.asyncio
async def test_stop_during_a_proactive_utterance_leaves_every_reply_intact():
    """R3-1: a subconscious turn never owns `last_assistant_response`, which
    still holds the last user-turn reply. The stop for the subconscious turn
    used to cut *that* text at the subconscious turn's playback offset and
    write it over the subconscious turn's row."""
    history = History()
    thought = "By the way, I remembered your sister's birthday is soon"
    agent = _agent(
        history,
        {
            "tell me": {"pieces": [REPLY_A]},
            "think": {"pieces": [thought]},
            "stop": {"stop": True},
        },
    )
    await _finished_reply_a(agent)
    await _progress(agent, "A", len(REPLY_A), completed=True)
    proactive = await _say(agent, "think", "S", subconscious=True)
    await proactive
    await _settle(agent)
    await _progress(agent, "S", 19)
    turn_u = await _say(agent, "stop", "U")
    await turn_u
    await _settle(agent)

    assert history.rows == [
        ["User", "tell me"],
        ["assistant", REPLY_A],
        ["assistant", thought],
        ["User", "stop"],
    ]
    assert [o[1] for o in _outcomes(agent)] == ["COMPLETED"]  # no second record for A


@pytest.mark.asyncio
async def test_repeated_stops_resolve_a_reply_once():
    """R3-3: a facial startle publishes a stop for the active turn on every
    startle, even while idle. Each one used to append (or cut and record)
    the same reply again."""
    history = History()
    agent = _agent(history, {"tell me": {"pieces": [REPLY_A]}, "stop": {"stop": True}})
    await _finished_reply_a(agent)
    await _progress(agent, "A", 14)
    for _ in range(4):
        await _startle(agent, "A")
    turn_b = await _say(agent, "stop", "B")
    await turn_b
    await _settle(agent)

    assert history.rows == [
        ["User", "tell me"],
        ["assistant", REPLY_A[:14].strip()],
        ["User", "stop"],
    ]
    assert _outcomes(agent) == [("A", "TRUNCATED", REPLY_A[:14].strip())]


@pytest.mark.asyncio
async def test_failed_insert_never_lets_the_cut_overwrite_the_previous_reply():
    """R3-4 (and R2-2b): when this reply has no row (its insert failed inside
    the store, which swallows the error, or it was cancelled first), "update
    the newest assistant row" used to rewrite the previous turn's reply."""
    history = History()
    agent = _agent(
        history,
        {
            "hi": {"pieces": ["Hello friend, how was your day today"]},
            "tell me": {"pieces": [REPLY_A]},
            "stop": {"stop": True},
        },
    )
    turn = await _say(agent, "hi", "Z")
    await turn
    await _settle(agent)
    history.fail_inserts = True
    await _finished_reply_a(agent)
    history.fail_inserts = False
    turn_b = await _say(agent, "stop", "B")
    await _progress(agent, "A", 22)
    await turn_b
    await _settle(agent)

    assert ["assistant", "Hello friend, how was your day today"] in history.rows


@pytest.mark.asyncio
async def test_stop_for_the_active_turn_still_cancels_generation_and_cuts():
    history = History()
    agent = _agent(history, {"tell me": {"pieces": PIECES_A, "delay": 0.1}})
    turn_a = await _say(agent, "tell me", "A")
    await asyncio.sleep(0.15)
    await _progress(agent, "A", 14)
    await _startle(agent, "A")
    await asyncio.gather(turn_a, return_exceptions=True)
    await _settle(agent)
    assert agent._active_generation_task is None
    assert history.rows == [["User", "tell me"]]  # never stored, nothing overwritten


@pytest.mark.asyncio
async def test_non_command_stop_for_the_superseded_reply_is_stale():
    history = History()
    agent = _agent(
        history, {"tell me": {"pieces": [REPLY_A]}, "hi": {"pieces": ["hey"]}}
    )
    await _finished_reply_a(agent)
    turn_b = await _say(agent, "hi", "B")
    while agent._active_response_turn_id != "B":
        await asyncio.sleep(0)
    await _startle(agent, "A")  # published for A, delivered after B took over
    await turn_b
    await _settle(agent)
    assert ["assistant", REPLY_A] in history.rows
    assert not turn_b.cancelled()


@pytest.mark.asyncio
async def test_rewrite_waits_for_the_replys_own_pending_insert():
    history = History()
    gate = asyncio.Event()
    real_log = history.log_message

    async def slow_log(role, content):
        if role == "assistant":
            await gate.wait()
        await real_log(role, content)

    history.log_message = slow_log
    agent = _agent(history, {"tell me": {"pieces": [REPLY_A]}})
    turn = await _say(agent, "tell me", "A")
    await turn  # the insert is spawned, still gated
    await _progress(agent, "A", 10)
    stop = asyncio.create_task(_startle(agent, "A"))
    await asyncio.sleep(0.01)
    assert not stop.done()  # waiting for the insert, not racing ahead of it
    gate.set()
    await stop
    await _settle(agent)
    assert history.rows[-1] == ["assistant", REPLY_A[:10].strip()]


@pytest.mark.asyncio
async def test_store_rewrites_only_the_row_that_still_holds_the_reply():
    """The guard in the real `ConversationHistoryStore` (SQLite fallback)."""
    from app.state.conversation_store import ConversationHistoryStore

    store = ConversationHistoryStore()
    store.dsn = "sqlite:///:memory:"
    await store.initialize()
    try:
        await store.start_session()

        async def contents():
            async with store.pool.acquire() as conn:
                rows = await conn.fetch("SELECT content FROM messages")
            return sorted(r["content"] for r in rows)

        await store.log_message("assistant", "the previous reply")
        # This reply was never stored: no row holds it, nothing is rewritten.
        await store.update_last_assistant_message("cut", expected=REPLY_A)
        assert await contents() == ["the previous reply"]

        await store.log_message("assistant", REPLY_A)
        await store.log_message("assistant", "a newer reply")
        await store.update_last_assistant_message("I went to", expected=REPLY_A)
        assert await contents() == ["I went to", "a newer reply", "the previous reply"]
    finally:
        await store.close()
