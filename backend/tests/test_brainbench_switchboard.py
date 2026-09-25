from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.cognitive.pipeline import CognitivePipeline
from app.config import AppSettings, Config
from evals.brainbench.adapters import build_cognitive_service, build_memory_store
from evals.brainbench.switchboard import (
    ARMS,
    Arm,
    ArmUnavailable,
    apply_arm,
    is_runnable,
    resolve_arm,
)


def test_registry_has_exact_arms_and_runnability_is_derived(monkeypatch):
    assert set(ARMS) == {
        "baseline",
        "v1-ranker",
        "-memory-truth",
        "-affect-control",
        "-reappraisal",
        "+reappraisal-weight-learning",
        "+temporal",
        "+affect-input",
        "+trust-model",
        "+graph",
        "+consolidation",
        "all",
    }
    assert resolve_arm("baseline").overrides == {}
    assert is_runnable(ARMS["v1-ranker"])
    planned = ARMS["+temporal"]
    assert not is_runnable(planned)
    monkeypatch.setitem(
        AppSettings.model_fields,
        "MEMORY_TEMPORAL_TRUTH_ENABLED",
        type("Field", (), {"annotation": bool})(),
    )
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", False, raising=False)
    assert is_runnable(planned)
    assert resolve_arm("+temporal") == planned


def test_bad_field_value_is_refused_and_unknown_arm_lists_names():
    bad = Arm("bad", {"MEMORY_RANKING_POLICY": True})
    assert not is_runnable(bad)
    with pytest.raises(ArmUnavailable, match="MEMORY_RANKING_POLICY"):
        with apply_arm(bad):
            pass
    with pytest.raises(ValueError, match="known arms"):
        resolve_arm("missing")


def test_all_union_is_registry_derived_and_conflicts_raise(monkeypatch):
    expected = {
        "MEMORY_TEMPORAL_TRUTH_ENABLED": True,
        "AFFECT_USER_INPUT_ENABLED": True,
        "TRUST_EVIDENCE_MODEL_ENABLED": True,
        "MEMORY_GRAPH_PPR_ENABLED": True,
        "MEMORY_SEMANTIC_CONSOLIDATION_ENABLED": True,
    }
    # Planned fields are intentionally absent; all still carries the full union.
    assert ARMS["all"].overrides == expected
    assert not is_runnable(ARMS["all"])
    with pytest.raises(ArmUnavailable, match="W1"):
        resolve_arm("all")
    for key in expected:
        monkeypatch.setitem(
            AppSettings.model_fields, key, type("Field", (), {"annotation": bool})()
        )
        monkeypatch.setattr(Config, key, False, raising=False)
    all_arm = resolve_arm("all")
    assert all_arm.overrides == expected
    monkeypatch.setitem(
        ARMS, "+conflict", Arm("+conflict", {"MEMORY_TEMPORAL_TRUTH_ENABLED": False})
    )
    with pytest.raises(ValueError, match="conflicting overrides"):
        resolve_arm("all")


def test_apply_arm_restores_even_when_body_raises():
    original = Config.MEMORY_RANKING_POLICY
    with pytest.raises(RuntimeError):
        with apply_arm(resolve_arm("v1-ranker")):
            assert Config.MEMORY_RANKING_POLICY == "actr_v1"
            raise RuntimeError("probe")
    assert Config.MEMORY_RANKING_POLICY == original


@pytest.mark.asyncio
async def test_v1_ranker_arm_changes_memory_store_dispatch(tmp_path, monkeypatch):
    store = build_memory_store(tmp_path)
    called = []

    async def hybrid(*args, **kwargs):
        called.append("hybrid")
        return []

    async def l1(*args, **kwargs):
        called.append("actr")
        raise LookupError("actr path reached")

    monkeypatch.setattr(store, "_search_memories_hybrid", hybrid)
    monkeypatch.setattr(store, "_l1_cache_hit", l1)
    with apply_arm(resolve_arm("baseline")):
        await store.search_memories("query")
    with apply_arm(resolve_arm("v1-ranker")), pytest.raises(LookupError, match="actr"):
        await store.search_memories("query")
    assert called == ["hybrid", "actr"]
    await store.close()


def test_memory_truth_and_affect_control_arms_change_pipeline_kwargs(monkeypatch):
    pipeline = CognitivePipeline.__new__(CognitivePipeline)
    pipeline._decision_accepts_memory_activations = lambda: True
    pipeline._decision_accepts_global_controls = lambda: True
    monkeypatch.setattr(
        "app.cognitive.pipeline.memories_to_activations",
        lambda rows: [SimpleNamespace(outage_flag=False)],
    )
    event = SimpleNamespace(metadata={})
    activations = pipeline._setup_memory_activations([{"id": "m"}], None, event)
    assert activations and event.metadata["retrieval_degraded"] is False
    assert pipeline._build_decide_kwargs(
        ["activation"], {"global_controls": {"x": 1}}
    ) == {"memory_activations": ["activation"], "global_controls": {"x": 1}}
    with apply_arm(resolve_arm("-memory-truth")):
        assert pipeline._setup_memory_activations([{"id": "m"}], None, event) is None
        assert "memory_activations" not in pipeline._build_decide_kwargs(
            ["activation"], {"global_controls": {"x": 1}}
        )
    with apply_arm(resolve_arm("-affect-control")):
        assert "global_controls" not in pipeline._build_decide_kwargs(
            ["activation"], {"global_controls": {"x": 1}}
        )


def test_reappraisal_arms_are_captured_during_service_construction(tmp_path):
    with apply_arm(resolve_arm("-reappraisal")):
        disabled = build_cognitive_service("architecture_only", tmp_path / "disabled")
    assert disabled.cognitive.reappraisal.enabled is False
    with apply_arm(resolve_arm("+reappraisal-weight-learning")):
        learning = build_cognitive_service("architecture_only", tmp_path / "learning")
    assert learning.cognitive.reappraisal.weight_learning is True
    asyncio.run(disabled.memory_store.close())
    asyncio.run(learning.memory_store.close())
