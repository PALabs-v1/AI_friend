"""Focused W5 regressions for the six frozen section-1 defects."""

import asyncio
import time

import pytest
from test_barge_in_real_flow import REPLY_A, History, _agent, _progress, _settle

from app.config import Config
from app.contracts import Topics


@pytest.fixture(autouse=True)
def _fast_w5_timers(monkeypatch):
    monkeypatch.setattr(Config, "BARGE_IN_ONSET_GRACE_S", 0.0)
    monkeypatch.setattr(Config, "PROACTIVE_GRACE_WINDOW_S", 0.01)
    monkeypatch.setattr(Config, "REPLY_TERMINAL_WAIT_S", 0.02)
    monkeypatch.setattr(Config, "USER_MID_UTTERANCE_TIMEOUT_S", 0.03)


async def _accept(agent, text, turn_id, *, subconscious=False, importance=None):
    await agent._on_chat_input(
        {
            "text": text,
            "turn_id": turn_id,
            "utterance_id": turn_id,
            "metadata": {
                "source": "subconscious" if subconscious else "whisper",
                "importance": importance,
                "category": "self_directed" if subconscious else None,
            },
        }
    )


async def _finish_generation(agent):
    task = agent._active_generation_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await _settle(agent)


def _resolution(agent, turn_id):
    return next(item for item in agent.reply_resolutions if item.turn_id == turn_id)


@pytest.mark.asyncio
async def test_f013_ordinary_user_speech_cuts_its_own_history_and_records_outcome():
    history = History()
    agent = _agent(history, {"answer": {"pieces": [REPLY_A]}})
    await _accept(agent, "answer", "reply-a")
    await _finish_generation(agent)
    await _progress(agent, "reply-a", 14)

    await _accept(agent, "actually", "reply-b")
    await _finish_generation(agent)

    cut = _resolution(agent, "reply-a")
    assert cut.status == "TRUNCATED"
    assert cut.heard_text == REPLY_A[:14].strip()
    assert history.rows[1] == ["assistant", cut.heard_text]
    outcomes = agent.get_outcome_history("reply-a")
    assert len(outcomes) == 1 and outcomes[0].status == "TRUNCATED"


@pytest.mark.asyncio
async def test_v3_chat_handler_returns_while_the_turn_generator_is_blocked():
    agent = _agent(History(), {"slow": {"pieces": ["one two three"], "think": 0.2}})
    publish = agent.publish
    published = []

    async def capture(subject, payload):
        published.append((time.perf_counter(), subject, payload))
        await publish(subject, payload)

    agent.publish = capture
    started = time.perf_counter()
    await _accept(agent, "slow", "slow-a")
    handler_ms = (time.perf_counter() - started) * 1000
    task = agent._active_generation_task
    assert handler_ms < 50
    assert task is not None and not task.done()
    await task
    first_output_at = next(
        at
        for at, subject, payload in published
        if subject == Topics.CHAT_OUTPUT and payload.get("turn_id") == "slow-a"
    )
    # The scripted generator contributes a known 200ms delay; pacing is zero.
    # Subtracting that delay leaves BrainAgent scheduling/handler overhead.
    assert (first_output_at - started) * 1000 - 200 < 50


@pytest.mark.asyncio
async def test_supersession_ledger_keeps_two_started_replies_until_their_terminals():
    agent = _agent(
        History(),
        {
            "first": {"pieces": [REPLY_A]},
            "second": {"pieces": ["Second reply here"]},
            "third": {"pieces": ["Third reply here"]},
        },
    )
    await _accept(agent, "first", "ledger-a")
    await _finish_generation(agent)
    await _progress(agent, "ledger-a", 12)
    await _accept(agent, "second", "ledger-b")
    await _finish_generation(agent)
    await _progress(agent, "ledger-b", 8)
    await _accept(agent, "third", "ledger-c")
    await _finish_generation(agent)

    assert {item.turn_id for item in agent.reply_resolutions} >= {
        "ledger-a",
        "ledger-b",
    }
    assert (
        len([item for item in agent.reply_resolutions if item.turn_id == "ledger-a"])
        == 1
    )
    assert (
        len([item for item in agent.reply_resolutions if item.turn_id == "ledger-b"])
        == 1
    )
    assert agent.reply_ledger_size() <= 32


@pytest.mark.asyncio
async def test_proactive_reply_is_stored_with_an_addressable_row_and_cut_by_partial():
    thought = "I remembered your appointment is tomorrow morning"
    history = History()
    agent = _agent(history, {"thought": {"pieces": [thought]}})
    await _accept(agent, "thought", "proactive-a", subconscious=True, importance=0.5)
    await _finish_generation(agent)
    await _progress(agent, "proactive-a", 18)

    await agent._on_user_speech_partial({"text": "wait", "utterance_id": "partial-a"})
    await agent._on_audio_playback_lifecycle(
        {
            "utterance_id": "proactive-a",
            "turn_id": "proactive-a",
            "seq": 0,
            "state": "INTERRUPTED",
            "words_played": 2,
            "words_streamed": 8,
            "heard_offset": 18,
            "streamed_offset": len(thought),
        }
    )

    cut = _resolution(agent, "proactive-a")
    assert cut.source == "proactive"
    assert history.rows[-1] == ["assistant", cut.heard_text]
    assert cut.heard_text == thought[:18].strip()
    assert agent.get_outcome_history("proactive-a") == []


@pytest.mark.asyncio
async def test_self_thought_interrupt_requires_importance_and_no_recent_partial():
    agent = _agent(
        History(),
        {"answer": {"pieces": [REPLY_A]}, "thought": {"pieces": ["urgent thought"]}},
    )
    await _accept(agent, "answer", "user-a")
    await asyncio.sleep(0.01)
    active_task = agent._active_generation_task
    await _accept(agent, "thought", "low-thought", subconscious=True, importance=0.5)
    assert "low-thought" in agent.declined_proactive_inputs
    assert agent._active_generation_task is active_task
    assert not any(
        payload.get("reason") == "self_thought_interrupt"
        for _, payload in agent.published
        if isinstance(payload, dict)
    )

    await agent._on_user_speech_partial(
        {"text": "one moment", "utterance_id": "partial-user"}
    )
    await _accept(
        agent,
        "thought",
        "high-thought-mid-utterance",
        subconscious=True,
        importance=0.99,
    )
    assert "high-thought-mid-utterance" in agent.declined_proactive_inputs

    agent._last_user_partial_at = None
    await _accept(
        agent,
        "thought",
        "high-thought",
        subconscious=True,
        importance=Config.SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE,
    )
    assert any(
        payload.get("reason") == "self_thought_interrupt"
        for _, payload in agent.published
        if isinstance(payload, dict)
    )


@pytest.mark.asyncio
async def test_self_thought_interrupt_before_the_first_chunk_still_publishes_its_stop():
    # Machine B found it: `_cut_reply` publishes only for a reply that has
    # started, so a significant thought that interrupted a user reply still
    # generating cut it silently. ADR-W5 §5 says the interrupt publishes its
    # scoped stop; a filler may already be playing under that turn.
    agent = _agent(
        History(),
        {
            "answer": {"pieces": [REPLY_A], "think": 1.0},
            "thought": {"pieces": ["urgent thought"]},
        },
    )
    await _accept(agent, "answer", "user-a")
    await asyncio.sleep(0.01)
    assert not agent._reply_ledger["user-a"].started

    await _accept(
        agent,
        "thought",
        "high-thought",
        subconscious=True,
        importance=Config.SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE,
    )

    stops = [
        payload
        for subject, payload in agent.published
        if subject == "audio.stop" and payload.get("reason") == "self_thought_interrupt"
    ]
    assert [stop.get("turn_id") for stop in stops] == ["user-a"]
    assert _resolution(agent, "user-a").status == "CANCELLED"
    assert [
        str(getattr(record.status, "value", record.status))
        for record in agent.get_outcome_history("user-a")
    ] == ["CANCELLED"]
    await _finish_generation(agent)


@pytest.mark.asyncio
async def test_unscoped_confirmed_stop_cuts_the_active_reply_through_the_ledger():
    # Machine B found it: a confirmed stop naming no turn missed the ledger
    # (looked up under "") and fell through to the pre-W5 truncation, which
    # wrote a TRUNCATED outcome and history row on its own; the ledger reply
    # then resolved again on the transport's terminal. Two outcomes for one
    # reply (I2). An unscoped confirmed stop is addressed to the active turn.
    history = History()
    agent = _agent(history, {"answer": {"pieces": [REPLY_A]}})
    await _accept(agent, "answer", "reply-a")
    await _finish_generation(agent)
    await _progress(agent, "reply-a", 14)

    await agent._on_audio_stop(
        {"interrupt": True, "speculative": False, "reason": "confirmed_command"}
    )
    await agent._on_audio_playback_lifecycle(
        {
            "utterance_id": "reply-a",
            "turn_id": "reply-a",
            "seq": 5,
            "state": "INTERRUPTED",
            "words_played": 3,
            "words_streamed": 10,
            "heard_offset": 14,
            "streamed_offset": len(REPLY_A),
        }
    )
    await _settle(agent)

    cut = _resolution(agent, "reply-a")
    assert cut.status == "TRUNCATED"
    assert cut.heard_text == REPLY_A[:14].strip()
    outcomes = agent.get_outcome_history("reply-a")
    assert [str(getattr(o.status, "value", o.status)) for o in outcomes] == [
        "TRUNCATED"
    ]
    assert history.rows[-1] == ["assistant", cut.heard_text]


@pytest.mark.asyncio
async def test_high_proactive_partial_gets_grace_but_low_proactive_cedes_now():
    high_agent = _agent(History(), {"high": {"pieces": ["A valuable thought"]}})
    await _accept(
        high_agent,
        "high",
        "high-a",
        subconscious=True,
        importance=Config.PROACTIVE_GRACE_MIN_IMPORTANCE,
    )
    await _finish_generation(high_agent)
    started = time.monotonic()
    await high_agent._on_user_speech_partial({"text": "wait", "utterance_id": "p-high"})
    assert not any(
        payload.get("reason") == "proactive_grace_expired"
        for _, payload in high_agent.published
        if isinstance(payload, dict)
    )
    await asyncio.sleep(Config.PROACTIVE_GRACE_WINDOW_S + 0.005)
    assert any(
        payload.get("reason") == "proactive_grace_expired"
        for _, payload in high_agent.published
        if isinstance(payload, dict)
    )
    assert time.monotonic() - started <= Config.PROACTIVE_GRACE_WINDOW_S + 0.05

    low_agent = _agent(History(), {"low": {"pieces": ["A small thought"]}})
    await _accept(low_agent, "low", "low-a", subconscious=True, importance=0.4)
    await _finish_generation(low_agent)
    await low_agent._on_user_speech_partial({"text": "wait", "utterance_id": "p-low"})
    assert any(
        payload.get("reason") == "proactive_ceded"
        for _, payload in low_agent.published
        if isinstance(payload, dict)
    )


@pytest.mark.asyncio
async def test_flush_preserves_dr029_self_correction_generation():
    agent = _agent(History(), {"answer": {"pieces": [REPLY_A], "think": 0.2}})
    await _accept(agent, "answer", "flush-a")
    task = agent._active_generation_task
    await agent._on_audio_stop(
        {
            "interrupt": True,
            "flush": True,
            "speculative": False,
            "reason": "flush",
            "turn_id": "flush-a",
        }
    )
    assert agent._active_generation_task is task and not task.done()
    assert not agent.reply_resolutions
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_terminal_wait_keeps_cut_pending_until_transport_or_deadline():
    agent = _agent(History(), {"answer": {"pieces": [REPLY_A]}})
    await _accept(agent, "answer", "wait-a")
    await _finish_generation(agent)
    entry = agent._reply_ledger["wait-a"]
    await agent._cut_reply(entry, "confirmed_command")
    await asyncio.sleep(0.001)
    assert not agent.reply_resolutions

    await agent._on_audio_playback_lifecycle(
        {
            "utterance_id": "wait-a",
            "turn_id": "wait-a",
            "seq": 0,
            "state": "INTERRUPTED",
            "words_played": 0,
            "words_streamed": 8,
            "heard_offset": 0,
            "streamed_offset": len(REPLY_A),
        }
    )
    assert _resolution(agent, "wait-a").status == "TRUNCATED"


async def _first_output_overhead_ms(agent, text, turn_id):
    """Brain-side ms from a user final to its turn's first chat.output.

    Pacing is zero and the scripted generator yields at once, so this is
    only BrainAgent's own handling and scheduling (ADR-W5 I12).
    """
    stamps = agent._w5_output_stamps
    started = time.perf_counter()
    await _accept(agent, text, turn_id)
    for _ in range(200):
        if turn_id in stamps:
            break
        await asyncio.sleep(0)
    assert turn_id in stamps, f"no chat.output for {turn_id}"
    return (stamps[turn_id] - started) * 1000


def _stamp_outputs(agent):
    agent._w5_output_stamps = {}
    publish = agent.publish

    async def capture(subject, payload):
        if (
            subject == Topics.CHAT_OUTPUT
            and isinstance(payload, dict)
            and payload.get("content")
        ):
            agent._w5_output_stamps.setdefault(
                payload.get("turn_id"), time.perf_counter()
            )
        await publish(subject, payload)

    agent.publish = capture


@pytest.mark.asyncio
async def test_i12_first_output_overhead_p95_under_50ms():
    """ADR-W5 I12, measured outside the state machines: real-time asserts
    inside a Hypothesis machine are flaky under load and time the harness
    too. 200 user finals, each barging in on the previous reply."""
    agent = _agent(History(), {"answer": {"pieces": ["Sure, here it is."]}})
    _stamp_outputs(agent)
    samples = []
    for index in range(200):
        samples.append(await _first_output_overhead_ms(agent, "answer", f"i12-{index}"))
    await _finish_generation(agent)
    ordered = sorted(samples)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    assert p95 < 50, f"p95 {p95:.1f} ms"


@pytest.mark.asyncio
async def test_i12_an_open_grace_window_never_delays_the_users_reply(monkeypatch):
    """DR-030 grace uses time the user is still talking; the reply to the
    user's final must not wait for it (ADR-W5 section 2 and I12)."""
    monkeypatch.setattr(Config, "PROACTIVE_GRACE_WINDOW_S", 5.0)
    agent = _agent(
        History(),
        {
            "high": {"pieces": ["A valuable thought"]},
            "answer": {"pieces": ["Sure, here it is."]},
        },
    )
    _stamp_outputs(agent)
    await _accept(
        agent,
        "high",
        "grace-p",
        subconscious=True,
        importance=Config.PROACTIVE_GRACE_MIN_IMPORTANCE,
    )
    await _finish_generation(agent)
    await agent._on_user_speech_partial({"text": "wait", "utterance_id": "p-1"})
    overhead = await _first_output_overhead_ms(agent, "answer", "grace-u")
    assert overhead < 50, f"{overhead:.1f} ms with a 5 s grace window open"
    await _finish_generation(agent)


@pytest.mark.asyncio
async def test_user_final_stops_every_playing_reply_not_only_the_active_turn():
    """Review finding: W5-A scoped `confirmed_user_speech` to the active
    turn. A significant thought (DR-026) cuts reply A and takes the floor;
    if A is still draining when the user speaks, a scoped stop left it
    playing over them. The stop stays unscoped and every started reply is
    cut from its own heard offset."""
    history = History()
    agent = _agent(
        history,
        {
            "answer": {"pieces": [REPLY_A]},
            "urgent": {"pieces": ["Something urgent came to mind"]},
            "wait": {"pieces": ["Sure, go ahead."]},
        },
    )
    await _accept(agent, "answer", "scope-a")
    await _finish_generation(agent)
    await _progress(agent, "scope-a", 10)
    await _accept(agent, "urgent", "scope-p", subconscious=True, importance=0.95)
    await _finish_generation(agent)
    await _progress(agent, "scope-p", 6)
    agent.published.clear()

    await _accept(agent, "wait", "scope-u")
    await _finish_generation(agent)
    await asyncio.sleep(Config.REPLY_TERMINAL_WAIT_S + 0.01)

    stops = [
        payload
        for subject, payload in agent.published
        if subject == "audio.stop" and payload.get("reason") == "confirmed_user_speech"
    ]
    assert stops and all(stop.get("turn_id") is None for stop in stops)
    resolved = {item.turn_id: item for item in agent.reply_resolutions}
    assert resolved["scope-p"].status == "TRUNCATED"
    assert resolved["scope-p"].character_offset == 6


@pytest.mark.asyncio
async def test_pending_speculative_intent_does_not_record_a_cut_reply():
    """Review finding: with a speculative intent pending no stop is
    published and Stage 2 may reject the interruption and resume the audio.
    W5-A still cut the playing reply, so it resolved TRUNCATED after the
    terminal wait while it actually played to the end."""
    history = History()
    agent = _agent(
        history,
        {"answer": {"pieces": [REPLY_A]}, "hmm": {"pieces": ["Mm."]}},
    )
    await _accept(agent, "answer", "spec-a")
    await _finish_generation(agent)
    await _progress(agent, "spec-a", 10)
    agent.cognitive_core.state.last_speculative_intent = {"name": "STOP"}

    await _accept(agent, "hmm", "spec-b")
    await _finish_generation(agent)
    await asyncio.sleep(Config.REPLY_TERMINAL_WAIT_S + 0.01)
    assert "spec-a" not in {item.turn_id for item in agent.reply_resolutions}

    await agent._on_audio_playback_lifecycle(
        {
            "utterance_id": "spec-a",
            "turn_id": "spec-a",
            "seq": 7,
            "state": "COMPLETED",
            "words_played": len(REPLY_A.split()),
            "words_streamed": len(REPLY_A.split()),
            "heard_offset": len(REPLY_A),
            "streamed_offset": len(REPLY_A),
        }
    )
    resolution = _resolution(agent, "spec-a")
    assert resolution.status == "COMPLETED"
    assert history.rows[1] == ["assistant", REPLY_A]


@pytest.mark.asyncio
async def test_resolution_and_declined_records_are_bounded():
    # One entry per reply for the life of the process otherwise; a stop that
    # named an evicted resolved reply falls back to the stale-turn guard.
    from app.agents.brain_agent import REPLY_RESOLUTIONS_MAX, ReplyResolution

    agent = _agent(History(), {})
    for n in range(REPLY_RESOLUTIONS_MAX + 10):
        agent.reply_resolutions.append(
            ReplyResolution(
                turn_id=f"t{n}",
                status="COMPLETED",
                heard_text="",
                character_offset=0,
                source="user",
                reason="completed",
            )
        )
        agent.declined_proactive_inputs.append(f"p{n}")
    assert len(agent.reply_resolutions) == REPLY_RESOLUTIONS_MAX
    assert len(agent.declined_proactive_inputs) == REPLY_RESOLUTIONS_MAX
    assert agent.reply_resolutions[0].turn_id == "t10"


@pytest.mark.asyncio
async def test_user_final_cut_resolves_at_the_last_heard_offset_when_the_terminal_is_lost():
    # The user final cuts every playing reply (CUT_PENDING). When the
    # transport's INTERRUPTED never arrives, REPLY_TERMINAL_WAIT_S resolves it
    # TRUNCATED at the last progress offset (§4), so no reply waits forever.
    # Without the cut nothing resolves it: every other test's transport
    # delivers the terminal, which is how W5_user_final_cuts_only_unstarted
    # survived.
    history = History()
    agent = _agent(
        history, {"answer": {"pieces": [REPLY_A]}, "next": {"pieces": ["ok"]}}
    )
    await _accept(agent, "answer", "reply-a")
    await _finish_generation(agent)
    await _progress(agent, "reply-a", 14)

    async def lossy_transport(subject, data):
        agent.published.append((subject, data))  # the terminal never comes

    agent.publish = lossy_transport
    await _accept(agent, "next", "reply-b")
    await asyncio.sleep(Config.REPLY_TERMINAL_WAIT_S * 3)
    await _settle(agent)

    cut = _resolution(agent, "reply-a")
    assert cut.status == "TRUNCATED"
    assert cut.character_offset == 14
    assert cut.heard_text == REPLY_A[:14].strip()
    assert [
        str(getattr(o.status, "value", o.status))
        for o in agent.get_outcome_history("reply-a")
    ] == ["TRUNCATED"]
    assert history.rows[1] == ["assistant", cut.heard_text]
    await _finish_generation(agent)
