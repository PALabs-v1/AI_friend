"""Suite-owned BrainBench summaries for one completed cell."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evals.brainbench.stats import SuiteOutcome

SummaryFunction = Callable[[list[SuiteOutcome], Any], dict[str, Any]]

SUMMARY_SCORERS: dict[str, tuple[str, ...]] = {
    "attention": ("repetition_novelty_delta", "interference_collision_rate"),
    "trust": ("hostility_response", "competence_warmth_separation", "background_drift"),
    "proactive": ("initiation_timing", "cooldown_integrity", "initiation_usefulness"),
    "bargein": ("invariant_violation_rates", "outcome_accounting"),
    "resources": ("latency_percentiles", "growth_curves", "unbounded_structures"),
    "memory": ("score_probe",),
    "affect": ("user_valence_reaches_mood", "system2_completion_rate"),
    "personality": ("tier_integrity", "evolution_throughput", "adaptive_drift"),
    "metacognition": (
        "uncertainty_calibration",
        "contradiction_recording",
        "retrieval_freshness",
        "outage_propagation",
    ),
}

_EMPTY_KEYS: dict[str, tuple[str, ...]] = {
    "repetition_novelty_delta": ("repeated_mean", "fresh_mean", "cliffs_delta"),
    "hostility_response": ("mean_trust_delta", "hostile_trust_rise_rate"),
    "user_valence_reaches_mood": (
        "positive_mean_delta",
        "negative_mean_delta",
        "cliffs_delta",
        "n_positive",
        "n_negative",
    ),
}


def _flat(prefix: str, value: Any, target: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flat(f"{prefix}.{key}" if prefix else str(key), item, target)
    elif isinstance(value, (int, float)) or value is None:
        target[prefix] = value


def _attention(outcomes: list[SuiteOutcome], simulation: Any) -> dict[str, Any]:
    from evals.brainbench import attention_suite

    sim, turns, annotations, _probes, _answers = simulation
    result: dict[str, Any] = {}
    try:
        result.update(attention_suite.repetition_novelty_delta(outcomes))
    except ValueError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        for key in _EMPTY_KEYS["repetition_novelty_delta"]:
            result[key] = None
            result[f"none_reason.{key}"] = reason
    result.update(
        attention_suite.interference_collision_rate(sim, outcomes, turns, annotations)
    )
    return result


def _trust(outcomes: list[SuiteOutcome], _simulation: Any) -> dict[str, Any]:
    from evals.brainbench import trust_suite

    result: dict[str, Any] = {}
    for name in SUMMARY_SCORERS["trust"]:
        try:
            value = getattr(trust_suite, name)(outcomes)
        except ValueError as exc:
            keys = {
                "hostility_response": (
                    "n_hostile",
                    "n_masked",
                    "mean_trust_delta",
                    "hostile_trust_rise_rate",
                ),
                "competence_warmth_separation": (
                    "competence_signal",
                    "n_competence_complaints",
                    "n_masked_competence_complaints",
                    "n_competence_thanks",
                    "n_masked_competence_thanks",
                    "warmth_signal",
                    "n_warmth_hostile",
                    "n_masked_warmth_hostile",
                    "n_warmth_positive",
                    "n_masked_warmth_positive",
                    "competence_leak",
                    "n_warmth_only",
                    "n_masked_warmth_only",
                ),
                "background_drift": (
                    "n",
                    "n_masked",
                    "mean_trust_delta",
                    "positive_rate",
                ),
            }[name]
            result.update({f"{name}.{key}": None for key in keys})
            reason = f"{type(exc).__name__}: {exc}"
            for key in keys:
                result[f"none_reason.{name}.{key}"] = reason
            if name == "background_drift":
                for component in ("benevolence", "competence", "integrity"):
                    result[f"background_drift.turns_to_ceiling.{component}"] = None
            continue
        _flat(name, value, result)
        if name == "background_drift":
            for component, turns in value.get("turns_to_ceiling", {}).items():
                result[f"background_drift.turns_to_ceiling.{component}"] = turns
    return result


def _generic(
    suite: str, outcomes: list[SuiteOutcome], _simulation: Any
) -> dict[str, Any]:
    from evals.brainbench import (
        affect_suite,
        bargein_suite,
        memory_suite,
        metacognition_suite,
        personality_suite,
        proactive_suite,
        resources_suite,
    )

    if suite == "memory":
        # memory_suite.score_probe is the public scorer invoked during replay;
        # its numeric results are already preserved per outcome.
        result: dict[str, Any] = {}
        for metric in sorted({key for row in outcomes for key in row.metrics}):
            values = [row.metrics[metric] for row in outcomes if metric in row.metrics]
            result[f"score_probe.{metric}.mean"] = (
                sum(values) / len(values) if values else None
            )
        return result

    module = {
        "proactive": proactive_suite,
        "bargein": bargein_suite,
        "resources": resources_suite,
        "affect": affect_suite,
        "personality": personality_suite,
        "metacognition": metacognition_suite,
        "memory": memory_suite,
    }[suite]
    result: dict[str, Any] = {}
    for name in SUMMARY_SCORERS[suite]:
        try:
            value = getattr(module, name)(outcomes)
        except ValueError as exc:
            for key in _EMPTY_KEYS.get(name, (name,)):
                result[f"{name}.{key}"] = None
            reason = f"{type(exc).__name__}: {exc}"
            for key in _EMPTY_KEYS.get(name, (name,)):
                result[f"none_reason.{name}.{key}"] = reason
            continue
        if suite == "resources":
            if name == "latency_percentiles":
                value = value.get("overall", {})
            elif name == "growth_curves":
                value = {
                    f"{metric}.{field}": curve[field]
                    for metric, curve in value.items()
                    for field in ("first", "last", "slope_per_simulated_day", "n")
                }
            elif name == "unbounded_structures":
                flattened: dict[str, Any] = {}
                for category, structures in value.items():
                    flattened[f"{category}.count"] = len(structures)
                    for structure, details in structures.items():
                        flattened[f"{category}.name.{structure}"] = 1
                        if isinstance(details, dict):
                            for field in (
                                "first",
                                "last",
                                "tail_slope",
                                "tail_slope_rows_per_turn",
                                "cap",
                            ):
                                if details.get(field) is not None:
                                    flattened[f"{category}.{structure}.{field}"] = (
                                        details[field]
                                    )
                value = flattened
        if suite == "metacognition" and name == "uncertainty_calibration":
            reliability = value.pop("reliability", [])
            _flat(name, value, result)
            for index, row in enumerate(reliability):
                _flat(f"{name}.reliability.{index}", row, result)
        else:
            _flat(name, value, result)
    return result


def _for_suite(suite: str) -> SummaryFunction:
    return (
        _attention
        if suite == "attention"
        else _trust
        if suite == "trust"
        else lambda outcomes, simulation: _generic(suite, outcomes, simulation)
    )


SUMMARY_FUNCTIONS: dict[str, SummaryFunction] = {
    suite: _for_suite(suite) for suite in SUMMARY_SCORERS
}


def summarize_cell(
    suite: str, outcomes: list[SuiteOutcome], simulation: Any = None
) -> tuple[dict[str, Any], dict[str, str]]:
    metrics: dict[str, Any] = {}
    reasons: dict[str, str] = {}
    try:
        metrics = SUMMARY_FUNCTIONS[suite](outcomes, simulation)
    except (ValueError, ZeroDivisionError) as exc:
        for key in _EMPTY_KEYS.get(suite, (suite,)):
            metrics[key] = None
            reasons[key] = f"{type(exc).__name__}: {exc}"
    for key in [key for key in metrics if key.startswith("none_reason.")]:
        reasons[key.removeprefix("none_reason.")] = str(metrics.pop(key))
    for key, value in metrics.items():
        if value is None and key not in reasons:
            reasons[key] = "scoring function has no eligible observations"
    return metrics, reasons
