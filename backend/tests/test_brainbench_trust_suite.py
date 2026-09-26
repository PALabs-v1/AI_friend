"""Fast trust scoring/replay tests and a real local architecture-only replay."""

from __future__ import annotations

import asyncio
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.brainbench.adapters import build_cognitive_service
from evals.brainbench.stats import SuiteOutcome
from evals.brainbench.trust_suite import (
    background_drift,
    competence_warmth_separation,
    hostility_response,
    run_trust_suite,
    run_trust_suite_for_seed,
)
from evals.lifesim.generate import build


def _outcome(
    turn_id: str,
    *,
    trust_delta: float = 0.0,
    benevolence_delta: float = 0.0,
    competence_delta: float = 0.0,
    competence_evidence: float = 0.0,
    toward_robot_valence: float | None = None,
    categories: tuple[str, ...] = ("not_robot_directed",),
    trust_after: float = 0.5,
    benevolence_before: float = 0.5,
    competence_before: float = 0.5,
    integrity_before: float = 0.5,
    integrity_delta: float = 0.0,
) -> SuiteOutcome:
    metrics = {
        "benevolence_before": benevolence_before,
        "competence_before": competence_before,
        "integrity_before": integrity_before,
        "benevolence_after": max(0.0, min(1.0, benevolence_before + benevolence_delta)),
        "competence_after": max(0.0, min(1.0, competence_before + competence_delta)),
        "integrity_after": max(0.0, min(1.0, integrity_before + integrity_delta)),
        "trust_delta": trust_delta,
        "benevolence_delta": benevolence_delta,
        "competence_delta": competence_delta,
        "integrity_delta": integrity_delta,
        "ceiling_masked": float(
            any(
                (before >= 1.0 or before <= 0.0) and delta == 0.0
                for before, delta in (
                    (benevolence_before, benevolence_delta),
                    (competence_before, competence_delta),
                    (integrity_before, integrity_delta),
                )
            )
        ),
        "trust_after": trust_after,
        "competence_evidence": competence_evidence,
    }
    if toward_robot_valence is not None:
        metrics["toward_robot_valence"] = toward_robot_valence
    return SuiteOutcome(
        probe_key=turn_id,
        persona_seed=1000,
        suite="trust",
        categories=categories,
        metrics=metrics,
        mode="architecture_only",
    )


def test_hostility_response_reports_trust_rise_rate_and_mean():
    outcomes = [
        _outcome("hostile-rise", trust_delta=0.2, toward_robot_valence=-0.4),
        _outcome("hostile-fall", trust_delta=-0.1, toward_robot_valence=-0.1),
        _outcome("warm", trust_delta=0.9, toward_robot_valence=0.5),
    ]

    assert hostility_response(outcomes) == {
        "n_hostile": 2,
        "n_masked": 0,
        "mean_trust_delta": pytest.approx(0.05),
        "hostile_trust_rise_rate": 0.5,
    }


def test_hostility_response_rejects_empty_hostile_group():
    with pytest.raises(ValueError, match="hostile"):
        hostility_response([_outcome("warm", toward_robot_valence=0.2)])


def test_missing_robot_valence_is_not_counted_as_hostile_or_warm():
    outcomes = [
        _outcome("no-oracle", competence_delta=99.0, competence_evidence=0),
        _outcome("hostile", toward_robot_valence=-0.2, competence_evidence=0),
        _outcome("warm", toward_robot_valence=0.2, competence_evidence=0),
    ]

    assert hostility_response(outcomes)["n_hostile"] == 1
    result = competence_warmth_separation(outcomes)
    assert result["n_warmth_hostile"] == 1
    assert result["n_warmth_positive"] == 1
    assert result["n_warmth_only"] == 2
    assert result["competence_leak"] == pytest.approx(0.0)


def test_competence_warmth_separation_reports_expected_directions():
    outcomes = [
        _outcome("complaint-a", competence_delta=-0.2, competence_evidence=-1),
        _outcome("complaint-b", competence_delta=-0.1, competence_evidence=-1),
        _outcome("thanks-a", competence_delta=0.2, competence_evidence=1),
        _outcome("thanks-b", competence_delta=0.1, competence_evidence=1),
        _outcome(
            "argument-a",
            benevolence_delta=-0.2,
            competence_delta=0.04,
            competence_evidence=0,
            toward_robot_valence=-0.7,
        ),
        _outcome(
            "argument-b",
            benevolence_delta=-0.1,
            competence_delta=0.02,
            competence_evidence=0,
            toward_robot_valence=-0.3,
        ),
        _outcome(
            "affection-a",
            benevolence_delta=0.2,
            competence_delta=0.06,
            competence_evidence=0,
            toward_robot_valence=0.6,
        ),
        _outcome(
            "affection-b",
            benevolence_delta=0.1,
            competence_delta=0.08,
            competence_evidence=0,
            toward_robot_valence=0.4,
        ),
    ]

    result = competence_warmth_separation(outcomes)

    assert result == {
        "competence_signal": 1.0,
        "n_competence_complaints": 2,
        "n_masked_competence_complaints": 0,
        "n_competence_thanks": 2,
        "n_masked_competence_thanks": 0,
        "warmth_signal": 1.0,
        "n_warmth_hostile": 2,
        "n_masked_warmth_hostile": 0,
        "n_warmth_positive": 2,
        "n_masked_warmth_positive": 0,
        "competence_leak": pytest.approx(0.05),
        "n_warmth_only": 4,
        "n_masked_warmth_only": 0,
    }


def test_competence_warmth_separation_returns_none_for_each_missing_group():
    result = competence_warmth_separation(
        [
            _outcome("thanks", competence_evidence=1),
            _outcome("hostile", competence_evidence=0, toward_robot_valence=-0.2),
        ]
    )

    assert result["competence_signal"] is None
    assert result["warmth_signal"] is None
    assert result["competence_leak"] == 0.0
    assert result["n_competence_complaints"] == 0
    assert result["n_masked_competence_complaints"] == 0
    assert result["n_competence_thanks"] == 1
    assert result["n_masked_competence_thanks"] == 0
    assert result["n_warmth_hostile"] == 1
    assert result["n_masked_warmth_hostile"] == 0
    assert result["n_warmth_positive"] == 0
    assert result["n_masked_warmth_positive"] == 0
    assert result["n_warmth_only"] == 1
    assert result["n_masked_warmth_only"] == 0


def test_competence_warmth_separation_excludes_masked_group_members():
    result = competence_warmth_separation(
        [
            _outcome(
                "masked-complaint",
                competence_evidence=-1,
                competence_before=1.0,
            ),
            _outcome("thanks-a", competence_evidence=1, competence_delta=0.1),
            _outcome("thanks-b", competence_evidence=1, competence_delta=0.2),
        ]
    )

    assert result["competence_signal"] is None
    assert result["n_competence_complaints"] == 1
    assert result["n_masked_competence_complaints"] == 1
    assert result["n_competence_thanks"] == 2
    assert result["n_masked_competence_thanks"] == 0


def test_component_metrics_mask_only_on_the_component_they_read():
    """V2 pins integrity around turn 6 but competence keeps moving until ~13.
    A competence reading on a turn where only integrity is pinned is still
    informative; masking it anyway (as an aggregate-trust mask would) threw
    away seed 1062's turn-8 robot_affection, whose competence delta was a
    real +0.04 leak from a warmth-only signal."""
    integrity_pinned_warmth_turn = _outcome(
        "affection",
        toward_robot_valence=0.44,
        competence_evidence=0,
        competence_delta=0.04,
        integrity_before=1.0,
        integrity_delta=0.0,
    )
    assert integrity_pinned_warmth_turn.metrics["ceiling_masked"] == 1.0

    result = competence_warmth_separation([integrity_pinned_warmth_turn])

    assert result["n_masked_warmth_only"] == 0
    assert result["competence_leak"] == pytest.approx(0.04)
    # The aggregate view still treats the same turn as masked.
    assert background_drift([integrity_pinned_warmth_turn])["saturated_fraction"] == 1.0


def test_background_drift_reports_subset_and_whole_run_endpoints():
    outcomes = [
        _outcome("background-a", trust_delta=0.1, trust_after=0.6),
        _outcome(
            "robot",
            trust_delta=-0.3,
            trust_after=0.3,
            categories=("robot_argument",),
            toward_robot_valence=-0.7,
        ),
        _outcome("background-b", trust_delta=-0.02, trust_after=0.28),
    ]

    assert background_drift(outcomes) == {
        "n": 2,
        "n_masked": 0,
        "mean_trust_delta": pytest.approx(0.04),
        "positive_rate": 0.5,
        "total_trust_delta": pytest.approx(0.08),
        "trust_start": pytest.approx(0.5),
        "trust_end": pytest.approx(0.28),
        "turns_to_ceiling": {
            "benevolence": None,
            "competence": None,
            "integrity": None,
        },
        "saturated_fraction": 0.0,
    }


def test_hostility_response_excludes_masked_turn_and_counts_unmasked_fall():
    outcomes = [
        _outcome(
            "pinned-hostile",
            toward_robot_valence=-0.5,
            benevolence_before=1.0,
            benevolence_delta=0.0,
            trust_delta=0.0,
        ),
        _outcome(
            "real-hostile-fall",
            toward_robot_valence=-0.6,
            benevolence_before=0.8,
            benevolence_delta=-0.2,
            trust_delta=-0.2 / 3,
        ),
    ]

    assert hostility_response(outcomes) == {
        "n_hostile": 2,
        "n_masked": 1,
        "mean_trust_delta": pytest.approx(-0.2 / 3),
        "hostile_trust_rise_rate": 0.0,
    }


def test_all_masked_hostile_group_reports_none_scores_without_raising():
    result = hostility_response(
        [
            _outcome(
                "pinned-hostile",
                toward_robot_valence=-0.5,
                integrity_before=1.0,
            )
        ]
    )

    assert result == {
        "n_hostile": 1,
        "n_masked": 1,
        "mean_trust_delta": None,
        "hostile_trust_rise_rate": None,
    }


def test_background_drift_masks_floor_and_reports_ceiling_turns():
    outcomes = [
        _outcome(
            "floor-pinned",
            benevolence_before=0.0,
            competence_before=0.4,
            competence_delta=0.2,
            integrity_before=0.6,
            integrity_delta=0.4,
            trust_delta=0.2,
            trust_after=0.7,
        ),
        _outcome(
            "ceiling-arrives",
            benevolence_before=0.2,
            competence_before=0.6,
            competence_delta=0.4,
            integrity_before=1.0,
            integrity_delta=-0.1,
            trust_delta=0.1,
            trust_after=0.8,
        ),
        _outcome(
            "ceiling-pinned",
            benevolence_before=0.2,
            competence_before=1.0,
            integrity_before=0.9,
            trust_delta=0.0,
            trust_after=0.8,
        ),
    ]

    result = background_drift(outcomes)

    assert result["turns_to_ceiling"] == {
        "benevolence": None,
        "competence": 2,
        "integrity": 1,
    }
    assert result["n"] == 3
    assert result["n_masked"] == 2
    assert result["saturated_fraction"] == pytest.approx(2 / 3)
    assert result["mean_trust_delta"] == pytest.approx(0.1)


def test_background_drift_returns_none_scores_when_all_background_is_masked():
    result = background_drift(
        [
            _outcome(
                "all-pinned",
                benevolence_before=1.0,
                trust_delta=0.0,
                trust_after=0.5,
            )
        ]
    )

    assert result["n"] == 1
    assert result["n_masked"] == 1
    assert result["mean_trust_delta"] is None
    assert result["positive_rate"] is None
    assert result["total_trust_delta"] is None


def test_floor_masking_is_symmetric_for_hostility_response():
    result = hostility_response(
        [
            _outcome(
                "floor-pinned-hostile",
                toward_robot_valence=-0.5,
                benevolence_before=0.0,
                benevolence_delta=0.0,
                trust_delta=0.0,
            )
        ]
    )

    assert result["n_hostile"] == 1
    assert result["n_masked"] == 1
    assert result["mean_trust_delta"] is None
    assert result["hostile_trust_rise_rate"] is None


def test_background_drift_rejects_empty_run():
    with pytest.raises(ValueError, match="at least one"):
        background_drift([])


def _fake_replay(process_event, *, turn_count: int = 1):
    now = datetime(2025, 1, 1, tzinfo=UTC)
    turns = [
        SimpleNamespace(
            turn_id=f"turn-{index}",
            t=now + timedelta(minutes=index),
            text=f"Test message {index}",
        )
        for index in range(turn_count)
    ]
    annotations = [
        SimpleNamespace(
            turn_id=turn.turn_id,
            event_ids=[],
            competence_evidence=0,
            toward_robot_valence=None,
        )
        for turn in turns
    ]

    class TrustState:
        trust_benevolence = 0.5
        trust_competence = 0.5
        trust_integrity = 0.5

        @property
        def trust(self):
            return (
                self.trust_benevolence + self.trust_competence + self.trust_integrity
            ) / 3

    state = SimpleNamespace(current_state=TrustState())
    pipeline = SimpleNamespace(_system2_task=None)
    cognitive = SimpleNamespace(
        state=state,
        pipeline=pipeline,
        last_reflection_task=None,
        process_event=process_event,
    )
    service = SimpleNamespace(mode="architecture_only", cognitive=cognitive)
    simulation_object = SimpleNamespace(seed=1000, start=now, events_by_id={})
    return (simulation_object, turns, annotations, [], []), service, state, pipeline


@pytest.mark.asyncio
async def test_run_trust_suite_records_snapshot_deltas_and_oracle_categories():
    async def process_event(event):
        state.current_state.trust_benevolence += 0.03
        state.current_state.trust_competence -= 0.02
        yield {"type": "appraisal"}

    simulation, service, state, _pipeline = _fake_replay(process_event)
    simulation[0].events_by_id["event-1"] = SimpleNamespace(
        kind="robot_thanks", toward_robot=True
    )
    simulation[2][0].event_ids = ["event-1"]
    simulation[2][0].competence_evidence = 1
    simulation[2][0].toward_robot_valence = 0.4

    outcomes = await run_trust_suite(simulation, service, persona_seed=1234)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.probe_key == "turn-0"
    assert outcome.persona_seed == 1234
    assert outcome.suite == "trust"
    assert outcome.mode == "architecture_only"
    assert outcome.categories == ("robot_thanks",)
    assert outcome.metrics == {
        "benevolence_before": 0.5,
        "competence_before": 0.5,
        "integrity_before": 0.5,
        "benevolence_after": 0.53,
        "competence_after": 0.48,
        "integrity_after": 0.5,
        "trust_delta": pytest.approx(0.01 / 3),
        "benevolence_delta": pytest.approx(0.03),
        "competence_delta": pytest.approx(-0.02),
        "integrity_delta": 0.0,
        "ceiling_masked": 0.0,
        "trust_after": pytest.approx(0.5033333333333333),
        "competence_evidence": 1.0,
        "toward_robot_valence": 0.4,
    }


@pytest.mark.asyncio
async def test_background_write_is_captured_in_its_turn_delta():
    async def process_event(event):
        async def delayed_write():
            await asyncio.sleep(0)
            state.current_state.trust_benevolence += 0.12

        if event["id"] == "turn-0":
            pipeline._system2_task = asyncio.create_task(delayed_write())
        yield {"type": "appraisal"}

    simulation, service, state, pipeline = _fake_replay(process_event, turn_count=2)
    outcomes = await run_trust_suite(simulation, service)

    assert outcomes[0].metrics["benevolence_delta"] == pytest.approx(0.12)
    assert outcomes[1].metrics["benevolence_delta"] == 0.0


@pytest.mark.asyncio
async def test_background_timeout_warns_and_continues():
    async def process_event(_event):
        async def too_slow():
            await asyncio.Event().wait()

        pipeline._system2_task = asyncio.create_task(too_slow())
        yield {"type": "appraisal"}

    simulation, service, _state, pipeline = _fake_replay(process_event)
    outcomes = await run_trust_suite(simulation, service, background_timeout=0.01)

    assert len(outcomes) == 1
    assert pipeline._system2_task.cancelled()


@pytest.mark.asyncio
async def test_run_trust_suite_propagates_caller_cancellation():
    started = asyncio.Event()

    async def process_event(_event):
        async def pending():
            started.set()
            await asyncio.Event().wait()

        pipeline._system2_task = asyncio.create_task(pending())
        yield {"type": "appraisal"}

    simulation, service, _state, pipeline = _fake_replay(process_event)
    replay = asyncio.create_task(run_trust_suite(simulation, service))
    await started.wait()
    replay.cancel()

    with pytest.raises(asyncio.CancelledError):
        await replay


@pytest.mark.asyncio
async def test_run_trust_suite_rejects_invalid_mode_alignment_and_errors():
    async def process_event(_event):
        yield {"type": "ok"}

    simulation, service, _state, _pipeline = _fake_replay(process_event)
    service.mode = "llm_augmented"
    with pytest.raises(ValueError, match="architecture_only"):
        await run_trust_suite(simulation, service)
    service.mode = "architecture_only"

    with pytest.raises(ValueError, match="counts differ"):
        await run_trust_suite((*simulation[:2], [], *simulation[3:]), service)
    with pytest.raises(ValueError, match="aligned"):
        simulation[2][0].turn_id = "misaligned"
        await run_trust_suite(simulation, service)
    simulation[2][0].turn_id = "turn-0"

    async def error_event(_event):
        yield {"type": "error", "message": "broken"}

    service.cognitive.process_event = error_event
    with pytest.raises(RuntimeError, match="turn-0"):
        await run_trust_suite(simulation, service)


@pytest.mark.asyncio
async def test_run_trust_suite_for_seed_uses_shared_builder(monkeypatch):
    import evals.brainbench.trust_suite as trust_module

    sentinel = (SimpleNamespace(seed=1000, start=datetime.now(UTC)), [], [], [], [])
    captured = {}

    def fake_build(seed, archetype, horizon_label):
        captured["args"] = seed, archetype, horizon_label
        return sentinel

    async def fake_run(simulation, service, **kwargs):
        captured["run"] = simulation, service, kwargs
        return []

    monkeypatch.setattr(trust_module, "build", fake_build)
    monkeypatch.setattr(trust_module, "run_trust_suite", fake_run)
    service = SimpleNamespace()

    await run_trust_suite_for_seed(
        1003, "chatty_student", "1w", service, progress_every=7, background_timeout=2
    )

    assert captured["args"] == (1003, "chatty_student", "1w")
    assert captured["run"] == (
        sentinel,
        service,
        {"persona_seed": 1003, "progress_every": 7, "background_timeout": 2},
    )


@pytest.mark.asyncio
async def test_real_architecture_only_trust_replay(tmp_path: Path, monkeypatch):
    # Dev split only: seed 1015, private_minimalist, 1m. Deterministic build
    # counts: 1 robot_thanks, 1 robot_complaint, and 1 robot_argument.
    simulation = build(1015, "private_minimalist", "1m")
    sim, turns, annotations, _probes, _answers = simulation
    service = build_cognitive_service("architecture_only", tmp_path)

    async def local_embedding(_text: str) -> list[float]:
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(service.memory_store, "get_embedding", local_embedding)
    try:
        outcomes = await run_trust_suite_for_seed(
            sim.seed,
            sim.archetype,
            sim.horizon.label,
            service,
            progress_every=25,
        )

        assert len(outcomes) == len(turns) == len(annotations)
        assert all(outcome.mode == "architecture_only" for outcome in outcomes)
        assert all(outcome.suite == "trust" for outcome in outcomes)
        assert all(
            math.isfinite(value)
            for outcome in outcomes
            for value in outcome.metrics.values()
        )
        robot_kinds = [
            sim.events_by_id[event_id].kind
            for annotation in annotations
            for event_id in annotation.event_ids
            if event_id in sim.events_by_id and sim.events_by_id[event_id].toward_robot
        ]
        counts = Counter(robot_kinds)
        assert counts["robot_thanks"] > 0
        assert counts["robot_complaint"] > 0
        assert counts["robot_argument"] > 0

        hostility = hostility_response(outcomes)
        separation = competence_warmth_separation(outcomes)
        drift = background_drift(outcomes)
        print(
            "BrainBench trust real-run scores:",
            {
                "hostility_response": hostility,
                "competence_warmth_separation": separation,
                "background_drift": drift,
            },
        )
    finally:
        service.cognitive.close()
        await service.memory_store.close()


@pytest.mark.asyncio
async def test_real_architecture_only_trust_replay_observes_hostility_before_saturation(
    tmp_path: Path, monkeypatch
):
    # The dev-split 1000-1099 search found seed 1062's first hostile robot turn
    # at turn 1. Its real replay must keep that turn unmasked, before V2's
    # background integrity updates saturate the component.
    simulation = build(1062, "private_minimalist", "1m")
    sim, turns, annotations, *_ = simulation
    service = build_cognitive_service("architecture_only", tmp_path)

    async def local_embedding(_text: str) -> list[float]:
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(service.memory_store, "get_embedding", local_embedding)
    try:
        outcomes = await run_trust_suite_for_seed(
            sim.seed,
            sim.archetype,
            sim.horizon.label,
            service,
            progress_every=25,
        )

        hostile_indices = [
            index
            for index, annotation in enumerate(annotations)
            if annotation.toward_robot_valence is not None
            and annotation.toward_robot_valence < 0
        ]
        assert hostile_indices
        first_hostile = hostile_indices[0]
        assert first_hostile == 0
        hostile_outcome = outcomes[first_hostile]
        assert hostile_outcome.metrics["toward_robot_valence"] < 0
        assert hostile_outcome.metrics["ceiling_masked"] == 0.0
        assert hostile_outcome.metrics["integrity_before"] < 1.0
        assert len(outcomes) == len(turns)
    finally:
        service.cognitive.close()
        await service.memory_store.close()
