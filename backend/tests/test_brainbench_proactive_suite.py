"""Proactive gate, score and architecture-only lifesim coverage."""

from __future__ import annotations

import asyncio
import gc
import itertools
import math
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import clock
from app.cognitive.subconscious import SubconsciousEngine
from app.config import Config
from app.state.agent_state import StateService
from evals.brainbench.adapters import build_cognitive_service
from evals.brainbench.proactive_suite import (
    _active_goal_context,
    _apply_pending_broadcasts,
    _broadcast_lowered_marked_attempt,
    _useful_initiation,
    category_distribution,
    category_usefulness,
    cooldown_integrity,
    importance_distribution,
    initiation_timing,
    initiation_usefulness,
    re_raise_decay,
    run_proactive_suite,
)
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build


def test_disclosed_plan_context_uses_readable_details_and_usefulness_oracle():
    now = datetime(2025, 1, 1, 9)
    values = {
        ("plan:p001", "what"): "submit the grant application",
        ("plan:p001", "when"): "2025-01-01T15:00:00",
        ("plan:p001", "status"): "planned",
    }

    def value_at(entity, attribute, _at):
        value = values.get((entity, attribute))
        return SimpleNamespace(value=value) if value is not None else None

    sim = SimpleNamespace(timeline=SimpleNamespace(value_at=value_at))

    context = _active_goal_context(sim, {"plan:p001"}, now)

    assert context == [
        "plan:p001: submit the grant application; due_in_hours=6.0 (2025-01-01T15:00:00)"
    ]
    assert _active_goal_context(sim, {"plan:p001"}, now + timedelta(hours=3)) == [
        "plan:p001: submit the grant application; due_in_hours=3.0 (2025-01-01T15:00:00)"
    ]
    assert _useful_initiation(
        sim,
        {"plan:p001"},
        now,
        "Would a check-in about the grant submission help?",
    )
    assert not _useful_initiation(
        sim, {"plan:p001"}, now, "Is there anything else on your mind?"
    )


def _outcome(
    key: str,
    *,
    gap_hours: float = 4.0,
    initiations: int = 0,
    delay: float | None = None,
    night: int = 0,
    collision: int = 0,
    violations: int = 0,
    reset: int = 0,
    spacing: float | None = None,
    useful: int = 0,
    irrelevant: int = 0,
    idle_hour_rate: float | None = None,
) -> SuiteOutcome:
    metrics = {
        "gap_hours": gap_hours,
        "initiations": float(initiations),
        "night_initiations": float(night),
        "collision": float(collision),
        "cooldown_violations": float(violations),
        "last_proactive_attempt_resets": float(reset),
        "useful_initiations": float(useful),
        "irrelevant_initiations": float(irrelevant),
    }
    if delay is not None:
        metrics["first_initiation_after_hours"] = delay
    if spacing is not None:
        metrics["min_seconds_between_initiations"] = spacing
    if idle_hour_rate is not None:
        metrics["initiations_per_idle_hour_after_threshold"] = idle_hour_rate
    return SuiteOutcome(
        probe_key=key,
        persona_seed=1000,
        suite="proactive",
        categories=("none", "2h-12h"),
        metrics=metrics,
    )


def test_proactive_scoring_reports_rates_and_absent_denominators():
    outcomes = [
        _outcome(
            "a",
            initiations=2,
            delay=2.0,
            night=1,
            collision=1,
            violations=1,
            reset=1,
            spacing=1200,
            useful=1,
            irrelevant=1,
            idle_hour_rate=1.5,
        ),
        _outcome(
            "b", gap_hours=8, initiations=1, delay=3.0, useful=1, idle_hour_rate=2.0
        ),
    ]

    assert initiation_timing(outcomes) == {
        "initiations_per_simulated_day": pytest.approx(6.0),
        "median_first_initiation_delay_hours": pytest.approx(2.5),
        "median_initiations_per_idle_hour_after_threshold": pytest.approx(1.75),
        "night_fraction": pytest.approx(1 / 3),
        "collision_rate": 0.5,
        "initiations": 3,
    }
    assert cooldown_integrity(outcomes) == {
        "cooldown_violations": 1,
        "last_proactive_attempt_resets": 1,
        "min_seconds_between_initiations": 1200,
    }
    assert initiation_usefulness(outcomes) == {
        "useful_initiations": 2,
        "irrelevant_initiations": 1,
        "annoyance_count": 1,
        "useful_rate": pytest.approx(2 / 3),
    }
    empty = _outcome("empty")
    assert initiation_timing([empty])["initiations"] == 0
    assert initiation_timing([empty])["night_fraction"] == 0.0
    assert (
        initiation_timing([empty])["median_initiations_per_idle_hour_after_threshold"]
        is None
    )
    assert initiation_timing([])["collision_rate"] is None
    assert cooldown_integrity([empty])["min_seconds_between_initiations"] is None
    assert initiation_usefulness([empty])["useful_rate"] is None


def test_reset_instrumentation_requires_a_lowering_of_a_marked_value():
    """Synthetic values exercise reset detection without grading V2 behavior."""
    assert _broadcast_lowered_marked_attempt(200.0, 100.0, 200.0)
    assert not _broadcast_lowered_marked_attempt(100.0, 50.0, 200.0)
    assert not _broadcast_lowered_marked_attempt(200.0, 100.0, None)


@pytest.mark.asyncio
async def test_consumed_state_broadcasts_are_released_each_tick():
    """Pending history stays empty after each drain, regardless of tick count."""

    class State:
        def __init__(self):
            self.current_state = SimpleNamespace(last_proactive_attempt=10.0)
            self.applied = 0

        async def apply_external_state(self, snapshot):
            self.current_state.last_proactive_attempt = snapshot[
                "last_proactive_attempt"
            ]
            self.applied += 1

    broadcasts = deque()
    state = State()
    resets = 0
    broadcasts.extend({"last_proactive_attempt": value} for value in (9.0, 11.0, 12.0))
    resets += await _apply_pending_broadcasts(broadcasts, state, 10.0)
    assert len(broadcasts) == 0
    assert resets == 1

    for tick in range(10_000):
        broadcasts.append({"last_proactive_attempt": float(12 + tick)})
        resets += await _apply_pending_broadcasts(broadcasts, state, 10.0)
        assert len(broadcasts) == 0

    assert state.applied == 10_003
    assert state.current_state.last_proactive_attempt == 10_011.0
    assert resets == 1


def test_proactive_scoring_captures_importance_categories_and_ignore_decay():
    outcome = SuiteOutcome(
        probe_key="measured",
        persona_seed=1000,
        suite="proactive",
        categories=("broadcast", "2h-12h"),
        mode="llm_augmented",
        metrics={
            "importance_sum": 1.0,
            "importance_count": 2.0,
            "importance_min": 0.3,
            "importance_max": 0.7,
            "useful_to_user_category_count": 3.0,
            "self_directed_category_count": 1.0,
            "useful_to_user_useful": 2.0,
            "self_directed_useful": 0.0,
            "ignored_thought_raises": 6.0,
            "ignored_thought_count": 3.0,
        },
    )

    assert importance_distribution([outcome]) == {
        "mean": 0.5,
        "min": 0.3,
        "max": 0.7,
        "count": 2.0,
        "non_degenerate": 1.0,
    }
    assert category_distribution([outcome])["self_directed_share"] == 0.25
    assert category_usefulness([outcome]) == {
        "useful_to_user_useful_rate": pytest.approx(2 / 3),
        "self_directed_useful_rate": 0.0,
    }
    assert re_raise_decay([outcome])["raises_per_ignored_thought"] == 2.0


class _FakeCognitive:
    """Minimal cognitive stand-in: every turn persists, so the brain broadcasts."""

    def __init__(self, state: StateService):
        self.state = state
        self.pipeline = SimpleNamespace(_system2_task=None)
        self.last_reflection_task = None
        self.identity = SimpleNamespace(persona=state.persona)

    async def process_event(self, event):
        self.state.current_state.last_user_interaction = clock.time()
        await self.state.persist_state()
        yield {"type": "content", "data": event["content"]}


def _four_hour_replay(monkeypatch: pytest.MonkeyPatch):
    """Two turns four hours apart with minute ticks: 239 ticks in the gap."""
    start = datetime(2025, 1, 1)
    turns = [
        SimpleNamespace(turn_id="t1", t=start, text="hello"),
        SimpleNamespace(turn_id="t2", t=start + timedelta(hours=4), text="back"),
    ]
    annotations = [
        SimpleNamespace(turn_id="t1", tags=[], claims=[], event_ids=[]),
        SimpleNamespace(turn_id="t2", tags=[], claims=[], event_ids=[]),
    ]
    sim = SimpleNamespace(
        seed=1000,
        start=start,
        persona=SimpleNamespace(chronotype="morning"),
        events_by_id={},
        timeline=SimpleNamespace(value_at=lambda *_args: None),
    )
    monkeypatch.setattr(Config, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(Config, "PROACTIVE_IDLE_THRESHOLD_SECONDS", 7200)
    monkeypatch.setattr(Config, "PROACTIVE_COOLDOWN_SECONDS", 3600)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_ENERGY", 0.0)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.0)
    monkeypatch.setattr(Config, "SYSTEM_TICK_INTERVAL", 60)
    return (sim, turns, annotations, [], [])


def _live_broadcast_snapshots() -> int:
    """Count state.broadcast payloads still reachable anywhere in the process."""
    gc.collect()
    return sum(
        1
        for obj in gc.get_objects()
        if type(obj) is dict and "proactive_goals" in obj and "implied_goals" in obj
    )


@pytest.mark.parametrize("sync", ["broadcast", "none"])
def test_replay_holds_no_consumed_state_broadcasts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sync: str
):
    """Retained snapshots must not grow with simulated time.

    Every tick the brain publishes a full state snapshot, which carries the
    W9 goal history. The harness used to keep all of them for the whole
    cell, so a 6m cell passed 3 GB RSS. Nothing here holds a snapshot, so
    what is still alive late in the gap is what the harness retains.
    """
    simulation = _four_hour_replay(monkeypatch)
    live_at_tick: dict[int, int] = {}

    async def run():
        brain = StateService(
            db_path=str(tmp_path / "brain.db"), writer_id="brain_agent"
        )
        brain.current_state.energy = 1.0
        brain.current_state.dominance = 1.0
        original_tick = brain.handle_system_tick
        ticks = 0

        async def sample_tick(data):
            nonlocal ticks
            ticks += 1
            if ticks in (20, 200):
                live_at_tick[ticks] = _live_broadcast_snapshots()
            await original_tick(data)

        brain.handle_system_tick = sample_tick
        service = SimpleNamespace(
            mode="architecture_only", cognitive=_FakeCognitive(brain)
        )
        await run_proactive_suite(
            simulation,
            service,
            sync=sync,
            tick_order="brain_first",
            run_dir=tmp_path / sync,
        )

    asyncio.run(run())
    assert set(live_at_tick) == {20, 200}
    # A snapshot in flight is fine; 180 more ticks must not add retained ones.
    assert live_at_tick[200] <= live_at_tick[20] <= 3, live_at_tick


def test_short_replay_crosses_idle_and_cooldown_thresholds_and_restores_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The actual StateService gate first opens at 2h, then at 3h in the none arm."""

    simulation = _four_hour_replay(monkeypatch)

    observations: dict[str, dict[str, Any]] = {}
    active_arm: str | None = None
    original_apply = StateService.apply_external_state

    async def count_apply(self, data):
        if active_arm:
            observations[active_arm]["applied"].append(data)
        await original_apply(self, data)

    monkeypatch.setattr(StateService, "apply_external_state", count_apply)

    original_evaluate = SubconsciousEngine.evaluate_and_think

    async def check_broadcasts_before_proactive_branch(self, snapshot, eligible):
        observations[active_arm]["activity_hours"] = snapshot.get(
            "user_interaction_hours", []
        )
        if active_arm == "broadcast":
            tick_timestamp = observations["broadcast"]["tick_timestamps"][-1]
            assert any(
                snapshot["timestamp"] == tick_timestamp
                for snapshot in observations["broadcast"]["emitted"]
            )
            assert any(
                snapshot["timestamp"] == tick_timestamp
                for snapshot in observations["broadcast"]["applied"]
            )
            assert (
                observations["broadcast"]["applied"]
                == observations["broadcast"]["emitted"]
            )
        return await original_evaluate(self, snapshot, eligible)

    monkeypatch.setattr(
        SubconsciousEngine,
        "evaluate_and_think",
        check_broadcasts_before_proactive_branch,
    )

    async def run_arm(arm: str):
        nonlocal active_arm
        active_arm = arm
        observations[arm] = {
            "ticks": 0,
            "tick_timestamps": [],
            "emitted": [],
            "applied": [],
        }
        brain = StateService(
            db_path=str(tmp_path / f"brain-{arm}.db"), writer_id="brain_agent"
        )
        original_tick = brain.handle_system_tick

        async def count_tick(data):
            observations[arm]["ticks"] += 1
            observations[arm]["tick_timestamps"].append(data["timestamp"])
            await original_tick(data)

        brain.handle_system_tick = count_tick

        async def count_publish(subject, data):
            if subject == "state.broadcast":
                observations[arm]["emitted"].append(data)

        brain.publish_cb = count_publish
        brain.current_state.energy = 1.0
        brain.current_state.dominance = 1.0
        original_callback = brain.publish_cb
        service = SimpleNamespace(
            mode="architecture_only",
            cognitive=_FakeCognitive(brain),
        )
        outcomes = await run_proactive_suite(
            simulation,
            service,
            sync=arm,
            tick_order="brain_first",
            run_dir=tmp_path / arm,
        )
        assert brain.publish_cb is original_callback
        return outcomes

    broadcast = asyncio.run(run_arm("broadcast"))
    none = asyncio.run(run_arm("none"))
    assert observations["broadcast"]["activity_hours"]
    assert observations["none"]["activity_hours"]
    assert observations["broadcast"]["ticks"] == 239
    assert observations["broadcast"]["applied"] == observations["broadcast"]["emitted"]
    assert observations["none"]["ticks"] == 239
    assert not observations["none"]["applied"]
    for outcomes in (broadcast, none):
        metrics = outcomes[0].metrics
        assert metrics["ticks"] == 239
        assert math.isfinite(metrics["initiations"])
        assert metrics["initiations_per_idle_hour_after_threshold"] >= 0
    assert broadcast[0].categories == ("broadcast", "2h-12h")


def test_reset_instrumentation_tracks_a_mark_across_an_empty_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stale raw broadcast no longer resets the merged attempt watermark."""
    start = datetime(2025, 1, 1)
    turns = [
        SimpleNamespace(turn_id="t1", t=start, text="hello"),
        SimpleNamespace(turn_id="t2", t=start + timedelta(hours=4), text="back"),
        SimpleNamespace(
            turn_id="t3", t=start + timedelta(hours=4, minutes=30), text="again"
        ),
    ]
    annotations = [
        SimpleNamespace(turn_id=turn.turn_id, tags=[], claims=[], event_ids=[])
        for turn in turns
    ]
    sim = SimpleNamespace(
        seed=1000,
        start=start,
        persona=SimpleNamespace(chronotype="morning"),
        events_by_id={},
        timeline=SimpleNamespace(value_at=lambda *_args: None),
    )
    monkeypatch.setattr(Config, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(Config, "PROACTIVE_IDLE_THRESHOLD_SECONDS", 7200)
    monkeypatch.setattr(Config, "PROACTIVE_COOLDOWN_SECONDS", 3600)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_ENERGY", 0.0)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.0)
    monkeypatch.setattr(Config, "SYSTEM_TICK_INTERVAL", 60)

    class StagedBroadcastCognitive:
        def __init__(self, state: StateService):
            self.state = state
            self.pipeline = SimpleNamespace(_system2_task=None)
            self.last_reflection_task = None
            self.identity = SimpleNamespace(persona=state.persona)

        async def process_event(self, event):
            self.state.current_state.last_user_interaction = clock.time()
            # Hold the marked value through t2, then inject a lower snapshot at
            # t3. This tests the metric state machine, not V2's behavior.
            self.state.current_state.last_proactive_attempt = (
                turns[1].t.timestamp() if event["id"] == "t2" else 0.0
            )
            await self.state.persist_state()
            yield {"type": "content", "data": event["content"]}

    async def replay():
        brain = StateService(
            db_path=str(tmp_path / "staged-brain.db"), writer_id="brain_agent"
        )
        brain.current_state.energy = 1.0
        brain.current_state.dominance = 1.0
        original_callback = brain.publish_cb
        service = SimpleNamespace(
            mode="architecture_only",
            cognitive=StagedBroadcastCognitive(brain),
        )
        outcomes = await run_proactive_suite(
            (sim, turns, annotations, [], []),
            service,
            sync="broadcast",
            run_dir=tmp_path / "staged-subconscious",
        )
        assert brain.publish_cb is original_callback
        return outcomes

    outcomes = asyncio.run(replay())
    assert outcomes[0].metrics["initiations"] >= 0
    assert outcomes[1].metrics["initiations"] >= 0
    assert outcomes[1].metrics["last_proactive_attempt_resets"] == 0


@pytest.mark.asyncio
async def test_real_architecture_only_lifesim_proactive_replay_both_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """One real V2 replay per arm; behavior values are reported, not graded."""
    simulation = build(1001, "steady_professional", "1w")
    _sim, turns, annotations, _probes, answers = simulation
    assert len(turns) == len(annotations) == 24
    assert any(answer.category == "commitment_due" for answer in answers)
    assert (
        sum(
            (right.t - left.t).total_seconds() > 2 * 3600
            for left, right in itertools.pairwise(turns)
        )
        >= 3
    )
    runs: dict[tuple[str, str], list[SuiteOutcome]] = {}
    for arm, tick_order in itertools.product(
        ("broadcast", "none"), ("brain_first", "subconscious_first")
    ):
        service = build_cognitive_service(
            "architecture_only", tmp_path / f"brain-{arm}-{tick_order}"
        )

        async def local_embedding(_text: str) -> list[float]:
            # Memory embeddings are orthogonal to proactive timing; keep the
            # integration replay local and avoid a hosted embedding endpoint.
            return [1.0] + [0.0] * 767

        monkeypatch.setattr(service.memory_store, "get_embedding", local_embedding)
        try:
            runs[(arm, tick_order)] = await run_proactive_suite(
                simulation,
                service,
                sync=arm,
                tick_order=tick_order,
                run_dir=tmp_path / f"{arm}-{tick_order}-state",
            )
        finally:
            service.cognitive.close()
    for (arm, tick_order), outcomes in runs.items():
        assert len(outcomes) == len(turns) - 1
        assert all(outcome.suite == "proactive" for outcome in outcomes)
        assert all(outcome.persona_seed == 1001 for outcome in outcomes)
        assert all(outcome.mode == "architecture_only" for outcome in outcomes)
        assert all(outcome.categories[0] == arm for outcome in outcomes)
        assert all(
            math.isfinite(value)
            for row in outcomes
            for value in row.metrics.values()
            if value is not None
        )
        capped_gaps = sum(int(row.metrics["capped"]) for row in outcomes)
        assert capped_gaps == 0
        print(
            arm,
            {
                "combination": (arm, tick_order),
                "turns": len(turns),
                "ticks": sum(int(row.metrics["ticks"]) for row in outcomes),
                "capped_gaps": capped_gaps,
                "gaps_over_2h": sum(row.metrics["gap_hours"] > 2 for row in outcomes),
                "timing": initiation_timing(outcomes),
                "cooldown": cooldown_integrity(outcomes),
                "usefulness": initiation_usefulness(outcomes),
            },
        )
