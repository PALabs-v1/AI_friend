"""
P4-2: `audio.playback.progress` was contract-and-subscriber-only -- nothing
ever published it, so `_truncate_interrupted_reply`'s progress-known branch
never ran in production. This covers the pipeline that makes it run:

  brain_agent computes (character_offset, word_index) as it publishes each
  speech chunk -> voice-agent passes them through unchanged (Rust side,
  covered in crates/voice-agent's own tests) -> transport_agent relays them
  as audio.playback.progress once that chunk's PCM has actually reached the
  LiveKit audio source, the closest observable "reached the speaker" point
  in this architecture (there is no frontend PCM player to instrument --
  the browser plays a LiveKit WebRTC track via `track.attach()`, opaque to
  application code).
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.brain_agent import BrainAgent, _char_offset_after_word
from app.agents.transport_agent import TransportAgent
from app.contracts import Topics

# --------------------------------------------------------------------------
# _char_offset_after_word
# --------------------------------------------------------------------------


def test_char_offset_after_word_zero_words_is_zero():
    assert _char_offset_after_word("hello there", 0) == 0


def test_char_offset_after_word_matches_true_text_position():
    text = "hello there, how are you"
    # After 2 words ("hello there,") the offset is right after the comma.
    assert _char_offset_after_word(text, 2) == text.index(",") + 1


def test_char_offset_after_word_all_words_is_full_length():
    text = "hello there friend"
    assert _char_offset_after_word(text, 3) == len(text)


def test_char_offset_after_word_clamps_past_the_end():
    """More words requested than exist must not raise -- the caller may ask
    for a count derived from a slightly-ahead source."""
    text = "only two"
    assert _char_offset_after_word(text, 99) == len(text)


def test_char_offset_after_word_is_exact_across_collapsed_whitespace():
    """The whole reason this exists rather than reconstructing via
    `" ".join(text.split())`: real offsets into text with irregular
    whitespace (newlines, doubled spaces), not an approximation."""
    text = "hello\n\nthere   friend"
    # word 2 is "there"; the true offset is right after it in the ORIGINAL
    # string, not in a whitespace-collapsed reconstruction.
    assert _char_offset_after_word(text, 2) == text.index("there") + len("there")


# --------------------------------------------------------------------------
# BrainAgent._publish_speech_chunk -- non-destructive metadata merge
# --------------------------------------------------------------------------


def _agent(mock_llm_service, mock_graph_db, mock_memory_store):
    return BrainAgent(
        ollama_url="http://dummy",
        graph_db=mock_graph_db,
        memory_store=mock_memory_store,
        conversation_store=None,
    )


@pytest.mark.asyncio
async def test_publish_speech_chunk_attaches_offsets_without_dropping_incoming_metadata(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent.publish = AsyncMock()

    await agent._publish_speech_chunk(
        ["hello", "there"],
        turn_id="turn-1",
        incoming_metadata={"session_id": "abc"},
        character_offset=11,
        word_index=2,
    )

    agent.publish.assert_awaited_once()
    _, payload = agent.publish.await_args.args
    assert payload["metadata"]["session_id"] == "abc"
    assert payload["metadata"]["character_offset"] == 11
    assert payload["metadata"]["word_index"] == 2


@pytest.mark.asyncio
async def test_publish_speech_chunk_without_offsets_is_unchanged_from_before(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    """The exception-handler fallback path deliberately omits offsets --
    metadata must pass through exactly as it did before this feature."""
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent.publish = AsyncMock()

    await agent._publish_speech_chunk(
        ["hello"], turn_id="turn-1", incoming_metadata={"session_id": "abc"}
    )

    _, payload = agent.publish.await_args.args
    assert payload["metadata"] == {"session_id": "abc"}


@pytest.mark.asyncio
async def test_publish_speech_chunk_with_offsets_and_no_incoming_metadata(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent.publish = AsyncMock()

    await agent._publish_speech_chunk(
        ["hi"],
        turn_id="turn-1",
        incoming_metadata=None,
        character_offset=2,
        word_index=1,
    )

    _, payload = agent.publish.await_args.args
    assert payload["metadata"] == {"character_offset": 2, "word_index": 1}


@pytest.mark.asyncio
async def test_stale_playback_progress_cannot_overwrite_active_turn(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent._active_response_turn_id = "turn-new"

    await agent._on_audio_playback_progress(
        {
            "utterance_id": "turn-old",
            "character_offset": 99,
            "word_index": 20,
            "completed": False,
        }
    )

    assert agent.last_audio_progress is None


# --------------------------------------------------------------------------
# BrainAgent._stream_to_speech -- offsets advance correctly across chunks
# --------------------------------------------------------------------------


async def _content_stream(chunks):
    for c in chunks:
        yield {"type": "content", "data": c}
    yield {"type": "done"}


def _chat_output_calls(publish_mock):
    """`_stream_to_speech` also calls `set_state`, which publishes its own
    `state.update` messages interleaved with the real chat.output chunks --
    filter down to just the latter."""
    return [
        call
        for call in publish_mock.await_args_list
        if call.args[0] == Topics.CHAT_OUTPUT
    ]


@pytest.mark.asyncio
async def test_stream_to_speech_stamps_increasing_offsets_matching_full_response(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent.publish = AsyncMock()
    # A low buffer/threshold-independent path: force every word over the
    # split-score threshold by using words the real segmenter is very
    # likely to flush eagerly is fragile: same as the plan's own note,
    # use `done` to guarantee at least one flush regardless of segmenter
    # tuning, and check that flush's offset lands at the true end.
    full_text = "The quick brown fox jumps over the lazy dog"

    result = await agent._stream_to_speech(
        _content_stream([full_text]), turn_id="turn-1"
    )

    assert result == full_text
    assert agent.publish.await_count >= 1

    published_offsets = []
    for call in agent.publish.await_args_list:
        _, payload = call.args
        meta = payload.get("metadata") or {}
        if "character_offset" in meta:
            published_offsets.append((meta["character_offset"], meta["word_index"]))

    assert published_offsets, "at least one chunk must carry playback-progress metadata"
    # Offsets must be non-decreasing and every one must be a real boundary
    # in full_text (i.e. never mid-word, and never past the end).
    prev = -1
    for offset, word_index in published_offsets:
        assert offset > prev
        assert offset <= len(full_text)
        assert word_index <= len(full_text.split())
        prev = offset
    # The last published offset for real content must reach the true end.
    assert published_offsets[-1][0] == len(full_text)
    assert published_offsets[-1][1] == len(full_text.split())


@pytest.mark.asyncio
async def test_stream_to_speech_empty_generation_fallback_is_tracked_against_itself(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    """The empty-generation fallback reassigns `full_response = fallback_text`
    before publishing, so -- unlike the exception-handler fallback -- it is
    safe to stamp: `last_assistant_response` really will equal fallback_text."""
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent.publish = AsyncMock()

    result = await agent._stream_to_speech(_content_stream([]), turn_id="turn-1")

    assert result == "I'm having trouble thinking right now..."
    chat_output_calls = _chat_output_calls(agent.publish)
    _, payload = chat_output_calls[0].args
    meta = payload["metadata"]
    assert meta["character_offset"] == len(result)
    assert meta["word_index"] == len(result.split())


@pytest.mark.asyncio
async def test_stream_to_speech_exception_fallback_omits_offsets(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    """Deliberately untracked -- see the comment at that call site.
    `last_assistant_response` will not equal the fallback text spoken here,
    so a fabricated offset would be actively misleading."""
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent.publish = AsyncMock()

    async def _broken_stream():
        yield {"type": "content", "data": "partial "}
        raise RuntimeError("boom")

    await agent._stream_to_speech(_broken_stream(), turn_id="turn-1")

    # Two chat.output publishes: the fallback speech chunk, then the done signal.
    chat_output_calls = _chat_output_calls(agent.publish)
    _, payload = chat_output_calls[0].args
    meta = payload.get("metadata")
    assert meta is None or "character_offset" not in (meta or {})


# --------------------------------------------------------------------------
# TransportAgent._maybe_publish_playback_progress
# --------------------------------------------------------------------------


def _transport_agent() -> TransportAgent:
    with patch("app.agents.transport_agent.Config") as mock_config:
        mock_config.NATS_URL = "nats://127.0.0.1:4222"
        mock_config.LIVEKIT_URL = "ws://127.0.0.1:7880"
        mock_config.LIVEKIT_API_KEY = "k"
        mock_config.LIVEKIT_API_SECRET = "s"
        mock_config.SAMPLE_RATE = 16000
        mock_config.TRANSPORT_AUDIO_QUEUE_SIZE = 8
        agent = TransportAgent()
    agent.publish = AsyncMock()
    agent.spawn = MagicMock(side_effect=lambda coro: coro.close())
    return agent


@pytest.mark.asyncio
async def test_progress_publishes_when_offset_advances():
    agent = _transport_agent()
    agent._maybe_publish_playback_progress("turn-1", 5, 1)
    agent.spawn.assert_called_once()


@pytest.mark.asyncio
async def test_progress_skips_a_duplicate_or_regressed_offset():
    agent = _transport_agent()
    agent._maybe_publish_playback_progress("turn-1", 5, 1)
    agent._maybe_publish_playback_progress("turn-1", 5, 1)  # duplicate
    agent._maybe_publish_playback_progress("turn-1", 3, 1)  # regressed
    assert agent.spawn.call_count == 1


@pytest.mark.asyncio
async def test_progress_republishes_after_the_offset_advances_again():
    agent = _transport_agent()
    agent._maybe_publish_playback_progress("turn-1", 5, 1)
    agent._maybe_publish_playback_progress("turn-1", 12, 3)
    assert agent.spawn.call_count == 2


@pytest.mark.asyncio
async def test_progress_resets_on_turn_change_even_if_the_new_offset_is_lower():
    """A new turn's first chunk must not be swallowed as a "regression"
    just because it happens to have a smaller offset than the previous
    turn's last one."""
    agent = _transport_agent()
    agent._maybe_publish_playback_progress("turn-1", 50, 10)
    agent._maybe_publish_playback_progress("turn-2", 5, 1)
    assert agent.spawn.call_count == 2


@pytest.mark.asyncio
async def test_progress_is_not_published_when_offsets_are_absent():
    agent = _transport_agent()
    agent._maybe_publish_playback_progress("turn-1", None, None)
    agent.spawn.assert_not_called()


@pytest.mark.asyncio
async def test_progress_payload_shape():
    published = {}

    def _capture(coro):
        published["awaitable"] = coro
        coro.close()

    agent = _transport_agent()
    agent.spawn = MagicMock(side_effect=_capture)

    with patch("app.agents.transport_agent.AudioPlaybackProgress") as mock_model:
        instance = MagicMock()
        instance.model_dump.return_value = {"fake": "payload"}
        mock_model.return_value = instance

        agent._maybe_publish_playback_progress("turn-1", 7, 2)

        mock_model.assert_called_once_with(
            utterance_id="turn-1", character_offset=7, word_index=2, completed=False
        )


# --------------------------------------------------------------------------
# TransportAgent._on_nats_audio -- threading offsets into the queue
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_nats_audio_queues_character_offset_and_word_index():
    agent = _transport_agent()

    await agent._on_nats_audio(
        b"pcm", metadata={"turn_id": "turn-1", "character_offset": 9, "word_index": 2}
    )

    item = await agent.audio_queue.get()
    _, _, _, turn_id, character_offset, word_index, completed = item
    assert turn_id == "turn-1"
    assert character_offset == 9
    assert word_index == 2
    assert completed is False


@pytest.mark.asyncio
async def test_on_nats_audio_queues_none_offsets_when_metadata_lacks_them():
    agent = _transport_agent()

    await agent._on_nats_audio(b"pcm", metadata={"turn_id": "turn-1"})

    item = await agent.audio_queue.get()
    _, _, _, turn_id, character_offset, word_index, completed = item
    assert turn_id == "turn-1"
    assert character_offset is None
    assert word_index is None
    assert completed is False


@pytest.mark.asyncio
async def test_on_nats_audio_overflow_drops_the_newest_frame_not_the_oldest():
    """Bucket 2 (VOICE_REMEDIATION_PLAN.md): synthesised speech is a fixed artifact
    that must arrive intact, unlike a live stream where freshness beats completeness.
    The old policy evicted the oldest queued frame and spliced the new one in behind
    it -- a hard discontinuity between non-adjacent PCM segments, reported live as
    "grainy" / "can't understand it." Dropping the new frame instead keeps everything
    already queued in its original order.
    """
    agent = _transport_agent()
    agent.audio_queue = asyncio.Queue(
        maxsize=2
    )  # constructor floors below 32; override

    await agent._on_nats_audio(b"first", metadata={"turn_id": "turn-1"})
    await agent._on_nats_audio(b"second", metadata={"turn_id": "turn-1"})
    await agent._on_nats_audio(b"third", metadata={"turn_id": "turn-1"})  # overflows

    assert agent.audio_queue.qsize() == 2
    remaining = []
    while not agent.audio_queue.empty():
        pcm_data, *_ = agent.audio_queue.get_nowait()
        remaining.append(pcm_data)
    assert remaining == [b"first", b"second"]
    assert agent.dropped_audio_frames == 1


# --------------------------------------------------------------------------
# End-to-end: draining a frame with offsets triggers exactly one publish
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_playback_worker_publishes_progress_after_capture_frame():
    agent = _transport_agent()
    agent.audio_source = MagicMock()
    agent.audio_source.capture_frame = AsyncMock()

    await agent._on_nats_audio(
        b"\x00\x00" * 320,  # 320 samples of 16-bit mono silence
        metadata={"turn_id": "turn-1", "character_offset": 40, "word_index": 8},
    )

    worker = asyncio.ensure_future(agent._audio_playback_worker())
    try:
        await asyncio.wait_for(agent.audio_queue.join(), timeout=2.0)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    agent.audio_source.capture_frame.assert_awaited_once()
    # Two spawned publishes: `_on_nats_audio` firing Bucket 3's playback
    # backlog telemetry as it enqueues the frame, and the playback worker
    # firing playback-progress after `capture_frame` (the original P4-2
    # behaviour this test was written for).
    assert agent.spawn.call_count == 2


# --------------------------------------------------------------------------
# Bucket 3 (VOICE_REMEDIATION_PLAN.md): playback backlog telemetry
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_nats_audio_publishes_playback_backlog_telemetry():
    """ConversationalRuntime needs transport_agent's outbound queue depth to
    suppress a new filler while a previous turn's audio is still draining
    (VOICE_FILLER_MAX_PLAYBACK_BACKLOG) -- this config existed but was wired
    nowhere in the codebase before this bucket, so the signal never reached
    anything that could act on it."""
    agent = _transport_agent()

    with patch("app.agents.transport_agent.AudioPlaybackBacklog") as mock_model:
        instance = MagicMock()
        instance.model_dump.return_value = {"fake": "backlog"}
        mock_model.return_value = instance

        await agent._on_nats_audio(b"\x00\x00" * 4, metadata={"turn_id": "turn-1"})

    mock_model.assert_called_once_with(
        queue_depth=1, capacity=agent.audio_queue.maxsize
    )
    agent.publish.assert_called_once()
    topic, payload = agent.publish.call_args.args
    assert topic == Topics.AUDIO_PLAYBACK_BACKLOG
    assert payload == {"fake": "backlog"}


@pytest.mark.asyncio
async def test_on_nats_audio_throttles_backlog_telemetry_publishes():
    """This fires on every PCM frame -- as often as every ~20ms of audio.
    Publishing backlog telemetry at that rate would flood NATS for no
    benefit: VOICE_FILLER_MIN_INTERVAL_SECONDS's 1.5s floor means nothing
    downstream needs data fresher than the throttle interval already gives."""
    agent = _transport_agent()

    await agent._on_nats_audio(b"\x00\x00" * 4, metadata={"turn_id": "turn-1"})
    await agent._on_nats_audio(b"\x00\x00" * 4, metadata={"turn_id": "turn-1"})

    assert agent.publish.call_count == 1

    # Simulate the throttle window having elapsed since the last publish.
    agent._last_backlog_publish_at = 0.0
    await agent._on_nats_audio(b"\x00\x00" * 4, metadata={"turn_id": "turn-1"})

    assert agent.publish.call_count == 2


@pytest.mark.asyncio
async def test_audio_playback_worker_publishes_backlog_telemetry_after_drain():
    """Reviewer finding: backlog telemetry used to publish only from the
    enqueue side (`_on_nats_audio`), never from the drain side. If a report
    reached the brain at a high depth and the queue then fully emptied
    during silence -- no new PCM arriving to trigger another enqueue-side
    publish -- `last_playback_backlog` on the brain stayed stuck high
    indefinitely, suppressing the next turn's filler for no live reason.
    Publishing from the drain side too means an emptied queue eventually
    reports its own zero depth."""
    agent = _transport_agent()
    agent.audio_source = MagicMock()
    agent.audio_source.capture_frame = AsyncMock()

    await agent._on_nats_audio(b"\x00\x00" * 320, metadata={"turn_id": "turn-1"})
    # Open the throttle window so the drain-side publish under test isn't
    # swallowed by the enqueue-side publish that just fired microseconds
    # earlier in the same call.
    agent._last_backlog_publish_at = 0.0
    agent.publish.reset_mock()

    worker = asyncio.ensure_future(agent._audio_playback_worker())
    try:
        await asyncio.wait_for(agent.audio_queue.join(), timeout=2.0)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    backlog_calls = [
        call
        for call in agent.publish.call_args_list
        if call.args and call.args[0] == Topics.AUDIO_PLAYBACK_BACKLOG
    ]
    assert len(backlog_calls) == 1
    assert backlog_calls[0].args[1]["queue_depth"] == 0


@pytest.mark.asyncio
async def test_on_playback_backlog_records_the_reported_queue_depth(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    """The consumer side of the same bucket: ConversationalRuntime reads
    `last_playback_backlog` off the agent, not off the raw NATS message, so
    this is what actually has to be right."""
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    assert agent.last_playback_backlog == 0  # cold-start default

    await agent._on_playback_backlog({"queue_depth": 7, "capacity": 256})

    assert agent.last_playback_backlog == 7


@pytest.mark.asyncio
async def test_on_playback_backlog_ignores_a_malformed_message(
    mock_llm_service, mock_graph_db, mock_memory_store
):
    agent = _agent(mock_llm_service, mock_graph_db, mock_memory_store)
    agent.last_playback_backlog = 3

    await agent._on_playback_backlog({"capacity": 256})  # missing queue_depth

    assert agent.last_playback_backlog == 3  # unchanged, not reset to a default
