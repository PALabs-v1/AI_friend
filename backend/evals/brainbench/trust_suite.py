"""Trust and relationship measurements for BrainBench lifesim turns.

This suite replays the production architecture-only path and compares the
agent's trust components before and after each complete turn. Lifesim's
robot-directed annotations remain the independent oracle for what the user
did toward the agent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import clock
from evals.brainbench.adapters import BrainBenchService
from evals.brainbench.stats import SuiteOutcome, cliffs_delta
from evals.lifesim.generate import build

logger = logging.getLogger(__name__)


_TRUST_COMPONENTS = ("benevolence", "competence", "integrity")


def _is_masked(
    outcome: SuiteOutcome, components: tuple[str, ...] | None = None
) -> bool:
    """Whether a zero component delta was hidden by a trust bound.

    `components=None` asks about the aggregate trust value, so any pinned
    component masks the turn. A component-specific metric passes only the
    component it reads: V2 pins integrity by turn ~6 and competence by
    ~13, and masking a competence reading because integrity is pinned
    throws away turns where competence was still free to move.
    """
    metrics = outcome.metrics
    if components is None and "ceiling_masked" in metrics:
        return metrics["ceiling_masked"] == 1.0
    for component in components or _TRUST_COMPONENTS:
        before = metrics.get(f"{component}_before")
        delta = metrics.get(f"{component}_delta")
        if before is None or delta is None:
            continue
        if (before >= 1.0 or before <= 0.0) and delta == 0.0:
            return True
    return False


def _unmasked_values(
    outcomes: list[SuiteOutcome],
    metric: str,
    components: tuple[str, ...] | None = None,
) -> tuple[list[float], int]:
    masked_count = sum(_is_masked(outcome, components) for outcome in outcomes)
    return (
        [
            outcome.metrics[metric]
            for outcome in outcomes
            if not _is_masked(outcome, components)
        ],
        masked_count,
    )


def hostility_response(
    outcomes: list[SuiteOutcome],
) -> dict[str, float | int | None]:
    """Summarize trust movement on turns with hostile robot-directed signals."""
    hostile_outcomes = [
        outcome
        for outcome in outcomes
        if outcome.metrics.get("toward_robot_valence", 0.0) < 0
    ]
    if not hostile_outcomes:
        raise ValueError("hostility response requires hostile robot-directed turns")
    hostile, n_masked = _unmasked_values(hostile_outcomes, "trust_delta")
    return {
        "n_hostile": len(hostile_outcomes),
        "n_masked": n_masked,
        "mean_trust_delta": sum(hostile) / len(hostile) if hostile else None,
        "hostile_trust_rise_rate": sum(delta > 0 for delta in hostile) / len(hostile)
        if hostile
        else None,
    }


def competence_warmth_separation(
    outcomes: list[SuiteOutcome],
) -> dict[str, float | int | None]:
    """Compare competence evidence and warmth signals without conflating them."""
    complaint_outcomes = [
        outcome
        for outcome in outcomes
        if outcome.metrics.get("competence_evidence") == -1
    ]
    thanks_outcomes = [
        outcome
        for outcome in outcomes
        if outcome.metrics.get("competence_evidence") == 1
    ]
    robot_zero_competence = [
        outcome
        for outcome in outcomes
        if "toward_robot_valence" in outcome.metrics
        and outcome.metrics.get("competence_evidence") == 0
    ]
    hostile_warmth_outcomes = [
        outcome
        for outcome in robot_zero_competence
        if outcome.metrics["toward_robot_valence"] < 0
    ]
    positive_warmth_outcomes = [
        outcome
        for outcome in robot_zero_competence
        if outcome.metrics["toward_robot_valence"] > 0
    ]
    competence = ("competence",)
    benevolence = ("benevolence",)
    complaints, n_masked_complaints = _unmasked_values(
        complaint_outcomes, "competence_delta", competence
    )
    thanks, n_masked_thanks = _unmasked_values(
        thanks_outcomes, "competence_delta", competence
    )
    hostile_warmth, n_masked_hostile_warmth = _unmasked_values(
        hostile_warmth_outcomes, "benevolence_delta", benevolence
    )
    positive_warmth, n_masked_positive_warmth = _unmasked_values(
        positive_warmth_outcomes, "benevolence_delta", benevolence
    )
    warmth_only_competence, n_masked_warmth_only = _unmasked_values(
        robot_zero_competence, "competence_delta", competence
    )

    return {
        "competence_signal": cliffs_delta(complaints, thanks)
        if complaints and thanks
        else None,
        "n_competence_complaints": len(complaint_outcomes),
        "n_masked_competence_complaints": n_masked_complaints,
        "n_competence_thanks": len(thanks_outcomes),
        "n_masked_competence_thanks": n_masked_thanks,
        "warmth_signal": cliffs_delta(hostile_warmth, positive_warmth)
        if hostile_warmth and positive_warmth
        else None,
        "n_warmth_hostile": len(hostile_warmth_outcomes),
        "n_masked_warmth_hostile": n_masked_hostile_warmth,
        "n_warmth_positive": len(positive_warmth_outcomes),
        "n_masked_warmth_positive": n_masked_positive_warmth,
        "competence_leak": sum(warmth_only_competence) / len(warmth_only_competence)
        if warmth_only_competence
        else None,
        "n_warmth_only": len(robot_zero_competence),
        "n_masked_warmth_only": n_masked_warmth_only,
    }


def background_drift(
    outcomes: list[SuiteOutcome],
) -> dict[str, float | int | None | dict[str, int | None]]:
    """Summarize trust movement on turns without a robot-directed event."""
    if not outcomes:
        raise ValueError("background drift requires at least one outcome")
    background_outcomes = [
        outcome for outcome in outcomes if "not_robot_directed" in outcome.categories
    ]
    background, n_masked = _unmasked_values(background_outcomes, "trust_delta")
    turns_to_ceiling: dict[str, int | None] = {}
    for component in _TRUST_COMPONENTS:
        first_turn = None
        for index, outcome in enumerate(outcomes, start=1):
            after = outcome.metrics.get(f"{component}_after")
            if after is None:
                before = outcome.metrics.get(f"{component}_before")
                delta = outcome.metrics.get(f"{component}_delta")
                if before is None or delta is None:
                    continue
                after = before + delta
            if after >= 1.0:
                first_turn = index
                break
        turns_to_ceiling[component] = first_turn

    return {
        "n": len(background_outcomes),
        "n_masked": n_masked,
        "mean_trust_delta": sum(background) / len(background) if background else None,
        "positive_rate": sum(delta > 0 for delta in background) / len(background)
        if background
        else None,
        "total_trust_delta": sum(background) if background else None,
        "trust_start": outcomes[0].metrics["trust_after"]
        - outcomes[0].metrics["trust_delta"],
        "trust_end": outcomes[-1].metrics["trust_after"],
        "turns_to_ceiling": turns_to_ceiling,
        "saturated_fraction": sum(_is_masked(outcome) for outcome in outcomes)
        / len(outcomes),
    }


def _trust_snapshot(state: Any) -> tuple[float, float, float, float]:
    benevolence = float(state.trust_benevolence)
    competence = float(state.trust_competence)
    integrity = float(state.trust_integrity)
    return benevolence, competence, integrity, float(state.trust)


async def _await_turn_background(
    service: BrainBenchService,
    *,
    seed: int,
    turn_id: str,
    timeout: float,
) -> None:
    tasks = (
        ("System2", service.cognitive.pipeline._system2_task),
        ("reflection", service.cognitive.last_reflection_task),
    )
    for name, task in tasks:
        if task is None or task.done():
            continue
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            logger.warning(
                "BrainBench trust %s task cancelled seed=%s turn=%s",
                name,
                seed,
                turn_id,
            )
        except Exception as exc:
            logger.warning(
                "BrainBench trust %s task failed seed=%s turn=%s: %s",
                name,
                seed,
                turn_id,
                exc,
            )


async def run_trust_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    persona_seed: int | None = None,
    progress_every: int = 100,
    background_timeout: float = 10.0,
) -> list[SuiteOutcome]:
    """Replay every turn and record production trust changes and oracle signals."""
    sim, turns, annotations, _probes, _answers = simulation
    if service.mode != "architecture_only":
        raise ValueError(
            "the trust suite requires an architecture_only BrainBenchService"
        )
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")
    if background_timeout <= 0:
        raise ValueError("background_timeout must be positive")
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
            pre_benevolence, pre_competence, pre_integrity, pre_trust = _trust_snapshot(
                service.cognitive.state.current_state
            )
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
                    f"BrainBench trust turn {turn.turn_id} failed: {errors!r}"
                )

            await _await_turn_background(
                service,
                seed=seed,
                turn_id=turn.turn_id,
                timeout=background_timeout,
            )
            post_benevolence, post_competence, post_integrity, post_trust = (
                _trust_snapshot(service.cognitive.state.current_state)
            )

            robot_events = [
                event
                for event_id in annotation.event_ids
                if (event := sim.events_by_id.get(event_id)) is not None
                and event.toward_robot
            ]
            categories = tuple(event.kind for event in robot_events) or (
                "not_robot_directed",
            )
            metrics = {
                "benevolence_before": pre_benevolence,
                "competence_before": pre_competence,
                "integrity_before": pre_integrity,
                "benevolence_after": post_benevolence,
                "competence_after": post_competence,
                "integrity_after": post_integrity,
                "trust_delta": post_trust - pre_trust,
                "benevolence_delta": post_benevolence - pre_benevolence,
                "competence_delta": post_competence - pre_competence,
                "integrity_delta": post_integrity - pre_integrity,
                "ceiling_masked": float(
                    any(
                        (before >= 1.0 or before <= 0.0) and delta == 0.0
                        for before, delta in (
                            (pre_benevolence, post_benevolence - pre_benevolence),
                            (pre_competence, post_competence - pre_competence),
                            (pre_integrity, post_integrity - pre_integrity),
                        )
                    )
                ),
                "trust_after": post_trust,
                "competence_evidence": float(annotation.competence_evidence),
            }
            if annotation.toward_robot_valence is not None:
                metrics["toward_robot_valence"] = float(annotation.toward_robot_valence)
            outcomes.append(
                SuiteOutcome(
                    probe_key=turn.turn_id,
                    persona_seed=seed,
                    suite="trust",
                    categories=categories,
                    metrics=metrics,
                    mode="architecture_only",
                )
            )

            if index % progress_every == 0:
                logger.info(
                    "BrainBench trust replay seed=%s turns=%d/%d",
                    seed,
                    index,
                    len(turns),
                )

    logger.info(
        "BrainBench trust replay complete seed=%s turns=%d", seed, len(outcomes)
    )
    return outcomes


async def run_trust_suite_for_seed(
    seed: int,
    archetype: str,
    horizon_label: str,
    service: BrainBenchService,
    *,
    progress_every: int = 100,
    background_timeout: float = 10.0,
) -> list[SuiteOutcome]:
    """Build one lifesim and run its architecture-only trust measurements."""
    simulation = build(seed, archetype, horizon_label)
    return await run_trust_suite(
        simulation,
        service,
        persona_seed=seed,
        progress_every=progress_every,
        background_timeout=background_timeout,
    )
