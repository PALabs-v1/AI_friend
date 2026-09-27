"""Focused W5 regressions for the six frozen section-1 defects."""

import asyncio
import time
import uuid

import pytest
from test_barge_in_real_flow import REPLY_A, History, _agent, _progress, _settle

from app.agents import brain_agent as brain_module
from app.cognitive.action_intent import build_action_intent
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


@pytest.mark.asyncio
async def test_typed_user_final_cancels_a_proactive_reply_still_generating():
    # A proactive reply still generating when a typed user final lands (no
    # partial came first, so `_on_user_speech_partial` never ran) is cut
    # CANCELLED by the replacement (§5, user final row). The replacement
    # gated that on `_reply_generating`, the *user* reply's flag: after an
    # earlier user reply had finished, the proactive entry was never
    # resolved and sat in the ledger until an overflow recorded it FAILED.
    agent = _agent(
        History(),
        {
            "answer": {"pieces": [REPLY_A]},
            "thought": {"pieces": ["a slow thought"], "think": 1.0},
            "next": {"pieces": ["ok"]},
        },
    )
    await _accept(agent, "answer", "user-a")
    await _finish_generation(agent)
    await agent._on_audio_playback_lifecycle(
        {
            "utterance_id": "user-a",
            "turn_id": "user-a",
            "seq": 0,
            "state": "COMPLETED",
            "words_played": 10,
            "words_streamed": 10,
            "heard_offset": len(REPLY_A),
            "streamed_offset": len(REPLY_A),
        }
    )
    assert _resolution(agent, "user-a").status == "COMPLETED"

    await _accept(agent, "thought", "thought-a", subconscious=True, importance=0.5)
    await asyncio.sleep(0.01)
    assert not agent._reply_ledger["thought-a"].started

    await _accept(agent, "next", "user-b")
    await _finish_generation(agent)

    assert "thought-a" not in agent._reply_ledger
    cut = _resolution(agent, "thought-a")
    assert (cut.status, cut.source) == ("CANCELLED", "proactive")
    assert [item.turn_id for item in agent.reply_resolutions].count("thought-a") == 1


@pytest.mark.asyncio
async def test_a_user_reply_in_its_pacing_sleep_is_in_flight_for_a_thought():
    # The user's turn has begun but sleeps its pre-response silence
    # (300-900 ms in production) before generating. "In flight" was keyed on
    # a generating flag the flow set only after that sleep, so a routine
    # thought (importance < SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE) landing
    # then was let in and ceded the user's reply CANCELLED: the question
    # went unanswered. DR-026: only a significant thought may interrupt.
    agent = _agent(
        History(),
        {"answer": {"pieces": [REPLY_A]}, "thought": {"pieces": ["by the way"]}},
    )
    agent.conversational_runtime.pacing_ms = 200.0
    await _accept(agent, "answer", "user-a")
    await asyncio.sleep(0.01)
    assert not agent._reply_ledger["user-a"].started  # still pacing

    await _accept(agent, "thought", "thought-a", subconscious=True, importance=0.5)

    assert "thought-a" in agent.declined_proactive_inputs
    assert "user-a" not in {item.turn_id for item in agent.reply_resolutions}
    await _finish_generation(agent)
    assert "user-a" in agent.finished_turns
    assert "thought-a" not in agent._reply_ledger


@pytest.mark.asyncio
async def test_a_terminal_that_beats_the_end_of_the_flow_hears_the_whole_reply():
    # A short proactive reply can play out before its flow reaches
    # `_finish_reply`: the transport's COMPLETED arrives while the done
    # marker is still being published. The reply's text must already be on
    # its ledger entry then, or it resolves COMPLETED with nothing heard and
    # the full row is stored afterwards with no entry to own it.
    thought = "I remembered your appointment is tomorrow"
    agent = _agent(History(), {"thought": {"pieces": [thought]}})
    publish = agent.publish

    async def fast_transport(subject, data):
        await publish(subject, data)
        if subject == Topics.CHAT_OUTPUT and data.get("done"):
            await agent._on_audio_playback_lifecycle(
                {
                    "utterance_id": data["turn_id"],
                    "turn_id": data["turn_id"],
                    "seq": 0,
                    "state": "COMPLETED",
                    "words_played": len(thought.split()),
                    "words_streamed": len(thought.split()),
                    "heard_offset": len(thought),
                    "streamed_offset": len(thought),
                }
            )

    agent.publish = fast_transport
    await _accept(agent, "thought", "thought-a", subconscious=True, importance=0.5)
    await _finish_generation(agent)

    done = _resolution(agent, "thought-a")
    assert (done.status, done.heard_text) == ("COMPLETED", thought)


@pytest.mark.asyncio
async def test_a_stop_while_the_cut_writes_history_is_not_a_second_interruption():
    # A resolved reply stays in the ledger while its history rewrite waits
    # for the reply's own insert (bounded by REPLY_INSERT_WAIT_S). A second,
    # different stop for it landing then found it, cancelled the generation
    # again and released adrenaline a second time: one interruption felt
    # twice.
    history = History()
    gate = asyncio.Event()
    real_log = history.log_message

    async def slow_log(role, content, message_id=None):
        if role == "assistant":
            await gate.wait()
        await real_log(role, content, message_id)

    history.log_message = slow_log
    agent = _agent(history, {"answer": {"pieces": [REPLY_A]}})
    await _accept(agent, "answer", "reply-a")
    await agent._active_generation_task  # its insert is spawned, still gated
    await _progress(agent, "reply-a", 14)

    def stop(reason):
        return {
            "interrupt": True,
            "speculative": False,
            "reason": reason,
            "turn_id": "reply-a",
        }

    await agent._on_audio_stop(stop("confirmed_command"))
    resolving = asyncio.create_task(
        agent._on_audio_playback_lifecycle(
            {
                "utterance_id": "reply-a",
                "turn_id": "reply-a",
                "seq": 0,
                "state": "INTERRUPTED",
                "words_played": 3,
                "words_streamed": 10,
                "heard_offset": 14,
                "streamed_offset": len(REPLY_A),
            }
        )
    )
    await asyncio.sleep(0.01)
    # Resolved and out of the ledger (critic r1 #2), its row still being cut.
    assert "reply-a" not in agent._reply_ledger
    assert _resolution(agent, "reply-a").status == "TRUNCATED"
    assert history.rows[-1] != ["assistant", REPLY_A[:14].strip()]

    await agent._on_audio_stop(stop("facial_reflex_startle"))
    gate.set()
    await resolving
    await _settle(agent)

    agent.cognitive_core.state.release_adrenaline.assert_awaited_once()
    assert [r.turn_id for r in agent.reply_resolutions].count("reply-a") == 1
    assert history.rows[-1] == ["assistant", REPLY_A[:14].strip()]


@pytest.mark.asyncio
async def test_a_stale_stop_for_a_superseded_reply_still_playing_cuts_nothing():
    # I4: B took the floor with a speculative intent pending, so A was not
    # cut and plays on while Stage 2 decides. A facial-startle stop published
    # for A before B took over lands now. It is stale (not Stage 2's command,
    # not the active turn): A must not be cut, time out TRUNCATED, or be
    # felt as an interruption. Every other stale-stop test had A finished.
    agent = _agent(
        History(), {"answer": {"pieces": [REPLY_A]}, "hmm": {"pieces": ["okay"]}}
    )
    await _accept(agent, "answer", "reply-a")
    await _finish_generation(agent)
    await _progress(agent, "reply-a", 14)
    agent.cognitive_core.state.last_speculative_intent = {
        "name": "STOP",
        "keywords": ["stop"],
        "text": "hmm",
    }
    await _accept(agent, "hmm", "reply-b")
    await _finish_generation(agent)
    assert agent._active_response_turn_id == "reply-b"

    await agent._on_audio_stop(
        {
            "interrupt": True,
            "speculative": False,
            "reason": "facial_reflex_startle",
            "turn_id": "reply-a",
        }
    )
    await asyncio.sleep(Config.REPLY_TERMINAL_WAIT_S * 3)
    await _settle(agent)

    entry = agent._reply_ledger["reply-a"]
    assert not entry.cut_pending and not entry.resolved
    agent.cognitive_core.state.release_adrenaline.assert_not_awaited()


# --- Codex cold critic, round 1 (ADR-W5 section 9) ---------------------------


@pytest.mark.asyncio
async def test_a_reply_whose_chunks_never_reach_the_broker_is_resolved_once():
    # Critic r1 #1: `started` was set before the chunk's publish and kept when
    # it raised, so with the fallback's publish failing too, the flow's end
    # skipped the reply as started and it never resolved: no OutcomeRecord.
    agent = _agent(History(), {"answer": {"pieces": [REPLY_A]}})
    publish = agent.publish

    async def broker_down(subject, payload):
        if subject == Topics.CHAT_OUTPUT:
            raise RuntimeError("broker down")
        await publish(subject, payload)

    agent.publish = broker_down
    await _accept(agent, "answer", "no-broker")
    task = agent._active_generation_task
    with pytest.raises(RuntimeError):
        await task
    await _settle(agent)

    assert "no-broker" not in agent._reply_ledger
    resolution = _resolution(agent, "no-broker")
    assert (resolution.status, resolution.reason) == ("CANCELLED", "generation_failed")
    assert [r.turn_id for r in agent.reply_resolutions].count("no-broker") == 1
    outcomes = agent.get_outcome_history("no-broker")
    assert [o.status for o in outcomes] == ["CANCELLED"]


@pytest.mark.asyncio
async def test_a_cancel_during_an_overflow_write_keeps_the_ledger_bounded():
    # Critic r1 #2: the overflowed reply was marked resolved, then its history
    # write awaited, then it left the ledger. Cancelled in the write, it stayed
    # forever: later overflows picked it, returned at once, and the ledger grew.
    history = History()
    gate, entered, written = asyncio.Event(), asyncio.Event(), []

    async def blocked_rewrite(text, *, message_id):
        entered.set()
        await gate.wait()
        written.append((text, message_id))

    history.rewrite_assistant_message = blocked_rewrite
    agent = _agent(history, {})
    for i in range(brain_module.REPLY_LEDGER_MAX):
        await agent._begin_turn(f"t{i}")
    oldest = agent._reply_ledger["t0"]
    oldest.started, oldest.text, oldest.message_id = True, "reply text", uuid.uuid4()

    pending = asyncio.create_task(agent._begin_turn("t32"))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert "t0" not in agent._reply_ledger
    assert agent.reply_ledger_size() == brain_module.REPLY_LEDGER_MAX
    assert [r.turn_id for r in agent.reply_resolutions] == ["t0"]
    gate.set()
    await _settle(agent)
    assert written == [("", oldest.message_id)]  # the shielded write still landed
    for i in range(33, 40):
        await agent._begin_turn(f"t{i}")
    assert agent.reply_ledger_size() == brain_module.REPLY_LEDGER_MAX


@pytest.mark.asyncio
async def test_a_flow_cancelled_while_its_overflow_resolves_resolves_its_own_reply():
    # Critic r1 #2, second half: `_begin_turn` ran outside the flow's cleanup,
    # so a flow cancelled in its overflow left its own new entry behind.
    history = History()
    gate, entered = asyncio.Event(), asyncio.Event()

    async def blocked_rewrite(text, *, message_id):
        entered.set()
        await gate.wait()

    history.rewrite_assistant_message = blocked_rewrite
    agent = _agent(history, {"late": {"pieces": ["never said"]}})
    for i in range(brain_module.REPLY_LEDGER_MAX):
        await agent._begin_turn(f"t{i}")
    oldest = agent._reply_ledger["t0"]
    oldest.started, oldest.text, oldest.message_id = True, "reply text", uuid.uuid4()

    await _accept(agent, "late", "late")
    flow = agent._active_generation_task
    await entered.wait()
    flow.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flow
    gate.set()
    await _settle(agent)

    assert "late" not in agent._reply_ledger
    assert _resolution(agent, "late").status == "CANCELLED"
    assert agent.reply_ledger_size() == brain_module.REPLY_LEDGER_MAX - 1


def _stalling_generator(entered_cleanup, release, *, keep_talking=False):
    async def process_event(raw_event, **_):
        turn = raw_event["metadata"]["turn_id"]
        intent = build_action_intent(
            turn_id=turn,
            workspace_epoch=0,
            workspace_revision=0,
            kind="SPEAK",
            behavior_decision={},
        )
        yield {"type": "action_intent", "data": intent.model_dump()}
        if raw_event["content"] != "first":
            yield {"type": "content", "data": "second reply. "}
            yield {"type": "done"}
            return
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            entered_cleanup.set()
            await release.wait()
            if not keep_talking:
                raise
        # A generator that swallows its cancellation and keeps going.
        yield {"type": "content", "data": "zombie words from the first reply. "}
        yield {"type": "done"}

    return process_event


@pytest.mark.asyncio
async def test_a_generation_stalled_in_its_cancel_cleanup_does_not_hold_chat_input(
    monkeypatch,
):
    # Critic r1 #3: the replacement awaited the cancelled task without bound
    # while holding `_generation_lock`, so every later chat.input waited on a
    # generator's cleanup.
    monkeypatch.setattr(brain_module, "GENERATION_TEARDOWN_WAIT_S", 0.05, raising=False)
    release, entered_cleanup = asyncio.Event(), asyncio.Event()
    agent = _agent(History(), {})
    agent.cognitive_core.process_event = _stalling_generator(entered_cleanup, release)
    await _accept(agent, "first", "first")
    await asyncio.sleep(0.01)

    replacement = asyncio.create_task(_accept(agent, "second", "second"))
    await asyncio.wait_for(entered_cleanup.wait(), 0.5)
    # `asyncio.wait`, not `wait_for`: a timeout must not cancel the handler,
    # since that cancel would reach the stalled task and unstick it.
    await asyncio.wait({replacement}, timeout=0.5)
    stalled = not replacement.done()
    release.set()
    await replacement
    assert not stalled, "chat.input waited on the old generation's cleanup"

    assert _resolution(agent, "first").status == "CANCELLED"
    assert "first" not in agent._reply_ledger
    await _finish_generation(agent)
    await _settle(agent)
    assert [r.turn_id for r in agent.reply_resolutions].count("first") == 1


@pytest.mark.asyncio
async def test_a_handler_cancelled_while_it_waits_for_teardown_stays_cancelled(
    monkeypatch,
):
    # Found while fixing critic r1 #3: `except asyncio.CancelledError: pass`
    # around `await prior_task` also swallowed the handler's OWN cancellation
    # (shutdown, a NATS callback timeout), and it went on to start a new turn.
    monkeypatch.setattr(brain_module, "GENERATION_TEARDOWN_WAIT_S", 5.0, raising=False)
    release, entered_cleanup = asyncio.Event(), asyncio.Event()
    agent = _agent(History(), {})
    agent.cognitive_core.process_event = _stalling_generator(entered_cleanup, release)
    await _accept(agent, "first", "first")
    await asyncio.sleep(0.01)

    replacement = asyncio.create_task(_accept(agent, "second", "second"))
    await asyncio.wait_for(entered_cleanup.wait(), 0.5)
    replacement.cancel()
    await asyncio.wait({replacement}, timeout=1.0)
    release.set()
    await _settle(agent)

    assert replacement.cancelled()
    assert "second" not in agent._reply_ledger
    assert all(r.turn_id != "second" for r in agent.reply_resolutions)


@pytest.mark.asyncio
async def test_a_generation_that_swallows_its_cancel_cannot_speak_afterwards(
    monkeypatch,
):
    # Critic r1 #3, the fence: once a successor has the floor, the old
    # generation can finish its cleanup but may not put its reply on the wire.
    monkeypatch.setattr(brain_module, "GENERATION_TEARDOWN_WAIT_S", 0.05, raising=False)
    release, entered_cleanup = asyncio.Event(), asyncio.Event()
    agent = _agent(History(), {})
    agent.cognitive_core.process_event = _stalling_generator(
        entered_cleanup, release, keep_talking=True
    )
    await _accept(agent, "first", "first")
    await asyncio.sleep(0.01)
    first_flow = agent._active_generation_task

    replacement = asyncio.create_task(_accept(agent, "second", "second"))
    await asyncio.wait({replacement}, timeout=0.5)  # never cancels the handler
    release.set()
    await replacement
    await asyncio.wait({first_flow}, timeout=1.0)
    await _finish_generation(agent)
    await _settle(agent)

    said = [
        payload
        for subject, payload in agent.published
        if subject == Topics.CHAT_OUTPUT and payload.get("turn_id") == "first"
    ]
    assert said == []
    assert [r.turn_id for r in agent.reply_resolutions].count("first") == 1


@pytest.mark.asyncio
async def test_a_stream_error_fallback_is_the_text_its_terminal_resolves():
    # Critic r1 #4: the fallback was published but never put on the reply's
    # entry, so its COMPLETED terminal resolved the reply as "" (or the
    # partial text) and the OutcomeRecord said nothing was delivered.
    history = History()
    agent = _agent(history, {"answer": {"pieces": [], "raise": True}})
    await _accept(agent, "answer", "fallback-a")
    await _finish_generation(agent)
    fallback = "I'm having trouble thinking right now..."
    assert any(
        s == Topics.CHAT_OUTPUT and p.get("content") == fallback
        for s, p in agent.published
    )

    await agent._on_audio_playback_lifecycle(
        {
            "utterance_id": "fallback-a",
            "turn_id": "fallback-a",
            "seq": 0,
            "state": "COMPLETED",
            "words_played": len(fallback.split()),
            "words_streamed": len(fallback.split()),
            "heard_offset": len(fallback),
            "streamed_offset": len(fallback),
        }
    )
    await _settle(agent)

    assert _resolution(agent, "fallback-a").heard_text == fallback
    outcome = agent.get_outcome_history("fallback-a")
    assert [o.actual_delivered_text for o in outcome] == [fallback]
    assert history.rows[-1] == ["assistant", fallback]
