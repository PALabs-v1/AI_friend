import asyncio

import pytest
from test_barge_in_real_flow import History, _agent, _settle
from test_bargein_w5_regressions import _accept, _finish_generation

from app.contracts import Topics


@pytest.mark.asyncio
async def test_partial_arriving_during_proactive_cut_must_decline_thought():
    agent = _agent(History(), {"answer": {"pieces": ["reply is playing now"]}})
    await _accept(agent, "answer", "user-reply")
    await _finish_generation(agent)
    entry = agent._reply_ledger["user-reply"]
    assert entry.started

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_stop_publish(subject, payload):
        if (
            subject == Topics.AUDIO_STOP
            and payload.get("reason") == "self_thought_interrupt"
        ):
            entered.set()
            await release.wait()
        else:
            await original_publish(subject, payload)

    original_publish = agent.publish
    agent.publish = blocked_stop_publish

    thought = asyncio.create_task(
        _accept(agent, "thought", "thought-a", subconscious=True, importance=0.95)
    )
    await asyncio.wait_for(entered.wait(), 1)
    await agent._on_user_speech_partial(
        {"text": "I am speaking", "utterance_id": "speech-a"}
    )
    assert agent._is_user_mid_utterance()
    release.set()
    await thought

    assert "thought-a" in agent.declined_proactive_inputs, (
        "thought was accepted based on stale mid-utterance read"
    )
    assert agent._active_response_turn_id == "user-reply"


@pytest.mark.asyncio
async def test_interrupted_terminal_before_finish_reply_must_not_store_full_history():
    from test_barge_in_real_flow import REPLY_A

    agent = _agent(History(), {"answer": {"pieces": [REPLY_A]}})
    entered = asyncio.Event()
    release = asyncio.Event()
    original_publish = agent.publish

    async def hold_done(subject, payload):
        if subject == Topics.CHAT_OUTPUT and payload.get("done"):
            entered.set()
            await release.wait()
        await original_publish(subject, payload)

    agent.publish = hold_done
    await _accept(agent, "answer", "terminal-before-store")
    flow = agent._active_generation_task
    await asyncio.wait_for(entered.wait(), 1)
    await agent._on_audio_playback_lifecycle(
        {
            "utterance_id": "terminal-before-store",
            "turn_id": "terminal-before-store",
            "seq": 0,
            "state": "INTERRUPTED",
            "words_played": 1,
            "words_streamed": 8,
            "heard_offset": 14,
            "streamed_offset": len(REPLY_A),
        }
    )
    release.set()
    await flow
    await _settle(agent)
    heard = REPLY_A[:14].strip()
    assistant_rows = [
        row[1] for row in agent.conversation_store.rows if row[0] == "assistant"
    ]
    assert assistant_rows == [heard], (
        f"history stored {assistant_rows!r}, resolution heard {heard!r}"
    )


@pytest.mark.asyncio
async def test_history_rewrite_is_unbounded_inside_lifecycle_handler():
    from test_barge_in_real_flow import REPLY_A

    agent = _agent(History(), {"answer": {"pieces": [REPLY_A]}})
    await _accept(agent, "answer", "blocked-rewrite")
    await _finish_generation(agent)
    entry = agent._reply_ledger["blocked-rewrite"]
    entry.progress = type("Progress", (), {"character_offset": 14, "word_index": 3})()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_rewrite(text, *, message_id):
        entered.set()
        await release.wait()

    agent.conversation_store.rewrite_assistant_message = blocked_rewrite
    terminal = asyncio.create_task(
        agent._on_audio_playback_lifecycle(
            {
                "utterance_id": "blocked-rewrite",
                "turn_id": "blocked-rewrite",
                "seq": 0,
                "state": "INTERRUPTED",
                "words_played": 3,
                "words_streamed": 8,
                "heard_offset": 14,
                "streamed_offset": len(REPLY_A),
            }
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    done, _ = await asyncio.wait({terminal}, timeout=0.03)
    release.set()
    await terminal
    assert done, "lifecycle handler did not return within a bounded history write wait"


@pytest.mark.asyncio
async def test_evicted_stop_redelivery_must_not_cut_a_later_turn(monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "REPLY_TERMINAL_WAIT_S", 60.0)
    agent = _agent(
        History(),
        {
            "first": {"pieces": ["first answer is playing"]},
            "second": {"pieces": ["second answer is playing"]},
        },
    )
    await _accept(agent, "first", "first-turn")
    await _finish_generation(agent)
    replay = {
        "interrupt": True,
        "speculative": False,
        "reason": "confirmed_command",
        "turn_id": None,
    }
    await agent._on_audio_stop(replay)
    assert agent._reply_ledger["first-turn"].cut_pending
    for i in range(256):
        await agent._on_audio_stop(
            {
                "interrupt": True,
                "speculative": False,
                "reason": "facial_reflex_startle",
                "turn_id": f"stale-{i}",
            }
        )
    await _accept(agent, "second", "second-turn")
    await _finish_generation(agent)
    assert not agent._reply_ledger["second-turn"].cut_pending
    await agent._on_audio_stop(replay)  # JetStream redelivery of the first stop
    assert not agent._reply_ledger["second-turn"].cut_pending, (
        "old unscoped stop redelivery cut the new active reply"
    )
