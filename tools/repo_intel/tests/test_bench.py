from __future__ import annotations

from tools.repo_intel import bench


def test_benchmark_results_are_stable_and_the_known_miss_stays_visible():
    rep = bench.run()
    m = rep.by_metric()
    # (cases, intel correct, baseline correct)
    assert m["contradiction_detected"][:2] == (6, 6)
    assert m["dependency_recall"][:2] == (4, 4)
    assert m["staleness_detected"][:2] == (2, 2)
    assert m["inventory_parse_coverage"][:2] == (1, 1)
    # the static index cannot see a worker started through importlib: this miss is expected and must stay reported
    dyn = next(
        o
        for o in rep.outcomes
        if o.scenario == "dynamic_worker" and o.metric == "false_absence"
    )
    assert dyn.intel_ok is False
    risk = next(o for o in rep.outcomes if o.metric == "risk_disclosed")
    assert risk.intel_ok is True


def test_classification_must_be_known(tmp_path):
    import pytest

    from tools.repo_intel import knowledge

    with pytest.raises(knowledge.ClaimError, match="classification"):
        knowledge.add_claim(
            None, tmp_path, {"text": "x" * 20, "classification": "wrong"}, "a", "b"
        )
