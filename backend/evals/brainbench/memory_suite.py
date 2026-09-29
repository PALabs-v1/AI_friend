"""Memory-retrieval measurements for the BrainBench lifesim replay."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from app import clock
from evals.brainbench.adapters import BrainBenchService
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build

logger = logging.getLogger(__name__)

# Hybrid scores are pool-relative rather than probabilities. On dev seed 1000,
# an offline SQLite search over a supporting/noise turn pair (with controlled
# unit embeddings) scores them 2.5/-1.0; 0.75 is the conservative midpoint.
ABSTAIN_RELEVANCE_THRESHOLD = 0.75
_SINGLE_VALUE_CATEGORIES = frozenset(
    {
        "current",
        "historical",
        "stale_trap",
        "temporal",
        "trivia_recent",
        "relationship_defining",
        "interference",
    }
)


def _contains_value(content: str, value: str) -> bool:
    """Case-insensitive literal match with the lifesim probe word boundaries."""
    if not value:
        return False
    return (
        re.search(rf"(?<!\w){re.escape(value)}(?!\w)", content, re.IGNORECASE)
        is not None
    )


def _contains_in_retrieval(retrieved: Sequence[dict[str, Any]], value: str) -> bool:
    return any(
        _contains_value(str(row.get("content", "")), value) for row in retrieved[:5]
    )


def stale_values_for(answer: Any, annotations_by_turn: dict[str, Any], sim: Any):
    """The superseded values a stale_trap probe is trying to trap into the top 5.

    A stale turn's truthful claims on the probe's own entity and attribute name
    the old value (same source `probes.py` used to pick the stale turns). The
    current value is excluded: a value the user still holds is not obsolete.
    """
    if answer.category != "stale_trap" or not answer.stale_turn_ids:
        return []
    from evals.lifesim.probes import _value

    entity = answer.derivation.get("entity")
    attribute = answer.derivation.get("attribute")
    current = {answer.answer[0].lower()} if answer.answer else set()
    values: list[str] = []
    for turn_id in answer.stale_turn_ids:
        annotation = annotations_by_turn.get(turn_id)
        if annotation is None:
            continue
        for claim in annotation.claims:
            if (
                claim.truthful
                and claim.entity == entity
                and claim.attribute == attribute
            ):
                value = _value(sim, claim.value)
                if value.lower() not in current and value not in values:
                    values.append(value)
    return values


def _obsolete_win(
    rows: Sequence[dict[str, Any]], current_value: str, stale_values: Sequence[str]
) -> float:
    """1.0 when a stale value beats the current one in the top 5, else 0.0.

    Same rule as `evals.cognitive.metrics` (the memory benchmark BrainBench's
    W1 targets are stated against): the current value missing from the top 5
    while a stale one is present counts as a win for the stale value, and a
    memory that names both at the same rank is not a win.
    """
    contents = [str(row.get("content", "")) for row in rows[:5]]

    def first_rank(values: Sequence[str]) -> int | None:
        return next(
            (
                index
                for index, content in enumerate(contents, start=1)
                if any(_contains_value(content, value) for value in values)
            ),
            None,
        )

    best_stale = first_rank(stale_values)
    if best_stale is None:
        return 0.0
    current_rank = first_rank([current_value])
    return 1.0 if current_rank is None or best_stale < current_rank else 0.0


def score_probe(
    retrieved: Sequence[dict[str, Any]],
    answer: Any,
    stale_values: Sequence[str] | None = None,
) -> dict[str, float]:
    """Score one ProbeAnswer against the top five retrieved memory contents.

    `stale_values` (resolved by `stale_values_for`) adds `obsolete_win` to a
    stale_trap probe. It is left out, not zero, when no stale value could be
    resolved, so the suite mean averages only probes where it can happen.
    """
    rows = list(retrieved[:5])
    metrics: dict[str, float] = {"n_retrieved": float(len(retrieved))}

    if answer.expected == "forgettable":
        metrics["retained"] = float(
            bool(answer.answer) and _contains_in_retrieval(rows, answer.answer[0])
        )
    elif answer.expected == "abstain":
        metrics["abstained"] = float(
            not any(
                float(row.get("score", 0.0) or 0.0) > ABSTAIN_RELEVANCE_THRESHOLD
                for row in rows
            )
        )
    elif answer.expected == "surface_conflict":
        # Detecting that a contradiction exists is out of scope; score only the
        # canonical correct value (the second oracle value).
        if len(answer.answer) < 2:
            raise ValueError(
                "surface_conflict answers must contain wrong and correct values"
            )
        values = [answer.answer[1]]
        _score_hit_and_rank(rows, values, metrics)
    elif answer.category == "commitment_due":
        values = answer.answer
        metrics["coverage"] = (
            sum(_contains_in_retrieval(rows, value) for value in values) / len(values)
            if values
            else 1.0
        )
    elif answer.category == "multi_hop":
        if len(answer.answer) != 2:
            raise ValueError("multi_hop answers must contain person and employer")
        metrics["hit@5"] = float(
            all(_contains_in_retrieval(rows, value) for value in answer.answer)
        )
    elif answer.expected == "answer" and answer.category in _SINGLE_VALUE_CATEGORIES:
        if not answer.answer:
            raise ValueError(
                f"answerable {answer.category} probe has no canonical value"
            )
        _score_hit_and_rank(rows, [answer.answer[0]], metrics)
        if stale_values:
            metrics["obsolete_win"] = _obsolete_win(
                rows, answer.answer[0], stale_values
            )
    else:
        raise ValueError(
            f"unsupported memory probe scoring combination: {answer.category}/{answer.expected}"
        )

    return metrics


def _score_hit_and_rank(
    retrieved: Sequence[dict[str, Any]],
    values: Sequence[str],
    metrics: dict[str, float],
) -> None:
    contents = [str(row.get("content", "")) for row in retrieved[:5]]
    present = [
        any(_contains_value(content, value) for content in contents) for value in values
    ]
    metrics["hit@5"] = float(bool(values) and all(present))
    first_rank = next(
        (
            index
            for index, content in enumerate(contents, start=1)
            if any(_contains_value(content, value) for value in values)
        ),
        None,
    )
    metrics["mrr"] = 1.0 / first_rank if first_rank is not None else 0.0


async def run_memory_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    persona_seed: int | None = None,
    progress_every: int = 100,
) -> list[SuiteOutcome]:
    """Replay one built lifesim and return one retrieval outcome per probe."""
    sim, turns, annotations, probes, answers = simulation
    annotations_by_turn = {annotation.turn_id: annotation for annotation in annotations}
    if service.mode != "llm_augmented":
        raise ValueError("the memory suite requires an llm_augmented BrainBenchService")
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")
    if len(probes) != len(answers):
        raise ValueError("lifesim probe and answer counts differ")
    seed = sim.seed if persona_seed is None else persona_seed
    ordered_turns = sorted(turns, key=lambda turn: turn.t)
    ordered_probes = sorted(
        zip(probes, answers, strict=True), key=lambda pair: pair[0].t
    )
    outcomes: list[SuiteOutcome] = []
    sim_clock = clock.ManualClock(sim.start)
    turn_index = 0

    async def drive_turn(turn: Any) -> None:
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
            raise RuntimeError(f"BrainBench turn {turn.turn_id} failed: {errors!r}")
        reflection_task = service.cognitive.last_reflection_task
        if reflection_task is not None:
            await reflection_task

    with clock.use_clock(sim_clock):
        for probe, answer in ordered_probes:
            while (
                turn_index < len(ordered_turns)
                and ordered_turns[turn_index].t <= probe.t
            ):
                await drive_turn(ordered_turns[turn_index])
                turn_index += 1
                if turn_index % progress_every == 0:
                    logger.info(
                        "BrainBench memory replay seed=%s turns=%d/%d probes=%d/%d",
                        seed,
                        turn_index,
                        len(ordered_turns),
                        len(outcomes),
                        len(ordered_probes),
                    )

            sim_clock.set(probe.t)
            retrieved = await service.memory_store.search_memories(
                probe.text, limit=5, current_time=sim_clock.now()
            )
            metrics = score_probe(
                retrieved, answer, stale_values_for(answer, annotations_by_turn, sim)
            )
            outcome = SuiteOutcome(
                probe_key=probe.probe_id,
                persona_seed=seed,
                suite="memory",
                categories=(answer.category,),
                metrics=metrics,
                mode="llm_augmented",
            )
            outcomes.append(outcome)
            logger.info(
                "BrainBench memory probe seed=%s probe=%s category=%s metrics=%s (%d/%d)",
                seed,
                probe.probe_id,
                answer.category,
                metrics,
                len(outcomes),
                len(ordered_probes),
            )

        # Keep the service's whole turn-driving loop inside the same clock
        # context, including any trailing turns after the final checkpoint.
        while turn_index < len(ordered_turns):
            await drive_turn(ordered_turns[turn_index])
            turn_index += 1
            if turn_index % progress_every == 0:
                logger.info(
                    "BrainBench memory replay seed=%s turns=%d/%d probes=%d/%d",
                    seed,
                    turn_index,
                    len(ordered_turns),
                    len(outcomes),
                    len(ordered_probes),
                )

    logger.info(
        "BrainBench memory replay complete seed=%s turns=%d probes=%d",
        seed,
        turn_index,
        len(outcomes),
    )
    return outcomes


async def run_memory_suite_for_seed(
    seed: int,
    archetype: str,
    horizon_label: str,
    service: BrainBenchService,
    *,
    progress_every: int = 100,
) -> list[SuiteOutcome]:
    """Build one dev/tune simulation and run its memory probes."""
    simulation = build(seed, archetype, horizon_label)
    return await run_memory_suite(
        simulation, service, persona_seed=seed, progress_every=progress_every
    )
