"""Instrument tests plus the prescribed local-LLM metacognition replay."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.agents.surfacing_agent import SurfacingAgent
from app.config import Config
from evals.brainbench import metacognition_suite
from evals.brainbench.adapters import build_cognitive_service
from evals.brainbench.metacognition_suite import (
    INJECTED_OUTAGE_MESSAGE,
    _auroc,
    _injected_search_failure,
    contradiction_recording,
    outage_propagation,
    retrieval_freshness,
    run_metacognition_suite,
    uncertainty_calibration,
)
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build


def _outcome(key: str, **metrics: float) -> SuiteOutcome:
    return SuiteOutcome(
        probe_key=key,
        persona_seed=1000,
        suite="metacognition",
        categories=("current",),
        metrics=metrics,
        mode="llm_augmented",
    )


def test_auroc_perfect_inverse_ties_and_missing_class():
    assert _auroc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert _auroc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0
    assert _auroc([0.5, 0.5, 0.5, 0.5], [1, 1, 0, 0]) == 0.5
    assert _auroc([0.5, 0.4], [1, 1]) is None


def test_reliability_table_uses_five_observed_score_bins():
    outcomes = [
        _outcome(
            f"p{index}",
            top_score=score,
            answerable=1.0,
            correct_at_1=float(index % 2),
        )
        for index, score in enumerate((0.0, 0.25, 0.5, 0.75, 1.0))
    ]
    scored = uncertainty_calibration(outcomes)
    assert scored["reliability"] == [
        {
            "bin": 0,
            "n": 1,
            "low": 0.0,
            "high": 0.2,
            "mean_top_score": 0.0,
            "accuracy": 0.0,
        },
        {
            "bin": 1,
            "n": 1,
            "low": 0.2,
            "high": 0.4,
            "mean_top_score": 0.25,
            "accuracy": 1.0,
        },
        {
            "bin": 2,
            "n": 1,
            "low": 0.4,
            "high": 0.6,
            "mean_top_score": 0.5,
            "accuracy": 0.0,
        },
        {
            "bin": 3,
            "n": 1,
            "low": 0.6,
            "high": 0.8,
            "mean_top_score": 0.75,
            "accuracy": 1.0,
        },
        {
            "bin": 4,
            "n": 1,
            "low": 0.8,
            "high": 1.0,
            "mean_top_score": 1.0,
            "accuracy": 0.0,
        },
    ]


def test_absent_retrieval_score_remains_in_auroc_without_becoming_a_metric():
    outcomes = [
        _outcome("answer-hit", top_score=0.7, answerable=1.0, correct_at_1=1.0),
        _outcome("answer-miss", answerable=1.0, correct_at_1=0.0),
        _outcome("abstain-empty", answerable=0.0),
        _outcome("abstain-retrieved", top_score=0.2, answerable=0.0),
    ]
    scored = uncertainty_calibration(outcomes)
    assert scored["answerability_auroc"] == 0.625
    assert scored["correctness_auroc"] == 1.0
    assert scored["n_answerability_scored"] == 4
    assert scored["n_retrieval_score_bearing"] == 2
    assert "top_score" not in outcomes[1].metrics
    assert "top_score" not in outcomes[2].metrics


class ScriptedLLM:
    """Small deterministic client with the production client's async shapes."""

    async def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        return (
            "The user shared details about studies, routines, relationships, and plans."
        )

    async def generate_stream(
        self, prompt: str, system: str | None = None, **kwargs
    ) -> AsyncIterator[str]:
        yield "I hear you, and I will keep that in mind."

    async def close(self) -> None:
        return None


@pytest.fixture
def scripted_service(tmp_path, monkeypatch):
    with patch.object(Config, "INTENT_CLASSIFIER_BACKEND", "heuristic"):
        service = build_cognitive_service(
            "llm_augmented", tmp_path, llm_service=ScriptedLLM()
        )

    async def embedding(_text: str) -> list[float]:
        return [1.0, 0.0]

    monkeypatch.setattr(service.memory_store, "get_embedding", embedding)
    yield service
    service.cognitive.close()


def _turn_outcomes(outcomes: list[SuiteOutcome]) -> list[SuiteOutcome]:
    return [outcome for outcome in outcomes if "turn" in outcome.categories]


@pytest.mark.asyncio
async def test_scripted_replay_instruments_all_groups_and_keeps_scoring_measurable(
    scripted_service,
):
    simulation = build(1001, "traveling_consultant", "1w")
    _sim, turns, annotations, probes, _answers = simulation
    outcomes = await run_metacognition_suite(
        simulation,
        scripted_service,
        outage_every=7,
        background_timeout=2.0,
        progress_every=500,
    )

    turn_rows = _turn_outcomes(outcomes)
    assert len(turn_rows) == len(turns)
    assert all(
        row.mode == "llm_augmented" and row.suite == "metacognition" for row in outcomes
    )
    assert any("tagged" in row.categories for row in turn_rows)
    assert any("control" in row.categories for row in turn_rows)
    assert all("surfaced_changed" in row.metrics for row in turn_rows)
    assert all("per_turn_retrieval_ran" in row.metrics for row in turn_rows)
    assert all(
        {
            "surfaced_this_turn",
            "surfaced_carried_over",
            "surfacing_sweep_would_have_been_throttled",
        }
        <= row.metrics.keys()
        for row in turn_rows
    )
    assert all(
        row.metrics["surfacing_sweep_would_have_been_throttled"] in {0.0, 1.0}
        for row in turn_rows
    )
    assert sum(row.metrics["outage_scheduled"] for row in turn_rows) == len(turns) // 7
    assert sum(row.metrics["outage_injected"] for row in turn_rows) <= len(turns) // 7
    assert sum("answerable" in row.metrics for row in outcomes) > 0

    contradiction = contradiction_recording(turn_rows)
    freshness = retrieval_freshness(turn_rows)
    outage = outage_propagation(turn_rows)
    uncertainty = uncertainty_calibration(outcomes)
    assert contradiction["tagged_n"] > 0 and contradiction["control_n"] > 0
    assert freshness["turn_n"] == len(turns)
    assert outage["outage_turn_n"] <= len(turns) // 7
    assert len(uncertainty["reliability"]) == 5
    async with scripted_service.memory_store.pool.acquire() as conn:
        stored_rows = await conn.fetchval("SELECT COUNT(*) FROM memories")
    assert stored_rows > 0
    assert len(probes) > 0 and len(annotations) == len(turns)


@pytest.mark.asyncio
async def test_injected_outage_takes_productions_failure_shape_not_an_exception(
    scripted_service,
):
    """Reviewer regression: production's search failure branch records
    `last_search_error`/`_at` and returns [] (memory_store.py ~3899-3903); it
    never raises to callers. The injection used to raise, testing M-8 against
    a failure shape V2 does not have."""
    store = SimpleNamespace(last_search_error=None, last_search_error_at=None)
    assert _injected_search_failure(store) == []
    assert store.last_search_error == INJECTED_OUTAGE_MESSAGE
    assert store.last_search_error_at is not None

    # And end to end: every turn's search fails, yet nothing raises to callers.
    simulation = build(1001, "traveling_consultant", "1w")
    sim, turns, annotations, _probes, _answers = simulation
    outcomes = await run_metacognition_suite(
        (sim, turns[:3], annotations[:3], [], []),
        scripted_service,
        outage_every=1,
        background_timeout=2.0,
    )
    turn_rows = _turn_outcomes(outcomes)
    assert any(row.metrics["outage_injected"] for row in turn_rows)
    assert all(row.metrics["turn_errored"] == 0.0 for row in turn_rows)


@pytest.mark.asyncio
async def test_surfacing_freshness_tracks_memory_carried_into_second_turn(
    scripted_service, monkeypatch
):
    simulation = build(1001, "traveling_consultant", "1w")
    sim, turns, annotations, _probes, _answers = simulation
    assert len(turns) >= 2
    sweep_count = 0

    async def scripted_sweep(agent, source_metadata=None):
        nonlocal sweep_count
        sweep_count += 1
        if sweep_count == 1:
            await agent.publish(
                "memory.surfaced",
                {"memories": [{"content": "Remembered hiking plans"}]},
            )

    monkeypatch.setattr(SurfacingAgent, "_surface_relevant_memories", scripted_sweep)
    outcomes = await run_metacognition_suite(
        (sim, turns[:2], annotations[:2], [], []),
        scripted_service,
        outage_every=10,
        background_timeout=2.0,
    )
    rows = _turn_outcomes(outcomes)
    assert len(rows) == 2
    assert rows[0].metrics["surfaced_this_turn"] == 1.0
    assert rows[0].metrics["surfaced_changed"] == 1.0
    assert rows[1].metrics["surfaced_this_turn"] == 0.0
    assert rows[1].metrics["surfaced_carried_over"] == 1.0
    assert rows[1].metrics["per_turn_retrieval_ran"] in {0.0, 1.0}
    freshness = retrieval_freshness(rows)
    assert freshness["surfaced_carried_over_n"] >= 1
    assert freshness["surfaced_carried_over_fraction"] is not None


@pytest.mark.asyncio
async def test_architecture_only_is_refused_with_dr037_reason(scripted_service):
    scripted_service.mode = "architecture_only"
    with pytest.raises(ValueError, match="DR-037"):
        await run_metacognition_suite(
            build(1000, "chatty_student", "1w"), scripted_service
        )


@pytest.mark.asyncio
async def test_every_instance_patch_is_restored_when_a_turn_raises(
    scripted_service, monkeypatch
):
    action = scripted_service.cognitive.action
    decision = scripted_service.cognitive.decision
    store = scripted_service.memory_store
    surfacing_agent = SurfacingAgent(
        memory_store=store, graph_db=MagicMock(), conversation_store=None
    )
    monkeypatch.setattr(
        metacognition_suite, "SurfacingAgent", lambda **_kwargs: surfacing_agent
    )
    before = (
        action.__dict__.get("_surface_fallback_memories"),
        decision.__dict__.get("decide"),
        store.__dict__.get("search_memories"),
        store.last_search_error,
        surfacing_agent.__dict__.get("publish"),
    )

    async def explode(_event):
        raise RuntimeError("scripted turn failure")
        yield {}

    monkeypatch.setattr(scripted_service.cognitive, "process_event", explode)
    with pytest.raises(RuntimeError, match="scripted turn failure"):
        await run_metacognition_suite(
            build(1001, "traveling_consultant", "1w"),
            scripted_service,
            outage_every=1,
        )
    after = (
        action.__dict__.get("_surface_fallback_memories"),
        decision.__dict__.get("decide"),
        store.__dict__.get("search_memories"),
        store.last_search_error,
        surfacing_agent.__dict__.get("publish"),
    )
    assert after == before


def test_group_scorers_return_none_without_denominators():
    assert uncertainty_calibration([])["answerability_auroc"] is None
    assert uncertainty_calibration([])["correctness_auroc"] is None
    assert contradiction_recording([]) == {
        "tagged_n": 0,
        "tagged_recorded_rate": None,
        "control_n": 0,
        "control_recorded_rate": None,
    }
    assert retrieval_freshness([])["retrieval_ran_fraction"] is None
    assert outage_propagation([])["failure_visible_rate"] is None
    scheduled_only = _outcome("scheduled", outage_scheduled=1.0, outage_injected=0.0)
    assert outage_propagation([scheduled_only])["outage_turn_n"] == 0


# Match the memory suite fixture exactly: the real-LLM replay is a home-gpu
# job, and the default endpoint must never silently use a local CPU Ollama.
BRAINBENCH_LLM_URL = os.environ.get("BRAINBENCH_LLM_URL", "http://100.88.246.46:11434")


@pytest.mark.asyncio
async def test_real_llm_augmented_lifesim_replay(tmp_path: Path, ollama_tags):
    from app.llm import build_llm_client

    assert (Config.LLM_PROVIDER or "ollama").lower() == "ollama"
    assert isinstance(ollama_tags.get("models", []), list)
    simulation = build(1000, "chatty_student", "1w")
    _sim, turns, _annotations, probes, _answers = simulation
    arm_results = {}
    for surfacing in (True, False):
        service = build_cognitive_service(
            "llm_augmented",
            tmp_path / ("surfacing" if surfacing else "control"),
            llm_service=build_llm_client(
                base_url=BRAINBENCH_LLM_URL,
                model=Config.LLM_CHAT_MODEL,
            ),
        )
        try:
            outcomes = await run_metacognition_suite(
                simulation,
                service,
                persona_seed=1000,
                progress_every=25,
                outage_every=7,
                surfacing=surfacing,
            )
            turn_rows = _turn_outcomes(outcomes)
            probe_rows = [
                outcome for outcome in outcomes if "answerable" in outcome.metrics
            ]
            assert len(turn_rows) == len(turns)
            assert len({row.probe_key for row in turn_rows}) == len(turns)
            assert len(probe_rows) <= len(probes)
            assert all(row.mode == "llm_augmented" for row in outcomes)
            arm_results["surfacing" if surfacing else "control"] = {
                "uncertainty_calibration": uncertainty_calibration(outcomes),
                "contradiction_recording": contradiction_recording(turn_rows),
                "retrieval_freshness": retrieval_freshness(turn_rows),
                "outage_propagation": outage_propagation(turn_rows),
            }
        finally:
            service.cognitive.close()
            await service.llm_service.close()
    print(f"BrainBench metacognition seed=1000 chatty_student 1w: {arm_results}")
    assert set(arm_results) == {"surfacing", "control"}
    assert all(
        result is not None
        for results in arm_results.values()
        for result in results.values()
    )
