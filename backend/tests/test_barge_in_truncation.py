"""
What the transcript records when the user interrupts.

The stored assistant message is not a log. Memory reads it, and the persona
prompt reads it back, so a wrong cut point does not merely misreport history --
it becomes what the agent believes it said.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.brain_agent import BrainAgent

REPLY = "I looked it up and the museum opens at ten, so we have plenty of time."


def _agent(progress=None, response=REPLY):
    agent = object.__new__(BrainAgent)
    agent.last_audio_progress = progress
    agent.last_assistant_response = response
    agent.conversation_store = SimpleNamespace(rewrite_assistant_message=AsyncMock())
    # P1-4: a confirmed stop now also cancels the interrupted turn's
    # generation task (`_on_audio_stop` -> `_cancel_active_generation`),
    # which reads this state -- set here the same way BrainAgent.__init__
    # does, since these tests build the agent via object.__new__.
    agent._active_generation_task = None
    agent._generation_lock = asyncio.Lock()
    # P2-14/M1-A14: _truncate_interrupted_reply now serializes the
    # read-compute-write against concurrent writers (chat.input's turn
    # reset, audio.playback.progress's tracker) via this lock -- set here
    # for the same reason the two locks above are, since object.__new__
    # skips BrainAgent.__init__ entirely.
    agent._turn_state_lock = asyncio.Lock()
    # The reply is turn "t1"'s and stored under a known row id, as
    # `_process_chat_input_flow` leaves it; the cut rewrites that row only.
    agent._reply_turn_id = "t1"
    agent._active_response_turn_id = "t1"
    agent._reply_message_id = "row-1"
    return agent


def _stop(speculative=False):
    return {
        "interrupt": True,
        "speculative": speculative,
        "reason": "confirmed_command",
        "command_text": "stop",
        "keywords": ["stop"],
        "utterance_id": "u1",
        "turn_id": None,
    }


def test_a_confirmed_stop_cancels_the_interrupted_turns_generation():
    """P1-4: this is now the only place a confirmed interrupt cancels
    generation -- the second, unscoped classifier that used to do it inline
    on every matching partial (`InterruptionClassifier` in brain_agent.py)
    is gone; decision.py's `is_speculative_stop_confirmed` is the sole
    arbiter, and this handler is what turns its confirmation into action."""
    agent = _agent(progress=None)
    agent._cancel_active_generation = AsyncMock()

    asyncio.run(agent._on_audio_stop(_stop()))

    agent._cancel_active_generation.assert_awaited_once()


def test_a_speculative_stop_does_not_cancel_generation():
    """Nothing has been confirmed yet -- a duck must not cancel a turn that
    may turn out not to have been interrupted at all."""
    agent = _agent(progress=None)
    agent._cancel_active_generation = AsyncMock()

    asyncio.run(agent._on_audio_stop(_stop(speculative=True)))

    agent._cancel_active_generation.assert_not_awaited()


# --------------------------------------------------------------------------
# Bucket 11 (voice remediation Phase 3, item 2): a genuine interruption
# releases adrenaline
# --------------------------------------------------------------------------


def _agent_with_endocrine_state(progress=None, response=REPLY):
    agent = _agent(progress=progress, response=response)
    agent.cognitive_core = SimpleNamespace(
        state=SimpleNamespace(release_adrenaline=AsyncMock())
    )
    return agent


def test_a_confirmed_stop_releases_adrenaline():
    """This is the one branch that reaches `_cancel_active_generation` --
    past the speculative filter and past the same-turn `confirmed_user_speech`
    early return -- so it is the textbook definition of "a genuine
    interruption" the plan's adrenaline channel exists to feel different
    from a false one."""
    agent = _agent_with_endocrine_state(progress=None)
    agent._cancel_active_generation = AsyncMock()

    asyncio.run(agent._on_audio_stop(_stop()))

    agent.cognitive_core.state.release_adrenaline.assert_awaited_once()


def test_a_speculative_stop_does_not_release_adrenaline():
    """A duck has not actually interrupted anything yet."""
    agent = _agent_with_endocrine_state(progress=None)
    agent._cancel_active_generation = AsyncMock()

    asyncio.run(agent._on_audio_stop(_stop(speculative=True)))

    agent.cognitive_core.state.release_adrenaline.assert_not_awaited()


def test_a_same_turn_silencing_stop_does_not_release_adrenaline():
    """`reason="confirmed_user_speech"` returns before generation is ever
    cancelled -- it silences playback for a *new* incoming turn, not an
    interruption of the agent's own speech, so it must not fire the
    "something just cut me off" response either."""
    agent = _agent_with_endocrine_state(progress=None)
    agent._cancel_active_generation = AsyncMock()

    stop = _stop()
    stop["reason"] = "confirmed_user_speech"
    asyncio.run(agent._on_audio_stop(stop))

    agent._cancel_active_generation.assert_not_awaited()
    agent.cognitive_core.state.release_adrenaline.assert_not_awaited()


def test_an_endocrine_failure_does_not_prevent_truncation():
    """Endocrine state modulates how the agent speaks; it must never decide
    whether truncation -- the part that keeps memory honest about what was
    actually said -- happens. Mirrors `cognitive/pipeline.py`'s own
    broad-except reasoning for every other hormone release site."""
    agent = _agent_with_endocrine_state(progress=None)
    agent._cancel_active_generation = AsyncMock()
    agent.cognitive_core.state.release_adrenaline = AsyncMock(
        side_effect=RuntimeError("endocrine backend down")
    )

    asyncio.run(agent._on_audio_stop(_stop()))

    agent._cancel_active_generation.assert_awaited_once()


def test_real_playback_progress_still_truncates_where_it_says():
    """The accurate path must keep working; it is the only one that knows."""
    progress = SimpleNamespace(completed=False, character_offset=26)
    agent = _agent(progress)

    asyncio.run(agent._on_audio_stop(_stop()))

    agent.conversation_store.rewrite_assistant_message.assert_awaited_once()
    call = agent.conversation_store.rewrite_assistant_message.await_args
    assert call.kwargs == {"message_id": "row-1"}  # this reply's row, no other
    stored = call.args[0]
    assert stored == REPLY[:26].strip()


def test_an_interruption_without_progress_does_not_invent_a_cut_point(caplog):
    """This replaces a hardcoded 15 characters/second estimate.

    Real speech rate varies with prosody, pauses and the synthesiser, so the
    estimate cut wherever the arithmetic landed and nothing downstream could
    tell the sentence had been reconstructed. Keeping the full text is also
    imperfect — the agent may believe it said more than was heard — but it is
    wrong honestly and visibly rather than by fabrication.
    """
    agent = _agent(progress=None)

    with caplog.at_level("INFO"):
        asyncio.run(agent._on_audio_stop(_stop()))

    agent.conversation_store.rewrite_assistant_message.assert_not_awaited()
    assert any("keeping the full" in r.getMessage() for r in caplog.records)


def test_a_speculative_stop_never_rewrites_history():
    """Speculative stops are guesses about the user, not confirmed interrupts.

    Truncating on one would edit the transcript because the agent *thought* it
    heard the user start talking.
    """
    progress = SimpleNamespace(completed=False, character_offset=26)
    agent = _agent(progress)

    asyncio.run(agent._on_audio_stop(_stop(speculative=True)))

    agent.conversation_store.rewrite_assistant_message.assert_not_awaited()


def test_progress_is_cleared_even_when_nothing_was_truncated():
    """Otherwise a stale offset survives into the next interruption.

    The reset lived only on the branch that truncated, so a stop matching none
    of the guards left the marker in place — and the *next* barge-in would cut
    the new reply at an offset measured against a reply that had already ended.
    """
    progress = SimpleNamespace(completed=True, character_offset=26)  # completed
    agent = _agent(progress)

    asyncio.run(agent._on_audio_stop(_stop()))

    agent.conversation_store.rewrite_assistant_message.assert_not_awaited()
    assert agent.last_audio_progress is None


def test_the_response_start_timestamp_is_gone():
    """It was written on one streaming path and read only by the estimate.

    Leaving it would be a field that looks like turn state, is set on one of the
    two paths that produce a reply, and is read by nothing — the exact shape of
    the bug it enabled, where elapsed time could be measured from an earlier
    turn.
    """
    import inspect

    from app.agents import brain_agent

    source = inspect.getsource(brain_agent)
    assert "self.assistant_response_start_time = time.time()" not in source


@pytest.mark.parametrize("offset", [0, len(REPLY), len(REPLY) + 50])
def test_an_out_of_range_offset_leaves_the_reply_alone(offset):
    """A zero or past-the-end offset means "nothing useful is known".

    Zero would store an empty message; past-the-end would strip the trailing
    whitespace off a reply that was heard in full and rewrite it for no reason.
    """
    progress = SimpleNamespace(completed=False, character_offset=offset)
    agent = _agent(progress)

    asyncio.run(agent._on_audio_stop(_stop()))

    agent.conversation_store.rewrite_assistant_message.assert_not_awaited()


def _chat_input_agent():
    """A real BrainAgent for `_on_chat_input`'s publish-gating logic.

    Unlike `_agent()` above (built via `object.__new__` for `_on_audio_stop`
    in isolation), `_on_chat_input` reads `self.cognitive_core.state` and
    calls `self.publish`/`self._replace_active_generation`, so it needs the
    real constructor. `_process_chat_input_flow` is replaced with a no-op so
    the test exercises only the gate, not a full cognitive turn.
    """
    agent = BrainAgent(graph_db=None, memory_store=None, conversation_store=None)
    agent.publish = AsyncMock()

    async def _noop_flow(_msg, _is_subconscious, _message):
        return None

    agent._process_chat_input_flow = _noop_flow
    return agent


def _chat_input(text="hello", utterance_id="utt-1"):
    return {
        "text": text,
        "utterance_id": utterance_id,
        "turn_id": "turn-1",
        "metadata": {"source": "user"},
    }


def test_confirmed_stop_is_suppressed_while_a_speculative_duck_is_pending():
    """Bucket 1: a pending speculative duck means the pipeline's own Stage 2
    conflict resolution (pipeline.py's `_resolve_turn_conflict`) is about to
    run the arbiter on this exact text and publish audio.stop or
    audio.resume itself. Firing an unconditional stop here first would race
    ahead of that verdict -- measured live on "Let's stop it.": the arbiter
    rejected the interruption, but this publish had already cut playback
    half a second earlier regardless of that rejection.
    """
    agent = _chat_input_agent()
    agent.cognitive_core.state.last_speculative_intent = {
        "name": "STOP",
        "keywords": ["stop"],
        "text": "let's stop it",
    }

    asyncio.run(agent._on_chat_input(_chat_input(text="let's stop it.")))

    stop_calls = [
        call
        for call in agent.publish.await_args_list
        if call.args and call.args[0] == "audio.stop"
    ]
    assert stop_calls == []


def test_confirmed_stop_still_fires_with_no_speculative_duck_pending():
    """Companion to the suppression test above: this publish's actual job
    (silencing old audio for a genuinely new turn, with no prior duck to
    defer to) must keep working unconditionally. Without this test, deleting
    the whole publish would "fix" the suppression test too.
    """
    agent = _chat_input_agent()
    assert agent.cognitive_core.state.last_speculative_intent is None

    asyncio.run(agent._on_chat_input(_chat_input(text="hello there")))

    stop_calls = [
        call
        for call in agent.publish.await_args_list
        if call.args and call.args[0] == "audio.stop"
    ]
    assert len(stop_calls) == 1
    assert stop_calls[0].args[1]["reason"] == "confirmed_user_speech"


def test_confirmed_stop_is_suppressed_within_the_onset_grace_period():
    """Bucket 1: a transcript arriving this soon after the agent's own audio
    started playing cannot be a real human reaction to it -- STT alone needs
    250ms min_speech_ms + 700ms endpoint silence before it emits a final
    transcript at all. Far more likely: onset noise (a click, pop, or brief
    echo tail) before echoCancellation has settled.
    """
    agent = _chat_input_agent()
    agent._last_audio_onset_at = time.time()

    asyncio.run(agent._on_chat_input(_chat_input(text="hello there")))

    stop_calls = [
        call
        for call in agent.publish.await_args_list
        if call.args and call.args[0] == "audio.stop"
    ]
    assert stop_calls == []


def test_confirmed_stop_fires_once_the_onset_grace_period_has_elapsed():
    """Companion to the grace-period test above: the suppression must not
    outlive the grace window, or every turn after the first would be muted
    for its whole duration instead of just the onset instant.
    """
    from app.config import Config

    agent = _chat_input_agent()
    agent._last_audio_onset_at = time.time() - (Config.BARGE_IN_ONSET_GRACE_S + 1.0)

    asyncio.run(agent._on_chat_input(_chat_input(text="hello there")))

    stop_calls = [
        call
        for call in agent.publish.await_args_list
        if call.args and call.args[0] == "audio.stop"
    ]
    assert len(stop_calls) == 1


def test_onset_grace_transcript_with_no_speculative_intent_starts_no_new_turn():
    """Reviewer finding (coderabbitai): suppressing audio.stop within the
    onset grace period isn't enough on its own -- `_on_chat_input` still fell
    through unconditionally to `_replace_active_generation`, cancelling
    whatever the agent was doing and generating a brand-new reply to what the
    onset-grace comment itself calls "far more likely onset noise," all while
    the prior audio kept playing uninterrupted. A transcript in this state
    (within grace, no speculative intent flagged by STT) must be dropped
    entirely, not just have its stop suppressed.
    """
    agent = _chat_input_agent()
    agent._last_audio_onset_at = time.time()
    agent._process_chat_input_flow = AsyncMock(return_value=None)
    assert agent.cognitive_core.state.last_speculative_intent is None

    asyncio.run(agent._on_chat_input(_chat_input(text="hello there")))

    agent._process_chat_input_flow.assert_not_called()


def test_onset_grace_transcript_with_speculative_intent_still_starts_a_new_turn():
    """Companion to the drop test above: onset grace only suppresses noise
    that STT itself gave no signal about. When STT's own speculative pipeline
    already flagged this utterance as command-like, it must still reach
    `_process_chat_input_flow` -- the grace period must not swallow a real,
    already-corroborated interruption."""
    agent = _chat_input_agent()
    agent._last_audio_onset_at = time.time()
    agent._process_chat_input_flow = AsyncMock(return_value=None)
    agent.cognitive_core.state.last_speculative_intent = {
        "name": "STOP",
        "keywords": ["stop"],
        "text": "stop",
    }

    asyncio.run(agent._on_chat_input(_chat_input(text="stop")))

    agent._process_chat_input_flow.assert_called_once()
