"""Fast affect analysis tests plus one real local-Ollama replay."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Config
from evals.brainbench.affect_suite import (
    run_affect_suite,
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
        "completion_rate": 0.0,
    }


def _fake_replay(process_event):
    now = datetime(2025, 1, 1, tzinfo=UTC)
    turn = SimpleNamespace(turn_id="turn-1", t=now, text="A test message")
    annotation = SimpleNamespace(turn_id="turn-1", tags=["test"], user_valence=0.7)
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
        "oracle_user_valence": 0.7,
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
async def test_run_affect_suite_catches_the_inner_semantic_drift_failure_too():
    """appraise_semantic_drift (app/cognitive/appraisal.py) has its own
    try/except around the LLM call: on failure it logs "Semantic drift
    evaluation failed" under the app.cognitive.appraisal logger and returns
    current_pad unchanged, without raising -- so the outer pipeline.py
    wrapper never sees an exception and never logs its own failure message.
    A watcher scoped to only app.cognitive.pipeline is blind to this, the
    far more common failure mode (an LLM call that fails or returns
    unparseable JSON), and would report false 100% completion. This is the
    regression test for a real bug this suite's own first draft had -- found
    by a real home-gpu replay reporting 100% completion with an exact 0.0
    mood delta on every one of 80 turns."""

    async def process_event(_event):
        async def appraisal_with_inner_drift_failure():
            logging.getLogger("app.cognitive.appraisal").error(
                "[System 2 Appraisal] Semantic drift evaluation failed: bad json"
            )
            # No exception raised, no pipeline.py-level log line -- exactly
            # what appraise_semantic_drift's own except block does before
            # falling through to `return current_pad`.

        pipeline._system2_task = asyncio.create_task(
            appraisal_with_inner_drift_failure()
        )
        yield {"type": "appraisal"}

    simulation, service, _state, pipeline = _fake_replay(process_event)

    outcomes = await run_affect_suite(simulation, service)

    assert len(outcomes) == 1
    assert outcomes[0].metrics["system2_completed"] == 0.0


@pytest.mark.asyncio
async def test_run_affect_suite_records_child_failure_and_continues():
    async def process_event(_event):
        async def failed_appraisal():
            raise RuntimeError("unparseable appraisal")

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


@pytest.fixture(scope="module")
def ollama_tags() -> dict:
    """Skip unless BRAINBENCH_LLM_URL (home-gpu by default) is reachable."""
    try:
        response = subprocess.run(
            [
                "curl",
                "-s",
                "--max-time",
                "2",
                BRAINBENCH_LLM_URL.rstrip("/") + "/api/tags",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(
            f"BrainBench LLM endpoint {BRAINBENCH_LLM_URL} is unreachable: {exc}"
        )
    if response.returncode != 0:
        pytest.skip(f"BrainBench LLM endpoint {BRAINBENCH_LLM_URL} is unreachable")
    try:
        return json.loads(response.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Ollama responded but /api/tags was not valid JSON: {exc}")


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
