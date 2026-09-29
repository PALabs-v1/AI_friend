"""User-valence and three-layer affect measurements on lifesim replay."""

from __future__ import annotations

import asyncio
import logging
from itertools import pairwise
from typing import Any

from app import clock
from evals.brainbench.adapters import BrainBenchService
from evals.brainbench.stats import SuiteOutcome, cliffs_delta, unpaired_cluster_delta
from evals.lifesim.generate import build

logger = logging.getLogger(__name__)
_PIPELINE_LOGGER = logging.getLogger("app.cognitive.pipeline")
_SYSTEM2_FAILURE_MESSAGE = "Background semantic appraisal failed"


class _System2FailureWatcher(logging.Handler):
    """Record failures from an optional background appraisal task."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.saw_failure = False

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if _SYSTEM2_FAILURE_MESSAGE in message:
            self.saw_failure = True


async def _background_completion(
    task, seed: int, turn_id: str, timeout: float
) -> float:
    watcher = _System2FailureWatcher()
    _PIPELINE_LOGGER.addHandler(watcher)
    try:
        await asyncio.wait_for(task, timeout=timeout)
        return 0.0 if watcher.saw_failure else 1.0
    except Exception as exc:
        logger.warning(
            "BrainBench affect background task failed seed=%s turn=%s: %s",
            seed,
            turn_id,
            exc,
        )
        return 0.0
    finally:
        _PIPELINE_LOGGER.removeHandler(watcher)


def user_valence_reaches_mood(
    outcomes: list[SuiteOutcome], *, valence_threshold: float = 0.1
) -> dict[str, float | int]:
    """Compare mood deltas on positive and negative user-valence turns.

    Cliff's delta receives negative turns first and positive turns second, so
    positive values indicate that positive-oracle turns tend to move mood
    more positively.

    W2's acceptance bar is a positive AND significant Cliff's delta, not
    direction alone. A raw effect size on its own can look perfect off a
    couple of turns (the critic's round-2 repro: one positive and one
    negative observation gave `cliffs_delta: 1.0`, indistinguishable in the
    output from a result backed by dozens of independent conversations), so
    this also runs an independent-groups cluster bootstrap (persona seed is
    the independent sampling unit, not the turn) and reports a 95% CI, a
    p-value and an explicit `significant` flag alongside the effect size
    (W2 critic round 2, finding 4).
    """
    positive = [
        outcome
        for outcome in outcomes
        if outcome.metrics.get(
            "oracle_expressed_valence",
            outcome.metrics.get("oracle_user_valence", 0.0),
        )
        > valence_threshold
    ]
    negative = [
        outcome
        for outcome in outcomes
        if outcome.metrics.get(
            "oracle_expressed_valence",
            outcome.metrics.get("oracle_user_valence", 0.0),
        )
        < -valence_threshold
    ]
    if not positive or not negative:
        raise ValueError("user valence comparison requires positive and negative turns")

    positive_deltas = [outcome.metrics["valence_delta"] for outcome in positive]
    negative_deltas = [outcome.metrics["valence_delta"] for outcome in negative]
    significance = unpaired_cluster_delta(
        negative_deltas,
        [outcome.persona_seed for outcome in negative],
        positive_deltas,
        [outcome.persona_seed for outcome in positive],
    )

    return {
        "positive_mean_delta": sum(positive_deltas) / len(positive_deltas),
        "negative_mean_delta": sum(negative_deltas) / len(negative_deltas),
        "cliffs_delta": cliffs_delta(negative_deltas, positive_deltas),
        "n_positive": len(positive_deltas),
        "n_negative": len(negative_deltas),
        "n_positive_clusters": significance["n_b_clusters"],
        "n_negative_clusters": significance["n_a_clusters"],
        "ci95": significance["ci95"],
        "p_value": significance["p_value"],
        "significant": significance["significant"],
    }


def system2_completion_rate(
    outcomes: list[SuiteOutcome],
) -> dict[str, float | int | None]:
    """Summarize System2 completion where that LLM-only stage applies."""
    applicable = [
        outcome.metrics["system2_completed"]
        for outcome in outcomes
        if outcome.metrics.get("system2_completed") is not None
    ]
    completed = sum(applicable)
    n_turns = len(applicable)
    return {
        "n_turns": n_turns,
        "completed": completed,
        "completion_rate": completed / n_turns if n_turns else None,
    }


def state_bound_rates(outcomes: list[SuiteOutcome]) -> dict[str, float | int]:
    """Report the share of turns at the scalar bounds for each affect layer."""
    layers = (
        "mood",
        "momentary_valence",
        "relationship_sentiment",
    )
    count = len(outcomes)
    return {
        f"{layer}_at_bound_rate": (
            sum(bool(row.metrics.get(f"{layer}_at_bound", False)) for row in outcomes)
            / count
            if count
            else 0.0
        )
        for layer in layers
    }


def _trajectory_layers():
    return (
        "mood",
        "momentary_valence",
        "relationship_sentiment",
    )


def persistence(outcomes: list[SuiteOutcome]) -> dict[str, float | int]:
    """Mean neutral-turn retention of each layer's baseline displacement.

    A transition is included when the current row is neutral and the previous
    row has a nonzero displacement. The ratio is |current| / |previous|.
    """
    result: dict[str, float | int] = {}
    ordered = sorted(outcomes, key=lambda row: row.metrics["trajectory_time_hours"])
    for layer in _trajectory_layers():
        ratios = []
        for previous, current in pairwise(ordered):
            if abs(float(current.metrics["oracle_expressed_valence"])) > 0.1:
                continue
            baseline = float(current.metrics[f"trajectory_baseline_{layer}"])
            before = abs(float(previous.metrics[f"trajectory_post_{layer}"]) - baseline)
            if before == 0:
                continue
            after = abs(float(current.metrics[f"trajectory_post_{layer}"]) - baseline)
            ratios.append(after / before)
        result[f"{layer}_retention_ratio_mean"] = (
            sum(ratios) / len(ratios) if ratios else 0.0
        )
        result[f"{layer}_n_transitions"] = len(ratios)
    return result


def decay(outcomes: list[SuiteOutcome]) -> dict[str, float | int]:
    """Mean hourly change in absolute baseline displacement on neutral turns."""
    result: dict[str, float | int] = {}
    ordered = sorted(outcomes, key=lambda row: row.metrics["trajectory_time_hours"])
    for layer in _trajectory_layers():
        rates = []
        for previous, current in pairwise(ordered):
            if abs(float(current.metrics["oracle_expressed_valence"])) > 0.1:
                continue
            elapsed = float(current.metrics["trajectory_time_hours"]) - float(
                previous.metrics["trajectory_time_hours"]
            )
            if elapsed <= 0:
                continue
            baseline = float(current.metrics[f"trajectory_baseline_{layer}"])
            before = abs(float(previous.metrics[f"trajectory_post_{layer}"]) - baseline)
            after = abs(float(current.metrics[f"trajectory_post_{layer}"]) - baseline)
            rates.append((after - before) / elapsed)
        result[f"{layer}_abs_change_per_hour_mean"] = (
            sum(rates) / len(rates) if rates else 0.0
        )
        result[f"{layer}_n_intervals"] = len(rates)
    return result


def recovery(
    outcomes: list[SuiteOutcome], *, tolerance: float = 0.25
) -> dict[str, float | int]:
    """Elapsed hours from a labelled affect turn to baseline tolerance.

    Conversations with no labelled affect turn or no recovered sample are
    omitted from the mean and reported through the recovered count.
    """
    if tolerance < 0:
        raise ValueError("recovery tolerance must be nonnegative")
    result: dict[str, float | int] = {}
    ordered = sorted(outcomes, key=lambda row: row.metrics["trajectory_time_hours"])
    for layer in _trajectory_layers():
        starts = [
            row
            for row in ordered
            if abs(float(row.metrics["oracle_expressed_valence"])) > 0.1
        ]
        elapsed_values = []
        for start in starts:
            start_time = float(start.metrics["trajectory_time_hours"])
            baseline = float(start.metrics[f"trajectory_baseline_{layer}"])
            recovered = next(
                (
                    float(row.metrics["trajectory_time_hours"]) - start_time
                    for row in ordered
                    if float(row.metrics["trajectory_time_hours"]) >= start_time
                    and abs(float(row.metrics[f"trajectory_post_{layer}"]) - baseline)
                    <= tolerance
                ),
                None,
            )
            if recovered is not None:
                elapsed_values.append(recovered)
        result[f"{layer}_recovery_hours_mean"] = (
            sum(elapsed_values) / len(elapsed_values) if elapsed_values else None
        )
        result[f"{layer}_n_recovered"] = len(elapsed_values)
        result[f"{layer}_n_started"] = len(starts)
    return result


def saturation(outcomes: list[SuiteOutcome]) -> dict[str, float]:
    """Per-layer share of turns at the existing scalar bound threshold."""
    return state_bound_rates(outcomes)


def one_conversation_long_term_effect(
    outcomes: list[SuiteOutcome],
) -> dict[str, float | int]:
    """Final layer value minus that conversation's first recorded baseline."""
    ordered = sorted(outcomes, key=lambda row: row.metrics["trajectory_time_hours"])
    if not ordered:
        return {f"{layer}_net_change": 0.0 for layer in _trajectory_layers()}
    first = ordered[0]
    final = ordered[-1]
    return {
        f"{layer}_net_change": float(final.metrics[f"trajectory_post_{layer}"])
        - float(first.metrics[f"trajectory_baseline_{layer}"])
        for layer in _trajectory_layers()
    }


async def run_affect_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    persona_seed: int | None = None,
    progress_every: int = 25,
    system2_timeout: float = 30.0,
) -> list[SuiteOutcome]:
    """Replay each lifesim turn and record affect state deltas."""
    sim, turns, annotations, _probes, _answers = simulation
    if service.mode not in {"architecture_only", "llm_augmented"}:
        raise ValueError("unsupported BrainBench affect suite mode")
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")
    if system2_timeout <= 0:
        raise ValueError("system2_timeout must be positive")
    if len(turns) != len(annotations):
        raise ValueError("lifesim turn and annotation counts differ")

    seed = sim.seed if persona_seed is None else persona_seed
    outcomes: list[SuiteOutcome] = []
    sim_clock = clock.ManualClock(sim.start)
    initial_state = service.cognitive.state.current_state
    trajectory_baselines = {
        "mood": float(getattr(initial_state, "baseline_valence", 0.0)),
        "momentary_valence": 0.0,
        "relationship_sentiment": float(
            getattr(initial_state, "relationship_sentiment", 0.0)
        ),
    }

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
            state = service.cognitive.state.current_state
            pre_valence = float(state.valence)
            pre_momentary = float(getattr(state, "momentary_valence", 0.0))
            pre_relationship = float(getattr(state, "relationship_sentiment", 0.0))
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
                    f"BrainBench affect turn {turn.turn_id} failed: {errors!r}"
                )

            task = service.cognitive.pipeline._system2_task
            system2_completed = None
            if task is not None:
                system2_completed = await _background_completion(
                    task, seed, turn.turn_id, system2_timeout
                )

            post_valence = float(service.cognitive.state.current_state.valence)
            final_state = service.cognitive.state.current_state
            outcomes.append(
                SuiteOutcome(
                    probe_key=turn.turn_id,
                    persona_seed=seed,
                    suite="affect",
                    categories=tuple(annotation.tags) or ("untagged",),
                    metrics={
                        "valence_delta": post_valence - pre_valence,
                        "mood": post_valence,
                        "mood_at_bound": abs(post_valence) >= 0.99,
                        "momentary_valence": float(
                            getattr(final_state, "momentary_valence", 0.0)
                        ),
                        "momentary_valence_at_bound": abs(
                            float(getattr(final_state, "momentary_valence", 0.0))
                        )
                        >= 0.99,
                        "momentary_valence_delta": float(
                            getattr(final_state, "momentary_valence", 0.0)
                        )
                        - pre_momentary,
                        "relationship_sentiment": float(
                            getattr(final_state, "relationship_sentiment", 0.0)
                        ),
                        "relationship_sentiment_at_bound": abs(
                            float(getattr(final_state, "relationship_sentiment", 0.0))
                        )
                        >= 0.99,
                        "relationship_sentiment_delta": float(
                            getattr(final_state, "relationship_sentiment", 0.0)
                        )
                        - pre_relationship,
                        "oracle_user_valence": float(annotation.user_valence),
                        "oracle_expressed_valence": float(
                            getattr(
                                annotation, "expressed_valence", annotation.user_valence
                            )
                        ),
                        "trajectory_time_hours": (turn.t - sim.start).total_seconds()
                        / 3600.0,
                        "trajectory_baseline_mood": trajectory_baselines["mood"],
                        "trajectory_baseline_momentary_valence": trajectory_baselines[
                            "momentary_valence"
                        ],
                        "trajectory_baseline_relationship_sentiment": trajectory_baselines[
                            "relationship_sentiment"
                        ],
                        "trajectory_post_mood": post_valence,
                        "trajectory_post_momentary_valence": float(
                            getattr(final_state, "momentary_valence", 0.0)
                        ),
                        "trajectory_post_relationship_sentiment": float(
                            getattr(final_state, "relationship_sentiment", 0.0)
                        ),
                        "system2_completed": system2_completed,
                    },
                    mode=service.mode,
                )
            )

            if index % progress_every == 0:
                logger.info(
                    "BrainBench affect replay seed=%s turns=%d/%d",
                    seed,
                    index,
                    len(turns),
                )

    logger.info(
        "BrainBench affect replay complete seed=%s turns=%d",
        seed,
        len(outcomes),
    )
    return outcomes


async def run_affect_suite_for_seed(
    seed: int,
    archetype: str,
    horizon_label: str,
    service: BrainBenchService,
    *,
    progress_every: int = 25,
    system2_timeout: float = 30.0,
) -> list[SuiteOutcome]:
    """Build one dev/tune simulation and run its affect measurements."""
    simulation = build(seed, archetype, horizon_label)
    return await run_affect_suite(
        simulation,
        service,
        persona_seed=seed,
        progress_every=progress_every,
        system2_timeout=system2_timeout,
    )
