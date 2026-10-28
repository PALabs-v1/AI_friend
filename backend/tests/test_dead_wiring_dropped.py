"""Dead phase services stay available while the live pipeline drops them."""

import importlib
from pathlib import Path
from unittest.mock import MagicMock

from app.cognitive.action_candidate import ActionCandidate, CandidateSelector
from app.cognitive.calibration import CapabilityLimitationModel
from app.cognitive.core import CognitiveService
from app.cognitive.planning import (
    DeterministicPlanExecutor,
    DeterministicPlanVerifier,
)
from app.cognitive.simulation import EpisodicSimulator
from app.llm.adapter_gate import OfflineAdapterGate


def test_live_core_and_pipeline_do_not_hold_dead_phase_objects(tmp_path):
    core = CognitiveService(
        llm_service=MagicMock(),
        memory_store=MagicMock(),
        graph_db=MagicMock(),
        base_path=str(tmp_path),
    )
    try:
        for attribute in (
            "plan_verifier",
            "plan_executor",
            "episodic_simulator",
            "offline_adapter_gate",
        ):
            assert not hasattr(core, attribute)
            assert not hasattr(core.pipeline, attribute)
        assert not hasattr(core, "capability_model")
        assert not hasattr(core.pipeline, "capability_model")
        assert isinstance(
            core.state.current_state.capability_model, CapabilityLimitationModel
        )
    finally:
        core.close()


def test_dead_modules_and_phase_benchmark_scripts_remain_importable():
    for module_name in (
        "app.cognitive.planning",
        "app.cognitive.simulation",
        "app.cognitive.calibration",
        "app.llm.adapter_gate",
    ):
        assert importlib.import_module(module_name)

    assert DeterministicPlanVerifier
    assert DeterministicPlanExecutor
    assert EpisodicSimulator
    assert CapabilityLimitationModel
    assert OfflineAdapterGate

    benchmark_root = Path(__file__).resolve().parents[1] / "scripts" / "benchmarks"
    script_names = (
        "run_local_benchmarks_phase4",
        "run_gpu_benchmarks_phase4",
        "run_local_benchmarks_phase6",
        "run_gpu_benchmarks_phase6",
    )
    for script_name in script_names:
        module = importlib.import_module(f"scripts.benchmarks.{script_name}")
        assert Path(module.__file__).resolve().parent == benchmark_root


def test_proceed_candidate_scores_match_pre_removal_baseline():
    candidates = [
        ActionCandidate(
            candidate_id="speak",
            kind="SPEAK",
            source="test",
            score=0.6,
            target_goal_ids=["goal-a"],
        ),
        ActionCandidate(
            candidate_id="ask",
            kind="ASK",
            source="test",
            score=0.4,
            target_goal_ids=["goal-a", "goal-b"],
        ),
        ActionCandidate(candidate_id="wait", kind="WAIT", source="test", score=0.2),
    ]

    result = CandidateSelector().score_and_select(
        candidates, active_goals=["goal-a"], metacognitive_directive="PROCEED"
    )

    assert result.effective_scores == {"speak": 0.8, "ask": 0.5, "wait": 0.2}
    assert result.winner.candidate_id == "speak"
