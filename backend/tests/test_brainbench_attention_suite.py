"""Fast attention scoring tests plus one real architecture-only replay."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.brainbench.adapters import build_cognitive_service
from evals.brainbench.attention_suite import (
    interference_collision_rate,
    repetition_novelty_delta,
    run_attention_suite_for_seed,
)
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build


def _outcome(turn_id: str, novelty: float, *categories: str) -> SuiteOutcome:
    return SuiteOutcome(
        probe_key=turn_id,
        persona_seed=1000,
        suite="attention",
        categories=categories,
        metrics={"novelty": novelty},
    )


def test_repetition_novelty_delta_reports_lower_repeated_novelty():
    outcomes = [
        _outcome("repeat-1", 0.1, "repeated"),
        _outcome("repeat-2", 0.2, "repeated", "changing_fact"),
        _outcome("fresh-1", 0.8, "changing_fact"),
        _outcome("fresh-2", 0.9, "trivial_episodic"),
    ]

    result = repetition_novelty_delta(outcomes)

    assert result["repeated_mean"] == pytest.approx(0.15)
    assert result["fresh_mean"] == pytest.approx(0.85)
    # stats.cliffs_delta(a, b) is positive when b tends to exceed a.
    assert result["cliffs_delta"] == 1.0


def test_repetition_novelty_delta_requires_both_groups():
    with pytest.raises(ValueError, match="repeated and fresh"):
        repetition_novelty_delta([_outcome("only-repeat", 0.1, "repeated")])


def test_interference_collision_rate_counts_only_low_novelty_pairs():
    sim = SimpleNamespace(
        events_by_id={
            "restaurant-a": SimpleNamespace(kind="restaurant_visit"),
            "restaurant-b": SimpleNamespace(kind="restaurant_visit"),
            "restaurant-c": SimpleNamespace(kind="restaurant_visit"),
        }
    )
    turns = [SimpleNamespace(turn_id=f"turn-{i}") for i in range(3)]
    annotations = [
        SimpleNamespace(event_ids=[event_id])
        for event_id in ("restaurant-a", "restaurant-b", "restaurant-c")
    ]
    outcomes = [
        _outcome("turn-0", 0.8),
        _outcome("turn-1", 0.2),
        _outcome("turn-2", 0.8),
    ]

    result = interference_collision_rate(
        sim, outcomes, turns, annotations, window=2, threshold=0.3
    )

    assert result == {"pairs_checked": 3, "collisions": 1, "collision_rate": 1 / 3}


def test_interference_collision_rate_returns_zero_when_no_distinct_same_kind_pair():
    sim = SimpleNamespace(
        events_by_id={
            "restaurant-a": SimpleNamespace(kind="restaurant_visit"),
            "meeting-a": SimpleNamespace(kind="meeting"),
        }
    )
    turns = [SimpleNamespace(turn_id="turn-0"), SimpleNamespace(turn_id="turn-1")]
    annotations = [
        SimpleNamespace(event_ids=["restaurant-a"]),
        SimpleNamespace(event_ids=["restaurant-a", "meeting-a"]),
    ]

    assert interference_collision_rate(
        sim, [_outcome("turn-1", 0.1)], turns, annotations
    ) == {"pairs_checked": 0, "collisions": 0, "collision_rate": 0.0}


@pytest.mark.asyncio
async def test_real_architecture_only_lifesim_attention_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    simulation = build(1000, "chatty_student", "1w")
    sim, turns, annotations, _probes, _answers = simulation
    service = build_cognitive_service("architecture_only", tmp_path)

    async def local_embedding(_text: str) -> list[float]:
        # Keep downstream explicit memory writes local; novelty itself is the
        # real production appraisal, and embedding availability is unrelated.
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(service.memory_store, "get_embedding", local_embedding)
    try:
        outcomes = await run_attention_suite_for_seed(
            1000,
            "chatty_student",
            "1w",
            service,
            progress_every=25,
        )

        assert len(outcomes) == len(turns)
        assert len(turns) == len(annotations)
        assert all(outcome.mode == "architecture_only" for outcome in outcomes)
        assert all(outcome.suite == "attention" for outcome in outcomes)
        assert all(0.0 <= outcome.metrics["novelty"] <= 1.0 for outcome in outcomes)
        assert all(
            outcome.metrics["importance"] == annotation.importance
            for outcome, annotation in zip(outcomes, annotations, strict=True)
        )

        # Exercise both oracle comparisons on the real simulation. Their
        # values are reported as research findings, not assumed directions.
        repetition = None
        if any("repeated" in annotation.tags for annotation in annotations) and any(
            "repeated" not in annotation.tags for annotation in annotations
        ):
            repetition = repetition_novelty_delta(outcomes)
            assert 0.0 <= repetition["repeated_mean"] <= 1.0
            assert 0.0 <= repetition["fresh_mean"] <= 1.0
        collision = interference_collision_rate(sim, outcomes, turns, annotations)
        assert 0.0 <= collision["collision_rate"] <= 1.0
    finally:
        service.cognitive.close()
        await service.memory_store.close()
