from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.contracts import (
    AudioPlaybackLifecycle,
    LifecycleApplyResult,
    PlaybackLifecycleTracker,
)


def _event(seq: int, state: str, words: int = 0) -> AudioPlaybackLifecycle:
    return AudioPlaybackLifecycle(
        utterance_id="utt-1",
        turn_id="turn-1",
        seq=seq,
        state=state,
        words_played=words,
        words_streamed=words,
        heard_offset=words,
        streamed_offset=words,
    )


@given(
    st.lists(
        st.sampled_from(["STARTED", "PLAYING", "COMPLETED", "INTERRUPTED", "FAILED"]),
        min_size=1,
        max_size=50,
    )
)
def test_lifecycle_reducer_applies_at_most_one_terminal_and_never_leaves_it(states):
    tracker = PlaybackLifecycleTracker()
    applied_terminals = []
    last_applied_state = None
    for seq, state in enumerate(states):
        result = tracker.apply(_event(seq, state))
        if result is LifecycleApplyResult.APPLIED:
            last_applied_state = state
            if state in tracker._TERMINAL:
                applied_terminals.append(state)
        elif last_applied_state in tracker._TERMINAL:
            assert tracker.get("utt-1", "turn-1").state == last_applied_state
    assert len(applied_terminals) <= 1


@given(st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=30))
def test_lifecycle_reducer_rejects_out_of_order_and_duplicate_sequences(sequences):
    tracker = PlaybackLifecycleTracker()
    applied = []
    for seq in sequences:
        event = _event(seq, "STARTED" if not applied else "PLAYING")
        result = tracker.apply(event)
        if result is LifecycleApplyResult.APPLIED:
            applied.append(seq)
    assert applied == sorted(set(applied))


@given(
    heard=st.integers(min_value=0, max_value=1000),
    streamed=st.integers(min_value=0, max_value=1000),
)
def test_lifecycle_contract_never_accepts_heard_offsets_beyond_streamed(
    heard, streamed
):
    if heard <= streamed:
        AudioPlaybackLifecycle(
            utterance_id="utt-1",
            turn_id="turn-1",
            seq=0,
            state="STARTED",
            words_played=heard,
            words_streamed=streamed,
            heard_offset=heard,
            streamed_offset=streamed,
        )
    else:
        with pytest.raises(ValidationError):
            AudioPlaybackLifecycle(
                utterance_id="utt-1",
                turn_id="turn-1",
                seq=0,
                state="STARTED",
                words_played=heard,
                words_streamed=streamed,
                heard_offset=heard,
                streamed_offset=streamed,
            )


def test_terminal_duplicate_is_ignored_and_conflicting_terminal_is_counted():
    tracker = PlaybackLifecycleTracker()
    assert tracker.apply(_event(0, "STARTED")) is LifecycleApplyResult.APPLIED
    assert tracker.apply(_event(1, "PLAYING")) is LifecycleApplyResult.APPLIED
    assert tracker.apply(_event(2, "COMPLETED")) is LifecycleApplyResult.APPLIED
    assert tracker.apply(_event(3, "COMPLETED")) is LifecycleApplyResult.DUPLICATE
    assert (
        tracker.apply(_event(4, "INTERRUPTED")) is LifecycleApplyResult.PROTOCOL_ERROR
    )
    assert tracker.protocol_errors == 1
    assert tracker.get("utt-1", "turn-1").state == "COMPLETED"


def test_source_failure_before_first_frame_is_terminal():
    tracker = PlaybackLifecycleTracker()
    assert tracker.apply(_event(0, "FAILED")) is LifecycleApplyResult.APPLIED
    assert tracker.apply(_event(1, "COMPLETED")) is LifecycleApplyResult.PROTOCOL_ERROR
    assert tracker.get("utt-1", "turn-1").state == "FAILED"
