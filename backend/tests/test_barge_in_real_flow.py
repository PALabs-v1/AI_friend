"""Barge-in through the real turn flow (ADR-003; reviews R2-2, R3-1..R3-4).

Each test drives `BrainAgent._on_chat_input` -> `_begin_turn` -> Stage 2's
`audio.stop` (looped back into `_on_audio_stop`, as NATS delivers the brain's
own publish) with a scripted cognitive core, and checks the one thing that
outlives the turn: what conversation history says the agent said. History is
what memory consolidation and the persona prompt read back, so a wrong row is
a false memory, not a cosmetic bug.

The history double mirrors `ConversationHistoryStore`: rows in insertion
order, `log_message(..., message_id=)` stores the row under the caller's id
and swallows its own failures, and `update_last_assistant_message(...,
message_id=)` rewrites exactly that row or nothing. The same contract is
tested against the real store (SQLite) at the bottom.

"Turn B survived" is checked with a flag the fake pipeline sets after
Stage 2; `_on_chat_input` swallows its flow's CancelledError, so the outer
task's `cancelled()` can never show it.
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
        self._rows: list[list] = []  # [id, role, content]
        self.fail_inserts = False

    @property
    def rows(self):
        return [[role, content] for _, role, content in self._rows]

    async def log_message(self, role, content, message_id=None):
        await asyncio.sleep(0.001)
        if self.fail_inserts and role == "assistant":
            return  # the real store logs and swallows insert failures
        self._rows.append([message_id, role, content])

    async def update_last_assistant_message(
        self, content, message_id=None, expected=None
    ):
        """Every contract the brain has used, so a test run against older
        brain code sees what that code would really have written:
        `message_id` (exact row), `expected` (newest row holding that text,
        b993dd8) and neither (newest assistant row)."""
        await asyncio.sleep(0.001)
        for row in reversed(self._rows):
            if row[1] != "assistant":
                continue
            if message_id is not None and row[0] != message_id:
                continue
            if expected is not None and row[2] != expected:
                continue
            row[2] = content
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
            await asyncio.sleep(0.02)  # the stop is delivered while B still runs
            agent.finished_turns.append(md["turn_id"])
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
        agent.finished_turns.append(md["turn_id"])
        yield {"type": "done"}

    async def proactive(thought_prompt):
        script = scripts[thought_prompt]
        for piece in script["pieces"]:
            yield {"type": "content", "data": piece}
            await asyncio.sleep(script.get("delay", 0))
        yield {"type": "done"}

    agent.finished_turns = []
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
    assert "B" in agent.finished_turns  # the "stop" turn itself was not cancelled


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
    assert "B" in agent.finished_turns  # the "stop" turn itself was not cancelled


@pytest.mark.asyncio
async def test_rewrite_waits_for_the_replys_own_pending_insert():
    history = History()
    gate = asyncio.Event()
    real_log = history.log_message

    async def slow_log(role, content, message_id=None):
        if role == "assistant":
            await gate.wait()
        await real_log(role, content, message_id)

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
async def test_cut_never_touches_an_older_reply_with_the_same_text():
    """R4-1: content-addressed rewrites cut the newest row *holding the same
    text* -- an older identical reply when this one was never stored."""
    history = History()
    agent = _agent(
        history,
        {
            "hi": {"pieces": ["Sure thing."]},
            "again": {"pieces": ["Sure thing.", " And one more thing"], "delay": 0.1},
        },
    )
    turn = await _say(agent, "hi", "Z")
    await turn
    await _settle(agent)
    turn_a = await _say(agent, "again", "A")
    await asyncio.sleep(0.05)  # "Sure thing." streamed, A not stored yet
    await _progress(agent, "A", 5)
    await _startle(agent, "A")
    await asyncio.gather(turn_a, return_exceptions=True)
    await _settle(agent)
    assert history.rows == [
        ["User", "hi"],
        ["assistant", "Sure thing."],
        ["User", "again"],
    ]


@pytest.mark.asyncio
async def test_superseded_reply_progress_never_becomes_the_new_turns_progress():
    history = History()
    agent = _agent(
        history, {"tell me": {"pieces": [REPLY_A]}, "hi": {"pieces": ["hey"]}}
    )
    await _finished_reply_a(agent)
    turn_b = await _say(agent, "hi", "B")
    while agent._active_response_turn_id != "B":
        await asyncio.sleep(0)
    await _progress(agent, "A", 30)
    live = agent.last_audio_progress
    assert live is None or live.character_offset != 30  # A's frame stayed out
    assert agent._superseded_reply.progress.character_offset == 30
    await turn_b
    await _settle(agent)


@pytest.mark.asyncio
async def test_superseded_stop_is_honoured_once():
    history = History()
    agent = _agent(history, {"tell me": {"pieces": [REPLY_A]}, "stop": {"stop": True}})
    await _finished_reply_a(agent)
    turn_b = await _say(agent, "stop", "B")
    await _progress(agent, "A", 22)
    await turn_b
    await _settle(agent)
    await _progress(agent, "A", 30)
    await agent._on_audio_stop(
        {
            "interrupt": True,
            "speculative": False,
            "reason": "confirmed_command",
            "turn_id": "A",
        }
    )
    await _settle(agent)
    assert history.rows[1] == ["assistant", REPLY_A[:22].strip()]
    assert [o[1] for o in _outcomes(agent)] == ["TRUNCATED"]
    # The second stop was not accepted at all: one interruption felt, once.
    agent.cognitive_core.state.release_adrenaline.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_genuinely_stale_command_stop_neither_cancels_nor_cuts():
    history = History()
    agent = _agent(
        history,
        {"tell me": {"pieces": [REPLY_A]}, "hi": {"pieces": ["hey"], "delay": 0.05}},
    )
    await _finished_reply_a(agent)
    turn_b = await _say(agent, "hi", "B")
    while agent._active_response_turn_id != "B":
        await asyncio.sleep(0)
    await agent._on_audio_stop(
        {
            "interrupt": True,
            "speculative": False,
            "reason": "confirmed_command",
            "turn_id": "turn-from-yesterday",
        }
    )
    await turn_b
    await _settle(agent)
    assert "B" in agent.finished_turns
    assert ["assistant", REPLY_A] in history.rows


@pytest.mark.asyncio
async def test_a_finishing_proactive_turn_does_not_re_record_the_user_reply():
    """R4-3 (pre-existing sibling of R3-1): a proactive turn's completed
    playback re-recorded the previous user reply's intent as COMPLETED."""
    history = History()
    agent = _agent(
        history, {"tell me": {"pieces": [REPLY_A]}, "think": {"pieces": ["hm"]}}
    )
    await _finished_reply_a(agent)
    await _progress(agent, "A", len(REPLY_A), completed=True)
    proactive = await _say(agent, "think", "S", subconscious=True)
    await proactive
    await _settle(agent)
    await _progress(agent, "S", 2, completed=True)
    assert [o[:2] for o in _outcomes(agent)] == [("A", "COMPLETED")]


@pytest.mark.asyncio
async def test_replacing_a_later_turn_does_not_cancel_a_finished_reply():
    """R4-4 (pre-existing): replacing a proactive turn (or any turn started
    after the user reply finished generating) recorded CANCELLED for that
    finished reply's intent."""
    history = History()
    agent = _agent(
        history,
        {
            "tell me": {"pieces": [REPLY_A]},
            "think": {"pieces": ["one", " two", " three"], "delay": 0.1},
            "hi": {"pieces": ["hey"]},
        },
    )
    await _finished_reply_a(agent)
    await _progress(agent, "A", len(REPLY_A), completed=True)
    await _say(agent, "think", "S", subconscious=True)
    await asyncio.sleep(0.05)
    assert not agent._active_generation_task.done()  # C really replaces S
    turn_c = await _say(agent, "hi", "C")
    await turn_c
    await _settle(agent)
    assert ("A", "CANCELLED") not in [o[:2] for o in _outcomes(agent)]


@pytest.mark.asyncio
async def test_a_stuck_insert_bounds_the_wait_and_leaves_the_reply_uncut(monkeypatch):
    """The cut waits for the reply's insert under `_turn_state_lock`; the
    store's pool has no command timeout, so the wait must be bounded."""
    from app.agents import brain_agent

    monkeypatch.setattr(brain_agent, "REPLY_INSERT_WAIT_S", 0.05)
    history = History()
    never = asyncio.Event()
    real_log = history.log_message

    async def stuck_log(role, content, message_id=None):
        if role == "assistant":
            await never.wait()
        await real_log(role, content, message_id)

    history.log_message = stuck_log
    agent = _agent(history, {"tell me": {"pieces": [REPLY_A]}})
    turn = await _say(agent, "tell me", "A")
    await turn
    await _progress(agent, "A", 10)
    await asyncio.wait_for(_startle(agent, "A"), timeout=1.0)
    assert not agent._turn_state_lock.locked()
    never.set()
    await _settle(agent)
    assert history.rows[-1] == ["assistant", REPLY_A]


@pytest.mark.asyncio
async def test_real_store_rewrites_exactly_the_addressed_row():
    """The contract the double mirrors, on the real `ConversationHistoryStore`
    (SQLite fallback): by id, identical text elsewhere untouched, a missing
    id writes nothing."""
    import uuid

    from app.state.conversation_store import ConversationHistoryStore

    store = ConversationHistoryStore()
    store.dsn = "sqlite:///:memory:"
    await store.initialize()
    try:
        await store.start_session()

        async def by_id():
            async with store.pool.acquire() as conn:
                rows = await conn.fetch("SELECT id, content FROM messages")
            return {str(r["id"]): r["content"] for r in rows}

        older, mine, newer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await store.log_message("assistant", "Sure thing.", message_id=older)
        await store.log_message("assistant", "Sure thing.", message_id=mine)
        await store.log_message("assistant", "a newer reply", message_id=newer)
        await store.update_last_assistant_message("Sure", message_id=mine)
        expected = {
            str(older): "Sure thing.",  # identical text, not addressed
            str(mine): "Sure",
            str(newer): "a newer reply",
        }
        assert await by_id() == expected

        await store.update_last_assistant_message("cut", message_id=uuid.uuid4())
        assert await by_id() == expected
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_repeated_completed_frame_records_completed_once():
    history = History()
    agent = _agent(history, {"tell me": {"pieces": [REPLY_A]}})
    await _finished_reply_a(agent)
    for _ in range(2):
        await _progress(agent, "A", len(REPLY_A), completed=True)
    assert _outcomes(agent) == [("A", "COMPLETED", REPLY_A)]


@pytest.mark.asyncio
async def test_replacing_a_proactive_turn_while_the_finished_reply_still_plays():
    """R4-4 with the reply generated but not yet played out (no completed
    frame, so it is unresolved): only "is it still generating" can tell
    that replacing the proactive turn did not cancel it."""
    history = History()
    agent = _agent(
        history,
        {
            "tell me": {"pieces": [REPLY_A]},
            "think": {"pieces": ["one", " two", " three"], "delay": 0.1},
            "hi": {"pieces": ["hey"]},
        },
    )
    await _finished_reply_a(agent)  # generated and stored, still playing
    await _say(agent, "think", "S", subconscious=True)
    await asyncio.sleep(0.05)
    assert not agent._active_generation_task.done()
    turn_c = await _say(agent, "hi", "C")
    await turn_c
    await _settle(agent)
    assert ("A", "CANCELLED") not in [o[:2] for o in _outcomes(agent)]


@pytest.mark.asyncio
async def test_a_reply_cut_mid_generation_is_not_cancelled_again_later():
    """R5: a startle cancels and cuts A mid-generation; a proactive turn then
    runs and is replaced by the next user turn. A's generation ended at the
    cut, so the replacement must not record A CANCELLED on top."""
    history = History()
    agent = _agent(
        history,
        {
            "tell me": {"pieces": PIECES_A, "delay": 0.1},
            "think": {"pieces": ["one", " two", " three"], "delay": 0.1},
            "hi": {"pieces": ["hey"]},
        },
    )
    turn_a = await _say(agent, "tell me", "A")
    await asyncio.sleep(0.15)
    await _progress(agent, "A", 14)
    await _startle(agent, "A")
    await asyncio.gather(turn_a, return_exceptions=True)
    await _settle(agent)
    await _say(agent, "think", "S", subconscious=True)
    await asyncio.sleep(0.05)
    assert not agent._active_generation_task.done()
    turn_c = await _say(agent, "hi", "C")
    await turn_c
    await _settle(agent)
    assert [o[:2] for o in _outcomes(agent) if o[0] == "A"] == [("A", "TRUNCATED")]
