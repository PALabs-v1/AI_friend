"""Metacognition measurements for the BrainBench lifesim replay.

This suite needs real reflection text because it observes consolidated memory
content. Per DR-037 it runs only in ``llm_augmented`` mode.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app import clock
from app.agents.surfacing_agent import SurfacingAgent
from evals.brainbench.adapters import BrainBenchService
from evals.brainbench.memory_suite import _contains_value
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build

logger = logging.getLogger(__name__)

_CONTRADICTION_TAGS = frozenset(
    {"contradiction_accidental", "contradiction_intentional", "correction"}
)
_FAILURE_KEYS = frozenset(
    {
        "error",
        "outage",
        "outage_flag",
        "retrieval_degraded",
        "memory_search_error",
        "last_search_error",
    }
)


def _auroc(scores: Sequence[float], labels: Sequence[float]) -> float | None:
    """Compute binary AUROC as P(positive ranks above negative), ties split."""
    if len(scores) != len(labels):
        raise ValueError("AUROC scores and labels must have equal lengths")
    positives = [
        score for score, label in zip(scores, labels, strict=True) if label == 1.0
    ]
    negatives = [
        score for score, label in zip(scores, labels, strict=True) if label == 0.0
    ]
    if not positives or not negatives:
        return None
    wins = sum(
        1.0 if positive > negative else 0.5 if positive == negative else 0.0
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _auroc_optional(
    scores: Sequence[float | None], labels: Sequence[float]
) -> float | None:
    """Keep no-retrieval cases in AUROC as a tied score below retrieved rows.

    Their ``top_score`` stays absent in the outcome. The absent retrieval is
    still a measured low-certainty state, so dropping it would bias AUROC.
    """
    if len(scores) != len(labels):
        raise ValueError("AUROC scores and labels must have equal lengths")
    positives = [
        score for score, label in zip(scores, labels, strict=True) if label == 1.0
    ]
    negatives = [
        score for score, label in zip(scores, labels, strict=True) if label == 0.0
    ]
    if not positives or not negatives:
        return None

    def rank(score: float | None) -> tuple[int, float]:
        return (0, 0.0) if score is None else (1, score)

    wins = sum(
        1.0
        if rank(positive) > rank(negative)
        else 0.5
        if rank(positive) == rank(negative)
        else 0.0
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _reliability_table(
    outcomes: Sequence[SuiteOutcome],
) -> list[dict[str, float | int | None]]:
    """Five equal-width bins over observed answerable top scores.

    Memory search scores are ranking scores, not probabilities. Bins therefore
    cover the observed score range for this replay; each row reports mean raw
    score and empirical top-1 accuracy rather than treating a score as a
    probability.
    """
    rows = [
        (outcome.metrics["top_score"], outcome.metrics["correct_at_1"])
        for outcome in outcomes
        if outcome.metrics.get("answerable") == 1.0
        and "top_score" in outcome.metrics
        and "correct_at_1" in outcome.metrics
    ]
    if not rows:
        return [
            {
                "bin": index,
                "n": 0,
                "low": None,
                "high": None,
                "mean_top_score": None,
                "accuracy": None,
            }
            for index in range(5)
        ]
    low = min(score for score, _ in rows)
    high = max(score for score, _ in rows)
    bins: list[list[tuple[float, float]]] = [[] for _ in range(5)]
    for score, correct in rows:
        index = 0 if high == low else min(4, int((score - low) / (high - low) * 5))
        bins[index].append((score, correct))
    return [
        {
            "bin": index,
            "n": len(bucket),
            "low": low + (high - low) * index / 5,
            "high": low + (high - low) * (index + 1) / 5,
            "mean_top_score": sum(score for score, _ in bucket) / len(bucket)
            if bucket
            else None,
            "accuracy": sum(correct for _, correct in bucket) / len(bucket)
            if bucket
            else None,
        }
        for index, bucket in enumerate(bins)
    ]


def uncertainty_calibration(
    outcomes: Sequence[SuiteOutcome],
) -> dict[str, float | int | None | list[dict[str, float | int | None]]]:
    """Score retrieval confidence against answerability and answer correctness."""
    answerability = [outcome for outcome in outcomes if "answerable" in outcome.metrics]
    answerability_auc = _auroc_optional(
        [outcome.metrics.get("top_score") for outcome in answerability],
        [outcome.metrics["answerable"] for outcome in answerability],
    )
    answerable_scored = [
        outcome
        for outcome in answerability
        if outcome.metrics["answerable"] == 1.0 and "correct_at_1" in outcome.metrics
    ]
    correctness_auc = _auroc_optional(
        [outcome.metrics.get("top_score") for outcome in answerable_scored],
        [outcome.metrics["correct_at_1"] for outcome in answerable_scored],
    )
    return {
        "answerability_auroc": answerability_auc,
        "correctness_auroc": correctness_auc,
        "n_answerability_scored": len(answerability),
        "n_answerable_scored": len(answerable_scored),
        "n_retrieval_score_bearing": sum(
            "top_score" in row.metrics for row in answerability
        ),
        "reliability": _reliability_table(outcomes),
    }


def contradiction_recording(
    outcomes: Sequence[SuiteOutcome],
) -> dict[str, float | int | None]:
    """Compare contradiction-linked writes on tagged turns and matched controls."""
    tagged = [outcome for outcome in outcomes if "tagged" in outcome.categories]
    controls = [outcome for outcome in outcomes if "control" in outcome.categories]

    def rate(rows: Sequence[SuiteOutcome]) -> float | None:
        measured = [
            row.metrics["contradiction_recorded"]
            for row in rows
            if "contradiction_recorded" in row.metrics
        ]
        return sum(measured) / len(measured) if measured else None

    return {
        "tagged_n": sum("contradiction_recorded" in row.metrics for row in tagged),
        "tagged_recorded_rate": rate(tagged),
        "control_n": sum("contradiction_recorded" in row.metrics for row in controls),
        "control_recorded_rate": rate(controls),
    }


def retrieval_freshness(
    outcomes: Sequence[SuiteOutcome],
) -> dict[str, float | int | None]:
    """Measure invocation of synchronous recall and stale surfacing reuse."""
    turns = [row for row in outcomes if "per_turn_retrieval_ran" in row.metrics]
    surfaced_total = sum(row.metrics.get("surfaced_count", 0.0) for row in turns)
    carried_total = sum(row.metrics.get("surfaced_carried_over", 0.0) for row in turns)
    skipped_with_surface = [
        row
        for row in turns
        if row.metrics.get("surfaced_count", 0.0) > 0
        and row.metrics["per_turn_retrieval_ran"] == 0.0
    ]
    return {
        "turn_n": len(turns),
        "retrieval_ran_fraction": sum(
            row.metrics["per_turn_retrieval_ran"] for row in turns
        )
        / len(turns)
        if turns
        else None,
        "surfaced_carried_over_fraction": carried_total / surfaced_total
        if surfaced_total
        else None,
        "retrieval_skipped_with_surface_fraction": len(skipped_with_surface)
        / len(turns)
        if turns
        else None,
        "surfaced_turn_n": sum(
            row.metrics.get("surfaced_count", 0.0) > 0 for row in turns
        ),
        "surfaced_item_n": int(surfaced_total),
        "surfaced_carried_over_n": int(carried_total),
    }


def outage_propagation(
    outcomes: Sequence[SuiteOutcome],
) -> dict[str, float | int | None]:
    """Measure whether injected search failures become visible to the brain."""
    injected = [row for row in outcomes if row.metrics.get("outage_injected") == 1.0]
    return {
        "outage_turn_n": len(injected),
        "turn_error_rate": sum(row.metrics["turn_errored"] for row in injected)
        / len(injected)
        if injected
        else None,
        "failure_visible_rate": sum(row.metrics["failure_visible"] for row in injected)
        / len(injected)
        if injected
        else None,
    }


async def _await_turn_background(
    service: BrainBenchService, *, seed: int, turn_id: str, timeout: float
) -> tuple[float, float, float]:
    """Wait for System2 and reflection, returning timeout flags without failing replay."""
    tasks = (
        ("System2", service.cognitive.pipeline._system2_task),
        ("reflection", service.cognitive.last_reflection_task),
    )
    timed_out: dict[str, float] = {"System2": 0.0, "reflection": 0.0}
    for name, task in tasks:
        if task is None or task.done():
            continue
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            logger.warning(
                "BrainBench metacognition %s cancelled seed=%s turn=%s",
                name,
                seed,
                turn_id,
            )
        except TimeoutError:
            timed_out[name] = 1.0
            logger.warning(
                "BrainBench metacognition %s timed out seed=%s turn=%s",
                name,
                seed,
                turn_id,
            )
        except Exception as exc:
            logger.warning(
                "BrainBench metacognition %s failed seed=%s turn=%s: %s",
                name,
                seed,
                turn_id,
                exc,
            )
    refresh_timeout = await _await_memory_refreshes(
        service, timeout=timeout, seed=seed, turn_id=turn_id
    )
    return timed_out["System2"], timed_out["reflection"], refresh_timeout


async def _await_memory_refreshes(
    service: BrainBenchService, *, timeout: float, seed: int, turn_id: str
) -> float:
    """Drain search-triggered ACT-R refresh writes so turn snapshots are stable."""
    tasks = tuple(getattr(service.memory_store, "_background_tasks", ()))
    timed_out = 0.0
    for task in tasks:
        if task.done():
            continue
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except TimeoutError:
            timed_out = 1.0
            logger.warning(
                "BrainBench metacognition memory refresh timed out seed=%s turn=%s",
                seed,
                turn_id,
            )
        except Exception as exc:
            logger.warning(
                "BrainBench metacognition memory refresh failed seed=%s turn=%s: %s",
                seed,
                turn_id,
                exc,
            )
    return timed_out


async def _memory_rows(service: BrainBenchService) -> dict[str, str | None]:
    """Read SQLite-backed memory IDs and contradiction links directly."""
    async with service.memory_store.pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, contradicts_id FROM memories")
    return {str(row["id"]): row["contradicts_id"] for row in rows}


INJECTED_OUTAGE_MESSAGE = "BrainBench injected memory search outage"


def _injected_search_failure(store: Any) -> list[dict[str, Any]]:
    """Fail a search the way production's own failure branch does.

    `MemoryStore`'s `except Exception` around the search (memory_store.py
    ~3899-3903) records `last_search_error`/`last_search_error_at` and returns
    an empty result; it never raises to callers. Raising here would test M-8
    against a failure shape V2 does not have. Note that any later successful
    search clears the shared field (~3942), so it is not a durable signal.
    """
    store.last_search_error = INJECTED_OUTAGE_MESSAGE
    store.last_search_error_at = clock.time()
    return []


def _contains_failure_marker(value: Any) -> bool:
    """Inspect yielded output dictionaries and decision metadata recursively.

    A marker is an output ``type == error``, a truthy ``error``/``outage``/
    ``retrieval_degraded``/search-error field, or text explicitly containing
    "outage", "retrieval failed", or "search error". Ordinary response text
    is searched only for those marker phrases; this does not infer awareness
    from a generic fallback sentence.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized == "type" and item == "error":
                return True
            if normalized in _FAILURE_KEYS and item not in (None, False, "", [], {}):
                return True
            if _contains_failure_marker(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_failure_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        return any(
            marker in lowered
            for marker in ("outage", "retrieval failed", "search error")
        )
    return False


def _matched_controls(
    tagged_indices: Sequence[int], untagged_indices: Sequence[int]
) -> set[int]:
    """Select one nearest untagged turn per tagged turn, without replacement."""
    available = set(untagged_indices)
    selected: set[int] = set()
    for tagged_index in tagged_indices:
        if not available:
            break
        match = min(
            available, key=lambda candidate: (abs(candidate - tagged_index), candidate)
        )
        selected.add(match)
        available.remove(match)
    return selected


def _restore_instance_attribute(instance: Any, name: str, prior: Any) -> None:
    if prior is _MISSING:
        instance.__dict__.pop(name, None)
    else:
        setattr(instance, name, prior)


_MISSING = object()


async def run_metacognition_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    persona_seed: int | None = None,
    progress_every: int = 100,
    background_timeout: float = 10.0,
    outage_every: int = 5,
    surfacing: bool = True,
) -> list[SuiteOutcome]:
    """Replay turns and probes, measuring uncertainty, contradiction, freshness and outages."""
    sim, turns, annotations, probes, answers = simulation
    if service.mode != "llm_augmented":
        raise ValueError(
            "metacognition requires llm_augmented mode; DR-037 forbids "
            "architecture_only memory content for scored signals"
        )
    if not 1000 <= sim.seed <= 1099:
        raise ValueError("BrainBench metacognition accepts dev seeds 1000-1099 only")
    if progress_every <= 0 or background_timeout <= 0 or outage_every <= 0:
        raise ValueError(
            "progress_every, background_timeout, and outage_every must be positive"
        )
    if len(turns) != len(annotations) or len(probes) != len(answers):
        raise ValueError("lifesim turn/annotation or probe/answer counts differ")

    seed = sim.seed if persona_seed is None else persona_seed
    if not 1000 <= seed <= 1099:
        raise ValueError("BrainBench metacognition accepts dev seeds 1000-1099 only")
    ordered = sorted(zip(turns, annotations, strict=True), key=lambda pair: pair[0].t)
    ordered_probes = sorted(
        zip(probes, answers, strict=True), key=lambda pair: pair[0].t
    )
    tagged_indices = [
        index
        for index, (_turn, annotation) in enumerate(ordered)
        if _CONTRADICTION_TAGS.intersection(annotation.tags)
    ]
    untagged_indices = [
        index
        for index, (_turn, annotation) in enumerate(ordered)
        if not _CONTRADICTION_TAGS.intersection(annotation.tags)
    ]
    controls = _matched_controls(tagged_indices, untagged_indices)
    eligible = set(tagged_indices) | controls

    outcomes: list[SuiteOutcome] = []
    sim_clock = clock.ManualClock(sim.start)
    turn_index = 0
    prior_action = service.cognitive.action
    prior_decision = service.cognitive.decision
    prior_memory = service.memory_store
    action_before = prior_action.__dict__.get("_surface_fallback_memories", _MISSING)
    decision_before = prior_decision.__dict__.get("decide", _MISSING)
    search_before = prior_memory.__dict__.get("search_memories", _MISSING)
    search_error_before = getattr(prior_memory, "last_search_error", _MISSING)
    current_outage = False
    outage_called = False
    retrieval_ran = False
    decision_snapshots: list[Any] = []

    # BaseAgent.__init__ only initializes local configuration and metrics;
    # NATS is opened by start()/connect(), neither of which this replay calls.
    surfacing_agent = None
    if surfacing:
        graph = MagicMock()
        graph.execute_query = AsyncMock(return_value=[])
        surfacing_agent = SurfacingAgent(
            memory_store=service.memory_store,
            graph_db=graph,
            conversation_store=None,
        )
    agent_publish_before = (
        surfacing_agent.__dict__.get("publish", _MISSING)
        if surfacing_agent is not None
        else _MISSING
    )
    delivered_this_turn: list[str] = []
    ignored_publish_count = 0
    surfaced_provenance: list[tuple[str, int | None]] = [
        (str(memory.get("content", "")), None)
        for memory in service.cognitive.surfaced_memories
    ]
    current_turn_index = -1
    last_sweep_attempt_sim: float | None = None
    last_surfaced_sim: float | None = None

    async def in_process_publish(subject: str, data: dict[str, Any], **kwargs: Any):
        nonlocal ignored_publish_count
        if subject == "memory.surfaced":
            memories = data.get("memories")
            contents = (
                [
                    str(item.get("content", ""))
                    for item in memories
                    if isinstance(item, dict) and item.get("content")
                ]
                if isinstance(memories, list) and memories
                else [str(data.get("content", ""))]
            )
            delivered_this_turn.extend(content for content in contents if content)
            await service.cognitive._on_memory_surfaced(data)
            surfaced_provenance.extend(
                (content, current_turn_index) for content in contents if content
            )
            surfaced_provenance[:] = surfaced_provenance[-5:]
        else:
            ignored_publish_count += 1

    if surfacing_agent is not None:
        surfacing_agent.publish = in_process_publish

    original_surface = prior_action._surface_fallback_memories
    original_decide = prior_decision.decide
    original_search = prior_memory.search_memories

    async def surface_spy(plan: Any, message: str):
        nonlocal retrieval_ran
        retrieval_ran = True
        return await original_surface(plan, message)

    async def decision_spy(event: Any, *args: Any, **kwargs: Any):
        plan = await original_decide(event, *args, **kwargs)
        decision_snapshots.append(
            {
                "event_metadata": dict(event.metadata or {}),
                "plan_payload": dict(getattr(plan, "payload", {}) or {}),
                "decision": getattr(plan, "behavior_decision", None),
            }
        )
        return plan

    async def search_spy(*args: Any, **kwargs: Any):
        nonlocal outage_called
        if current_outage:
            outage_called = True
            return _injected_search_failure(prior_memory)
        return await original_search(*args, **kwargs)

    prior_action._surface_fallback_memories = surface_spy
    prior_decision.decide = decision_spy
    prior_memory.search_memories = search_spy
    try:

        async def drive_turn(index: int, turn: Any) -> None:
            nonlocal current_outage, outage_called, retrieval_ran
            nonlocal last_sweep_attempt_sim, last_surfaced_sim, current_turn_index
            sim_clock.set(turn.t)
            current_turn_index = index
            delivered_this_turn.clear()
            ignored_before = ignored_publish_count
            before_surface = {
                str(memory.get("content", ""))
                for memory in service.cognitive.surfaced_memories
                if memory.get("content")
            }
            surfacing_sweep_would_have_been_throttled = False
            if surfacing_agent is not None:
                turn_seconds = turn.t.timestamp()
                chat_gate_open = (
                    last_surfaced_sim is None or turn_seconds - last_surfaced_sim > 10
                )
                attempt_gate_open = (
                    last_sweep_attempt_sim is None
                    or turn_seconds - last_sweep_attempt_sim
                    >= surfacing_agent.min_sweep_interval
                )
                surfacing_sweep_would_have_been_throttled = not (
                    chat_gate_open and attempt_gate_open
                )
                if chat_gate_open and attempt_gate_open:
                    last_sweep_attempt_sim = turn_seconds
                surfacing_agent.last_context = turn.text
                await surfacing_agent._surface_relevant_memories(source_metadata={})
                if delivered_this_turn:
                    last_surfaced_sim = turn_seconds
            before_rows = await _memory_rows(service)
            decision_snapshots.clear()
            retrieval_ran = False
            injected = (index + 1) % outage_every == 0
            outage_called = False
            current_outage = injected
            raw_event = {
                "id": turn.turn_id,
                "type": "USER_MESSAGE",
                "content": turn.text,
                "metadata": {},
            }
            outputs = [
                output async for output in service.cognitive.process_event(raw_event)
            ]
            (
                system2_timeout,
                reflection_timeout,
                refresh_timeout,
            ) = await _await_turn_background(
                service, seed=seed, turn_id=turn.turn_id, timeout=background_timeout
            )
            current_outage = False
            after_rows = await _memory_rows(service)
            written = {
                memory_id: contradiction_id
                for memory_id, contradiction_id in after_rows.items()
                if memory_id not in before_rows
            }
            after_surface = {
                str(memory.get("content", ""))
                for memory in service.cognitive.surfaced_memories
                if memory.get("content")
            }
            surfaced_carried_over = sum(
                1
                for memory, (content, delivery_turn) in zip(
                    service.cognitive.surfaced_memories,
                    surfaced_provenance,
                    strict=True,
                )
                if memory.get("content") == content
                and delivery_turn is not None
                and delivery_turn < index
            )
            decision_metadata = decision_snapshots[-1] if decision_snapshots else {}
            visible = _contains_failure_marker(outputs) or _contains_failure_marker(
                decision_metadata
            )
            group = (
                "tagged"
                if index in tagged_indices
                else "control"
                if index in controls
                else "other"
            )
            metrics: dict[str, float] = {
                "surfaced_changed": float(after_surface != before_surface),
                "per_turn_retrieval_ran": float(retrieval_ran),
                "surfaced_count": float(len(service.cognitive.surfaced_memories)),
                "surfaced_this_turn": float(len(delivered_this_turn)),
                "surfaced_carried_over": float(surfaced_carried_over),
                "surfacing_sweep_would_have_been_throttled": float(
                    surfacing_sweep_would_have_been_throttled
                ),
                "surfacing_ignored_publish_count": float(
                    ignored_publish_count - ignored_before
                ),
                "outage_scheduled": float(injected),
                "outage_injected": float(outage_called),
                "turn_errored": float(
                    any(output.get("type") == "error" for output in outputs)
                ),
                "failure_visible": float(visible),
                "system2_timed_out": system2_timeout,
                "reflection_timed_out": reflection_timeout,
                "memory_refresh_timed_out": refresh_timeout,
            }
            if index in eligible:
                metrics["rows_written"] = float(len(written))
                metrics["contradiction_recorded"] = float(
                    any(contradiction_id for contradiction_id in written.values())
                )
            outcomes.append(
                SuiteOutcome(
                    probe_key=turn.turn_id,
                    persona_seed=seed,
                    suite="metacognition",
                    categories=("turn", group),
                    metrics=metrics,
                    mode="llm_augmented",
                )
            )
            if (index + 1) % progress_every == 0:
                logger.info(
                    "BrainBench metacognition replay seed=%s turns=%d/%d",
                    seed,
                    index + 1,
                    len(ordered),
                )

        with clock.use_clock(sim_clock):
            for probe, answer in ordered_probes:
                while turn_index < len(ordered) and ordered[turn_index][0].t <= probe.t:
                    turn, _annotation = ordered[turn_index]
                    await drive_turn(turn_index, turn)
                    turn_index += 1
                sim_clock.set(probe.t)
                retrieved = await service.memory_store.search_memories(
                    probe.text, limit=5, current_time=sim_clock.now()
                )
                refresh_timeout = await _await_memory_refreshes(
                    service,
                    timeout=background_timeout,
                    seed=seed,
                    turn_id=probe.probe_id,
                )
                if answer.expected not in {"answer", "abstain"}:
                    continue
                metrics: dict[str, float] = {
                    "answerable": 1.0 if answer.expected == "answer" else 0.0,
                    "memory_refresh_timed_out": refresh_timeout,
                }
                if retrieved:
                    score = retrieved[0].get("score")
                    if score is not None and math.isfinite(float(score)):
                        metrics["top_score"] = float(score)
                if answer.expected == "answer":
                    if not answer.answer:
                        raise ValueError(
                            f"answerable probe {probe.probe_id} has no canonical value"
                        )
                    top_content = (
                        str(retrieved[0].get("content", "")) if retrieved else ""
                    )
                    metrics["correct_at_1"] = float(
                        all(
                            _contains_value(top_content, value)
                            for value in answer.answer
                        )
                    )
                outcomes.append(
                    SuiteOutcome(
                        probe_key=probe.probe_id,
                        persona_seed=seed,
                        suite="metacognition",
                        categories=(answer.category,),
                        metrics=metrics,
                        mode="llm_augmented",
                    )
                )
            while turn_index < len(ordered):
                turn, _annotation = ordered[turn_index]
                await drive_turn(turn_index, turn)
                turn_index += 1
    finally:
        current_outage = False
        _restore_instance_attribute(
            prior_action, "_surface_fallback_memories", action_before
        )
        _restore_instance_attribute(prior_decision, "decide", decision_before)
        _restore_instance_attribute(prior_memory, "search_memories", search_before)
        _restore_instance_attribute(
            prior_memory, "last_search_error", search_error_before
        )
        if surfacing_agent is not None:
            _restore_instance_attribute(
                surfacing_agent, "publish", agent_publish_before
            )

    logger.info(
        "BrainBench metacognition replay complete seed=%s turns=%d outcomes=%d",
        seed,
        turn_index,
        len(outcomes),
    )
    return outcomes


async def run_metacognition_suite_for_seed(
    seed: int,
    archetype: str,
    horizon_label: str,
    service: BrainBenchService,
    *,
    progress_every: int = 100,
    background_timeout: float = 10.0,
    outage_every: int = 5,
    surfacing: bool = True,
) -> list[SuiteOutcome]:
    """Build one dev-seed lifesim and run its metacognition measurements."""
    if not 1000 <= seed <= 1099:
        raise ValueError("BrainBench metacognition accepts dev seeds 1000-1099 only")
    return await run_metacognition_suite(
        build(seed, archetype, horizon_label),
        service,
        persona_seed=seed,
        progress_every=progress_every,
        background_timeout=background_timeout,
        outage_every=outage_every,
        surfacing=surfacing,
    )
