"""Fast affect analysis tests plus one real local-Ollama replay."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Config
from evals.brainbench.affect_suite import (
    run_affect_suite,
    state_bound_rates,
    system2_completion_rate,
    user_valence_reaches_mood,
)
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build


def _outcome(turn_id: str, valence_delta: float, oracle: float, completed: float = 1.0):
    return SuiteOutcome(
        probe_key=turn_id,
        persona_seed=1000,
        suite="affect",
        categories=("test",),
        metrics={
            "valence_delta": valence_delta,
            "oracle_user_valence": oracle,
            "oracle_expressed_valence": oracle,
            "system2_completed": completed,
        },
        mode="llm_augmented",
    )


def test_user_valence_reaches_mood_reports_expected_direction():
    outcomes = [
        _outcome("negative-1", -0.4, -0.8),
        _outcome("negative-2", -0.2, -0.5),
        _outcome("positive-1", 0.3, 0.5),
        _outcome("positive-2", 0.5, 0.8),
    ]

    result = user_valence_reaches_mood(outcomes)

    assert result["positive_mean_delta"] == pytest.approx(0.4)
    assert result["negative_mean_delta"] == pytest.approx(-0.3)
    # stats.cliffs_delta(negative, positive) is positive when positive tends higher.
    assert result["cliffs_delta"] == 1.0
    assert result["n_positive"] == 2
    assert result["n_negative"] == 2


def test_user_valence_reaches_mood_flags_a_perfect_effect_size_as_not_significant_off_one_seed():
    """W2 critic round 2, finding 4: a perfect Cliff's delta off a single
    persona seed per group must not be reported as significant -- the
    critic's own repro used one positive and one negative observation and
    got `cliffs_delta: 1.0` with no significance field to say the sample
    couldn't support that number. `_outcome` fixes persona_seed=1000 for
    every row, so even this file's own passing-direction test above is,
    without the fix, exactly this failure mode.
    """
    outcomes = [
        _outcome("negative-1", -0.4, -0.8),
        _outcome("positive-1", 0.5, 0.8),
    ]

    result = user_valence_reaches_mood(outcomes)

    assert result["cliffs_delta"] == 1.0
    assert result["significant"] is False
    assert result["p_value"] == 1.0
    assert result["ci95"] is None
    assert result["n_positive_clusters"] == 1
    assert result["n_negative_clusters"] == 1


def test_user_valence_reaches_mood_can_report_significant_with_enough_seeds():
    """The same mechanism must also be able to say yes: a consistent effect
    across several independent persona seeds should clear significance,
    proving the guard above is about insufficient evidence, not a gate that
    can never open."""
    outcomes = []
    for seed in range(1000, 1008):
        outcomes.append(
            SuiteOutcome(
                probe_key=f"negative-{seed}",
                persona_seed=seed,
                suite="affect",
                categories=("test",),
                metrics={
                    "valence_delta": -0.4,
                    "oracle_user_valence": -0.8,
                    "oracle_expressed_valence": -0.8,
                },
            )
        )
        outcomes.append(
            SuiteOutcome(
                probe_key=f"positive-{seed}",
                persona_seed=seed,
                suite="affect",
                categories=("test",),
                metrics={
                    "valence_delta": 0.4,
                    "oracle_user_valence": 0.8,
                    "oracle_expressed_valence": 0.8,
                },
            )
        )

    result = user_valence_reaches_mood(outcomes)

    assert result["n_positive_clusters"] == 8
    assert result["n_negative_clusters"] == 8
    assert result["significant"] is True
    assert result["p_value"] < 0.05


def test_user_valence_reaches_mood_uses_expressed_label_when_available():
    outcomes = [
        SuiteOutcome(
            probe_key="event-negative-surface-positive",
            persona_seed=1000,
            suite="affect",
            categories=("test",),
            metrics={
                "valence_delta": 0.4,
                "oracle_user_valence": -0.8,
                "oracle_expressed_valence": 0.8,
            },
        ),
        SuiteOutcome(
            probe_key="event-positive-surface-negative",
            persona_seed=1000,
            suite="affect",
            categories=("test",),
            metrics={
                "valence_delta": -0.4,
                "oracle_user_valence": 0.8,
                "oracle_expressed_valence": -0.8,
            },
        ),
    ]

    result = user_valence_reaches_mood(outcomes)

    assert result["positive_mean_delta"] == pytest.approx(0.4)
    assert result["negative_mean_delta"] == pytest.approx(-0.4)


def test_user_valence_reaches_mood_excludes_near_neutral_turns():
    outcomes = [
        _outcome("negative", -0.2, -0.5),
        _outcome("neutral-low", 0.9, 0.1),
        _outcome("neutral-high", -0.9, -0.1),
        _outcome("positive", 0.4, 0.5),
    ]

    result = user_valence_reaches_mood(outcomes, valence_threshold=0.1)

    assert result["n_positive"] == 1
    assert result["n_negative"] == 1
    assert result["positive_mean_delta"] == 0.4
    assert result["negative_mean_delta"] == -0.2


@pytest.mark.parametrize(
    "outcomes",
    [
        [],
        [_outcome("positive", 0.1, 0.5)],
        [_outcome("negative", -0.1, -0.5)],
    ],
)
def test_user_valence_reaches_mood_requires_both_groups(outcomes):
    with pytest.raises(ValueError, match="positive and negative"):
        user_valence_reaches_mood(outcomes)


def test_system2_completion_rate_summarizes_and_handles_empty_input():
    outcomes = [
        _outcome("one", 0.0, 0.2, 1.0),
        _outcome("two", 0.0, -0.2, 0.0),
        _outcome("three", 0.0, 0.1, 1.0),
    ]

    assert system2_completion_rate(outcomes) == {
        "n_turns": 3,
        "completed": 2.0,
        "completion_rate": pytest.approx(2 / 3),
    }
    assert system2_completion_rate([]) == {
        "n_turns": 0,
        "completed": 0,
        "completion_rate": None,
    }


def test_state_bound_rates_reports_each_affect_layer():
    outcomes = [
        SuiteOutcome(
            probe_key="one",
            persona_seed=1000,
            suite="affect",
            categories=("test",),
            metrics={
                "mood_at_bound": True,
                "momentary_valence_at_bound": False,
                "relationship_sentiment_at_bound": True,
            },
        ),
        SuiteOutcome(
            probe_key="two",
            persona_seed=1001,
            suite="affect",
            categories=("test",),
            metrics={
                "mood_at_bound": False,
                "momentary_valence_at_bound": False,
                "relationship_sentiment_at_bound": False,
            },
        ),
    ]

    assert state_bound_rates(outcomes) == {
        "mood_at_bound_rate": 0.5,
        "momentary_valence_at_bound_rate": 0.0,
        "relationship_sentiment_at_bound_rate": 0.5,
    }


def _fake_replay(process_event):
    now = datetime(2025, 1, 1, tzinfo=UTC)
    turn = SimpleNamespace(turn_id="turn-1", t=now, text="A test message")
    annotation = SimpleNamespace(
        turn_id="turn-1", tags=["test"], user_valence=0.7, expressed_valence=0.9
    )
    state = SimpleNamespace(current_state=SimpleNamespace(valence=0.2))
    pipeline = SimpleNamespace(_system2_task=None)
    cognitive = SimpleNamespace(
        state=state, pipeline=pipeline, process_event=process_event
    )
    service = SimpleNamespace(mode="llm_augmented", cognitive=cognitive)
    simulation = (SimpleNamespace(seed=1000, start=now), [turn], [annotation], [], [])
    return simulation, service, state, pipeline


@pytest.mark.asyncio
async def test_run_affect_suite_success_without_failure_log_records_mood_delta():
    task_states = []
    state = None
    pipeline = None

    async def process_event(_event):
        async def appraisal():
            task_states.append("started")
            await asyncio.sleep(0)
            state.current_state.valence = 0.65
            task_states.append("finished")

        pipeline._system2_task = asyncio.create_task(appraisal())
        yield {"type": "appraisal"}

    simulation, service, state, pipeline = _fake_replay(process_event)

    outcomes = await run_affect_suite(simulation, service)

    assert task_states == ["started", "finished"]
    assert len(outcomes) == 1
    assert outcomes[0].metrics == {
        "valence_delta": pytest.approx(0.45),
        "mood": 0.65,
        "mood_at_bound": False,
        "momentary_valence": 0.0,
        "momentary_valence_delta": 0.0,
        "momentary_valence_at_bound": False,
        "relationship_sentiment": 0.0,
        "relationship_sentiment_delta": 0.0,
        "relationship_sentiment_at_bound": False,
        "oracle_user_valence": 0.7,
        "oracle_expressed_valence": 0.9,
        "trajectory_time_hours": 0.0,
        "trajectory_baseline_mood": 0.0,
        "trajectory_baseline_momentary_valence": 0.0,
        "trajectory_baseline_relationship_sentiment": 0.0,
        "trajectory_post_mood": 0.65,
        "trajectory_post_momentary_valence": 0.0,
        "trajectory_post_relationship_sentiment": 0.0,
        "system2_completed": 1.0,
    }


@pytest.mark.asyncio
async def test_run_affect_suite_failure_log_marks_returned_task_incomplete():
    async def process_event(_event):
        async def appraisal_with_logged_failure():
            logging.getLogger("app.cognitive.pipeline").error(
                "[System 2 Appraisal] Background semantic appraisal failed: bad output"
            )

        pipeline._system2_task = asyncio.create_task(appraisal_with_logged_failure())
        yield {"type": "appraisal"}

    simulation, service, _state, pipeline = _fake_replay(process_event)

    outcomes = await run_affect_suite(simulation, service)

    assert len(outcomes) == 1
    assert outcomes[0].metrics["system2_completed"] == 0.0


@pytest.mark.asyncio
async def test_removed_semantic_drift_logger_does_not_mark_background_task_failed():
    """The old semantic-drift path is gone; its former log is not a live metric."""

    async def process_event(_event):
        async def appraisal_with_inner_drift_failure():
            logging.getLogger("app.cognitive.appraisal").error(
                "[System 2 Appraisal] Semantic drift evaluation failed: bad json"
            )
            # No current affect path emits this legacy message.

        pipeline._system2_task = asyncio.create_task(
            appraisal_with_inner_drift_failure()
        )
        yield {"type": "appraisal"}

    simulation, service, _state, pipeline = _fake_replay(process_event)

    outcomes = await run_affect_suite(simulation, service)

    assert len(outcomes) == 1
    assert outcomes[0].metrics["system2_completed"] == 1.0


@pytest.mark.asyncio
async def test_run_affect_suite_records_child_failure_and_continues():
    async def process_event(_event):
        async def failed_appraisal():
            raise RuntimeError("unparsable appraisal")

        pipeline._system2_task = asyncio.create_task(failed_appraisal())
        yield {"type": "appraisal"}

    simulation, service, _state, pipeline = _fake_replay(process_event)

    outcomes = await run_affect_suite(simulation, service)

    assert len(outcomes) == 1
    assert outcomes[0].metrics["system2_completed"] == 0.0


@pytest.mark.asyncio
async def test_run_affect_suite_propagates_caller_cancellation():
    started = asyncio.Event()

    async def process_event(_event):
        async def pending_appraisal():
            started.set()
            await asyncio.Event().wait()

        pipeline._system2_task = asyncio.create_task(pending_appraisal())
        yield {"type": "appraisal"}

    simulation, service, _state, pipeline = _fake_replay(process_event)
    replay = asyncio.create_task(run_affect_suite(simulation, service))
    await started.wait()
    replay.cancel()

    with pytest.raises(asyncio.CancelledError):
        await replay


# llm_augmented replays are GPU work and belong on home-gpu, not whatever
# Ollama happens to be running on the dev machine (a real week-long persona
# replay is not a "seconds-long smoke check"). Point this at the Mac's local
# Ollama only by explicit override, never by default, so this test can never
# silently burn Mac CPU/battery just because port 11434 answers.
BRAINBENCH_LLM_URL = os.environ.get("BRAINBENCH_LLM_URL", "http://100.88.246.46:11434")


@pytest.mark.asyncio
async def test_real_llm_augmented_lifesim_replay(
    tmp_path: Path, ollama_tags, caplog: pytest.LogCaptureFixture
):
    from app.llm import build_llm_client
    from evals.brainbench.adapters import build_cognitive_service

    caplog.set_level(logging.ERROR, logger="app.cognitive.pipeline")
    assert (Config.LLM_PROVIDER or "ollama").lower() == "ollama"
    assert isinstance(ollama_tags.get("models", []), list)
    service = build_cognitive_service(
        "llm_augmented",
        tmp_path,
        llm_service=build_llm_client(
            base_url=BRAINBENCH_LLM_URL,
            model=Config.LLM_CHAT_MODEL,
        ),
    )
    simulation = build(1000, "chatty_student", "80t")
    _sim, turns, annotations, _probes, _answers = simulation
    try:
        outcomes = await run_affect_suite(simulation, service, progress_every=25)

        assert len(outcomes) == len(turns) == len(annotations)
        assert all(outcome.mode == "llm_augmented" for outcome in outcomes)
        assert all(outcome.suite == "affect" for outcome in outcomes)
        assert all(
            math.isfinite(outcome.metrics["valence_delta"]) for outcome in outcomes
        )
        completion = system2_completion_rate(outcomes)
        assert completion["n_turns"] == len(turns)
        assert isinstance(completion["completion_rate"], float)
        assert 0.0 <= completion["completion_rate"] <= 1.0

        user_groups = {
            "positive": sum(1 for item in annotations if item.user_valence > 0.1),
            "negative": sum(1 for item in annotations if item.user_valence < -0.1),
        }
        if user_groups["positive"] and user_groups["negative"]:
            valence_result = user_valence_reaches_mood(outcomes)
            print(f"BrainBench affect user-valence result: {valence_result}")
        else:
            print(
                "BrainBench affect user-valence result unavailable: "
                f"positive={user_groups['positive']} negative={user_groups['negative']}"
            )
        print(f"BrainBench affect System2 completion result: {completion}")
        internal_failures = [
            record
            for record in caplog.records
            if record.name == "app.cognitive.pipeline"
            and "Background semantic appraisal failed" in record.getMessage()
        ]
        print(
            "BrainBench affect internal System2 appraisal failures detected: "
            f"{len(internal_failures)}"
        )
    finally:
        service.cognitive.close()
        await service.llm_service.close()
