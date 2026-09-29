"""Trajectory outcome formulas are pinned against hand-built affect traces."""

import pytest

from evals.brainbench import affect_suite
from evals.brainbench.stats import SuiteOutcome
from evals.brainbench.summaries import SUMMARY_SCORERS

LAYERS = ("mood", "momentary_valence", "relationship_sentiment")


def _trajectory():
    values = {
        "mood": (0.8, 0.4, 0.2),
        "momentary_valence": (0.9, 0.45, 0.225),
        "relationship_sentiment": (0.4, 0.2, 0.1),
    }
    outcomes = []
    for index, hours in enumerate((0.0, 1.0, 2.0)):
        metrics = {
            "trajectory_time_hours": hours,
            "oracle_expressed_valence": 0.8 if index == 0 else 0.0,
            "mood_at_bound": index == 1,
            "momentary_valence_at_bound": index == 0,
            "relationship_sentiment_at_bound": index == 2,
        }
        for layer in LAYERS:
            metrics[f"trajectory_baseline_{layer}"] = 0.0
            metrics[f"trajectory_post_{layer}"] = values[layer][index]
        outcomes.append(
            SuiteOutcome(
                probe_key=f"turn-{index}",
                persona_seed=1000,
                suite="affect",
                categories=("test",),
                metrics=metrics,
                mode="architecture_only",
            )
        )
    return outcomes


def test_affect_trajectory_summaries_are_registered():
    registered = SUMMARY_SCORERS["affect"]
    for name in (
        "persistence",
        "decay",
        "recovery",
        "saturation",
        "one_conversation_long_term_effect",
    ):
        assert name in registered


def test_persistence_formula_is_mean_neutral_turn_displacement_retention():
    result = affect_suite.persistence(_trajectory())

    for layer in LAYERS:
        assert result[f"{layer}_retention_ratio_mean"] == pytest.approx(0.5)
        assert result[f"{layer}_n_transitions"] == 2


def test_decay_formula_is_change_in_absolute_displacement_per_simulated_hour():
    result = affect_suite.decay(_trajectory())

    assert result["mood_abs_change_per_hour_mean"] == pytest.approx(-0.3)
    assert result["momentary_valence_abs_change_per_hour_mean"] == pytest.approx(
        -0.3375
    )
    assert result["relationship_sentiment_abs_change_per_hour_mean"] == pytest.approx(
        -0.15
    )


def test_recovery_formula_is_elapsed_time_to_first_baseline_tolerance():
    result = affect_suite.recovery(_trajectory(), tolerance=0.25)

    assert result["mood_recovery_hours_mean"] == pytest.approx(2.0)
    assert result["momentary_valence_recovery_hours_mean"] == pytest.approx(2.0)
    assert result["relationship_sentiment_recovery_hours_mean"] == pytest.approx(1.0)
    assert all(result[f"{layer}_n_recovered"] == 1 for layer in LAYERS)


def test_saturation_formula_reports_per_layer_bound_turn_share():
    result = affect_suite.saturation(_trajectory())

    assert result == {
        "mood_at_bound_rate": pytest.approx(1 / 3),
        "momentary_valence_at_bound_rate": pytest.approx(1 / 3),
        "relationship_sentiment_at_bound_rate": pytest.approx(1 / 3),
    }


def test_one_conversation_effect_is_final_layer_value_minus_baseline():
    result = affect_suite.one_conversation_long_term_effect(_trajectory())

    assert result["mood_net_change"] == pytest.approx(0.2)
    assert result["momentary_valence_net_change"] == pytest.approx(0.225)
    assert result["relationship_sentiment_net_change"] == pytest.approx(0.1)
