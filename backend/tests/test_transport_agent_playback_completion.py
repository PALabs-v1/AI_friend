"""Regression coverage for transport-owned playback lifecycle terminals."""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.transport_agent import TransportAgent
from app.contracts import AudioPlaybackLifecycle, Topics


def _make_agent() -> TransportAgent:
    with patch("app.agents.transport_agent.Config") as mock_config:
        mock_config.NATS_URL = "nats://127.0.0.1:4222"
        mock_config.LIVEKIT_URL = "ws://127.0.0.1:7880"
        mock_config.LIVEKIT_API_KEY = "k"
        mock_config.LIVEKIT_API_SECRET = "s"
        mock_config.SAMPLE_RATE = 16000
        mock_config.TRANSPORT_AUDIO_QUEUE_SIZE = 8
        agent = TransportAgent()
    agent.publish = AsyncMock()
    return agent


@pytest.mark.asyncio
async def test_pcm_bytes_do_not_create_a_terminal_without_typed_trailer():
    agent = _make_agent()
    await agent._on_nats_audio(
        b"\x00\x00" * 4,
        metadata={"turn_id": "turn-1", "character_offset": 4, "word_index": 1},
    )

    assert agent.audio_queue.qsize() == 1
    frame = agent.audio_queue.get_nowait()
    assert frame[0] == b"\x00\x00" * 4
    assert frame[-1] is None


@pytest.mark.asyncio
async def test_typed_trailer_follows_pcm_and_waits_for_playout_before_completed():
    agent = _make_agent()
    agent.audio_source = MagicMock()
    agent.audio_source.capture_frame = AsyncMock()
    agent.audio_source.wait_for_playout = AsyncMock()
    tasks = []

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    agent.spawn = MagicMock(side_effect=spawn)
    await agent._on_nats_audio(
        b"\x00\x00" * 4,
        metadata={"turn_id": "turn-1", "character_offset": 4, "word_index": 1},
    )
    await agent._on_nats_audio(
        b'{"kind":"END_OF_STREAM","utterance_id":"turn-1",'
        b'"turn_id":"turn-1","failed":false}',
        metadata={
            "audio_stream_kind": "trailer",
            "utterance_id": "turn-1",
            "turn_id": "turn-1",
            "character_offset": 4,
            "word_index": 1,
        },
    )
    assert agent.audio_queue.qsize() == 2

    worker = asyncio.create_task(agent._audio_playback_worker())
    try:
        await asyncio.wait_for(agent.audio_queue.join(), timeout=2)
        await asyncio.gather(*tasks)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    agent.audio_source.capture_frame.assert_awaited_once()
    agent.audio_source.wait_for_playout.assert_awaited_once()
    events = [
        AudioPlaybackLifecycle.model_validate(call.args[1])
        for call in agent.publish.await_args_list
        if call.args[0] == Topics.AUDIO_PLAYBACK_LIFECYCLE
    ]
    assert [event.state for event in events] == ["STARTED", "PLAYING", "COMPLETED"]
    assert [event.seq for event in events] == [0, 1, 2]


@pytest.mark.asyncio
async def test_audio_playback_progress_never_carries_terminal_completion():
    agent = _make_agent()
    agent.audio_source = MagicMock()
    agent.audio_source.capture_frame = AsyncMock()

    await agent._on_nats_audio(
        b"\x00\x00" * 4,
        metadata={"turn_id": "turn-1", "character_offset": 4, "word_index": 1},
    )
    worker = asyncio.create_task(agent._audio_playback_worker())
    try:
        await asyncio.wait_for(agent.audio_queue.join(), timeout=2)
        await asyncio.sleep(0)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    progress = [
        call.args[1]
        for call in agent.publish.await_args_list
        if call.args[0] == Topics.AUDIO_PLAYBACK_PROGRESS
    ]
    assert progress
    assert all(item["completed"] is False for item in progress)


@pytest.mark.asyncio
async def test_flush_stop_interrupts_wrong_take_and_next_frame_gets_new_utterance():
    agent = _make_agent()
    agent._active_turn_id = "turn-1"
    agent._active_utterance_id = "utterance-1"
    agent._active_utterance_turn_id = "turn-1"
    agent._lifecycle_active_by_turn["turn-1"] = "utterance-1"
    agent._lifecycle_attempts["turn-1"] = 1
    agent._lifecycle_position[("utterance-1", "turn-1")] = (2, 8)
    agent._flush_downstream_audio = AsyncMock()
    tasks = []

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    agent.spawn = MagicMock(side_effect=spawn)
    await agent._on_audio_stop({"turn_id": "turn-1", "flush": True})
    await agent._on_nats_audio(
        b"\x00\x00" * 4,
        metadata={"turn_id": "turn-1", "character_offset": 16, "word_index": 4},
    )
    await asyncio.gather(*tasks)

    lifecycle = [
        AudioPlaybackLifecycle.model_validate(call.args[1])
        for call in agent.publish.await_args_list
        if call.args[0] == Topics.AUDIO_PLAYBACK_LIFECYCLE
    ]
    retry_frame = agent.audio_queue.get_nowait()
    assert [event.state for event in lifecycle] == ["INTERRUPTED"]
    assert lifecycle[0].utterance_id == "utterance-1"
    assert lifecycle[0].heard_offset == 8
    assert retry_frame[3] == "turn-1:1"
    assert retry_frame[4] == "turn-1"


@pytest.mark.asyncio
async def test_scoped_stop_preserves_the_identity_of_the_frame_actually_playing():
    agent = _make_agent()
    agent._active_turn_id = "new-turn"
    agent._active_utterance_id = "old-utterance"
    agent._active_utterance_turn_id = "old-turn"
    lifecycle_key = ("old-utterance", "old-turn")
    agent._lifecycle_started.add(lifecycle_key)
    agent._lifecycle_seq[lifecycle_key] = 2
    agent._lifecycle_position[("old-utterance", "old-turn")] = (3, 12)
    agent._flush_downstream_audio = AsyncMock()
    tasks = []

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    agent.spawn = MagicMock(side_effect=spawn)
    await agent._on_audio_stop({"turn_id": "new-turn", "flush": True})
    await asyncio.gather(*tasks)

    lifecycle = [
        AudioPlaybackLifecycle.model_validate(call.args[1])
        for call in agent.publish.await_args_list
        if call.args[0] == Topics.AUDIO_PLAYBACK_LIFECYCLE
    ]
    assert len(lifecycle) == 1
    assert lifecycle[0].state == "INTERRUPTED"
    assert lifecycle[0].turn_id == "old-turn"
    assert lifecycle[0].utterance_id == "old-utterance"
    assert lifecycle[0].heard_offset == 12


@pytest.mark.asyncio
async def test_first_frame_source_failure_emits_failed_without_claiming_heard_audio():
    agent = _make_agent()
    agent.audio_source = MagicMock()
    agent.audio_source.capture_frame = AsyncMock(
        side_effect=RuntimeError("source down")
    )
    tasks = []

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    agent.spawn = MagicMock(side_effect=spawn)
    await agent._on_nats_audio(
        b"\x00\x00" * 4,
        metadata={"turn_id": "turn-1", "character_offset": 20, "word_index": 5},
    )
    worker = asyncio.create_task(agent._audio_playback_worker())
    try:
        await asyncio.wait_for(agent.audio_queue.join(), timeout=2)
        await asyncio.gather(*tasks)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    event = next(
        AudioPlaybackLifecycle.model_validate(call.args[1])
        for call in agent.publish.await_args_list
        if call.args[0] == Topics.AUDIO_PLAYBACK_LIFECYCLE
    )
    assert event.state == "FAILED"
    assert event.seq == 0
    assert event.heard_offset == 0
    assert event.streamed_offset == 20
    assert agent.lifecycle_protocol_errors == 0
