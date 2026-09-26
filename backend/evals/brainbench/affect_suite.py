"""User-valence-to-agent-mood measurements for the BrainBench lifesim replay."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import clock
from evals.brainbench.adapters import BrainBenchService
from evals.brainbench.stats import SuiteOutcome, cliffs_delta
from evals.lifesim.generate import build

logger = logging.getLogger(__name__)
_PIPELINE_LOGGER = logging.getLogger("app.cognitive.pipeline")
# appraise_semantic_drift (app/cognitive/appraisal.py) has its OWN try/except
# around the LLM call and JSON parse -- on any failure there it logs "Semantic
# drift evaluation failed" and returns `current_pad` unchanged, without ever
# raising. That means pipeline.py's outer "Background semantic appraisal
# failed" log line -- the only one a first pass at this watcher checked --
# never fires for an LLM/parsing failure, only for a bug in the PAD write
# itself. Watching only the outer message reports 100% "completion" even
# when every single call silently no-opped; both loggers must be watched.
_APPRAISAL_LOGGER = logging.getLogger("app.cognitive.appraisal")
_SYSTEM2_FAILURE_MESSAGE = "Background semantic appraisal failed"
_SEMANTIC_DRIFT_FAILURE_MESSAGE = "Semantic drift evaluation failed"


class _System2FailureWatcher(logging.Handler):
    """Record appraisal failures reported by either the pipeline's background
    task or the semantic-drift call it awaits -- see the module-level note
    on why both loggers must be watched."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.saw_failure = False

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if (
            _SYSTEM2_FAILURE_MESSAGE in message
            or _SEMANTIC_DRIFT_FAILURE_MESSAGE in message
        ):
            self.saw_failure = True


def user_valence_reaches_mood(
    outcomes: list[SuiteOutcome], *, valence_threshold: float = 0.1
) -> dict[str, float | int]:
    """Compare mood deltas on positive and negative user-valence turns.

    Cliff's delta receives negative turns first and positive turns second, so
    positive values indicate that positive-oracle turns tend to move mood
    more positively.
    """
    positive = [
        outcome.metrics["valence_delta"]
        for outcome in outcomes
        if outcome.metrics["oracle_user_valence"] > valence_threshold
    ]
    negative = [
        outcome.metrics["valence_delta"]
        for outcome in outcomes
        if outcome.metrics["oracle_user_valence"] < -valence_threshold
    ]
    if not positive or not negative:
        raise ValueError("user valence comparison requires positive and negative turns")

    return {
        "positive_mean_delta": sum(positive) / len(positive),
        "negative_mean_delta": sum(negative) / len(negative),
        "cliffs_delta": cliffs_delta(negative, positive),
        "n_positive": len(positive),
        "n_negative": len(negative),
    }


def system2_completion_rate(outcomes: list[SuiteOutcome]) -> dict[str, float | int]:
    """Summarize the fraction of turns whose System2 task completed."""
    completed = sum(outcome.metrics["system2_completed"] for outcome in outcomes)
    n_turns = len(outcomes)
    return {
        "n_turns": n_turns,
        "completed": completed,
        "completion_rate": completed / n_turns if n_turns else 0.0,
    }


async def run_affect_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    persona_seed: int | None = None,
    progress_every: int = 25,
    system2_timeout: float = 30.0,
) -> list[SuiteOutcome]:
    """Replay each lifesim turn and record its System2-driven mood delta."""
    sim, turns, annotations, _probes, _answers = simulation
    if service.mode != "llm_augmented":
        raise ValueError("the affect suite requires an llm_augmented BrainBenchService")
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")
    if system2_timeout <= 0:
        raise ValueError("system2_timeout must be positive")
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
            pre_valence = float(service.cognitive.state.current_state.valence)
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
            system2_completed = 0.0
            if task is not None:
                watcher = _System2FailureWatcher()
                _PIPELINE_LOGGER.addHandler(watcher)
                _APPRAISAL_LOGGER.addHandler(watcher)
                try:
                    await asyncio.wait_for(task, timeout=system2_timeout)
                    system2_completed = 0.0 if watcher.saw_failure else 1.0
                except (TimeoutError, Exception) as exc:
                    logger.warning(
                        "BrainBench affect System2 task failed seed=%s turn=%s: %s",
                        seed,
                        turn.turn_id,
                        exc,
                    )
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                    logger.warning(
                        "BrainBench affect System2 task cancelled seed=%s turn=%s",
                        seed,
                        turn.turn_id,
                    )
                finally:
                    _PIPELINE_LOGGER.removeHandler(watcher)
                    _APPRAISAL_LOGGER.removeHandler(watcher)

            post_valence = float(service.cognitive.state.current_state.valence)
            outcomes.append(
                SuiteOutcome(
                    probe_key=turn.turn_id,
                    persona_seed=seed,
                    suite="affect",
                    categories=tuple(annotation.tags) or ("untagged",),
                    metrics={
                        "valence_delta": post_valence - pre_valence,
                        "oracle_user_valence": float(annotation.user_valence),
                        "system2_completed": system2_completed,
                    },
                    mode="llm_augmented",
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
