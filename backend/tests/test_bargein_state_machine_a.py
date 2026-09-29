"""W5-A property checks over the real BrainAgent turn and playback handlers."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from app import clock as app_clock
from app.agents.brain_agent import BrainAgent
from app.clock import ManualClock
from app.cognitive.action_intent import build_action_intent
from app.config import Config
from app.contracts import Topics


class _History:
    def __init__(self):
        self.rows = []

    async def log_message(self, role, text, message_id=None):
        self.rows.append([message_id, role, text])

    async def rewrite_assistant_message(self, text, *, message_id):
        for row in self.rows:
            if row[0] == message_id and row[1] == "assistant":
                row[2] = text
                return


class _NATS:
    """Subject-serial delivery with cross-subject interleaving and redelivery."""

    def __init__(self, agent):
        self.agent = agent
        self.locks = defaultdict(asyncio.Lock)
        self.published = []

    async def deliver(self, subject, payload):
        handlers = {
            Topics.CHAT_INPUT: self.agent._on_chat_input,
            Topics.AUDIO_PERCEPTION: self.agent._on_user_speech_partial,
            Topics.AUDIO_STOP: self.agent._on_audio_stop,
            Topics.AUDIO_PLAYBACK_PROGRESS: self.agent._on_audio_playback_progress,
            Topics.AUDIO_PLAYBACK_LIFECYCLE: self.agent._on_audio_playback_lifecycle,
        }
        async with self.locks[subject]:
            await handlers[subject](payload)

    async def publish(self, subject, payload):
        self.published.append((app_clock.monotonic(), subject, payload))


class BargeInMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        # Every W5 timer sleeps on this clock (`app.clock.sleep`), so it fires
        # only when a rule settles time forward, never between rules.
        self.clock = ManualClock(datetime.fromtimestamp(1_000_000.0))
        self._clock_token = app_clock._current.set(self.clock)
        self._old_config = {
            name: getattr(Config, name)
            for name in (
                "BARGE_IN_ONSET_GRACE_S",
                "PROACTIVE_GRACE_WINDOW_S",
                "REPLY_TERMINAL_WAIT_S",
                "USER_MID_UTTERANCE_TIMEOUT_S",
            )
        }
        Config.BARGE_IN_ONSET_GRACE_S = 0.0
        Config.PROACTIVE_GRACE_WINDOW_S = 0.005
        Config.REPLY_TERMINAL_WAIT_S = 0.01
        Config.USER_MID_UTTERANCE_TIMEOUT_S = 0.02
        self.history = _History()
        with patch("app.agents.brain_agent.CognitiveService"):
            self.agent = BrainAgent(
                ollama_url="http://unused",
                graph_db=MagicMock(),
                memory_store=MagicMock(),
                conversation_store=self.history,
            )
        self.agent.publish = AsyncMock()
        self.agent.set_state = AsyncMock()
        self.agent.conversational_runtime = MagicMock()
        self.agent.conversational_runtime.calculate_pacing_parameters.return_value = {
            "silence_duration_ms": 0.0
        }
        self.agent.conversational_runtime.monitor_stream_and_fill.side_effect = (
            lambda generator, **_: generator
        )
        core = MagicMock()
        core.state.last_speculative_intent = None
        core.state.get_context_snapshot.return_value = {}
        core.state.release_adrenaline = AsyncMock()
        core.state.persist_state = AsyncMock()
        core.workspace_store.get_snapshot = AsyncMock(return_value=None)

        async def process_event(raw_event, **_):
            turn_id = raw_event["metadata"]["turn_id"]
            intent = build_action_intent(
                turn_id=turn_id,
                workspace_epoch=0,
                workspace_revision=0,
                kind="SPEAK",
                behavior_decision={},
            )
            yield {"type": "action_intent", "data": intent.model_dump()}
            await asyncio.sleep(0)
            yield {"type": "content", "data": "One reply has several words."}
            yield {"type": "done"}

        async def proactive(thought_prompt):
            await app_clock.sleep(0.01)
            yield {"type": "content", "data": "A brief thought."}
            yield {"type": "done"}

        core.process_event = process_event
        core.generate_proactive_response = proactive
        self._real_core = self.agent.cognitive_core
        self.agent.cognitive_core = core
        self.nats = _NATS(self.agent)
        self.agent.published = self.nats.published
        self.agent.publish = self.nats.publish
        self.turns: list[str] = []
        self.started_replies: set[str] = set()
        self.terminal_replies: set[str] = set()
        self.reply_rows: dict[str, object] = {}
        self.reply_texts: dict[str, str] = {}
        self.seq: dict[str, int] = defaultdict(int)
        self._serial = 0

    def _run(self, awaitable):
        return self.loop.run_until_complete(asyncio.wait_for(awaitable, 2.0))

    def _settle(self, seconds):
        """Move virtual time forward `seconds`, letting woken work run."""

        async def advance():
            for _ in range(4):
                self.clock.advance(seconds / 4)
                for _ in range(8):
                    await asyncio.sleep(0)

        self._run(advance())

    def _id(self):
        self._serial += 1
        return f"w5-{self._serial}"

    def _input(self, source="whisper", importance=None, utterance_id=None):
        turn_id = self._id()
        payload = {
            "text": "hello" if source != "subconscious" else "thought",
            "turn_id": turn_id,
            "utterance_id": utterance_id or turn_id,
            "metadata": {"source": source, "importance": importance},
        }
        self._run(self.nats.deliver(Topics.CHAT_INPUT, payload))
        self.turns.append(turn_id)
        self.reply_texts[turn_id] = (
            "A brief thought."
            if source == "subconscious"
            else "One reply has several words."
        )
        return turn_id, payload

    def _terminal(self, turn_id, state="INTERRUPTED", offset=4, flushed=False):
        entry = self.agent._reply_ledger.get(turn_id)
        if entry is not None and entry.started:
            self.started_replies.add(turn_id)
            self.reply_rows[turn_id] = entry.message_id
            self.reply_texts[turn_id] = entry.text
        seq = self.seq[turn_id]
        payload = {
            "utterance_id": turn_id,
            "turn_id": turn_id,
            "seq": seq,
            "state": state,
            "words_played": 1 if offset else 0,
            "words_streamed": 8,
            "heard_offset": offset,
            "streamed_offset": 28,
            "flushed": flushed,
        }
        self.seq[turn_id] += 1
        self._run(self.nats.deliver(Topics.AUDIO_PLAYBACK_LIFECYCLE, payload))
        self._run(self.nats.deliver(Topics.AUDIO_PLAYBACK_LIFECYCLE, payload))
        if not flushed:
            self.terminal_replies.add(turn_id)

    def _finish_active_user_reply(self):
        active_id = self.agent._active_response_turn_id
        entry = self.agent._reply_ledger.get(active_id)
        if entry is not None and entry.source == "user":
            self._settle(0.012)
            if active_id in self.agent._reply_ledger:
                self._terminal(active_id, state="COMPLETED", offset=28)
        self.agent._last_user_partial_at = None

    @rule()
    def accept_user_input_returns_without_waiting_for_generation(self):
        turn_id, _ = self._input()
        # I5, structurally: the handler returned and the turn is still
        # running. A wall-clock bound here was flaky under load (70 ms on a
        # busy machine); I12's timing is test_bargein_w5_regressions::test_i12_*.
        task = self.agent._active_generation_task
        assert task is not None and not task.done()
        self._settle(0.012)
        assert any(
            subject == Topics.CHAT_OUTPUT and payload.get("turn_id") == turn_id
            for _at, subject, payload in self.nats.published
        )
        self._terminal(turn_id, state="COMPLETED", offset=28)

    @rule()
    def subconscious_input_can_be_accepted_or_declined(self):
        self._input("subconscious", importance=0.95)

    @rule()
    def high_proactive_turn_waits_for_exact_grace_window(self):
        self._finish_active_user_reply()
        turn_id, _ = self._input(
            "subconscious", importance=Config.PROACTIVE_GRACE_MIN_IMPORTANCE
        )
        self._settle(0.012)
        entry = self.agent._reply_ledger.get(turn_id)
        assert entry is not None and entry.started
        self.started_replies.add(turn_id)
        self.reply_rows[turn_id] = entry.message_id
        self.reply_texts[turn_id] = entry.text
        partial_at = app_clock.monotonic()
        self._run(
            self.nats.deliver(
                Topics.AUDIO_PERCEPTION,
                {"text": "I have a thought", "utterance_id": self._id()},
            )
        )
        assert not any(
            payload.get("reason") == "proactive_grace_expired"
            and payload.get("turn_id") == turn_id
            for _, _, payload in self.nats.published
        )
        self._settle(Config.PROACTIVE_GRACE_WINDOW_S + 0.002)
        stopped_at = next(
            at
            for at, _, payload in self.nats.published
            if payload.get("reason") == "proactive_grace_expired"
            and payload.get("turn_id") == turn_id
        )
        elapsed = stopped_at - partial_at
        assert Config.PROACTIVE_GRACE_WINDOW_S <= elapsed
        assert elapsed <= Config.PROACTIVE_GRACE_WINDOW_S + 0.05
        self._terminal(turn_id, offset=4)

    @rule()
    def low_proactive_turn_cedes_during_first_partial(self):
        self._finish_active_user_reply()
        turn_id, _ = self._input("subconscious", importance=0.5)
        self._settle(0.012)
        entry = self.agent._reply_ledger.get(turn_id)
        assert entry is not None and entry.started
        self.started_replies.add(turn_id)
        self.reply_rows[turn_id] = entry.message_id
        self.reply_texts[turn_id] = entry.text
        started = app_clock.monotonic()
        self._run(
            self.nats.deliver(
                Topics.AUDIO_PERCEPTION,
                {"text": "please", "utterance_id": self._id()},
            )
        )
        stopped = [
            at
            for at, _, payload in self.nats.published
            if payload.get("reason") == "proactive_ceded"
            and payload.get("turn_id") == turn_id
        ]
        assert stopped and stopped[-1] - started < Config.PROACTIVE_GRACE_WINDOW_S
        self._terminal(turn_id, offset=4)

    @rule()
    def user_partial_declines_self_thought_without_other_effects(self):
        user_id, _ = self._input()
        self._settle(0.012)
        self._run(
            self.nats.deliver(
                Topics.AUDIO_PERCEPTION,
                {"text": "one moment", "utterance_id": self._id()},
            )
        )
        before = (
            self.agent._active_response_turn_id,
            len(self.agent._outcome_history),
            len(self.agent.published),
            len(self.history.rows),
        )
        _, payload = self._input("subconscious", importance=0.99)
        after = (
            self.agent._active_response_turn_id,
            len(self.agent._outcome_history),
            len(self.agent.published),
            len(self.history.rows),
        )
        assert before == after
        assert payload["utterance_id"] in self.agent.declined_proactive_inputs
        assert self.agent._active_response_turn_id == user_id

    @rule()
    def significant_thought_interrupts_user_reply_outside_partial_window(self):
        user_id, _ = self._input()
        self._settle(0.012)
        thought_id, _ = self._input(
            "subconscious", importance=Config.SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE
        )
        assert any(
            payload.get("reason") == "self_thought_interrupt"
            and payload.get("turn_id") == user_id
            for _, _, payload in self.nats.published
        )
        self._terminal(user_id, offset=4)
        self._settle(0.012)
        self._terminal(thought_id, state="COMPLETED", offset=16)

    @rule()
    def partial_speech_is_accepted_and_updates_mid_utterance_clock(self):
        self._run(
            self.nats.deliver(
                Topics.AUDIO_PERCEPTION,
                {"text": "please", "utterance_id": self._id()},
            )
        )

    @rule()
    def every_stop_kind_is_safe_to_redeliver(self):
        target = self.turns[-1] if self.turns else "unknown"
        for reason, speculative, flush in (
            ("confirmed_command", False, False),
            ("facial_reflex_startle", False, False),
            ("speculative", True, False),
            ("flush", False, True),
        ):
            payload = {
                "interrupt": True,
                "speculative": speculative,
                "flush": flush,
                "reason": reason,
                "turn_id": target,
            }
            self._run(self.nats.deliver(Topics.AUDIO_STOP, payload))
            before = len(self.agent.reply_resolutions)
            before_outcomes = len(self.agent._outcome_history)
            before_rows = len(self.history.rows)
            before_hormone = (
                self.agent.cognitive_core.state.release_adrenaline.await_count
            )
            self._run(self.nats.deliver(Topics.AUDIO_STOP, payload))
            assert len(self.agent.reply_resolutions) == before
            assert len(self.agent._outcome_history) == before_outcomes
            assert len(self.history.rows) == before_rows
            assert (
                self.agent.cognitive_core.state.release_adrenaline.await_count
                == before_hormone
            )

    @rule()
    def stale_stop_does_not_touch_current_or_ledger_state(self):
        self._input()
        self._settle(0.012)
        before = (
            len(self.agent.reply_resolutions),
            len(self.agent._outcome_history),
            tuple(tuple(row) for row in self.history.rows),
            tuple(self.agent._reply_ledger),
        )
        self._run(
            self.nats.deliver(
                Topics.AUDIO_STOP,
                {
                    "interrupt": True,
                    "speculative": False,
                    "flush": False,
                    "reason": "facial_reflex_startle",
                    "turn_id": "unknown-stale-turn",
                },
            )
        )
        after = (
            len(self.agent.reply_resolutions),
            len(self.agent._outcome_history),
            tuple(tuple(row) for row in self.history.rows),
            tuple(self.agent._reply_ledger),
        )
        assert before == after

    @rule()
    def progress_can_arrive_for_any_reply(self):
        if not self.turns:
            return
        turn_id = self.turns[-1]
        self._run(
            self.nats.deliver(
                Topics.AUDIO_PLAYBACK_PROGRESS,
                {
                    "utterance_id": turn_id,
                    "character_offset": 7,
                    "word_index": 1,
                    "completed": False,
                },
            )
        )

    @rule()
    def duplicate_user_delivery_is_ignored(self):
        utterance_id = self._id()
        _, payload = self._input(utterance_id=utterance_id)
        before = (len(self.turns), len(self.agent._outcome_history))
        self._run(self.nats.deliver(Topics.CHAT_INPUT, payload))
        assert before == (len(self.turns), len(self.agent._outcome_history))

    @rule()
    def next_user_final_preempts_a_running_generation(self):
        first, _ = self._input()
        prior = self.agent._active_generation_task
        assert prior is not None and not prior.done()
        second, _ = self._input()
        assert prior.done()
        assert self.agent._active_response_turn_id == second
        self._terminal(first, offset=0)

    @invariant()
    def ledger_is_bounded_and_resolutions_are_unique(self):
        assert self.agent.reply_ledger_size() <= 32
        ids = [resolution.turn_id for resolution in self.agent.reply_resolutions]
        assert len(ids) == len(set(ids))
        resolved = {resolution.turn_id for resolution in self.agent.reply_resolutions}
        assert self.started_replies - self.terminal_replies <= set(
            self.agent._reply_ledger
        )
        assert self.terminal_replies <= resolved | set(self.agent._reply_ledger)

    @invariant()
    def one_user_outcome_per_resolved_intent_and_none_for_proactive(self):
        for resolution in self.agent.reply_resolutions:
            outcomes = self.agent.get_outcome_history(resolution.turn_id)
            if resolution.source == "user":
                assert len(outcomes) == 1
                assert outcomes[0].status == resolution.status
            else:
                assert outcomes == []
            source_text = self.reply_texts.get(resolution.turn_id, "")
            assert source_text[: resolution.character_offset].strip() == (
                resolution.heard_text
            )
            message_id = self.reply_rows.get(resolution.turn_id)
            if message_id is not None:
                matching_rows = [
                    row
                    for row in self.history.rows
                    if row[0] == message_id and row[1] == "assistant"
                ]
                assert not matching_rows or matching_rows[0][2] == resolution.heard_text

    # I12 (first-output overhead) is measured by
    # test_bargein_w5_regressions.py::test_i12_*: a wall-clock assert inside a
    # Hypothesis machine is flaky under load and also times this harness.

    def teardown(self):
        for task in tuple(self.agent._background_tasks):
            if not task.done():
                task.cancel()
        pending = tuple(self.agent._background_tasks)
        if pending:
            self.loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        for name, value in self._old_config.items():
            setattr(Config, name, value)
        self.loop.close()
        app_clock._current.reset(self._clock_token)
        # Every BrainAgent (and its CognitiveService) starts a SubjectMetrics
        # worker that wakes every 50 ms and never exits on its own; one leaks
        # per example otherwise (hundreds while Hypothesis shrinks).
        for owner in (self.agent, self._real_core):
            metrics = getattr(owner, "_metrics", None)
            if metrics is not None and hasattr(metrics, "shutdown"):
                metrics.shutdown(timeout=0.2)


_examples = int(os.getenv("W5_SM_EXAMPLES", "20"))
BargeInMachine.TestCase.settings = settings(
    max_examples=_examples,
    stateful_step_count=12,
    deadline=None,
    derandomize=True,
)
TestBargeInStateMachineA = BargeInMachine.TestCase
