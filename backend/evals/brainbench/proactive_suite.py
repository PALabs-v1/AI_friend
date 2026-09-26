"""Executive and proactive timing measurements for BrainBench lifesim.

This is an architecture-only timing suite: it replays the real CognitiveService
and a separate StateService like the subconscious process. The tick path calls
the production eligibility gate and SubconsciousEngine. NullLLM returns the
non-empty string ``"{}"``, so an eligible tick records an initiation; the suite
does not call ``generate_proactive_response`` and therefore does not add a
reflection episode. Broadcasts are captured from the brain StateService's real
``persist_state`` callback payload.

Lifesim models interaction-hour distributions, not sleep. For ``night`` counts
we use fixed sleep-window proxies aligned to the CHRONO session peaks:
morning 22:00-06:00, evening 01:00-08:00, night 03:00-11:00, flat 23:00-07:00.
An initiation is useful only for a previously disclosed, still-planned
commitment whose timeline ``when`` value is within the following 24 hours.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
import statistics
from collections import deque
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock

from app import clock
from app.cognitive.subconscious import SubconsciousEngine
from app.config import Config
from app.state.agent_state import StateService
from evals.brainbench.adapters import BrainBenchService, NullLLM, isolated_runtime
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build

logger = logging.getLogger(__name__)

SyncArm = Literal["broadcast", "none"]
TickOrder = Literal["brain_first", "subconscious_first"]
_SLEEP_WINDOWS = {
    "morning": (22, 6),
    "evening": (1, 8),
    "night": (3, 11),
    "flat": (23, 7),
}
_GAP_BUCKETS = ((2, "<2h"), (12, "2h-12h"), (72, "12h-3d"))


def _gap_bucket(hours: float) -> str:
    for bound, label in _GAP_BUCKETS:
        if hours < bound:
            return label
    return ">3d"


def _is_night(at: datetime, chronotype: str) -> bool:
    """Use the suite's documented local-time sleep proxy for this chronotype."""
    start, end = _SLEEP_WINDOWS.get(chronotype, _SLEEP_WINDOWS["flat"])
    return at.hour >= start or at.hour < end if start > end else start <= at.hour < end


def _known_commitments(sim: Any, annotations: Iterable[Any]) -> set[str]:
    """Return plan entities disclosed by commitment-tagged user turns."""
    known: set[str] = set()
    for annotation in annotations:
        if "commitment" not in annotation.tags:
            continue
        known.update(
            claim.entity
            for claim in annotation.claims
            if claim.entity.startswith("plan:")
        )
        for event_id in annotation.event_ids:
            event = sim.events_by_id.get(event_id)
            if event is not None:
                plan = event.payload.get("plan")
                if isinstance(plan, str) and plan.startswith("plan:"):
                    known.add(plan)
    return known


def _active_goal_context(sim: Any, known_plans: set[str], at: datetime) -> list[str]:
    """Give the brain readable details only for plans disclosed by this time."""
    result = []
    for entity in sorted(known_plans):
        what = sim.timeline.value_at(entity, "what", at)
        when = sim.timeline.value_at(entity, "when", at)
        details = what.value if what is not None else "a disclosed plan"
        due = ""
        if when is not None:
            due_at = datetime.fromisoformat(when.value)
            if due_at.tzinfo is None and at.tzinfo is not None:
                due_at = due_at.replace(tzinfo=at.tzinfo)
            elif due_at.tzinfo is not None and at.tzinfo is None:
                due_at = due_at.replace(tzinfo=None)
            hours = (due_at - at).total_seconds() / 3600
            due = f"; due_in_hours={hours:.1f} ({when.value})"
        result.append(f"{entity}: {details}{due}")
    return result


_PLAN_DESCRIPTION_STOPWORDS = {
    "about",
    "after",
    "before",
    "check",
    "finish",
    "for",
    "from",
    "help",
    "have",
    "into",
    "make",
    "plan",
    "some",
    "submit",
    "the",
    "their",
    "them",
    "this",
    "work",
    "would",
}


def _normalised_terms(text: str) -> set[str]:
    terms = set(re.findall(r"[a-z0-9]{3,}", text.casefold()))
    return terms - _PLAN_DESCRIPTION_STOPWORDS


def _mentions_plan(candidate: str, entity: str, what: str) -> bool:
    if entity in candidate:
        return True
    plan_terms = _normalised_terms(what)
    if not plan_terms:
        return False
    overlap = len(plan_terms & _normalised_terms(candidate))
    return overlap > 0 and overlap / len(plan_terms) >= 0.5


def _useful_initiation(
    sim: Any, known_plans: set[str], at: datetime, description: str
) -> bool:
    for entity in known_plans:
        status = sim.timeline.value_at(entity, "status", at)
        when = sim.timeline.value_at(entity, "when", at)
        what = sim.timeline.value_at(entity, "what", at)
        if status is None or status.value != "planned" or when is None:
            continue
        plan_text = what.value if what is not None else ""
        if not _mentions_plan(description, entity, plan_text):
            continue
        try:
            due = datetime.fromisoformat(when.value)
        except ValueError:
            continue
        if at < due <= at + timedelta(hours=24):
            return True
    return False


def _broadcast_lowered_marked_attempt(
    local_before: float, incoming: float, last_marked: float | None
) -> bool:
    """Detect a transition below a timestamp this replay actually marked."""
    return (
        last_marked is not None
        and local_before >= last_marked
        and incoming < last_marked
    )


async def _await_turn_background(
    service: BrainBenchService, *, timeout: float, turn_id: str
) -> None:
    tasks = [
        service.cognitive.pipeline._system2_task,
        service.cognitive.last_reflection_task,
        *tuple(service.cognitive.state._background_tasks),
    ]
    for task in tasks:
        if task is None or task.done():
            continue
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            logger.warning(
                "BrainBench proactive background task cancelled turn=%s", turn_id
            )
        except Exception as exc:
            logger.warning(
                "BrainBench proactive background task failed turn=%s: %s", turn_id, exc
            )


async def _apply_pending_broadcasts(
    broadcasts: deque[dict[str, Any]],
    subconscious_state: StateService,
    last_marked_proactive_attempt: float | None,
) -> int:
    """Apply and release queued snapshots; retain no history already consumed."""
    resets = 0
    while broadcasts:
        snapshot = broadcasts.popleft()
        before = subconscious_state.current_state.last_proactive_attempt
        await subconscious_state.apply_external_state(snapshot)
        after = subconscious_state.current_state.last_proactive_attempt
        resets += int(
            _broadcast_lowered_marked_attempt(
                before, after, last_marked_proactive_attempt
            )
        )
    return resets


async def _tick_gap(
    *,
    sim: Any,
    start: datetime,
    end: datetime,
    sim_clock: clock.ManualClock,
    subconscious_state: StateService,
    brain_state: StateService,
    broadcasts: deque[dict[str, Any]],
    sync: SyncArm,
    tick_order: TickOrder,
    background_timeout: float,
    engine: SubconsciousEngine,
    max_ticks: int,
    known_plans: set[str],
    chronotype: str,
    prior_initiation: datetime | None,
    last_marked_proactive_attempt: float | None,
) -> dict[str, Any]:
    interval = int(Config.SYSTEM_TICK_INTERVAL)
    if interval <= 0:
        raise ValueError("SYSTEM_TICK_INTERVAL must be positive")
    gap_seconds = max(0.0, (end - start).total_seconds())
    total_ticks = int(max(0.0, gap_seconds - 1e-9) // interval)
    ticks = min(total_ticks, max_ticks)
    capped = total_ticks > max_ticks
    initiations: list[datetime] = []
    eligible_ticks = 0
    useful = irrelevant = night = cooldown_violations = 0
    quiet = self_directed = useful_category = 0
    importance_values: list[float] = []
    useful_by_category = {"useful_to_user": 0, "self_directed": 0}
    count_by_category = {"useful_to_user": 0, "self_directed": 0}
    resets = 0
    ignored_events = 0
    ignored_raise_sum = 0
    last_attempt_set_by_suite: float | None = None
    previous_init = prior_initiation
    for tick_index in range(1, ticks + 1):
        tick_at = start + timedelta(seconds=tick_index * interval)
        sim_clock.set(tick_at)

        async def apply_brain_tick() -> None:
            pending_before = set(brain_state._background_tasks)
            await brain_state.handle_system_tick(
                {
                    "timestamp": tick_at.timestamp(),
                    "interval": Config.SYSTEM_TICK_INTERVAL,
                }
            )
            tick_broadcasts = brain_state._background_tasks - pending_before
            if tick_broadcasts:
                await asyncio.wait_for(
                    asyncio.gather(*tick_broadcasts), timeout=background_timeout
                )

        async def apply_pending_broadcasts() -> None:
            nonlocal last_marked_proactive_attempt, resets
            if sync != "broadcast":
                return
            resets += await _apply_pending_broadcasts(
                broadcasts,
                subconscious_state,
                last_marked_proactive_attempt,
            )

        async def run_subconscious_tick() -> None:
            nonlocal eligible_ticks, last_attempt_set_by_suite, previous_init
            nonlocal cooldown_violations, useful, irrelevant, night
            nonlocal last_marked_proactive_attempt
            nonlocal importance_values, self_directed, useful_category, quiet
            nonlocal ignored_events, ignored_raise_sum
            if tick_at.timestamp() - 0.0 < 300:
                return
            # Commitment timing advances during an idle gap. Refresh the
            # already-disclosed set at this tick so an upcoming plan crosses
            # the 24-hour urgency window without admitting future disclosures.
            active_goal_context = _active_goal_context(sim, known_plans, tick_at)
            subconscious_state.current_state.active_goals = active_goal_context
            brain_state.current_state.active_goals = active_goal_context
            state_snapshot = subconscious_state.get_context_snapshot()
            eligible = subconscious_state.check_proactive_eligibility()
            eligible_ticks += int(eligible)
            candidate = await engine.evaluate_and_think(state_snapshot, eligible)
            if not candidate:
                return
            importance_values.append(candidate.importance)
            ignored_before = {
                goal.goal_id: goal.proactive_ignored_count
                for goal in subconscious_state.proactive_goals
            }
            accepted = subconscious_state.proactive_candidate_eligible(
                importance=candidate.importance,
                category=candidate.category,
                description=candidate.text,
                goal_id=candidate.goal_id,
            )
            for goal in subconscious_state.proactive_goals:
                newly_ignored = goal.proactive_ignored_count - ignored_before.get(
                    goal.goal_id, 0
                )
                ignored_events += newly_ignored
                ignored_raise_sum += newly_ignored * goal.proactive_raise_count
            subconscious_state.mark_proactive_attempt()
            if not accepted:
                return
            goal_id, _ = subconscious_state.record_proactive_thought(
                candidate.text, goal_id=candidate.goal_id
            )
            brain_state.record_proactive_thought(candidate.text, goal_id=goal_id)
            last_attempt_set_by_suite = (
                subconscious_state.current_state.last_proactive_attempt
            )
            last_marked_proactive_attempt = last_attempt_set_by_suite
            initiations.append(tick_at)
            if previous_init is not None:
                spacing = (tick_at - previous_init).total_seconds()
                cooldown_violations += int(spacing < Config.PROACTIVE_COOLDOWN_SECONDS)
            previous_init = tick_at
            if _useful_initiation(sim, known_plans, tick_at, candidate.text):
                useful += 1
                useful_by_category[candidate.category] += 1
            else:
                irrelevant += 1
            count_by_category[candidate.category] += 1
            self_directed += int(candidate.category == "self_directed")
            useful_category += int(candidate.category == "useful_to_user")
            quiet += int(subconscious_state.is_quiet_hour(tick_at.timestamp()))
            night += int(_is_night(tick_at, chronotype))

        if tick_order == "brain_first":
            await apply_brain_tick()
            await apply_pending_broadcasts()
        # `_on_system_tick` suppresses its proactive branch for the first five
        # minutes after benchmark mode was entered; production's default
        # `_last_benchmark_time` is zero, so ordinary lifesim timestamps pass.
        if tick_order == "subconscious_first":
            await run_subconscious_tick()
            await apply_brain_tick()
            await apply_pending_broadcasts()
        else:
            await run_subconscious_tick()
    spacing_points = (
        [prior_initiation, *initiations]
        if prior_initiation is not None and initiations
        else initiations
    )
    spacings = [
        (right - left).total_seconds()
        for left, right in itertools.pairwise(spacing_points)
    ]
    return {
        "ticks": ticks,
        "eligible_ticks": eligible_ticks,
        "initiations_at": initiations,
        "useful_initiations": useful,
        "irrelevant_initiations": irrelevant,
        "night_initiations": night,
        "quiet_hour_initiations": quiet,
        "importance_values": importance_values,
        "self_directed_initiations": self_directed,
        "useful_to_user_initiations": useful_category,
        "useful_to_user_category_count": count_by_category["useful_to_user"],
        "self_directed_category_count": count_by_category["self_directed"],
        "useful_by_category": useful_by_category,
        "cooldown_violations": cooldown_violations,
        "min_seconds_between_initiations": min(spacings) if spacings else None,
        "first_initiation_after_hours": (
            (initiations[0] - start).total_seconds() / 3600 if initiations else None
        ),
        "capped": capped,
        "resets": resets,
        "ignored_thought_count": ignored_events,
        "ignored_thought_raises": ignored_raise_sum,
        "last_marked_proactive_attempt": last_marked_proactive_attempt,
        "last_attempt_set_by_suite": last_attempt_set_by_suite,
    }


def initiation_timing(outcomes: list[SuiteOutcome]) -> dict[str, float | int | None]:
    """Summarize outreach rate, delay, night share and user-turn collisions."""
    if not outcomes:
        return {
            "initiations_per_simulated_day": None,
            "median_first_initiation_delay_hours": None,
            "median_initiations_per_idle_hour_after_threshold": None,
            "night_fraction": None,
            "collision_rate": None,
            "initiations": 0,
        }
    initiations = sum(int(row.metrics["initiations"]) for row in outcomes)
    days = sum(float(row.metrics["gap_hours"]) for row in outcomes) / 24
    delays = [
        float(row.metrics["first_initiation_after_hours"])
        for row in outcomes
        if "first_initiation_after_hours" in row.metrics
    ]
    nights = sum(int(row.metrics["night_initiations"]) for row in outcomes)
    collisions = sum(int(row.metrics["collision"]) for row in outcomes)
    idle_hour_rates = [
        float(row.metrics["initiations_per_idle_hour_after_threshold"])
        for row in outcomes
        if "initiations_per_idle_hour_after_threshold" in row.metrics
    ]
    return {
        "initiations_per_simulated_day": initiations / days if days else None,
        "median_first_initiation_delay_hours": statistics.median(delays)
        if delays
        else None,
        "median_initiations_per_idle_hour_after_threshold": (
            statistics.median(idle_hour_rates) if idle_hour_rates else None
        ),
        # No outreach means no nighttime outreach; keep the gated share at
        # zero while the separate initiation count still exposes inactivity.
        "night_fraction": nights / initiations if initiations else 0.0,
        "collision_rate": collisions / len(outcomes),
        "initiations": initiations,
    }


def cooldown_integrity(outcomes: list[SuiteOutcome]) -> dict[str, float | int | None]:
    """Summarize sub-cooldown outreach, observed timestamp resets and spacing."""
    spacings = [
        float(row.metrics["min_seconds_between_initiations"])
        for row in outcomes
        if row.metrics.get("min_seconds_between_initiations") is not None
    ]
    return {
        "cooldown_violations": sum(
            int(row.metrics["cooldown_violations"]) for row in outcomes
        ),
        "last_proactive_attempt_resets": sum(
            int(row.metrics["last_proactive_attempt_resets"]) for row in outcomes
        ),
        "min_seconds_between_initiations": min(spacings) if spacings else None,
    }


def initiation_usefulness(
    outcomes: list[SuiteOutcome],
) -> dict[str, float | int | None]:
    """Count useful (upcoming commitment) and irrelevant outreach."""
    useful = sum(int(row.metrics["useful_initiations"]) for row in outcomes)
    irrelevant = sum(int(row.metrics["irrelevant_initiations"]) for row in outcomes)
    total = useful + irrelevant
    return {
        "useful_initiations": useful,
        "irrelevant_initiations": irrelevant,
        "annoyance_count": irrelevant,
        "useful_rate": useful / total if total else None,
    }


def importance_distribution(
    outcomes: list[SuiteOutcome],
) -> dict[str, float | None]:
    count = sum(float(row.metrics.get("importance_count", 0.0)) for row in outcomes)
    total = sum(float(row.metrics.get("importance_sum", 0.0)) for row in outcomes)
    observed_min = [
        float(row.metrics["importance_min"])
        for row in outcomes
        if row.metrics.get("importance_count", 0)
    ]
    observed_max = [
        float(row.metrics["importance_max"])
        for row in outcomes
        if row.metrics.get("importance_count", 0)
    ]
    return {
        "mean": total / count if count else None,
        "min": min(observed_min) if observed_min else None,
        "max": max(observed_max) if observed_max else None,
        "count": count,
        "non_degenerate": float(
            bool(observed_min and min(observed_min) < max(observed_max))
        ),
    }


def category_distribution(outcomes: list[SuiteOutcome]) -> dict[str, float | None]:
    useful = sum(
        float(row.metrics.get("useful_to_user_category_count", 0.0)) for row in outcomes
    )
    self_directed = sum(
        float(row.metrics.get("self_directed_category_count", 0.0)) for row in outcomes
    )
    total = useful + self_directed
    return {
        "useful_to_user_count": useful,
        "self_directed_count": self_directed,
        "useful_to_user_share": useful / total if total else None,
        "self_directed_share": self_directed / total if total else None,
    }


def category_usefulness(outcomes: list[SuiteOutcome]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for category, prefix in (
        ("useful_to_user", "useful_to_user"),
        ("self_directed", "self_directed"),
    ):
        count = sum(
            float(row.metrics.get(f"{prefix}_category_count", 0.0)) for row in outcomes
        )
        useful = sum(
            float(row.metrics.get(f"{prefix}_useful", 0.0)) for row in outcomes
        )
        result[f"{category}_useful_rate"] = useful / count if count else None
    return result


def re_raise_decay(outcomes: list[SuiteOutcome]) -> dict[str, float | None]:
    """Report raised thoughts per ignored thought, retaining zero as evidence."""
    ignored = sum(
        float(row.metrics.get("ignored_thought_count", 0.0)) for row in outcomes
    )
    raises = sum(
        float(row.metrics.get("ignored_thought_raises", 0.0)) for row in outcomes
    )
    return {
        "ignored_thoughts": ignored,
        "raises_per_ignored_thought": raises / ignored if ignored else None,
    }


async def run_proactive_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    sync: SyncArm,
    tick_order: TickOrder = "brain_first",
    run_dir: Path,
    persona_seed: int | None = None,
    max_ticks_per_gap: int = 7 * 24 * 60,
    collision_window_seconds: int | None = None,
    background_timeout: float = 10.0,
) -> list[SuiteOutcome]:
    """Replay brain turns and ticks alongside the subconscious gate.

    ``brain_first`` is the default because the brain's tick handler is
    milliseconds of arithmetic plus a SQLite write, while the subconscious
    proactive branch awaits an LLM call before marking; its broadcast therefore
    normally reaches the subconscious first. ``subconscious_first`` models the
    opposite scheduling order for sensitivity analysis.
    """
    sim, turns, annotations, _probes, _answers = simulation
    if sync not in ("broadcast", "none"):
        raise ValueError(f"unknown proactive state-sync arm: {sync!r}")
    if tick_order not in ("brain_first", "subconscious_first"):
        raise ValueError(f"unknown system tick order: {tick_order!r}")
    if service.mode not in {"architecture_only", "llm_augmented"}:
        raise ValueError(
            "the proactive suite requires a supported BrainBenchService mode"
        )
    if len(turns) != len(annotations):
        raise ValueError("lifesim turn and annotation counts differ")
    if max_ticks_per_gap <= 0:
        raise ValueError("max_ticks_per_gap must be positive")
    if background_timeout <= 0:
        raise ValueError("background_timeout must be positive")
    if any(
        turn.turn_id != annotation.turn_id
        for turn, annotation in zip(turns, annotations)
    ):
        raise ValueError("lifesim turns and annotations are not aligned")
    if len(turns) < 2:
        return []

    run_dir.mkdir(parents=True, exist_ok=True)
    seed = sim.seed if persona_seed is None else persona_seed
    brain_state = service.cognitive.state
    # Same isolation as the brain's own service (adapters.isolated_runtime):
    # no Redis, so this cell's subconscious state lives only in run_dir.
    with isolated_runtime():
        subconscious_state = StateService(
            graph_store=MagicMock(),
            db_path=str(run_dir / "subconscious_state.db"),
            persona=service.cognitive.identity.persona,
            writer_id="subconscious_agent",
        )
    subconscious_state.current_state.last_user_interaction = sim.start.timestamp()
    subconscious_state.current_state.last_proactive_attempt = 0.0
    engine = SubconsciousEngine(llm_client=getattr(service, "llm_service", NullLLM()))
    sim_clock = clock.ManualClock(sim.start)
    original_publish_cb = brain_state.publish_cb
    broadcasts: deque[dict[str, Any]] = deque()
    broadcasts_emitted = 0

    async def capture_publish(subject: str, data: dict[str, Any]) -> None:
        nonlocal broadcasts_emitted
        if subject == "state.broadcast" and sync == "broadcast":
            broadcasts.append(data)
            broadcasts_emitted += 1
        if original_publish_cb is not None:
            await original_publish_cb(subject, data)

    brain_state.publish_cb = capture_publish
    known_plans: set[str] = set()
    visible_plans = _known_commitments(sim, annotations)
    all_outcomes: list[SuiteOutcome] = []
    prior_initiation: datetime | None = None
    last_marked_proactive_attempt: float | None = None
    window = int(
        Config.SYSTEM_TICK_INTERVAL
        if collision_window_seconds is None
        else collision_window_seconds
    )
    if window <= 0:
        brain_state.publish_cb = original_publish_cb
        raise ValueError("collision_window_seconds must be positive")
    try:
        with clock.use_clock(sim_clock):
            first_turn = turns[0]
            sim_clock.set(first_turn.t)
            brain_state.current_state.last_user_interaction = first_turn.t.timestamp()
            subconscious_state.current_state.last_user_interaction = (
                first_turn.t.timestamp()
            )
            brain_state.record_user_interaction()
            subconscious_state.record_user_interaction()
            broadcasts_before_turn = broadcasts_emitted
            first_outputs = [
                output
                async for output in service.cognitive.process_event(
                    {
                        "id": first_turn.turn_id,
                        "type": "USER_MESSAGE",
                        "content": first_turn.text,
                        "metadata": {},
                    }
                )
            ]
            first_errors = [
                item for item in first_outputs if item.get("type") == "error"
            ]
            if first_errors:
                raise RuntimeError(
                    f"BrainBench proactive turn {first_turn.turn_id} failed: {first_errors!r}"
                )
            await _await_turn_background(
                service, timeout=background_timeout, turn_id=first_turn.turn_id
            )
            if sync == "broadcast":
                if broadcasts_emitted == broadcasts_before_turn:
                    raise RuntimeError(
                        f"brain emitted no state.broadcast after {first_turn.turn_id}"
                    )
                await _apply_pending_broadcasts(
                    broadcasts, subconscious_state, last_marked_proactive_attempt
                )
            known_plans.update(
                claim.entity
                for claim in annotations[0].claims
                if "commitment" in annotations[0].tags and claim.entity in visible_plans
            )
            # Lifesim's annotation is attached to the turn that disclosed the
            # commitment; copy only those already-visible entities into the
            # state signal consumed by the model-free brain path.
            known_context = _active_goal_context(sim, known_plans, first_turn.t)
            brain_state.current_state.active_goals = known_context
            subconscious_state.current_state.active_goals = known_context

            for index in range(1, len(turns)):
                previous, turn = turns[index - 1], turns[index]
                gap_hours = (turn.t - previous.t).total_seconds() / 3600
                if gap_hours < 0:
                    raise ValueError("lifesim turns are not chronological")
                gap = await _tick_gap(
                    sim=sim,
                    start=previous.t,
                    end=turn.t,
                    sim_clock=sim_clock,
                    subconscious_state=subconscious_state,
                    brain_state=brain_state,
                    broadcasts=broadcasts,
                    sync=sync,
                    tick_order=tick_order,
                    background_timeout=background_timeout,
                    engine=engine,
                    max_ticks=max_ticks_per_gap,
                    known_plans=known_plans,
                    chronotype=sim.persona.chronotype,
                    prior_initiation=prior_initiation,
                    last_marked_proactive_attempt=last_marked_proactive_attempt,
                )
                last_marked_proactive_attempt = gap["last_marked_proactive_attempt"]
                initiated_at = gap["initiations_at"]
                if initiated_at:
                    prior_initiation = initiated_at[-1]
                if gap["last_attempt_set_by_suite"] is not None:
                    last_marked_proactive_attempt = gap["last_attempt_set_by_suite"]
                collision = bool(
                    prior_initiation is not None
                    and 0 <= (turn.t - prior_initiation).total_seconds() <= window
                )

                sim_clock.set(turn.t)
                brain_state.current_state.last_user_interaction = turn.t.timestamp()
                subconscious_state.current_state.last_user_interaction = (
                    turn.t.timestamp()
                )
                brain_state.resolve_proactive_thoughts(turn.text)
                subconscious_state.resolve_proactive_thoughts(turn.text)
                brain_state.record_user_interaction()
                subconscious_state.record_user_interaction()
                broadcasts_before_turn = broadcasts_emitted
                outputs = [
                    output
                    async for output in service.cognitive.process_event(
                        {
                            "id": turn.turn_id,
                            "type": "USER_MESSAGE",
                            "content": turn.text,
                            "metadata": {},
                        }
                    )
                ]
                errors = [item for item in outputs if item.get("type") == "error"]
                if errors:
                    raise RuntimeError(
                        f"BrainBench proactive turn {turn.turn_id} failed: {errors!r}"
                    )
                await _await_turn_background(
                    service, timeout=background_timeout, turn_id=turn.turn_id
                )
                if sync == "broadcast":
                    if broadcasts_emitted == broadcasts_before_turn:
                        raise RuntimeError(
                            f"brain emitted no state.broadcast after {turn.turn_id}"
                        )
                    gap["resets"] += await _apply_pending_broadcasts(
                        broadcasts,
                        subconscious_state,
                        last_marked_proactive_attempt,
                    )
                else:
                    subconscious_state.current_state.last_user_interaction = (
                        turn.t.timestamp()
                    )

                annotation = annotations[index]
                if "commitment" in annotation.tags:
                    known_plans.update(
                        claim.entity
                        for claim in annotation.claims
                        if claim.entity in visible_plans
                    )
                    for event_id in annotation.event_ids:
                        event = sim.events_by_id.get(event_id)
                        if event is not None:
                            plan = event.payload.get("plan")
                            if isinstance(plan, str) and plan in visible_plans:
                                known_plans.add(plan)

                # Keep the next idle-gap snapshot limited to commitments
                # disclosed up through this user turn.
                known_context = _active_goal_context(sim, known_plans, turn.t)
                brain_state.current_state.active_goals = known_context
                subconscious_state.current_state.active_goals = known_context

                useful_count = gap["useful_initiations"]
                irrelevant_count = gap["irrelevant_initiations"]
                gap_metrics: dict[str, float] = {
                    "gap_hours": gap_hours,
                    "ticks": float(gap["ticks"]),
                    "eligible_ticks": float(gap["eligible_ticks"]),
                    "initiations": float(len(initiated_at)),
                    "cooldown_violations": float(gap["cooldown_violations"]),
                    "night_initiations": float(gap["night_initiations"]),
                    "capped": float(gap["capped"]),
                    "collision": float(collision),
                    "last_proactive_attempt_resets": float(gap["resets"]),
                    "useful_initiations": float(useful_count),
                    "irrelevant_initiations": float(irrelevant_count),
                    "annoyance_count": float(irrelevant_count),
                    "goal_resurfacing_reachable": 0.0,
                    "importance_sum": float(sum(gap["importance_values"])),
                    "importance_count": float(len(gap["importance_values"])),
                    "importance_min": min(gap["importance_values"])
                    if gap["importance_values"]
                    else 0.0,
                    "importance_max": max(gap["importance_values"])
                    if gap["importance_values"]
                    else 0.0,
                    "self_directed_initiations": float(
                        gap["self_directed_initiations"]
                    ),
                    "useful_to_user_initiations": float(
                        gap["useful_to_user_initiations"]
                    ),
                    "self_directed_category_count": float(
                        gap["self_directed_category_count"]
                    ),
                    "useful_to_user_category_count": float(
                        gap["useful_to_user_category_count"]
                    ),
                    "self_directed_useful": float(
                        gap["useful_by_category"]["self_directed"]
                    ),
                    "useful_to_user_useful": float(
                        gap["useful_by_category"]["useful_to_user"]
                    ),
                    "quiet_hour_initiations": float(gap["quiet_hour_initiations"]),
                    "ignored_thought_raises": float(gap["ignored_thought_raises"]),
                    "ignored_thought_count": float(gap["ignored_thought_count"]),
                }
                idle_hours_after_threshold = (
                    gap_hours - Config.PROACTIVE_IDLE_THRESHOLD_SECONDS / 3600
                )
                if idle_hours_after_threshold > 0:
                    gap_metrics["initiations_per_idle_hour_after_threshold"] = (
                        len(initiated_at) / idle_hours_after_threshold
                    )
                if gap["first_initiation_after_hours"] is not None:
                    gap_metrics["first_initiation_after_hours"] = float(
                        gap["first_initiation_after_hours"]
                    )
                if gap["min_seconds_between_initiations"] is not None:
                    gap_metrics["min_seconds_between_initiations"] = float(
                        gap["min_seconds_between_initiations"]
                    )
                all_outcomes.append(
                    SuiteOutcome(
                        probe_key=f"{previous.turn_id}->{turn.turn_id}",
                        persona_seed=seed,
                        suite="proactive",
                        categories=(sync, _gap_bucket(gap_hours)),
                        metrics=gap_metrics,
                        mode=service.mode,
                    )
                )
    finally:
        brain_state.publish_cb = original_publish_cb
    return all_outcomes


async def run_proactive_suite_for_seed(
    seed: int,
    archetype: str,
    horizon_label: str,
    service: BrainBenchService,
    *,
    sync: SyncArm,
    tick_order: TickOrder = "brain_first",
    run_dir: Path,
    max_ticks_per_gap: int = 7 * 24 * 60,
) -> list[SuiteOutcome]:
    """Build a lifesim and run one proactive sync arm in the service's mode."""
    simulation = build(seed, archetype, horizon_label)
    return await run_proactive_suite(
        simulation,
        service,
        sync=sync,
        tick_order=tick_order,
        run_dir=run_dir,
        max_ticks_per_gap=max_ticks_per_gap,
    )
