"""Novelty and repeated-mention measurements for BrainBench lifesim turns."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app import clock
from evals.brainbench.adapters import BrainBenchService
from evals.brainbench.stats import SuiteOutcome, cliffs_delta
from evals.lifesim.generate import build

logger = logging.getLogger(__name__)

_REPEATED_TAG = "repeated"


def repetition_novelty_delta(outcomes: list[SuiteOutcome]) -> dict[str, float]:
    """Compare mean appraisal novelty for repeated and other turns.

    The arguments to ``cliffs_delta`` follow the requested order:
    repeated scores first, fresh scores second. The current stats helper
    defines positive delta as the second sample tending to be larger.
    """
    repeated = [
        outcome.metrics["novelty"]
        for outcome in outcomes
        if _REPEATED_TAG in outcome.categories and "novelty" in outcome.metrics
    ]
    fresh = [
        outcome.metrics["novelty"]
        for outcome in outcomes
        if _REPEATED_TAG not in outcome.categories and "novelty" in outcome.metrics
    ]
    if not repeated or not fresh:
        raise ValueError(
            "repetition comparison requires repeated and fresh novelty scores"
        )

    return {
        "repeated_mean": sum(repeated) / len(repeated),
        "fresh_mean": sum(fresh) / len(fresh),
        "cliffs_delta": cliffs_delta(repeated, fresh),
    }


def interference_collision_rate(
    sim: Any,
    outcomes: list[SuiteOutcome],
    turns: list[Any],
    annotations: list[Any],
    *,
    window: int = 20,
    threshold: float = 0.3,
) -> dict[str, float | int]:
    """Measure distinct same-kind event turn pairs scored as near-duplicates.

    Each qualifying chronological turn pair contributes once, even if its
    annotations reference several distinct events of matching kinds.
    """
    if window < 0:
        raise ValueError("window must be non-negative")
    if len(turns) != len(annotations):
        raise ValueError("lifesim turn and annotation counts differ")

    novelty_by_turn = {
        outcome.probe_key: outcome.metrics["novelty"]
        for outcome in outcomes
        if "novelty" in outcome.metrics
    }
    events_by_id = sim.events_by_id
    # Build kind -> event-id sets explicitly; annotations can refer to more
    # than one event, and one matching distinct-id kind is enough per pair.
    event_kinds = []
    for annotation in annotations:
        kinds: dict[str, set[str]] = {}
        for event_id in annotation.event_ids:
            event = events_by_id.get(event_id)
            if event is not None:
                kinds.setdefault(event.kind, set()).add(event_id)
        event_kinds.append(kinds)

    pairs_checked = collisions = 0
    for later_index, later_turn in enumerate(turns):
        start = max(0, later_index - window)
        for earlier_index in range(start, later_index):
            earlier_kinds = event_kinds[earlier_index]
            later_kinds = event_kinds[later_index]
            has_distinct_same_kind = any(
                earlier_ids - later_ids or later_ids - earlier_ids
                for kind in earlier_kinds.keys() & later_kinds.keys()
                for earlier_ids in (earlier_kinds[kind],)
                for later_ids in (later_kinds[kind],)
            )
            if not has_distinct_same_kind:
                continue

            if later_turn.turn_id not in novelty_by_turn:
                raise ValueError(
                    f"missing novelty outcome for turn {later_turn.turn_id}"
                )
            pairs_checked += 1
            if novelty_by_turn[later_turn.turn_id] < threshold:
                collisions += 1

    return {
        "pairs_checked": pairs_checked,
        "collisions": collisions,
        "collision_rate": collisions / pairs_checked if pairs_checked else 0.0,
    }


async def run_attention_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    persona_seed: int | None = None,
    progress_every: int = 100,
) -> list[SuiteOutcome]:
    """Replay every lifesim turn and record its real appraisal novelty."""
    sim, turns, annotations, _probes, _answers = simulation
    if service.mode != "architecture_only":
        raise ValueError(
            "the attention suite requires an architecture_only BrainBenchService"
        )
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")
    if len(turns) != len(annotations):
        raise ValueError("lifesim turn and annotation counts differ")

    seed = sim.seed if persona_seed is None else persona_seed
    outcomes: list[SuiteOutcome] = []
    sim_clock = clock.ManualClock(sim.start)

    with clock.use_clock(sim_clock):
        for index, (turn, annotation) in enumerate(
            zip(turns, annotations, strict=True), start=1
        ):
            if turn.turn_id != annotation.turn_id:
                raise ValueError(
                    "lifesim turns and annotations are not aligned: "
                    f"{turn.turn_id!r} != {annotation.turn_id!r}"
                )
            sim_clock.set(turn.t)
            raw_event = {
                "id": turn.turn_id,
                "type": "USER_MESSAGE",
                "content": turn.text,
                "metadata": {},
            }
            outputs = [
                output async for output in service.cognitive.process_event(raw_event)
            ]
            errors = [output for output in outputs if output.get("type") == "error"]
            if errors:
                raise RuntimeError(
                    f"BrainBench attention turn {turn.turn_id} failed: {errors!r}"
                )
            appraisal_events = [
                output for output in outputs if output.get("type") == "appraisal"
            ]
            if not appraisal_events:
                raise RuntimeError(
                    f"BrainBench attention turn {turn.turn_id} yielded no appraisal event"
                )

            vector = appraisal_events[0]["data"]
            novelty = float(
                vector["novelty"] if isinstance(vector, Mapping) else vector.novelty
            )
            outcomes.append(
                SuiteOutcome(
                    probe_key=turn.turn_id,
                    persona_seed=seed,
                    suite="attention",
                    categories=tuple(annotation.tags) or ("untagged",),
                    metrics={"novelty": novelty, "importance": annotation.importance},
                    mode="architecture_only",
                )
            )

            if index % progress_every == 0:
                logger.info(
                    "BrainBench attention replay seed=%s turns=%d/%d",
                    seed,
                    index,
                    len(turns),
                )

    logger.info(
        "BrainBench attention replay complete seed=%s turns=%d",
        seed,
        len(outcomes),
    )
    return outcomes


async def run_attention_suite_for_seed(
    seed: int,
    archetype: str,
    horizon_label: str,
    service: BrainBenchService,
    *,
    progress_every: int = 100,
) -> list[SuiteOutcome]:
    """Build one dev/tune simulation and record its attention outcomes."""
    simulation = build(seed, archetype, horizon_label)
    return await run_attention_suite(
        simulation, service, persona_seed=seed, progress_every=progress_every
    )
