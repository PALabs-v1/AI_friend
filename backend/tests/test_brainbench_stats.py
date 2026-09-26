"""Gate tests for evals/brainbench/stats.py: the generic SuiteOutcome
aggregation/delta machinery, and the new Cliff's delta / Cohen's d / Holm
correction primitives 06-benchmark-plan.md calls for and evals.cognitive
doesn't have.
"""

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from evals.brainbench.stats import (
    SuiteOutcome,
    aggregate,
    bootstrap_p_value,
    cliffs_delta,
    cohens_d,
    holm_correction,
    paired_delta,
)


def _outcome(probe_key, persona_seed, suite, categories, **metrics):
    return SuiteOutcome(
        probe_key=probe_key,
        persona_seed=persona_seed,
        suite=suite,
        categories=categories,
        metrics=metrics,
    )


def test_aggregate_computes_mean_and_ci_per_category():
    results = [
        _outcome("p1", 1000, "memory", ("current",), hit_at_3=1.0),
        _outcome("p2", 1000, "memory", ("current",), hit_at_3=0.0),
        _outcome("p3", 1001, "memory", ("historical",), hit_at_3=1.0),
    ]
    agg = aggregate(results)
    assert agg["all"]["n"] == 3
    assert agg["all"]["metrics"]["hit_at_3"]["mean"] == pytest.approx(2 / 3, abs=1e-4)
    assert agg["current"]["n"] == 2
    assert agg["historical"]["n"] == 1


def test_aggregate_with_no_results_is_empty_not_crashing():
    agg = aggregate([])
    assert agg["all"] == {"n": 0, "metrics": {}}


def test_aggregate_restricts_to_requested_categories():
    results = [
        _outcome("p1", 1000, "memory", ("current",), hit_at_3=1.0),
        _outcome("p2", 1000, "memory", ("historical",), hit_at_3=0.0),
    ]
    agg = aggregate(results, categories=("current",))
    assert set(agg) == {"all", "current"}


def test_paired_delta_matches_probes_by_seed_and_key():
    baseline = [
        _outcome("p1", 1000, "memory", ("current",), hit_at_3=0.0),
        _outcome("p2", 1001, "memory", ("current",), hit_at_3=0.0),
    ]
    treatment = [
        _outcome("p1", 1000, "memory", ("current",), hit_at_3=1.0),
        _outcome("p2", 1001, "memory", ("current",), hit_at_3=1.0),
    ]
    delta = paired_delta(baseline, treatment, "hit_at_3")
    assert delta["n"] == 2
    assert delta["delta"] == pytest.approx(1.0)
    assert delta["wins"] == 2
    assert delta["losses"] == 0


def test_paired_delta_ignores_unmatched_probes():
    baseline = [_outcome("p1", 1000, "memory", ("current",), hit_at_3=0.0)]
    treatment = [
        _outcome("p1", 1000, "memory", ("current",), hit_at_3=1.0),
        _outcome("p2_only_in_treatment", 2000, "memory", ("current",), hit_at_3=1.0),
    ]
    delta = paired_delta(baseline, treatment, "hit_at_3")
    assert delta["n"] == 1


def test_paired_delta_empty_when_no_overlap():
    baseline = [_outcome("p1", 1000, "memory", ("current",), hit_at_3=0.0)]
    treatment = [_outcome("p2", 2000, "memory", ("current",), hit_at_3=1.0)]
    delta = paired_delta(baseline, treatment, "hit_at_3")
    assert delta == {"n": 0}


def test_cliffs_delta_all_b_greater_is_positive_one():
    assert cliffs_delta([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]) == pytest.approx(1.0)


def test_cliffs_delta_all_b_less_is_negative_one():
    assert cliffs_delta([1.0, 1.0], [0.0, 0.0]) == pytest.approx(-1.0)


def test_cliffs_delta_identical_distributions_is_zero():
    assert cliffs_delta([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]) == pytest.approx(0.0)


def test_cliffs_delta_empty_inputs_returns_zero():
    assert cliffs_delta([], [1.0]) == 0.0
    assert cliffs_delta([1.0], []) == 0.0


def _pairwise_cliffs_delta(a, b):
    """The definition, pair by pair: the reference the fast version must match."""
    if not a or not b:
        return 0.0
    gt = sum(1 for x in a for y in b if y > x)
    lt = sum(1 for x in a for y in b if y < x)
    return (gt - lt) / (len(a) * len(b))


_values = st.lists(
    st.one_of(
        st.sampled_from([0.0, 1.0, 0.5, -1.0]),  # ties, like 0/1 metrics
        st.floats(allow_infinity=False, width=32),  # includes NaN
        st.integers(-5, 5),
    ),
    max_size=40,
)


@settings(max_examples=300, deadline=None)
@given(_values, _values)
@example([0.0, 1.0], [float("nan"), 0.5])  # NaN in b must count as neither
@example([float("nan"), 0.5], [0.0, 1.0])  # NaN in a must count as neither
@example([0.5, 0.5], [0.5, 1.0, 0.0])  # ties
def test_cliffs_delta_matches_the_pairwise_definition_exactly(a, b):
    assert cliffs_delta(a, b) == _pairwise_cliffs_delta(a, b)


def test_cohens_d_no_difference_no_variance_is_zero():
    assert cohens_d([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]) == 0.0


def test_cohens_d_sign_matches_direction_of_shift():
    a = [1.0, 2.0, 3.0, 4.0]
    b = [3.0, 4.0, 5.0, 6.0]
    assert cohens_d(a, b) > 0
    assert cohens_d(b, a) < 0


def test_cohens_d_too_few_samples_returns_zero():
    assert cohens_d([1.0], [2.0]) == 0.0


def test_bootstrap_p_value_is_small_for_a_clear_consistent_effect():
    diffs = [1.0] * 20
    clusters = list(range(20))
    p = bootstrap_p_value(diffs, clusters, seed=1, n=500)
    assert p < 0.05


def test_bootstrap_p_value_is_large_for_no_effect():
    diffs = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
    clusters = list(range(8))
    p = bootstrap_p_value(diffs, clusters, seed=1, n=500)
    assert p > 0.2


def test_bootstrap_p_value_empty_is_one():
    assert bootstrap_p_value([], []) == 1.0


def test_holm_correction_single_hypothesis_matches_uncorrected():
    result = holm_correction({"only": 0.03}, alpha=0.05)
    assert result["only"]["significant"] is True
    assert result["only"]["threshold"] == pytest.approx(0.05)


def test_holm_correction_stops_rejecting_after_first_failure():
    # Three comparisons: two clearly significant, one clearly not. Holm's
    # step-down must not let a later, larger p-value "borrow" significance
    # from an earlier, smaller one once the chain breaks.
    p_values = {"a": 0.001, "b": 0.6, "c": 0.002}
    result = holm_correction(p_values, alpha=0.05)
    assert result["a"]["significant"] is True
    assert result["c"]["significant"] is True
    assert result["b"]["significant"] is False


def test_holm_correction_is_stricter_than_uncorrected_alpha():
    # With 5 comparisons all at p=0.04, plain alpha=0.05 would call every one
    # significant; Holm's first threshold is alpha/5 = 0.01, so none should be.
    p_values = {f"arm_{i}": 0.04 for i in range(5)}
    result = holm_correction(p_values, alpha=0.05)
    assert all(not r["significant"] for r in result.values())
