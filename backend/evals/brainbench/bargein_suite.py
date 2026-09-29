"""Seeded lifecycle event fuzzing for BrainAgent's V2 voice boundary.

This is an architecture-only harness: it drives the real BrainAgent with a
scripted cognitive stream and in-memory history, without audio, NATS, or an
LLM. The event generator is deterministic so later voice-contract work can
compare the same lifecycle cases against this V2 baseline.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.brain_agent import BrainAgent
from app.cognitive.action_intent import build_action_intent
from app.config import Config
from app.contracts import Topics
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build

FAMILIES = (
    "clean",
    "confirmed_barge_in",
    "superseded_stop",
    "stale_stop",
    "unknown_stop",
    "rapid_fire",
    "self_thought_interrupt",
    "proactive_grace_high",
    "proactive_grace_low",
    "random",
)
V2_FAMILIES = (
    "clean",
    "confirmed_barge_in",
    "superseded_stop",
    "stale_stop",
    "unknown_stop",
    "rapid_fire",
    "random",
)
V2_INVARIANTS = (
    "terminal_outcomes_per_reply",
    "history_matches_heard",
    "stale_stop_applied",
    "current_turn_harmed",
    "completed_outcome_seen",
    "hung",
)
TARGETS = ("playing", "superseded", "stale", "unknown")
_CLAIMED = {
    "terminal_outcomes_per_reply": True,
    "history_matches_heard": True,
    "stale_stop_applied": True,
    "current_turn_harmed": True,
    "completed_outcome_seen": True,
    "hung": True,
    "proactive_grace_bound": True,
    "low_proactive_ceded": True,
    "self_thought_interrupt_gate": True,
}


@dataclass(frozen=True)
class Event:
    """One deterministic operation in a scenario."""

    type: Literal[
        "user_utterance",
        "subconscious_input",
        "partial",
        "progress",
        "stop",
        "stream_chunk_delay",
        "idle",
    ]
    turn_id: str | None = None
    target: str | None = None
    word_offset: int = 0
    chunks: int = 0
    reason: str = "confirmed_command"
    importance: float | None = None
    # A user final STT flagged as command-like (a speculative duck is
    # pending): no unscoped stop is published, and Stage 2 decides (ADR-003).
    speculative: bool = False


@dataclass(frozen=True)
class Scenario:
    family: str
    index: int
    events: tuple[Event, ...]
    reply_texts: dict[str, str]


@dataclass
class ScenarioEvidence:
    """Observed facts consumed by the invariant checker.

    Values are explicit rather than inferred from metric names, which keeps
    the checker useful for hand-built adversarial cases in unit tests.
    """

    started_replies: tuple[str, ...] = ()
    terminal_counts: dict[str, int] = field(default_factory=dict)
    history_expected: dict[str, str] = field(default_factory=dict)
    history_actual: dict[str, str] = field(default_factory=dict)
    history_rows: tuple[tuple[str, str], ...] = ()
    history_claimed: dict[str, bool] = field(default_factory=dict)
    history_row_claimed: dict[str, bool] = field(default_factory=dict)
    unattributed_assistant_rows: int = 0
    terminal_claimed: dict[str, bool] = field(default_factory=dict)
    stale_stop_changed: bool = False
    superseded_stop_harmed_current: bool = False
    completed_without_producer: bool = False
    completed_outcome_count: int = 0
    hung: bool = False
    terminal_eligible: tuple[str, ...] = ()
    proactive_grace_bound_violated: bool = False
    low_proactive_ceded_violated: bool = False
    self_thought_interrupt_violated: bool = False


def _word_prefix(text: str, words: int) -> str:
    return " ".join(text.split()[: max(0, words)])


def _text_bank(reply_texts: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(text.strip() for text in (reply_texts or ()) if text.strip()) or (
        "I can help with that after I check the details",
        "That sounds important and I will remember what you said",
        "Let us take this one step at a time together",
    )


def _utterance(turn_id: str) -> Event:
    return Event("user_utterance", turn_id=turn_id)


def _stop_reason(target: str | None) -> str:
    """The reason a real stop for `target` would carry.

    Stage 2 sends its confirmed voice command only to the playing or the
    superseded reply (ADR-003); a stop for an older or unknown turn comes from
    the reflex path. Giving a "stale" stop the command reason lets it land on
    the superseded id, which BrainAgent rightly accepts, and the suite then
    scored a correct stop as a stale one that applied.
    """
    if target in ("stale", "unknown"):
        return "facial_reflex_startle"
    return "confirmed_command"


def _scenario(
    family: str,
    index: int,
    sequence: list[Event],
    texts: tuple[str, ...],
) -> Scenario:
    ids = [
        e.turn_id
        for e in sequence
        if e.type in {"user_utterance", "subconscious_input"}
    ]
    tokens = [f"[reply:{turn_id}]" for turn_id in ids]
    assert len(tokens) == len(set(tokens))
    reply_texts = {
        turn_id: f"{tokens[i]} {texts[i % len(texts)]}" for i, turn_id in enumerate(ids)
    }
    assert len(reply_texts) == len(ids)
    assert len(set(reply_texts.values())) == len(reply_texts)
    return Scenario(
        family,
        index,
        tuple(sequence),
        reply_texts,
    )


def _attribute_assistant_rows(
    rows: list[list[Any]],
    reply_texts: dict[str, str],
    origins: dict[Any, str] | None = None,
) -> tuple[list[tuple[str, str]], int]:
    """Attribute every assistant row to its longest prefix-compatible reply.

    A row is identified by the text it was first logged with (`origins`, by
    message id) when that is known, and its current content is what gets
    compared. A reply cut before any word was heard is rewritten to "" (ADR-W5
    section 4), and "" by content alone matches no reply.
    """
    attributed: list[tuple[str, str]] = []
    unattributed = 0
    for row_id, role, content in rows:
        if role != "assistant":
            continue
        identity = (origins or {}).get(row_id, content)
        matches = [
            (turn_id, reply)
            for turn_id, reply in reply_texts.items()
            if identity and (reply.startswith(identity) or identity.startswith(reply))
        ]
        if not matches:
            unattributed += 1
            continue
        turn_id, _reply = max(matches, key=lambda item: len(item[1]))
        attributed.append((turn_id, content))
    return attributed, unattributed


def _random_scenario(seed, index, rng, weights, texts, n_events):
    a = f"{seed}-{index}-a"
    random_events = [_utterance(a)]
    while len(random_events) < n_events - 1:
        kind = rng.choices(
            ("user_utterance", "progress", "stop", "stream_chunk_delay"),
            weights=(2, 4, 2, 3),
            k=1,
        )[0]
        if kind == "user_utterance":
            random_events.append(_utterance(f"{seed}-{index}-r{len(random_events)}"))
        elif kind in ("progress", "stop"):
            target = rng.choices(list(weights), weights=list(weights.values()), k=1)[0]
            random_events.append(
                Event(
                    kind,
                    target=target,
                    word_offset=rng.randint(0, 8),
                    reason=(
                        _stop_reason(target) if kind == "stop" else "confirmed_command"
                    ),
                )
            )
        elif kind == "stream_chunk_delay":
            random_events.append(Event(kind, chunks=rng.randint(1, 3)))
    random_events.append(Event("idle"))
    return _scenario("random", index, random_events, texts)


def generate_scenarios(
    seed: int,
    *,
    families: tuple[str, ...] | None = None,
    n_scenarios_per_family: int = 1,
    n_events: int = 20,
    mix_weights: dict[str, float] | None = None,
    reply_texts: list[str] | tuple[str, ...] | None = None,
) -> list[Scenario]:
    """Generate every defining family plus weighted, seeded random cases."""
    if not 1000 <= seed <= 1099:
        raise ValueError("BrainBench barge-in uses dev seeds 1000-1099 only")
    if n_scenarios_per_family <= 0 or n_events < 2:
        raise ValueError(
            "scenario count must be positive and n_events must be at least 2"
        )
    weights = mix_weights or {name: 1.0 for name in TARGETS}
    if any(key not in TARGETS or value < 0 for key, value in weights.items()):
        raise ValueError(
            "mix_weights keys must be event targets with nonnegative weights"
        )
    if not any(weights.values()):
        raise ValueError("mix_weights must contain a positive weight")
    rng = random.Random(seed)
    texts = _text_bank(reply_texts)
    selected_families = families or FAMILIES
    unknown_families = set(selected_families) - set(FAMILIES)
    if unknown_families:
        raise ValueError(f"unknown barge-in families: {sorted(unknown_families)}")
    generated: list[Scenario] = []
    for index in range(n_scenarios_per_family):
        a, b, c = (f"{seed}-{index}-{suffix}" for suffix in ("a", "b", "c"))
        first = [
            _utterance(a),
            Event("idle"),
            Event("progress", target="playing", word_offset=2),
        ]
        cases = {
            "clean": first
            + [
                Event("progress", target="playing", word_offset=10_000),
                Event("idle"),
            ],
            "confirmed_barge_in": first
            + [
                Event(
                    "stop",
                    target="playing",
                    reason="confirmed_user_speech",
                ),
                _utterance(b),
                Event("idle"),
            ],
            "superseded_stop": first
            + [
                Event("user_utterance", turn_id=b, speculative=True),
                Event("progress", target="superseded", word_offset=4),
                Event("stop", target="superseded"),
                Event("idle"),
            ],
            "stale_stop": first
            + [
                _utterance(b),
                Event("idle"),
                _utterance(c),
                Event("stop", target="stale", reason=_stop_reason("stale")),
                Event("idle"),
            ],
            "unknown_stop": first
            + [
                Event("stop", target="unknown", reason=_stop_reason("unknown")),
                Event("idle"),
            ],
            "rapid_fire": [_utterance(a), _utterance(b), _utterance(c), Event("idle")],
        }
        cases["self_thought_interrupt"] = [
            _utterance(a),
            Event("stream_chunk_delay", chunks=1),
            Event("progress", target="playing", word_offset=1),
            Event("subconscious_input", turn_id=b, importance=0.95),
            Event("idle"),
        ]
        cases["proactive_grace_high"] = [
            Event("subconscious_input", turn_id=a, importance=0.9),
            Event("stream_chunk_delay", chunks=1),
            Event("progress", target="playing", word_offset=1),
            Event("partial", target="playing"),
            Event("idle"),
        ]
        cases["proactive_grace_low"] = [
            Event("subconscious_input", turn_id=a, importance=0.5),
            Event("stream_chunk_delay", chunks=1),
            Event("progress", target="playing", word_offset=1),
            Event("partial", target="playing"),
            Event("idle"),
        ]
        for family in selected_families:
            if family == "random":
                continue
            generated.append(_scenario(family, index, cases[family], texts))
        if "random" in selected_families:
            generated.append(
                _random_scenario(seed, index, rng, weights, texts, n_events)
            )
    return generated


def check_invariants(evidence: ScenarioEvidence) -> dict[str, float | int | None]:
    """Return per-scenario invariant violations and diagnostic counts.

    A reply without a terminal record is counted when it has a terminal
    trigger. In the clean case V2 has no live completion producer (F-002), so
    an otherwise fully played reply remains unresolved and is accounted for
    separately rather than mislabelled as a lifecycle failure.
    """
    eligible = set(evidence.terminal_eligible)
    missing = sum(evidence.terminal_counts.get(reply, 0) == 0 for reply in eligible)
    duplicate = sum(evidence.terminal_counts.get(reply, 0) > 1 for reply in eligible)
    total_mismatch = missing + duplicate
    claimed_terminal_bad = sum(
        (evidence.terminal_counts.get(reply, 0) == 0)
        or (evidence.terminal_counts.get(reply, 0) > 1)
        for reply in eligible
        if evidence.terminal_claimed.get(reply, True)
    )
    unclaimed_terminal_bad = total_mismatch - claimed_terminal_bad
    history_rows = evidence.history_rows or tuple(evidence.history_actual.items())
    mismatches = [
        (reply, expected, actual)
        for reply, actual in history_rows
        if (expected := evidence.history_expected.get(reply)) is not None
        and actual != expected
    ]
    mismatch_length = sum(
        abs(len(expected) - len(actual)) + sum(a != b for a, b in zip(expected, actual))
        for _, expected, actual in mismatches
        if actual is not None
    )
    attributed_counts: dict[str, int] = {}
    for reply, _actual in history_rows:
        attributed_counts[reply] = attributed_counts.get(reply, 0) + 1
    history_n = sum(reply in evidence.history_expected for reply, _ in history_rows)
    missing_rows = [
        reply
        for reply in evidence.history_expected
        if attributed_counts.get(reply, 0) == 0
    ]
    missing_rows_claimed = sum(
        evidence.history_row_claimed.get(reply, True) for reply in missing_rows
    )
    missing_rows_unclaimed = len(missing_rows) - missing_rows_claimed
    claimed_bad = sum(
        evidence.history_claimed.get(reply, True) for reply, _, _ in mismatches
    )
    metrics: dict[str, float | int | None] = {
        "terminal_outcomes_per_reply_violation": float(total_mismatch > 0),
        "terminal_outcomes_per_reply_claimed_violation": float(
            claimed_terminal_bad > 0
        ),
        "terminal_outcomes_per_reply_unclaimed_violation": float(
            unclaimed_terminal_bad > 0
        ),
        "replies_with_zero_terminal_outcomes": missing,
        "replies_with_multiple_terminal_outcomes": duplicate,
        "terminal_outcome_replies_eligible": len(eligible),
        # Over every started reply, eligible or not: a reply that played to
        # the end and never got COMPLETED is unresolved too (F-002).
        "started_reply_count": len(set(evidence.started_replies)),
        "started_replies_without_terminal": sum(
            evidence.terminal_counts.get(reply, 0) == 0
            for reply in set(evidence.started_replies)
        ),
        "replies_unresolved_without_completion": sum(
            reply not in eligible and evidence.terminal_counts.get(reply, 0) == 0
            for reply in evidence.started_replies
        ),
        "history_matches_heard_violation": float(bool(mismatches)),
        "history_matches_heard_claimed_violation": float(claimed_bad > 0),
        "history_matches_heard_unclaimed_violation": float(
            len(mismatches) > claimed_bad
        ),
        "history_mismatch_count": len(mismatches),
        "history_mismatch_char_length": mismatch_length,
        "history_rows_compared": history_n,
        "unattributed_assistant_rows": evidence.unattributed_assistant_rows,
        "unattributed_assistant_rows_violation": float(
            evidence.unattributed_assistant_rows > 0
        ),
        "unattributed_assistant_rows_claimed_violation": float(
            evidence.unattributed_assistant_rows > 0
        ),
        "unattributed_assistant_rows_unclaimed_violation": 0.0,
        "replies_without_history_row": len(missing_rows),
        "replies_without_history_row_claimed": missing_rows_claimed,
        "replies_without_history_row_unclaimed": missing_rows_unclaimed,
        "stale_stop_applied_violation": float(evidence.stale_stop_changed),
        "stale_stop_applied_claimed_violation": float(evidence.stale_stop_changed),
        "stale_stop_applied_unclaimed_violation": 0.0,
        "current_turn_harmed_violation": float(evidence.superseded_stop_harmed_current),
        "current_turn_harmed_claimed_violation": float(
            evidence.superseded_stop_harmed_current
        ),
        "current_turn_harmed_unclaimed_violation": 0.0,
        "completed_outcome_seen_violation": float(evidence.completed_without_producer),
        "completed_outcome_seen_claimed_violation": float(
            evidence.completed_without_producer
        ),
        "completed_outcome_seen_unclaimed_violation": 0.0,
        "completed_outcome_count": evidence.completed_outcome_count,
        "hung_violation": float(evidence.hung),
        "hung_claimed_violation": float(evidence.hung),
        "hung_unclaimed_violation": 0.0,
        "hung": float(evidence.hung),
        "proactive_grace_bound_violation": float(
            evidence.proactive_grace_bound_violated
        ),
        "proactive_grace_bound_claimed_violation": float(
            evidence.proactive_grace_bound_violated
        ),
        "proactive_grace_bound_unclaimed_violation": 0.0,
        "low_proactive_ceded_violation": float(evidence.low_proactive_ceded_violated),
        "low_proactive_ceded_claimed_violation": float(
            evidence.low_proactive_ceded_violated
        ),
        "low_proactive_ceded_unclaimed_violation": 0.0,
        "self_thought_interrupt_gate_violation": float(
            evidence.self_thought_interrupt_violated
        ),
        "self_thought_interrupt_gate_claimed_violation": float(
            evidence.self_thought_interrupt_violated
        ),
        "self_thought_interrupt_gate_unclaimed_violation": 0.0,
    }
    return metrics


def _violation_rate(rows: list[SuiteOutcome], metric: str) -> float | None:
    values = [row.metrics[metric] for row in rows if metric in row.metrics]
    return sum(values) / len(values) if values else None


def invariant_violation_rates(
    outcomes: list[SuiteOutcome],
) -> dict[str, dict[str, float | int | None]]:
    """Per-invariant violation rates, split by family and ADR claim status."""
    result: dict[str, dict[str, float | int | None]] = {}
    for invariant in _CLAIMED:
        metric = f"{invariant}_violation"
        families = sorted({family for row in outcomes for family in row.categories})
        for family in families:
            rows = [row for row in outcomes if family in row.categories]
            result[f"{invariant}:{family}"] = {
                "n": len(rows),
                "rate": _violation_rate(rows, metric),
                "claimed_rate": _violation_rate(rows, f"{invariant}_claimed_violation"),
                "unclaimed_rate": _violation_rate(
                    rows, f"{invariant}_unclaimed_violation"
                ),
            }
    return result


def outcome_accounting(
    outcomes: list[SuiteOutcome],
) -> dict[str, dict[str, int | None]]:
    """Count reply-level terminal bookkeeping by scenario family."""
    families = sorted({family for row in outcomes for family in row.categories})
    return {
        family: {
            "scenarios": sum(family in row.categories for row in outcomes),
            "zero_terminal_replies": sum(
                int(row.metrics.get("replies_with_zero_terminal_outcomes", 0))
                for row in outcomes
                if family in row.categories
            ),
            "multiple_terminal_replies": sum(
                int(row.metrics.get("replies_with_multiple_terminal_outcomes", 0))
                for row in outcomes
                if family in row.categories
            ),
            "unresolved_without_completion": sum(
                int(row.metrics.get("replies_unresolved_without_completion", 0))
                for row in outcomes
                if family in row.categories
            ),
            "completed_outcomes": sum(
                int(row.metrics.get("completed_outcome_count", 0))
                for row in outcomes
                if family in row.categories
            ),
        }
        for family in families
    }


class _History:
    def __init__(self) -> None:
        self._rows: list[list[Any]] = []
        # message id -> the text the row was first logged with, so a row
        # rewritten to what was heard (possibly "") stays attributable.
        self.origins: dict[Any, str] = {}

    @property
    def rows(self) -> list[list[Any]]:
        return [[row_id, role, text] for row_id, role, text in self._rows]

    async def log_message(
        self, role: str, content: str, message_id: Any = None
    ) -> None:
        self._rows.append([message_id, role, content])
        if message_id is not None:
            self.origins.setdefault(message_id, content)

    async def rewrite_assistant_message(self, content: str, *, message_id: Any) -> None:
        for row in self._rows:
            if row[0] == message_id and row[1] == "assistant":
                row[2] = content
                return


class _Runtime:
    def calculate_pacing_parameters(self, _snapshot: Any) -> dict[str, float]:
        return {"silence_duration_ms": 0.0}

    def monitor_stream_and_fill(self, generator: Any, **_: Any) -> Any:
        return generator


def _make_agent(
    texts: dict[str, str], gates: dict[str, asyncio.Queue]
) -> tuple[BrainAgent, _History]:
    history = _History()
    # BrainAgent's production CognitiveService constructor builds a Redis
    # working-memory adapter. Replace that composition root before creating
    # the agent so the harness has no network side effects at all.
    with patch("app.agents.brain_agent.CognitiveService"):
        agent = BrainAgent(
            ollama_url="http://not-used",
            graph_db=MagicMock(),
            memory_store=MagicMock(),
            conversation_store=history,
        )
    agent.published = []

    async def publish(subject: str, payload: dict[str, Any]) -> None:
        agent.published.append((time.monotonic(), subject, payload))

    agent.publish = publish
    agent.set_state = AsyncMock()
    agent.conversational_runtime = _Runtime()
    core = MagicMock()
    core.state.last_speculative_intent = None
    core.state.get_context_snapshot = MagicMock(return_value={})
    core.state.record_user_interaction = MagicMock()
    core.state.persist_state = AsyncMock()
    core.state.release_adrenaline = AsyncMock()
    core.workspace_store.get_snapshot = AsyncMock(return_value=None)

    async def process_event(raw_event: dict[str, Any], **_: Any):
        turn_id = raw_event["metadata"]["turn_id"]
        intent = build_action_intent(
            turn_id=turn_id,
            workspace_epoch=0,
            workspace_revision=0,
            kind="SPEAK",
            behavior_decision={},
        )
        yield {"type": "action_intent", "data": intent.model_dump()}
        chunks = texts[turn_id].split()
        for index, chunk in enumerate(chunks):
            await gates[turn_id].get()
            suffix = " " if index < len(chunks) - 1 else ""
            yield {"type": "content", "data": chunk + suffix}
        yield {"type": "done"}

    async def proactive(thought_prompt: str):
        words = texts[thought_prompt].split()
        for index, word in enumerate(words):
            await gates[thought_prompt].get()
            suffix = " " if index < len(words) - 1 else ""
            yield {"type": "content", "data": word + suffix}
        yield {"type": "done"}

    core.process_event = process_event
    core.generate_proactive_response = proactive
    agent.cognitive_core = core
    return agent, history


def _target_id(
    target: str | None, playing: str | None, superseded: str | None, older: list[str]
) -> str:
    if target == "playing" and playing:
        return playing
    if target == "superseded" and superseded:
        return superseded
    if target == "stale" and older:
        return older[0]
    return "unknown-bargein-id"


def _signature(
    agent: BrainAgent, history: _History
) -> tuple[int | None, bool, int, tuple[str, ...]]:
    task = agent._active_generation_task
    cancelled = bool(task and task.cancelling())
    return (
        id(task) if task is not None else None,
        cancelled,
        len(agent._outcome_history),
        tuple(str(row[2]) for row in history.rows),
    )


async def _run_scenario(
    scenario: Scenario, seed: int, index: int, timeout: float
) -> SuiteOutcome:
    gates = {turn_id: asyncio.Queue() for turn_id in scenario.reply_texts}
    agent, history = _make_agent(scenario.reply_texts, gates)
    user_tasks: dict[str, asyncio.Task] = {}
    started: list[str] = []
    older: list[str] = []
    playing: str | None = None
    superseded: str | None = None
    progress_prefixes: dict[str, str] = {}
    progress_offsets: dict[str, int] = {}
    expected_heard: dict[str, str] = {}
    unmeasurable_history: set[str] = set()
    unclaimed_history: set[str] = set()
    terminal_eligible: set[str] = set()
    terminal_claimed: dict[str, bool] = {}
    lifecycle_seq: dict[str, int] = {}
    lifecycle_terminal: set[str] = set()
    lifecycle_completed: set[str] = set()
    stale_changed = False
    current_harmed = False
    hung = False
    grace_bound_violated = False
    low_grace_violated = False
    self_thought_violated = False

    async def wait_ready(turn_id: str) -> None:
        for _ in range(100):
            if agent._active_response_turn_id == turn_id:
                return
            await asyncio.sleep(0)
        raise TimeoutError(f"turn {turn_id} did not enter the reply flow")

    # Words streamed per reply: one gate release yields one word, so this is
    # also the most audio that can exist to have been played.
    released: dict[str, int] = {}

    async def release(turn_id: str, count: int) -> None:
        gate = gates.get(turn_id)
        if gate is not None:
            released[turn_id] = released.get(turn_id, 0) + count
            for _ in range(count):
                gate.put_nowait(None)

    async def lifecycle(turn_id: str, state: str, offset: int, words: int) -> None:
        seq = lifecycle_seq.get(turn_id, 0)
        await agent._on_audio_playback_lifecycle(
            {
                "utterance_id": turn_id,
                "turn_id": turn_id,
                "seq": seq,
                "state": state,
                "words_played": words,
                "words_streamed": max(words, released.get(turn_id, 0)),
                "heard_offset": offset,
                "streamed_offset": max(offset, len(scenario.reply_texts[turn_id])),
            }
        )
        lifecycle_seq[turn_id] = seq + 1
        if state in {"COMPLETED", "INTERRUPTED", "FAILED"}:
            lifecycle_terminal.add(turn_id)
        if state == "COMPLETED":
            lifecycle_completed.add(turn_id)

    async def start_lifecycle(turn_id: str, offset: int, words: int) -> None:
        if turn_id not in lifecycle_seq:
            await lifecycle(turn_id, "STARTED", 0, 0)
        await lifecycle(turn_id, "PLAYING", offset, words)

    async def complete(turn_id: str) -> None:
        text = scenario.reply_texts[turn_id]
        words = len(text.split())
        progress_offsets[turn_id] = len(text)
        progress_prefixes[turn_id] = text
        await start_lifecycle(turn_id, len(text), words)
        await lifecycle(turn_id, "COMPLETED", len(text), words)

    async def settle(final: bool = False) -> None:
        # An idle lets generation finish; it does not mean the audio has
        # played. Mid-scenario, a reply completes only when a progress event
        # reaches its end. Once the scenario is over, a reply nobody stopped
        # plays out, so it completes then.
        for turn_id in tuple(started):
            await release(turn_id, len(scenario.reply_texts[turn_id].split()) + 2)
        tasks = list(user_tasks.values())
        if tasks:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout
            )
        pending = list(agent._background_tasks)
        if pending:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout
            )
        if not final:
            return
        for turn_id in started:
            task = user_tasks.get(turn_id)
            if (
                turn_id not in lifecycle_terminal
                and task is not None
                and task.done()
                and not task.cancelled()
                and task.exception() is None
            ):
                await complete(turn_id)

    try:
        for event in scenario.events:
            if event.type == "user_utterance" and event.turn_id:
                # A user final publishes an unscoped `confirmed_user_speech`
                # stop (ADR-W5 section 5): the transport flushes every reply
                # still playing and reports each INTERRUPTED at its heard
                # offset, and W5 cuts each row to that prefix (DR-028).
                flushed = (
                    []
                    if event.speculative
                    else [
                        turn_id
                        for turn_id in started
                        if turn_id not in lifecycle_terminal
                        and turn_id in scenario.reply_texts
                    ]
                )
                agent.cognitive_core.state.last_speculative_intent = (
                    {"name": "STOP", "text": "stop"} if event.speculative else None
                )
                if playing:
                    older.append(playing)
                superseded = playing
                playing = event.turn_id
                started.append(event.turn_id)
                user_tasks[event.turn_id] = asyncio.create_task(
                    agent._on_chat_input(
                        {
                            "text": f"synthetic utterance for {event.turn_id}",
                            "turn_id": event.turn_id,
                            "utterance_id": event.turn_id,
                            "metadata": {"source": "whisper"},
                        }
                    )
                )
                await wait_ready(event.turn_id)
                for turn_id in flushed:
                    text = scenario.reply_texts[turn_id]
                    offset = min(progress_offsets.get(turn_id, 0), len(text))
                    if offset >= len(text):
                        # Played to the end before the user spoke.
                        await complete(turn_id)
                        continue
                    expected_heard[turn_id] = (
                        progress_prefixes.get(turn_id, "") if offset else ""
                    )
                    unmeasurable_history.discard(turn_id)
                    terminal_eligible.add(turn_id)
                    terminal_claimed[turn_id] = True
                    await lifecycle(
                        turn_id, "INTERRUPTED", offset, released.get(turn_id, 0)
                    )
                await asyncio.sleep(0)
            elif event.type == "subconscious_input" and event.turn_id:
                if playing:
                    older.append(playing)
                superseded = playing
                playing = event.turn_id
                started.append(event.turn_id)
                user_tasks[event.turn_id] = asyncio.create_task(
                    agent._on_chat_input(
                        {
                            "text": event.turn_id,
                            "turn_id": event.turn_id,
                            "utterance_id": event.turn_id,
                            "metadata": {
                                "source": "subconscious",
                                "importance": event.importance,
                                "category": "self_directed",
                            },
                        }
                    )
                )
                await wait_ready(event.turn_id)
                if scenario.family == "self_thought_interrupt":
                    self_thought_violated = not any(
                        subject == Topics.AUDIO_STOP
                        and payload.get("reason") == "self_thought_interrupt"
                        and payload.get("turn_id") == superseded
                        for _, subject, payload in agent.published
                    )
                    if superseded in scenario.reply_texts:
                        text = scenario.reply_texts[superseded]
                        offset = progress_offsets.get(superseded, 0)
                        expected_heard[superseded] = progress_prefixes.get(
                            superseded, ""
                        )
                        if offset == 0:
                            unclaimed_history.add(superseded)
                        elif offset < len(text):
                            terminal_eligible.add(superseded)
                            terminal_claimed[superseded] = True
                    if (
                        superseded in scenario.reply_texts
                        and superseded not in lifecycle_terminal
                    ):
                        await lifecycle(
                            superseded,
                            "INTERRUPTED",
                            progress_offsets.get(superseded, 0),
                            released.get(superseded, 0),
                        )
                await asyncio.sleep(0)
            elif event.type == "partial":
                target_id = _target_id(event.target, playing, superseded, older)
                started_at = time.monotonic()
                await agent._on_user_speech_partial(
                    {
                        "text": "I want to say something",
                        "utterance_id": f"partial-{index}",
                    }
                )
                if scenario.family == "proactive_grace_low":
                    low_grace_violated = not any(
                        subject == Topics.AUDIO_STOP
                        and payload.get("reason") == "proactive_ceded"
                        and payload.get("turn_id") == target_id
                        for _, subject, payload in agent.published
                    )
                    if target_id in scenario.reply_texts:
                        expected_heard[target_id] = progress_prefixes.get(target_id, "")
                        if progress_offsets.get(target_id, 0) == 0:
                            unclaimed_history.add(target_id)
                    if (
                        target_id in scenario.reply_texts
                        and target_id not in lifecycle_terminal
                    ):
                        await lifecycle(
                            target_id,
                            "INTERRUPTED",
                            progress_offsets.get(target_id, 0),
                            released.get(target_id, 0),
                        )
                if scenario.family == "proactive_grace_high":
                    await asyncio.sleep(Config.PROACTIVE_GRACE_WINDOW_S + 0.01)
                    stop_at = next(
                        (
                            at
                            for at, subject, payload in agent.published
                            if subject == Topics.AUDIO_STOP
                            and payload.get("reason") == "proactive_grace_expired"
                            and payload.get("turn_id") == target_id
                        ),
                        None,
                    )
                    grace_bound_violated = stop_at is None or (
                        stop_at - started_at > Config.PROACTIVE_GRACE_WINDOW_S + 0.05
                    )
                    if target_id in scenario.reply_texts:
                        expected_heard[target_id] = progress_prefixes.get(target_id, "")
                        if progress_offsets.get(target_id, 0) == 0:
                            unclaimed_history.add(target_id)
                    if (
                        target_id in scenario.reply_texts
                        and target_id not in lifecycle_terminal
                    ):
                        await lifecycle(
                            target_id,
                            "INTERRUPTED",
                            progress_offsets.get(target_id, 0),
                            released.get(target_id, 0),
                        )
            elif event.type == "stream_chunk_delay" and playing:
                await release(playing, max(1, event.chunks))
                await asyncio.sleep(0)
            elif event.type == "progress":
                target_id = _target_id(event.target, playing, superseded, older)
                actual = scenario.reply_texts.get(target_id, "")
                # Playback cannot report words that have not been generated
                # yet; an uncapped offset fabricated "mid-playback" stops on
                # audio that never existed (reviewer run, n=50 random family).
                word_offset = event.word_offset
                if target_id in scenario.reply_texts:
                    word_offset = min(word_offset, released.get(target_id, 0))
                offset = min(len(actual), len(_word_prefix(actual, word_offset)))
                await agent._on_audio_playback_progress(
                    {
                        "utterance_id": target_id,
                        "character_offset": offset,
                        "word_index": word_offset,
                        "completed": False,
                    }
                )
                if target_id in scenario.reply_texts:
                    progress_prefixes[target_id] = actual[:offset].strip()
                    progress_offsets[target_id] = offset
                    await start_lifecycle(target_id, offset, word_offset)
                    task = user_tasks.get(target_id)
                    if (
                        word_offset >= len(actual.split())
                        and target_id not in lifecycle_terminal
                        and task is not None
                        and task.done()
                        and not task.cancelled()
                    ):
                        await complete(target_id)
            elif event.type == "stop":
                target_id = _target_id(event.target, playing, superseded, older)
                before = _signature(agent, history)
                current_task = agent._active_generation_task
                # Harm is judged on what *this* stop changed: the playing turn
                # may already have been truncated by an earlier, legitimate
                # stop aimed at it, which must not count against ADR-003.
                was_cancelling = bool(current_task and current_task.cancelling())
                was_live = current_task is not None and not current_task.done()
                playing_terminal_before = sum(
                    record.turn_id == playing
                    and record.status in ("TRUNCATED", "CANCELLED")
                    for record in agent._outcome_history
                )
                await agent._on_audio_stop(
                    {
                        "interrupt": True,
                        "speculative": False,
                        "reason": event.reason,
                        "turn_id": target_id,
                    }
                )
                applies_to_playback = target_id == playing or (
                    target_id == superseded and event.reason == "confirmed_command"
                )
                if (
                    applies_to_playback
                    and target_id in scenario.reply_texts
                    and target_id not in lifecycle_terminal
                ):
                    if target_id not in lifecycle_seq:
                        await lifecycle(target_id, "STARTED", 0, 0)
                    offset = progress_offsets.get(target_id, 0)
                    words = released.get(target_id, 0)
                    await lifecycle(target_id, "INTERRUPTED", offset, words)
                after = _signature(agent, history)
                # A "superseded" stop with no superseded reply resolves to the
                # unknown id, so it is judged as an unknown stop.
                resolved_unknown = target_id not in scenario.reply_texts
                if (
                    event.target in ("stale", "unknown") or resolved_unknown
                ) and before != after:
                    stale_changed = True
                if event.target == "superseded" and playing and not resolved_unknown:
                    playing_terminal_after = sum(
                        record.turn_id == playing
                        and record.status in ("TRUNCATED", "CANCELLED")
                        for record in agent._outcome_history
                    )
                    current_harmed = (
                        current_harmed
                        or (
                            was_live
                            and not was_cancelling
                            and (
                                current_task.cancelling() > 0
                                or agent._active_generation_task is not current_task
                            )
                        )
                        or playing_terminal_after > playing_terminal_before
                    )
                if (
                    target_id in scenario.reply_texts
                    and event.target
                    in (
                        "playing",
                        "superseded",
                    )
                    and target_id not in lifecycle_completed
                ):
                    reply_text = scenario.reply_texts[target_id]
                    offset = progress_offsets.get(target_id)
                    if offset is None:
                        unmeasurable_history.add(target_id)
                    else:
                        unmeasurable_history.discard(target_id)
                        expected_heard[target_id] = (
                            progress_prefixes[target_id]
                            if 0 < offset < len(reply_text)
                            else reply_text
                        )
                        if offset == 0:
                            expected_heard[target_id] = ""
                            unclaimed_history.add(target_id)
                    mid_playback = offset is not None and 0 < offset < len(reply_text)
                    if mid_playback:
                        # W5 (DR-028, F-013) claims ordinary confirmed speech
                        # too: ADR-003's "flushes without cutting the row" is
                        # closed, so every mid-playback cut is claimed.
                        terminal_eligible.add(target_id)
                        terminal_claimed[target_id] = True
            elif event.type == "idle":
                await settle()
    except TimeoutError:
        hung = True
        for task in user_tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*user_tasks.values(), return_exceptions=True)

    # Drain any tasks after the final event, within the same scenario budget.
    if not hung:
        try:
            await asyncio.wait_for(settle(final=True), timeout)
        except TimeoutError:
            hung = True
            for task in user_tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*user_tasks.values(), return_exceptions=True)

    records = list(getattr(agent, "_outcome_history", []))
    cancelled_before_insert = {
        record.turn_id for record in records if record.status == "CANCELLED"
    }
    # A proactive reply has no ActionIntent, so no OutcomeRecord (ADR-W5 I2);
    # its ReplyResolution is its terminal record.
    proactive_turns = {
        event.turn_id for event in scenario.events if event.type == "subconscious_input"
    }
    terminal_counts = {
        turn_id: (
            sum(
                item.turn_id == turn_id
                for item in getattr(agent, "reply_resolutions", ())
            )
            if turn_id in proactive_turns
            else len(agent.get_outcome_history(turn_id))
        )
        for turn_id in started
    }
    actual_history, unattributed_rows = _attribute_assistant_rows(
        history.rows, scenario.reply_texts, history.origins
    )
    expected_history: dict[str, str] = {}
    claimed: dict[str, bool] = {}
    history_row_claimed: dict[str, bool] = {}
    for turn_id, text in scenario.reply_texts.items():
        if turn_id in unmeasurable_history:
            continue
        expected_history[turn_id] = expected_heard.get(turn_id, text)
        claimed[turn_id] = turn_id not in unclaimed_history
        # ADR-003 lines 56-59: a reply cancelled before insert has no row;
        # that absence is measured separately and is unclaimed by the ADR.
        history_row_claimed[turn_id] = (
            turn_id not in unclaimed_history and turn_id not in cancelled_before_insert
        )
    completed_count = sum(record.status == "COMPLETED" for record in records)
    completed_without_producer = any(
        record.status == "COMPLETED" and record.turn_id not in lifecycle_completed
        for record in records
    )
    evidence = ScenarioEvidence(
        started_replies=tuple(started),
        terminal_counts=terminal_counts,
        history_expected=expected_history,
        history_rows=tuple(row for row in actual_history if row[0] in expected_history),
        history_claimed=claimed,
        history_row_claimed=history_row_claimed,
        unattributed_assistant_rows=unattributed_rows,
        terminal_claimed=terminal_claimed,
        stale_stop_changed=stale_changed,
        superseded_stop_harmed_current=current_harmed,
        completed_without_producer=completed_without_producer,
        completed_outcome_count=completed_count,
        hung=hung,
        terminal_eligible=tuple(terminal_eligible),
        proactive_grace_bound_violated=grace_bound_violated,
        low_proactive_ceded_violated=low_grace_violated,
        self_thought_interrupt_violated=self_thought_violated,
    )
    metrics = check_invariants(evidence)
    # Each BrainAgent starts a SubjectMetrics worker thread that wakes every
    # 50 ms and never exits on its own; at 50 scenarios per family one
    # leaked per scenario drove the load average into the hundreds.
    agent._metrics.shutdown(timeout=0.2)
    return SuiteOutcome(
        probe_key=f"{seed}:{scenario.family}:{index}",
        persona_seed=seed,
        suite="bargein",
        categories=(scenario.family,),
        metrics={
            key: float(value) for key, value in metrics.items() if value is not None
        },
        mode="architecture_only",
    )


async def _run(
    seed: int, scenarios: list[Scenario], timeout: float
) -> list[SuiteOutcome]:
    original_grace = Config.BARGE_IN_ONSET_GRACE_S
    original_terminal_wait = Config.REPLY_TERMINAL_WAIT_S
    Config.BARGE_IN_ONSET_GRACE_S = 0.0
    # The harness has no audio transport to deliver a lifecycle terminal for
    # every synthetic cut. Keep the brain's fallback inside the scenario
    # budget so pending replies resolve before `settle()` times out.
    Config.REPLY_TERMINAL_WAIT_S = min(original_terminal_wait, timeout / 10)
    try:
        return [
            await _run_scenario(scenario, seed, scenario.index, timeout)
            for scenario in scenarios
        ]
    finally:
        Config.BARGE_IN_ONSET_GRACE_S = original_grace
        Config.REPLY_TERMINAL_WAIT_S = original_terminal_wait


def run_bargein_suite(
    seed: int,
    *,
    families: tuple[str, ...] | None = None,
    n_scenarios_per_family: int = 1,
    n_events: int = 20,
    mix_weights: dict[str, float] | None = None,
    timeout: float = 1.0,
) -> list[SuiteOutcome]:
    """Run all deterministic lifecycle families against fresh BrainAgents."""
    scenarios = generate_scenarios(
        seed,
        families=families,
        n_scenarios_per_family=n_scenarios_per_family,
        n_events=n_events,
        mix_weights=mix_weights,
    )
    return asyncio.run(_run(seed, scenarios, timeout))


def run_bargein_suite_for_seed(
    seed: int,
    archetype: str,
    horizon_label: str,
    *,
    families: tuple[str, ...] | None = None,
    n_scenarios_per_family: int = 1,
    n_events: int = 20,
    mix_weights: dict[str, float] | None = None,
    timeout: float = 1.0,
) -> list[SuiteOutcome]:
    """Use public lifesim utterances to size replies, without a cognitive service."""
    if not 1000 <= seed <= 1099:
        raise ValueError("BrainBench barge-in uses dev seeds 1000-1099 only")
    _sim, turns, _annotations, _probes, _answers = build(seed, archetype, horizon_label)
    realistic = [turn.text for turn in turns if turn.text.strip()]
    scenarios = generate_scenarios(
        seed,
        families=families,
        n_scenarios_per_family=n_scenarios_per_family,
        n_events=n_events,
        mix_weights=mix_weights,
        reply_texts=realistic,
    )
    return asyncio.run(_run(seed, scenarios, timeout))
