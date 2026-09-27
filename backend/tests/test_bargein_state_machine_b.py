"""Independent W5-B state machine, authored from the frozen ADR only.

The main machine is feature-gated because this branch is the pre-W5 code. Set
W5_SM_EXAMPLES=1000 for the reconciliation run. Set W5_SM_LEGACY=1 to run the
three small Hypothesis counterexample probes against the old implementation.

Spec map: ADR-W5 §3-7 (especially §4 transitions, §5 event matrix, §6 I1-I12,
§7 frozen interface), W5.md lines 46-64, ADR-003 lines 47-88, and DR-026..030.
The reference model below records accepted turns, reply text/playback offsets,
terminal facts, and history effects independently of BrainAgent's internals.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import find, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from app import clock as app_clock
from app.agents.brain_agent import BrainAgent
from app.clock import ManualClock
from app.cognitive.action_intent import build_action_intent
from app.config import Config
from app.contracts import (
    AudioPlaybackLifecycle,
    LifecycleApplyResult,
    PlaybackLifecycleTracker,
)

TEXT = "I went to the market and bought apples and pears"


def _reply_text(turn_id: str) -> str:
    return f"{turn_id}: {TEXT}"


CONFIG_DEFAULTS = {
    "PROACTIVE_GRACE_MIN_IMPORTANCE": 0.75,
    "PROACTIVE_GRACE_WINDOW_S": 0.6,
    "SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE": 0.9,
    "USER_MID_UTTERANCE_TIMEOUT_S": 1.2,
    "REPLY_TERMINAL_WAIT_S": 2.0,
}
FEATURE_READY = hasattr(BrainAgent, "_on_user_speech_partial")
LEGACY = os.getenv("W5_SM_LEGACY") == "1"


class _History:
    """History double with ADR-003's message-id-only rewrite contract."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str, str | None]] = []

    async def log_message(
        self, role: str, content: str, message_id: str | None = None
    ) -> None:
        self.rows.append({"role": role, "text": content, "id": message_id})
        self.writes.append(("append", role, message_id))

    async def rewrite_assistant_message(self, content: str, *, message_id: str) -> None:
        self.writes.append(("rewrite", "assistant", message_id))
        for row in self.rows:
            if row["role"] == "assistant" and row["id"] == message_id:
                row["text"] = content
                return


@dataclass
class _ExpectedReply:
    turn_id: str
    source: str
    text: str = TEXT
    offset: int = 0
    started: bool = False
    speaking: bool = False
    terminal: str | None = None
    heard_text: str = ""
    history_id: str | None = None
    intent: bool = True
    generating: bool = False


class _FakeNATS:
    """FIFO per subject, concurrent across subjects, with transport terminals.

    publish captures every wire effect. Each subject owns one worker, matching
    nats-py's per-subscription serial callback rule. A confirmed stop flushes
    the targeted started utterance and schedules its terminal lifecycle event;
    a normal chat.output done frame schedules COMPLETED. Terminal callbacks are
    idempotently deduplicated by (turn, state, flushed), while explicit test
    redelivery still reaches the brain a second time.
    """

    def __init__(self, agent: BrainAgent) -> None:
        self.agent = agent
        self.effects: list[tuple[str, dict[str, Any]]] = []
        self.effect_times: list[tuple[float, str, dict[str, Any]]] = []
        self.queues: dict[str, deque[tuple[dict[str, Any], asyncio.Future]]] = (
            defaultdict(deque)
        )
        self.workers: dict[str, asyncio.Task] = {}
        self.started: dict[str, int] = {}
        self.suppress_started_for: set[str] = set()
        self.active_turn: str | None = None
        self.speaking: set[str] = set()
        self.lifecycle_seq: dict[str, int] = defaultdict(int)
        self.terminals: set[tuple[str, str, bool]] = set()
        self.lifecycle_events: list[dict[str, Any]] = []
        self.lifecycle_terminal_utterances: set[str] = set()
        self.started_utterances: set[str] = set()
        self.transport_terminal_utterances: set[str] = set()
        # Reviewer fix: what an event means for the stream is decided by the
        # W4 reducer the brain also runs (verified by W4's own property
        # tests), not by a looser seq rule of this harness's own.
        self.reducer = PlaybackLifecycleTracker()
        # turn -> the self-correction retry take queued or playing after a
        # flushed INTERRUPTED (DR-029).
        self.retry_tasks: dict[str, asyncio.Task] = {}
        self.last_completion: dict[str, asyncio.Future] = {}
        self.hold_transport_terminals = False
        self._message_n = 0
        self._clock = 1_000.0

    def now(self) -> float:
        return app_clock.monotonic()

    async def advance(self, seconds: float) -> None:
        # Virtual time only: every brain timer and transport delay sleeps on
        # the harness's ManualClock, so nothing fires between rules on its own
        # and Hypothesis replays are deterministic.
        clock = app_clock._current.get()
        end = clock.monotonic() + seconds
        await self.drain()
        # Step to each pending timer's own deadline, so a timer fires at its
        # deadline and not at the end of a coarse step (a 5 ms grace window
        # once fired 0.525 s late).
        while True:
            deadline = clock.next_deadline()
            if deadline is None or deadline > end:
                break
            clock.advance(max(0.0, deadline - clock.monotonic()))
            await self.drain()
            # A timer that is still pending at its own deadline would make
            # this loop spin forever; fail loudly instead.
            assert clock.next_deadline() != deadline, (
                f"timer at {deadline} did not fire when the clock reached it"
            )
        clock.advance(max(0.0, end - clock.monotonic()))
        await self.drain()

    async def publish(self, subject: str, data: dict[str, Any]) -> None:
        payload = dict(data)
        self.effects.append((subject, payload))
        self.effect_times.append((app_clock.monotonic(), subject, payload))
        if subject == "chat.output" and payload.get("content"):
            turn = str(payload.get("turn_id") or "")
            first_chunk = turn not in self.started
            self.started.setdefault(turn, 0)
            self.started[turn] += 1
            if first_chunk:
                self.started_utterances.add(turn)
                if turn not in self.suppress_started_for:
                    await self._lifecycle(turn, "STARTED", flushed=False)
        if subject == "audio.stop":
            await self._brain_self_stop(payload)
            if payload.get("speculative"):
                return
            if payload.get("flush"):
                turn = payload.get("turn_id")
                if turn in self.started:
                    await self._terminal(turn, "INTERRUPTED", flushed=True)
                    self._spawn_retry(turn)
                return
            # Reviewer fix: an unscoped stop (a user final) flushes everything
            # playing, including an older reply still draining, not only the
            # active turn, as the real transport does.
            turns = (
                [payload["turn_id"]]
                if payload.get("turn_id")
                else [started for started in self.started if self._playing(started)]
                or [self.active_turn]
            )
            for turn in turns:
                if (
                    not self.hold_transport_terminals
                    and turn
                    and self._stop_applies(str(payload.get("reason")), turn)
                ):
                    await self._interrupt(turn)
        elif subject == "chat.output" and payload.get("done"):
            turn = str(payload.get("turn_id") or "")
            if turn in self.started:
                asyncio.create_task(self._terminal_after(turn))

    async def _terminal_after(self, turn: str) -> None:
        await app_clock.sleep(0.012)
        if (
            not self.hold_transport_terminals
            and turn not in self.transport_terminal_utterances
        ):
            await self._terminal(turn, "COMPLETED", flushed=False)

    def _spawn_retry(self, turn: str) -> None:
        self.started_utterances.add(f"{turn}:retry")
        self.retry_tasks[turn] = asyncio.create_task(self._retry_after_flush(turn))

    def _playing(self, turn: str) -> bool:
        """Audio for `turn` is playing or queued: its own take, or the retry
        take a flush left queued (reviewer fix: a stop drops that too, as the
        real transport drops queued audio)."""
        retry = f"{turn}:retry"
        return turn not in self.transport_terminal_utterances or (
            retry in self.started_utterances
            and retry not in self.transport_terminal_utterances
        )

    async def _interrupt(self, turn: str) -> None:
        if turn not in self.transport_terminal_utterances:
            await self._terminal(turn, "INTERRUPTED", flushed=False)
            return
        task = self.retry_tasks.pop(turn, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        await self._terminal(
            turn, "INTERRUPTED", flushed=False, utterance_id=f"{turn}:retry"
        )

    async def _retry_after_flush(self, turn: str) -> None:
        utterance = f"{turn}:retry"
        self.started_utterances.add(utterance)
        await app_clock.sleep(0.003)
        await self._lifecycle(turn, "STARTED", flushed=False, utterance_id=utterance)
        await app_clock.sleep(0.003)
        await self._terminal(turn, "COMPLETED", flushed=False, utterance_id=utterance)

    async def _brain_self_stop(self, payload: dict[str, Any]) -> None:
        # Self-stops are observed on receipt but must not cause another brain
        # action. The publish itself remains observable to the transport.
        if payload.get("reason") in {
            "confirmed_user_speech",
            "proactive_ceded",
            "proactive_grace_expired",
            "self_thought_interrupt",
        }:
            await self._dispatch("audio.stop", payload, internal=True)

    def _stop_applies(self, reason: str, turn: str) -> bool:
        if turn not in self.started or not self._playing(turn):
            return False
        if reason == "confirmed_command":
            return True
        if reason in {
            "confirmed_user_speech",
            "proactive_ceded",
            "proactive_grace_expired",
            "self_thought_interrupt",
        }:
            return True
        return turn == self.active_turn

    async def _terminal(
        self,
        turn: str,
        state: str,
        *,
        flushed: bool,
        utterance_id: str | None = None,
    ) -> None:
        utterance = utterance_id or turn
        key = (utterance, state, flushed)
        if key in self.terminals:
            return
        self.terminals.add(key)
        await self._lifecycle(turn, state, flushed=flushed, utterance_id=utterance)

    async def _lifecycle(
        self,
        turn: str,
        state: str,
        *,
        flushed: bool,
        utterance_id: str | None = None,
    ) -> None:
        utterance = utterance_id or turn
        if state == "STARTED":
            self.started_utterances.add(utterance)
        if state in {"STARTED", "PLAYING"}:
            self.speaking.add(turn)
        elif state in {"COMPLETED", "INTERRUPTED", "FAILED"} and not (
            flushed and state == "INTERRUPTED"
        ):
            self.speaking.discard(turn)
        seq = self.lifecycle_seq[utterance]
        self.lifecycle_seq[utterance] += 1
        event = {
            "utterance_id": utterance,
            "turn_id": turn,
            "seq": seq,
            "state": state,
            "words_played": 10 if state == "COMPLETED" else 3,
            "words_streamed": 10,
            "heard_offset": 100 if state == "COMPLETED" else 12,
            "streamed_offset": 100,
            "flushed": flushed,
            "timestamp": self._clock,
        }
        self.lifecycle_events.append(dict(event))
        self._record_terminal(event)
        await self._dispatch("audio.playback.lifecycle", event, internal=True)

    def _record_terminal(self, event: dict[str, Any]) -> bool:
        """Apply one of the transport's own events; True if the stream took it."""
        utterance = str(event.get("utterance_id"))
        sequence = int(event.get("seq", -1))
        # The transport's next seq for this utterance always comes after any
        # seq already on the stream, its own or injected.
        self.lifecycle_seq[utterance] = max(self.lifecycle_seq[utterance], sequence + 1)
        try:
            result = self.reducer.apply(AudioPlaybackLifecycle.model_validate(event))
        except ValueError:
            return False
        if result is not LifecycleApplyResult.APPLIED:
            return False
        if event.get("state") in {"COMPLETED", "INTERRUPTED", "FAILED"}:
            self.transport_terminal_utterances.add(utterance)
            # Reviewer fix: only a flushed INTERRUPTED is a rejected take that
            # resolves nothing (ADR-W5 section 4, DR-029).
            if not (event.get("flushed") and event.get("state") == "INTERRUPTED"):
                self.lifecycle_terminal_utterances.add(utterance)
        return True

    async def _dispatch(
        self, subject: str, payload: dict[str, Any], *, internal: bool = False
    ) -> asyncio.Future:
        self._message_n += 1
        loop = asyncio.get_running_loop()
        done = loop.create_future()
        item = (dict(payload, _sm_internal=internal), done)
        self.queues[subject].append(item)
        if subject not in self.workers or self.workers[subject].done():
            self.workers[subject] = asyncio.create_task(self._worker(subject))
        return done

    async def _worker(self, subject: str) -> None:
        while self.queues[subject]:
            payload, done = self.queues[subject].popleft()
            payload.pop("_sm_internal", None)
            try:
                await self._handler(subject)(payload)
            except Exception as exc:  # surface callback errors to the driver
                if not done.done():
                    done.set_exception(exc)
                raise
            else:
                if not done.done():
                    done.set_result(None)

    def _handler(self, subject: str):
        name = {
            "chat.input": "_on_chat_input",
            "audio.stop": "_on_audio_stop",
            "audio.perception": "_on_user_speech_partial",
            "audio.playback.lifecycle": "_on_audio_playback_lifecycle",
            "audio.playback.progress": "_on_audio_playback_progress",
        }[subject]
        return getattr(self.agent, name)

    async def deliver(self, subject: str, payload: dict[str, Any]) -> None:
        if subject == "audio.playback.lifecycle":
            self.lifecycle_events.append(dict(payload))
            # Reviewer fix: an injected event is one of the transport's own,
            # delivered out of band. The transport's stream takes it the way
            # its own events are taken: its later seqs come after it, it
            # emits no second terminal for an utterance it ended, and a
            # flushed INTERRUPTED (a self-correction's rejected take, DR-029)
            # is followed by the retry take, as `_retry_after_flush` models.
            # Otherwise the transport reused a seq with a different state, or
            # played on after its own terminal: streams no producer emits.
            utterance = str(payload.get("utterance_id"))
            turn = str(payload.get("turn_id"))
            applied = self._record_terminal(payload)
            if (
                applied
                and payload.get("state") == "INTERRUPTED"
                and payload.get("flushed")
                and utterance == turn
                and turn in self.started
                and f"{turn}:retry" not in self.started_utterances
            ):
                self._spawn_retry(turn)
        done = await self._dispatch(subject, payload)
        self.last_completion[subject] = done
        await self.drain()
        for _ in range(20):
            if done.done():
                break
            await asyncio.sleep(0.001)
        if not done.done():
            raise AssertionError(
                f"{subject} handler did not return within scheduling bound"
            )
        done.result()
        if subject == "chat.input":
            declined = getattr(self.agent, "declined_proactive_inputs", ())
            if payload.get("utterance_id") not in declined:
                self.active_turn = str(payload.get("turn_id"))
        if subject == "audio.stop" and not payload.get("speculative"):
            # Reviewer fix: the voice agent honours a stop that names no turn
            # by flushing what is playing, i.e. the active turn.
            turn = payload.get("turn_id") or self.active_turn
            reason = str(payload.get("reason"))
            self_stop_reasons = {
                "confirmed_user_speech",
                "proactive_ceded",
                "proactive_grace_expired",
                "self_thought_interrupt",
            }
            applies = (
                not self.hold_transport_terminals
                and not payload.get("flush")
                and bool(turn)
                and reason not in self_stop_reasons
                and self._stop_applies(reason, str(turn))
            )
            if applies:
                await self._interrupt(str(turn))
                await self.drain()

    async def redeliver(self, subject: str, payload: dict[str, Any]) -> None:
        await self.deliver(subject, payload)

    async def drain(self) -> None:
        # Bounded scheduling slack; intentionally do not await blocked turn
        # generators or a per-subject worker that is blocked in a handler.
        for _ in range(8):
            await asyncio.sleep(0)


def _stop_metrics_threads(*owners) -> None:
    # Every BrainAgent (and its CognitiveService) starts a SubjectMetrics
    # worker that wakes every 50 ms and never exits on its own. Hypothesis
    # builds one harness per example and hundreds while shrinking; left
    # running they reached 1,453 threads and a load average above 800.
    for owner in owners:
        metrics = getattr(owner, "_metrics", None)
        if metrics is not None and hasattr(metrics, "shutdown"):
            metrics.shutdown(timeout=0.2)


class _Runtime:
    def calculate_pacing_parameters(
        self, _snapshot: dict[str, Any]
    ) -> dict[str, float]:
        return {"silence_duration_ms": 0.0}

    def monitor_stream_and_fill(self, generator, **_kwargs):
        return generator


class _Harness:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.clock = ManualClock(datetime.fromtimestamp(1_000_000.0))
        self._clock_token = app_clock._current.set(self.clock)
        self.history = _History()
        self.agent = BrainAgent(
            ollama_url="http://unused",
            graph_db=MagicMock(),
            memory_store=MagicMock(),
            conversation_store=self.history,
        )
        self.agent.publish = self.nats_publish
        self.agent.set_state = AsyncMock()
        self.agent.conversational_runtime = _Runtime()
        self.nats = _FakeNATS(self.agent)
        self._blocks: dict[str, asyncio.Event] = {}
        self._all_started = asyncio.Event()
        # Turns whose Stage 6 `action_intent` the stub has handed to the
        # brain. I2 applies to a user reply *with an intent*: one cancelled
        # while still blocked never reached Stage 6 and has no outcome.
        self.intent_turns: set[str] = set()

        core = MagicMock()
        core.state.last_speculative_intent = None
        core.state.get_context_snapshot = MagicMock(return_value={})
        core.state.release_adrenaline = AsyncMock()
        core.state.persist_state = AsyncMock()
        core.workspace_store.get_snapshot = AsyncMock(return_value=None)

        async def process_event(raw_event: dict[str, Any], **_kwargs):
            md = raw_event["metadata"]
            turn = md["turn_id"]
            block = self._blocks.get(turn)
            if block:
                self._all_started.set()
                await block.wait()
            intent = build_action_intent(
                turn_id=turn,
                workspace_epoch=0,
                workspace_revision=0,
                kind="SPEAK",
                behavior_decision={},
            )
            self.intent_turns.add(turn)
            yield {"type": "action_intent", "data": intent.model_dump()}
            yield {"type": "content", "data": _reply_text(raw_event["content"])}
            yield {"type": "done"}

        async def proactive(thought_prompt: str | None = None, **_kwargs):
            yield {"type": "content", "data": _reply_text(thought_prompt or "")}
            yield {"type": "done"}

        self._real_core = self.agent.cognitive_core
        core.process_event = process_event
        core.generate_proactive_response = proactive
        self.agent.cognitive_core = core
        self.core = core

    async def nats_publish(self, subject: str, payload: dict[str, Any]) -> None:
        await self.nats.publish(str(getattr(subject, "value", subject)), payload)

    def run(self, awaitable):
        return self.loop.run_until_complete(awaitable)

    def close(self) -> None:
        tasks = list(asyncio.all_tasks(self.loop))
        for task in tasks:
            task.cancel()
        if self.loop.is_running():
            return
        if tasks:
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        self.loop.run_until_complete(asyncio.sleep(0))
        self.loop.close()
        app_clock._current.reset(self._clock_token)
        _stop_metrics_threads(self.agent, self._real_core)

    def input(
        self,
        turn: str,
        *,
        source: str = "whisper",
        importance: float = 0.5,
        category: str = "useful_to_user",
        blocked: bool = False,
    ) -> dict[str, Any]:
        if blocked:
            self._blocks.setdefault(turn, asyncio.Event())
        return {
            "text": f"say {turn}",
            "utterance_id": f"utt-{turn}",
            "turn_id": turn,
            "metadata": {
                "source": source,
                "importance": importance,
                "category": category,
            },
        }

    async def release(self, turn: str) -> None:
        gate = self._blocks.get(turn)
        if gate:
            gate.set()
        await self.nats.drain()


@dataclass
class _Model:
    """Small black-box oracle: accepted floor and observable reply facts."""

    floor: str | None = None
    replies: dict[str, _ExpectedReply] = field(default_factory=dict)
    accepted_utterances: deque[str] = field(default_factory=lambda: deque(maxlen=256))
    partial_at: float | None = None
    resolved: dict[str, str] = field(default_factory=dict)
    baseline_rows: dict[str, str] = field(default_factory=dict)


class BargeInMachine(RuleBasedStateMachine):
    """The rules deliberately call public frozen handlers through FakeNATS."""

    def __init__(self) -> None:
        super().__init__()
        self.h = _Harness()
        self.model = _Model()
        self.serial = 0
        self._chat_payloads: list[dict[str, Any]] = []
        self._stop_payloads: list[dict[str, Any]] = []
        self._lifecycle_payloads: list[dict[str, Any]] = []
        self._progress_payloads: list[dict[str, Any]] = []
        self._input_meta: dict[str, tuple[str, float, str]] = {}
        self._grace_started_at: dict[str, tuple[float, float]] = {}
        self._closed_turns: set[str] = set()
        self._input_latencies: list[float] = []
        self._overflowed_turns: set[str] = set()
        self._timeout_cancelled: set[str] = set()
        self._seen_lifecycle_events = 0
        self._reducer = PlaybackLifecycleTracker()
        self._terminal_by_turn: dict[str, str] = {}

    def teardown(self) -> None:
        # Quiescence (I1): every timer fires and the transport delivers a
        # terminal for every utterance it started.
        self.h.run(self.h.nats.advance(60.0))
        # W4's contract: the transport eventually delivers a terminal for
        # every utterance it started. Deliver any this model still owes.
        self.h.nats.hold_transport_terminals = False

        async def owed_terminals() -> None:
            for turn in tuple(self.h.nats.started):
                if turn not in self.h.nats.transport_terminal_utterances:
                    await self.h.nats._terminal(turn, "COMPLETED", flushed=False)
            for utterance in tuple(self.h.nats.started_utterances):
                if utterance not in self.h.nats.transport_terminal_utterances:
                    turn = utterance.removesuffix(":retry")
                    await self.h.nats._terminal(
                        turn, "COMPLETED", flushed=False, utterance_id=utterance
                    )
            await self.h.nats.drain()

        self.h.run(owed_terminals())
        self.h.run(self.h.nats.advance(60.0))
        self._assert_final_invariants()
        self.h.close()

    def _observed_resolutions(self) -> list[Any]:
        """Every resolution seen so far, in order.

        Reviewer fix: `reply_resolutions` keeps the last 256 only. Invariants
        run after every rule and a rule adds at most 34, so accumulating here
        sees each one before it is evicted; references are kept, so a
        duplicate is still a duplicate (first terminal wins).
        """
        seen = self.__dict__.setdefault("_seen_resolutions", {})
        for item in getattr(self.h.agent, "reply_resolutions", ()):
            seen.setdefault(id(item), item)
        return list(seen.values())

    def _assert_final_invariants(self) -> None:
        resolutions = self._observed_resolutions()
        resolved = {getattr(item, "turn_id", None) for item in resolutions}
        assert (
            self.h.nats.started_utterances <= self.h.nats.transport_terminal_utterances
        )
        for turn in self._terminal_by_turn:
            assert turn in resolved  # I1 §6 128
        if hasattr(self.h.agent, "reply_ledger_size"):
            assert self.h.agent.reply_ledger_size() <= 32  # I11 §6 138

    def _turn(self) -> str:
        self.serial += 1
        return f"t{self.serial}"

    @rule(
        source=st.sampled_from(["whisper", "subconscious"]),
        importance=st.sampled_from([0.2, 0.75, 0.9, 1.0]),
        category=st.sampled_from(["useful_to_user", "self_directed"]),
        blocked=st.booleans(),
    )
    def chat_input(
        self, source: str, importance: float, category: str, blocked: bool
    ) -> None:
        # Reviewer fix: only the user-turn stub honours a block; a blocked
        # proactive turn never signalled that it started.
        blocked = blocked and source == "whisper"
        turn = self._turn()
        payload = self.h.input(
            turn,
            source=source,
            importance=importance,
            category=category,
            blocked=blocked,
        )
        if blocked:
            self.h._all_started.clear()
        self._chat_payloads.append(payload)
        self._input_meta[turn] = (source, importance, category)
        self.model.replies[turn] = _ExpectedReply(
            turn,
            "proactive" if source == "subconscious" else "user",
            text=_reply_text(payload["text"]),
            intent=source != "subconscious",
            generating=blocked,
        )
        prior = self.model.replies.get(self.model.floor)
        mid_utterance = (
            self.model.partial_at is not None
            and self.h.nats.now() - self.model.partial_at
            <= getattr(Config, "USER_MID_UTTERANCE_TIMEOUT_S", 1.2)
        )
        should_decline = (
            source == "subconscious"
            and prior is not None
            and prior.source == "user"
            and (prior.generating or (prior.started and prior.terminal is None))
            and (
                importance
                < getattr(Config, "SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE", 0.9)
                or mid_utterance
            )
        )
        should_self_interrupt = (
            source == "subconscious"
            and prior is not None
            and prior.source == "user"
            and (prior.generating or (prior.started and prior.terminal is None))
            and not mid_utterance
            and importance
            >= getattr(Config, "SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE", 0.9)
        )
        outputs_before = sum(
            1
            for subject, event in self.h.nats.effects
            if subject == "chat.output" and event.get("content")
        )
        before_observables = self._observable_snapshot()
        previous_floor = self.model.floor
        if source == "whisper" and previous_floor is not None:
            self._closed_turns.add(previous_floor)
        accepted_at = app_clock.monotonic()
        accepted_real = time.perf_counter()
        self.h.run(self.h.nats.deliver("chat.input", payload))
        assert self.h.nats.last_completion["chat.input"].done()  # I5 §6 132
        if blocked and not self._is_declined(payload):
            self.h.run(asyncio.wait_for(self.h._all_started.wait(), timeout=0.02))
            assert not self.h._blocks[turn].is_set()  # I5 §6 132; §5 111-119
        # This is an oracle record, not copied from BrainAgent's active fields.
        if source != "subconscious" or not self._is_declined(payload):
            self.model.floor = turn
        else:
            self.model.replies.pop(turn, None)
        if source == "subconscious" and should_decline:
            assert self._is_declined(payload)  # I8 §6 135; DR-026
        if self._is_declined(payload):
            after_observables = self._observable_snapshot()
            assert before_observables[:2] == after_observables[:2]
            assert before_observables[3:] == after_observables[3:]
            assert before_observables[2] != after_observables[2]
        if should_self_interrupt:
            assert any(
                subject == "audio.stop"
                and effect.get("reason") == "self_thought_interrupt"
                and effect.get("turn_id") == prior.turn_id
                for subject, effect in self.h.nats.effects
            )  # I8 §6 135; §5 103; DR-026
        if source == "whisper" and prior and prior.generating:
            resolutions = getattr(self.h.agent, "reply_resolutions", ())
            old = [
                item
                for item in resolutions
                if getattr(item, "turn_id", None) == prior.turn_id
            ]
            assert (
                old
                and str(getattr(old[0].status, "value", old[0].status)) == "CANCELLED"
            )  # I9 §6 136
        if (
            source == "whisper"
            and previous_floor is not None
            and previous_floor in self._grace_started_at
            and prior is not None
            and prior.terminal is None
        ):
            grace_start, _ = self._grace_started_at[previous_floor]
            boundary = min(
                grace_start + getattr(Config, "PROACTIVE_GRACE_WINDOW_S", 0.6),
                accepted_at,
            )
            stop_times = [
                clock
                for clock, subject, effect in self.h.nats.effect_times
                if subject == "audio.stop"
                # Reviewer fix: the user-final stop is unscoped (it silences
                # whatever is playing), so it names no turn. An unscoped stop
                # from an earlier final predates this grace window and says
                # nothing about it.
                and clock >= grace_start
                and effect.get("turn_id") in (previous_floor, None)
                and effect.get("reason")
                in {"confirmed_user_speech", "proactive_grace_expired"}
            ]
            assert stop_times and min(stop_times) >= boundary - 0.002  # I6 §6 133
            assert min(stop_times) <= boundary + 0.05  # I6 §6 133; §5 104
        if source == "whisper":  # reviewer fix: a user final is "whisper" here
            self.model.partial_at = None
            if previous_floor is not None:
                self._grace_started_at.pop(previous_floor, None)
        self._refresh_model()
        outputs_after = sum(
            1
            for subject, event in self.h.nats.effects
            if subject == "chat.output" and event.get("content")
        )
        if not blocked and outputs_after > outputs_before:
            self._input_latencies.append(time.perf_counter() - accepted_real)

    def _is_declined(self, payload: dict[str, Any]) -> bool:
        declined = getattr(self.h.agent, "declined_proactive_inputs", ())
        return payload["utterance_id"] in declined

    @rule(text=st.sampled_from(["", "uh", "I have a question"]))
    def partial(self, text: str) -> None:
        active = self.model.replies.get(self.model.floor)
        importance = self._input_meta.get(self.model.floor or "", ("", 0.0, ""))[1]
        proactive_generating = (
            active is not None
            and active.source == "proactive"
            and active.generating
            and not active.started
            and bool(text.strip())
        )
        low_speaking_proactive = (
            active is not None
            and active.source == "proactive"
            and active.started
            and active.speaking
            # Reviewer fix: a reply that already reached its terminal (it
            # finished playing) is not speaking; nothing to cede.
            and active.terminal is None
            and importance < getattr(Config, "PROACTIVE_GRACE_MIN_IMPORTANCE", 0.75)
            and bool(text.strip())
            # Reviewer fix: a reply cedes once. After the first partial it is
            # cut (awaiting its terminal); a second partial publishes nothing.
            and not any(
                subject == "audio.stop"
                and effect.get("reason") == "proactive_ceded"
                and effect.get("turn_id") == active.turn_id
                for subject, effect in self.h.nats.effects
            )
        )
        ceded_before = sum(
            subject == "audio.stop" and effect.get("reason") == "proactive_ceded"
            for subject, effect in self.h.nats.effects
        )
        payload = {
            "text": text,
            "utterance_id": f"partial-{self.serial}",
            "timestamp": time.time(),
        }
        grace_arrival = app_clock.monotonic()
        self.h.run(self.h.nats.deliver("audio.perception", payload))
        if proactive_generating and active is not None:
            resolutions = getattr(self.h.agent, "reply_resolutions", ())
            assert any(
                getattr(item, "turn_id", None) == active.turn_id
                and str(getattr(item.status, "value", item.status)) == "CANCELLED"
                for item in resolutions
            )  # ADR-W5 §4 66-79; §5 104
        if text.strip():
            self.model.partial_at = self.h.nats.now()
            if (
                self.model.floor is not None
                and active is not None
                and active.source == "proactive"
                and active.speaking
                and importance
                >= getattr(Config, "PROACTIVE_GRACE_MIN_IMPORTANCE", 0.75)
            ):
                self._grace_started_at.setdefault(
                    self.model.floor,
                    (grace_arrival, self.h.nats.now()),
                )
        if low_speaking_proactive:
            ceded = [
                effect
                for subject, effect in self.h.nats.effects
                if subject == "audio.stop" and effect.get("reason") == "proactive_ceded"
            ]
            assert len(ceded) == ceded_before + 1
            assert ceded[-1].get("turn_id") == active.turn_id  # I7 §6 134; §5 104

    @rule(
        reason=st.sampled_from(
            [
                "confirmed_command",
                "confirmed_user_speech",
                "facial_reflex_startle",
                "proactive_ceded",
                "proactive_grace_expired",
                "self_thought_interrupt",
                "system_halt",
                "unknown_reason",
            ]
        ),
        target_kind=st.sampled_from(["active", "ledger", "stale", "unknown", "none"]),
        speculative=st.booleans(),
        flush=st.booleans(),
    )
    def audio_stop(
        self, reason: str, target_kind: str, speculative: bool, flush: bool
    ) -> None:
        turn = self._stop_target(target_kind)
        if target_kind in {"ledger", "stale"} and turn is None:
            return
        payload = {
            "interrupt": True,
            "turn_id": turn,
            "reason": reason,
            "speculative": speculative,
            "flush": flush,
            "utterance_id": f"stop-{self.serial}-{target_kind}-{reason}",
        }
        self._stop_payloads.append(payload)
        before = self._observable_snapshot()
        writes_before = len(self.h.history.writes)
        self.h.run(self.h.nats.deliver("audio.stop", payload))
        self._refresh_model()
        assert not any(
            operation == "append"
            for operation, _, _ in self.h.history.writes[writes_before:]
        )  # I3 §6 130
        # Reviewer fix: a stop naming no turn is unscoped and applies to what
        # is playing (ADR-003; the voice agent always honours one), so it is
        # addressed to the active turn, not stale (I4 covers named turns).
        active = turn == self.model.floor or (
            turn is None and self.model.floor is not None
        )
        ledger = turn is not None and any(
            reply.turn_id == turn and reply.started and reply.terminal is None
            for reply in self.model.replies.values()
        )
        if not active and not ledger:
            assert self._observable_snapshot() == before  # I4 §6 131; §5 105
        floor_reply = self.model.replies.get(self.model.floor or "")
        if (
            active
            and not speculative
            and not flush
            and reason
            not in {
                "confirmed_user_speech",
                "proactive_ceded",
                "proactive_grace_expired",
                "self_thought_interrupt",
            }
            and floor_reply is not None
            and floor_reply.generating
            and not floor_reply.started
            and floor_reply.terminal is None
        ):
            # Reviewer fix: a confirmed stop for the active turn cuts it
            # (§5 105); one that never started resolves CANCELLED at once
            # (§4), so it is no longer a reply in flight (DR-026 gate).
            assert any(
                item.turn_id == floor_reply.turn_id
                and str(getattr(item.status, "value", item.status)) == "CANCELLED"
                for item in getattr(self.h.agent, "reply_resolutions", ())
            )  # §4 66-79
            floor_reply.generating = False
        if (
            speculative
            or flush
            or reason
            in {
                "confirmed_user_speech",
                "proactive_ceded",
                "proactive_grace_expired",
                "self_thought_interrupt",
            }
        ):
            assert self._observable_snapshot() == before  # §5 105; DR-029 7-11

    def _nothing_published(self, turn: str) -> bool:
        """A reply of ours that is still generating: no chunk has gone out.

        Reviewer fix: the transport cannot play what was never sent, so a
        playback fact for such a turn is not an event this world produces
        (the model already ignores one, `_refresh_model`). In production a
        filler can precede the first chunk under the same turn id and the
        brain then treats the reply as speaking; this harness has no fillers.
        """
        reply = self.model.replies.get(turn)
        return (
            reply is not None
            and reply.terminal is None
            and self.h.nats.started.get(turn, 0) == 0
        )

    def _stop_target(self, kind: str) -> str | None:
        if kind == "active":
            return self.model.floor
        if kind == "ledger":
            return next(
                (
                    r.turn_id
                    for r in self.model.replies.values()
                    if r.turn_id != self.model.floor and r.started and not r.terminal
                ),
                None,
            )
        if kind == "stale":
            return next(
                (
                    turn
                    for turn in reversed(tuple(self.model.replies))
                    if turn != self.model.floor and turn in self._terminal_by_turn
                ),
                None,
            )
        if kind == "unknown":
            return "t-unknown"
        return None

    @rule(reason=st.sampled_from(["confirmed_command", "facial_reflex_startle"]))
    @precondition(lambda self: self._stop_target("ledger") is not None)
    def confirmed_stop_for_nonactive_ledger(self, reason: str) -> None:
        turn = self._stop_target("ledger")
        assert turn is not None
        self.h.run(
            self.h.nats.deliver(
                "audio.stop",
                {
                    "interrupt": True,
                    "turn_id": turn,
                    "reason": reason,
                    "speculative": False,
                    "flush": False,
                },
            )
        )
        self._refresh_model()

    @rule(reason=st.sampled_from(["confirmed_command", "facial_reflex_startle"]))
    @precondition(lambda self: self._stop_target("stale") is not None)
    def confirmed_stop_for_stale_reply(self, reason: str) -> None:
        turn = self._stop_target("stale")
        assert turn is not None
        before = self._observable_snapshot()
        self.h.run(
            self.h.nats.deliver(
                "audio.stop",
                {
                    "interrupt": True,
                    "turn_id": turn,
                    "reason": reason,
                    "speculative": False,
                    "flush": False,
                },
            )
        )
        self._refresh_model()
        assert self._observable_snapshot() == before  # I4 §6 131; ADR-003 33-45

    @rule(
        target_kind=st.sampled_from(["active", "ledger", "stale", "unknown"]),
        offset=st.sampled_from([0, 12, len(TEXT) + 7]),
    )
    def progress(self, target_kind: str, offset: int) -> None:
        turn = self._stop_target(target_kind)
        if turn is None:
            turn = "t-unknown"
        if self._nothing_published(turn):
            return
        payload = {
            "utterance_id": turn,
            "character_offset": offset,
            "word_index": 0 if offset == 0 else 3,
            "completed": False,
            "timestamp": self.h.nats.now(),
        }
        self._progress_payloads.append(payload)
        before = self._observable_snapshot()
        self.h.run(self.h.nats.deliver("audio.playback.progress", payload))
        assert self._observable_snapshot() == before  # ADR-W5 §3 42-45; §5 107
        # Progress changes only this reference offset; it cannot resolve.
        if turn in self.model.replies and not self.model.replies[turn].terminal:
            self.model.replies[turn].offset = min(
                offset, len(self.model.replies[turn].text)
            )
            self.model.replies[turn].speaking = True

    @rule(
        state=st.sampled_from(
            ["STARTED", "PLAYING", "COMPLETED", "INTERRUPTED", "FAILED"]
        ),
        flushed=st.booleans(),
        duplicate=st.booleans(),
        sequence=st.integers(min_value=0, max_value=8),
    )
    def lifecycle(
        self, state: str, flushed: bool, duplicate: bool, sequence: int
    ) -> None:
        turn = self._stop_target("ledger") or self.model.floor or "t-unknown"
        if self._nothing_published(turn):
            return
        payload = {
            "utterance_id": turn,
            "turn_id": turn,
            "seq": sequence,
            "state": state,
            "words_played": 3,
            "words_streamed": 10,
            "heard_offset": 12,
            "streamed_offset": len(TEXT),
            "flushed": flushed,
            "timestamp": self.h.nats.now(),
        }
        self._lifecycle_payloads.append(payload)
        before = self._observable_snapshot()
        self.h.run(self.h.nats.deliver("audio.playback.lifecycle", payload))
        self._refresh_model()
        if state == "INTERRUPTED" and flushed:
            assert self._observable_snapshot() == before  # ADR-W5 §4 76-77; DR-029
        if duplicate:
            self.h.run(self.h.nats.redeliver("audio.playback.lifecycle", payload))

    @rule()
    def reordered_lifecycle_pair(self) -> None:
        turn = self.model.floor
        if turn is None or turn not in self.model.replies:
            return
        if self._nothing_published(turn):
            return
        sequence = self.h.nats.lifecycle_seq[turn]
        later = {
            "utterance_id": turn,
            "turn_id": turn,
            "seq": sequence + 1,
            "state": "PLAYING",
            "words_played": 2,
            "words_streamed": 10,
            "heard_offset": 8,
            "streamed_offset": len(self.model.replies[turn].text),
            "flushed": False,
        }
        earlier = dict(later, seq=sequence, state="STARTED", heard_offset=0)
        self.h.run(self.h.nats.deliver("audio.playback.lifecycle", later))
        self._refresh_model()
        snapshot = self._observable_snapshot()
        self.h.run(self.h.nats.deliver("audio.playback.lifecycle", earlier))
        self._refresh_model()
        assert self._observable_snapshot() == snapshot  # ADR-W5 §5 106; W4 ordering

    @rule(seconds=st.sampled_from([0.05, 0.25, 0.7, 1.25, 2.1]))
    def advance_time(self, seconds: float) -> None:
        self.h.run(self.h.nats.advance(seconds))
        for turn, reply in self.model.replies.items():
            importance = self._input_meta.get(turn, ("", 0.0, ""))[1]
            if (
                reply.source == "proactive"
                and reply.speaking
                and reply.terminal is None
                and importance
                >= getattr(Config, "PROACTIVE_GRACE_MIN_IMPORTANCE", 0.75)
                and turn in self._grace_started_at
                and self.h.nats.now() - self._grace_started_at[turn][1]
                >= getattr(Config, "PROACTIVE_GRACE_WINDOW_S", 0.6)
            ):
                assert any(
                    subject == "audio.stop"
                    and effect.get("reason") == "proactive_grace_expired"
                    and effect.get("turn_id") == turn
                    for subject, effect in self.h.nats.effects
                )  # I6 §6 133; §5 104
        for clock, subject, effect in self.h.nats.effect_times:
            if (
                subject != "audio.stop"
                or effect.get("reason") != "proactive_grace_expired"
            ):
                continue
            started = self._grace_started_at.get(effect.get("turn_id"))
            if started is not None:
                elapsed = clock - started[0]
                window = getattr(Config, "PROACTIVE_GRACE_WINDOW_S", 0.6)
                assert window <= elapsed <= window + 0.05  # I6 §6 133

    @rule()
    @precondition(
        lambda self: (
            hasattr(self.h.agent, "reply_ledger_size")
            and self.h.agent.reply_ledger_size() == 0
        )
    )
    def overflow_burst(self) -> None:
        """Drive 33 unresolved started replies despite the normal 20-step run."""
        wait = getattr(Config, "REPLY_TERMINAL_WAIT_S", 2.0)
        burst = 34
        first = self.serial + 1
        # Reviewer fix: 34 unresolved replies overflow a 32-entry ledger
        # twice, so the two oldest are evicted, each at the last heard
        # offset the transport reported for it (STARTED carries 12 here),
        # per ADR-W5 section 3. The first draft expected one eviction at 0.
        evicted = [f"t{first + i}" for i in range(burst - 32)]
        self.h.nats.hold_transport_terminals = True
        Config.REPLY_TERMINAL_WAIT_S = 30.0
        try:
            for _ in range(burst):
                self.chat_input("whisper", 0.5, "useful_to_user", False)
                self.h.run(asyncio.sleep(0.001))
            for turn in evicted:
                resolution = next(
                    (
                        item
                        for item in self.h.agent.reply_resolutions
                        if item.turn_id == turn
                    ),
                    None,
                )
                assert resolution is not None
                assert (
                    str(getattr(resolution.status, "value", resolution.status))
                    == "FAILED"
                )
                assert str(getattr(resolution.reason, "value", resolution.reason)) == (
                    "reply_ledger_overflow"
                )  # §5 120-122; I11 138
                expected = self.model.replies[turn]
                expected.terminal = "FAILED"
                heard = 12 if turn in self.h.nats.started_utterances else 0
                expected.offset = min(heard, len(expected.text))
                expected.heard_text = expected.text[: expected.offset].strip()
                self._overflowed_turns.add(turn)
            assert self.h.agent.reply_ledger_size() <= 32
        finally:
            self.h.nats.hold_transport_terminals = False
            Config.REPLY_TERMINAL_WAIT_S = wait

        # The transport still owes a terminal for each started utterance. Send
        # them after checking the brain's overflow resolution.
        async def settle_transport() -> None:
            for turn in tuple(self.h.nats.started):
                if turn not in self.h.nats.transport_terminal_utterances:
                    await self.h.nats._terminal(turn, "INTERRUPTED", flushed=False)
            await self.h.nats.drain()

        self.h.run(settle_transport())
        self._refresh_model()

    @rule()
    @precondition(
        lambda self: (
            self.model.floor is not None
            and self.model.replies[self.model.floor].started
            and self.model.replies[self.model.floor].terminal is None
        )
    )
    def terminal_wait_timeout(self) -> None:
        """Withhold transport terminal; a speaking cut resolves at last progress."""
        turn = self.model.floor
        assert turn is not None
        expected = self.model.replies[turn]
        progress = {
            "utterance_id": turn,
            "character_offset": 12,
            "word_index": 3,
            "completed": False,
            "timestamp": self.h.nats.now(),
        }
        self.h.run(self.h.nats.deliver("audio.playback.progress", progress))
        self._progress_payloads.append(progress)
        expected.offset = 12
        expected.speaking = True
        self.h.nats.hold_transport_terminals = True

        async def cut_and_expire() -> None:
            pending = asyncio.create_task(
                self.h.nats.deliver(
                    "audio.stop",
                    {
                        "interrupt": True,
                        "turn_id": turn,
                        "reason": "confirmed_command",
                        "speculative": False,
                        "flush": False,
                    },
                )
            )
            await asyncio.sleep(0)
            await self.h.nats.advance(0.02)
            await pending

        try:
            self.h.run(cut_and_expire())
            resolution = next(
                item for item in self.h.agent.reply_resolutions if item.turn_id == turn
            )
            assert (
                str(getattr(resolution.status, "value", resolution.status))
                == "TRUNCATED"
            )
            assert resolution.character_offset == 12  # §4 68-79; §2 35
            expected.terminal = "INTERRUPTED"
            # Reviewer fix: the timeout is this reply's first terminal. A
            # transport terminal that lands later (e.g. a retry take's
            # COMPLETED after an injected flush) is a no-op (§4 80-82).
            self._terminal_by_turn[turn] = "INTERRUPTED"
            expected.heard_text = expected.text[:12].strip()
        finally:
            self.h.nats.hold_transport_terminals = False
            self.h.run(self.h.nats._terminal(turn, "INTERRUPTED", flushed=False))
            self._refresh_model()

    @rule()
    @precondition(
        lambda self: (
            hasattr(self.h.agent, "reply_ledger_size")
            and self.h.agent.reply_ledger_size() == 0
        )
    )
    def terminal_wait_cancelled_before_speech(self) -> None:
        """A started chunk with no STARTED/progress frame times out as CANCELLED."""
        turn = f"t{self.serial + 1}"
        self.h.nats.suppress_started_for.add(turn)
        self.h.nats.hold_transport_terminals = True
        try:
            self.chat_input("whisper", 0.5, "useful_to_user", False)
            self.h.run(asyncio.sleep(0.002))
            self._refresh_model()
            expected = self.model.replies[turn]
            assert expected.started and not expected.speaking

            async def cut_and_expire() -> None:
                pending = asyncio.create_task(
                    self.h.nats.deliver(
                        "audio.stop",
                        {
                            "interrupt": True,
                            "turn_id": turn,
                            "reason": "confirmed_command",
                            "speculative": False,
                            "flush": False,
                        },
                    )
                )
                await asyncio.sleep(0)
                await self.h.nats.advance(0.02)
                await pending

            self.h.run(cut_and_expire())
            resolution = next(
                item for item in self.h.agent.reply_resolutions if item.turn_id == turn
            )
            assert str(getattr(resolution.status, "value", resolution.status)) == (
                "CANCELLED"
            )  # ADR-W5 §4 68-79
            self._timeout_cancelled.add(turn)
            expected.terminal = "CANCELLED"
            expected.heard_text = ""
        finally:
            self.h.nats.hold_transport_terminals = False
            self.h.nats.suppress_started_for.discard(turn)
            self.h.run(self.h.nats._terminal(turn, "INTERRUPTED", flushed=False))
            self._refresh_model()

    @rule(which=st.sampled_from(["chat", "stop", "lifecycle", "progress"]))
    def redelivery(self, which: str) -> None:
        source = {
            "chat": self._chat_payloads,
            "stop": self._stop_payloads,
            "lifecycle": self._lifecycle_payloads,
            "progress": self._progress_payloads,
        }[which]
        if not source:
            return
        payload = dict(source[-1])
        subject = {
            "chat": "chat.input",
            "stop": "audio.stop",
            "lifecycle": "audio.playback.lifecycle",
            "progress": "audio.playback.progress",
        }[which]
        before = self._observable_snapshot()
        self.h.run(self.h.nats.redeliver(subject, payload))
        assert self._observable_snapshot() == before  # I10 §6 137

    def _observable_snapshot(self) -> tuple[Any, ...]:
        a = self.h.agent
        return (
            tuple((r.get("id"), r["text"]) for r in self.h.history.rows),
            tuple(getattr(a, "reply_resolutions", ())),
            tuple(getattr(a, "declined_proactive_inputs", ())),
            tuple(
                (
                    payload["turn_id"],
                    tuple(
                        getattr(a, "get_outcome_history", lambda _t: ())(
                            payload["turn_id"]
                        )
                    ),
                )
                for payload in self._chat_payloads
            ),
            getattr(a, "reply_ledger_size", lambda: None)(),
            tuple(self.h.nats.effects),
            getattr(self.h.core.state.persist_state, "await_count", 0),
        )

    def _refresh_model(self) -> None:
        events = self.h.nats.lifecycle_events[self._seen_lifecycle_events :]
        self._seen_lifecycle_events = len(self.h.nats.lifecycle_events)
        for event in events:
            turn = str(event.get("turn_id"))
            try:
                parsed = AudioPlaybackLifecycle.model_validate(event)
            except ValueError:
                continue
            if self._reducer.apply(parsed) is not LifecycleApplyResult.APPLIED:
                continue
            expected = self.model.replies.get(turn)
            if expected is None or turn not in self.h.nats.started:
                continue
            if turn in self._overflowed_turns:
                continue
            if turn in self._timeout_cancelled:
                continue
            state = event.get("state")
            if state in {"STARTED", "PLAYING"}:
                expected.speaking = True
            if state in {"COMPLETED", "INTERRUPTED", "FAILED"}:
                if (
                    event.get("flushed") and state == "INTERRUPTED"
                ) or turn in self._terminal_by_turn:
                    continue
                self._terminal_by_turn[turn] = state
                expected.terminal = state
                expected.offset = (
                    len(expected.text)
                    if state == "COMPLETED"
                    else min(int(event.get("heard_offset", 0)), len(expected.text))
                )
                expected.heard_text = expected.text[: expected.offset].strip()
        for turn, reply in self.model.replies.items():
            reply.started = reply.started or self.h.nats.started.get(turn, 0) > 0
            reply.speaking = reply.speaking or turn in self.h.nats.speaking
            if reply.started:
                reply.generating = False
            if reply.started and reply.history_id is None:
                for row in self.h.history.rows:
                    if row["role"] == "assistant" and row["text"] == reply.text:
                        reply.history_id = row["id"]
                        break

    @initialize()
    def configure_short_timers(self) -> None:
        if not FEATURE_READY:
            pytest.xfail("frozen W5 interface is not present on the pre-fix branch")
        # Frozen names are installed on this imported Config proxy at runtime;
        # monkeypatch in the pytest fixture restores them after this machine.
        for name, value in CONFIG_DEFAULTS.items():
            try:
                setattr(
                    Config, name, min(value, 0.005) if name.endswith("_S") else value
                )
            except (AttributeError, TypeError):
                pass

    @invariant()
    def ledger_bound_and_one_terminal(self) -> None:
        self._refresh_model()
        agent = self.h.agent
        if hasattr(agent, "reply_ledger_size"):
            assert agent.reply_ledger_size() <= 32  # ADR-W5 §5 120-122; I11 §6 138
        resolutions = self._observed_resolutions()
        ids = [getattr(resolution, "turn_id", None) for resolution in resolutions]
        assert len(ids) == len(set(ids))  # I1 §6 128; §4 first-terminal-wins 80-82
        resolved = {getattr(item, "turn_id", None) for item in resolutions}
        for turn in self.model.replies:
            if turn in self._terminal_by_turn:
                assert turn in resolved  # I1 §6 128; §4 80-82

    @invariant()
    def history_is_never_appended_by_cut(self) -> None:
        known_ids = {
            reply.history_id
            for reply in self.model.replies.values()
            if reply.history_id is not None
        }
        for operation, role, message_id in self.h.history.writes:
            if operation == "rewrite":
                assert (
                    role == "assistant" and message_id in known_ids
                )  # I3 §6 130; ADR-003 49-55
        for resolution in getattr(self.h.agent, "reply_resolutions", ()):
            expected = self.model.replies.get(resolution.turn_id)
            text = expected.text if expected is not None else TEXT
            offset = min(max(resolution.character_offset, 0), len(text))
            status = str(getattr(resolution.status, "value", resolution.status))
            heard = text if status == "COMPLETED" else text[:offset].strip()
            assert resolution.heard_text == heard  # ADR-W5 §3 42-45; §6 I3 130
            assert offset <= len(text)

    @invariant()
    def outcomes_and_own_history_match_the_reference(self) -> None:
        self._refresh_model()
        agent = self.h.agent
        resolutions = {
            getattr(item, "turn_id", None): item
            for item in getattr(agent, "reply_resolutions", ())
        }
        for turn, expected in self.model.replies.items():
            resolution = resolutions.get(turn)
            if resolution is None:
                continue
            expected_status = {
                "COMPLETED": "COMPLETED",
                "INTERRUPTED": "TRUNCATED",
                "FAILED": "FAILED",
            }.get(expected.terminal)
            actual_status = str(getattr(resolution.status, "value", resolution.status))
            if expected_status is not None:
                assert actual_status == expected_status  # ADR-W5 §4 68-90
                assert resolution.character_offset == expected.offset
                assert resolution.heard_text == expected.heard_text
            outcomes = list(agent.get_outcome_history(turn))
            if expected.source == "user" and turn in self.h.intent_turns:
                assert len(outcomes) == 1  # I2 §6 129
                outcome_status = getattr(outcomes[0], "status", None)
                resolved_status = getattr(resolution.status, "value", resolution.status)
                assert str(getattr(outcome_status, "value", outcome_status)) == str(
                    resolved_status
                )
            else:
                assert not outcomes  # I2 §6 129
            if expected.history_id is not None:
                own_row = next(
                    row
                    for row in self.h.history.rows
                    if row["id"] == expected.history_id
                )
                assert own_row["text"] == resolution.heard_text  # I3 §6 130

    # I12 (first-output overhead) is measured by
    # test_bargein_w5_regressions.py::test_i12_*: a wall-clock assert inside a
    # Hypothesis machine is flaky under load and also times this harness.


_examples = max(20, int(os.getenv("W5_SM_EXAMPLES", "20")))
BargeInMachine.TestCase.settings = settings(
    max_examples=_examples,
    stateful_step_count=20,
    deadline=None,
    suppress_health_check=[],
)
TestBargeInMachine = BargeInMachine.TestCase
if not FEATURE_READY:
    TestBargeInMachine = pytest.mark.xfail(
        strict=False,
        reason="frozen W5 interface is not present on the pre-fix branch",
    )(TestBargeInMachine)


def _legacy_harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    monkeypatch.setattr(Config, "BARGE_IN_ONSET_GRACE_S", 0.0)
    for name, value in CONFIG_DEFAULTS.items():
        monkeypatch.setattr(Config, name, min(value, 0.005), raising=False)
    return _Harness()


@pytest.mark.skipif(not LEGACY, reason="set W5_SM_LEGACY=1 for pre-fix probes")
def test_legacy_hypothesis_minimal_counterexamples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print minimal traces for the known pre-fix gaps without failing pytest."""
    probe_settings = settings(max_examples=100, deadline=None, database=None)

    def f013(trace: tuple[str, ...]) -> bool:
        if not any(
            trace[index : index + 3]
            == ("start_reply", "progress_12", "confirmed_user_speech")
            for index in range(len(trace) - 2)
        ):
            return False
        h = _legacy_harness(monkeypatch)
        try:

            async def run_probe() -> bool:
                for action in trace:
                    if action == "start_reply":
                        await h.agent._on_chat_input(h.input("legacy-a"))
                    elif action == "progress_12":
                        await h.agent._on_audio_playback_progress(
                            {
                                "utterance_id": "legacy-a",
                                "character_offset": 12,
                                "word_index": 3,
                                "completed": False,
                            }
                        )
                    elif action == "confirmed_user_speech":
                        await h.agent._on_audio_stop(
                            {
                                "interrupt": True,
                                "speculative": False,
                                "flush": False,
                                "reason": "confirmed_user_speech",
                                "turn_id": "legacy-a",
                            }
                        )
                full_text = _reply_text("say legacy-a")
                return not any(
                    row["role"] == "assistant" and row["text"] != full_text
                    for row in h.history.rows
                )

            return h.run(run_probe())
        finally:
            h.close()

    def single_slot(trace: tuple[str, ...]) -> bool:
        if not any(
            trace[index : index + 3] == ("begin_a", "begin_b", "begin_c")
            for index in range(len(trace) - 2)
        ):
            return False
        h = _legacy_harness(monkeypatch)
        try:

            async def run_probe() -> bool:
                first = None
                for action in trace:
                    if action == "begin_a":
                        await h.agent._begin_turn("legacy-a")
                    elif action == "begin_b":
                        await h.agent._begin_turn("legacy-b")
                        first = getattr(h.agent, "_superseded_reply", None)
                    elif action == "begin_c":
                        await h.agent._begin_turn("legacy-c")
                return first is not None and first is not getattr(
                    h.agent, "_superseded_reply", None
                )

            return h.run(run_probe())
        finally:
            h.close()

    def v3(trace: tuple[str, ...]) -> bool:
        if not any(
            trace[index : index + 2] == ("start_blocked_generation", "observe_callback")
            for index in range(len(trace) - 1)
        ):
            return False
        h = _legacy_harness(monkeypatch)
        try:

            async def run_probe() -> bool:
                callback = None
                for action in trace:
                    if action == "start_blocked_generation":
                        payload = h.input("legacy-blocked", blocked=True)
                        callback = asyncio.create_task(h.agent._on_chat_input(payload))
                        await asyncio.sleep(0)
                    elif action == "observe_callback" and callback is not None:
                        was_pending = not callback.done()
                        await h.release("legacy-blocked")
                        await callback
                        return was_pending
                if callback is None:
                    return False
                was_pending = not callback.done()
                await h.release("legacy-blocked")
                await callback
                return was_pending

            return h.run(run_probe())
        finally:
            h.close()

    trace_strategy = st.lists(
        st.sampled_from(["start_reply", "progress_12", "confirmed_user_speech"]),
        min_size=3,
        max_size=6,
    ).map(tuple)
    slot_strategy = st.lists(
        st.sampled_from(["begin_a", "begin_b", "begin_c"]),
        min_size=3,
        max_size=6,
    ).map(tuple)
    v3_strategy = st.lists(
        st.sampled_from(["start_blocked_generation", "observe_callback"]),
        min_size=2,
        max_size=4,
    ).map(tuple)
    cases = [
        (
            "F-013 / I3: ordinary confirmed_user_speech leaves full history",
            trace_strategy,
            f013,
            ("start_reply", "progress_12", "confirmed_user_speech"),
        ),
        (
            "single slot: second supersession overwrites the prior snapshot",
            slot_strategy,
            single_slot,
            ("begin_a", "begin_b", "begin_c"),
        ),
        (
            "V-3 / I5: blocked generator keeps chat.input callback pending",
            v3_strategy,
            v3,
            ("start_blocked_generation", "observe_callback"),
        ),
    ]
    for label, strategy, predicate, minimal_trace in cases:
        minimal = find(strategy, predicate, settings=probe_settings)
        print(
            f"LEGACY FAIL {label}; minimal hypothesis trace={minimal!r}; "
            f"reduced semantic trace={minimal_trace!r}"
        )
    print("Legacy-only support surface: chat.input, audio.stop, progress, _begin_turn.")


@pytest.fixture(autouse=True)
def _shorten_frozen_time_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "BARGE_IN_ONSET_GRACE_S", 0.0)
    for name, value in CONFIG_DEFAULTS.items():
        monkeypatch.setattr(Config, name, min(value, 0.005), raising=False)
