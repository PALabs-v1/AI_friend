"""BrainBench regression gates (06-benchmark-plan.md, "Regression gates").

`test_v2_capabilities_hold_their_bands` is the gate itself: the fixed
architecture_only dev slice in `evals/brainbench/gates.py`, run for real and
checked against `evals/brainbench/baseline/v2_gate_bands.json`. The rest prove
the gate is not vacuous: the band logic, the pinned "unmeasurable at V2" set,
and one real production mutation the gate must catch.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Config
from evals.brainbench import gates

GATE_BUDGET_SECONDS = 60.0


@pytest.fixture(scope="module")
def recorded() -> dict[str, float | None]:
    return {name: m["value"] for name, m in gates.load_bands()["metrics"].items()}


@pytest.fixture(scope="module")
def measured() -> tuple[dict[str, float | None], dict[str, float], float]:
    started = time.perf_counter()
    metrics, budgets = asyncio.run(gates.measure())
    return metrics, budgets, time.perf_counter() - started


def test_v2_capabilities_hold_their_bands(measured, recorded):
    metrics, budgets, wall = measured
    problems = gates.band_violations(metrics, recorded) + gates.budget_violations(
        budgets
    )
    assert not problems, "\n".join(problems)
    # The plan's budget is a design constraint, not a timing assertion that
    # could flake: report it, and fail only far past it.
    print(f"gate slice wall time: {wall:.1f} s (budget {GATE_BUDGET_SECONDS:.0f} s)")
    assert wall < 3 * GATE_BUDGET_SECONDS


def test_recorded_bands_cover_exactly_the_gated_metrics(recorded):
    assert set(recorded) == {m.name for m in gates.GATE_METRICS}
    bands = gates.load_bands()
    assert bands["tolerance"] == {"relative": gates.REL_TOL, "absolute": gates.ABS_TOL}
    assert bands["slice"]["personas"] == [list(p) for p in gates.SLICE]
    assert not bands["dirty"], "bands must be recorded from a committed tree"


def test_unmeasurable_at_v2_is_pinned(recorded):
    # A metric that is None at V2 is not gated. That set may only change on
    # purpose: when a workstream makes one measurable, re-record and update
    # UNMEASURABLE_AT_V2 in the same commit.
    assert {name for name, value in recorded.items() if value is None} == set(
        gates.UNMEASURABLE_AT_V2
    )


def _metric(direction: str) -> gates.GateMetric:
    return gates.GateMetric("m", direction, "test")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("direction", "base", "now", "fails"),
    [
        ("higher", 0.5, 0.6, False),  # improvement
        ("higher", 0.5, 0.496, False),  # inside the 1% band
        ("higher", 0.5, 0.49, True),
        ("lower", 0.5, 0.2, False),
        ("lower", 0.5, 0.504, False),
        ("lower", 0.5, 0.51, True),
        ("lower", 0.0, 1e-7, False),  # absolute floor for a zero band
        ("lower", 0.0, 1e-3, True),
        ("toward_zero", 0.04, -0.03, False),
        ("toward_zero", 0.04, -0.05, True),
        ("toward_zero", -0.04, 0.05, True),
    ],
)
def test_band_direction_and_tolerance(direction, base, now, fails):
    with patch.object(gates, "GATE_METRICS", (_metric(direction),)):
        assert bool(gates.band_violations({"m": now}, {"m": base})) is fails


def test_losing_a_measurable_metric_fails_and_gaining_one_passes():
    with patch.object(gates, "GATE_METRICS", (_metric("lower"),)):
        assert gates.band_violations({"m": None}, {"m": 0.3})
        assert not gates.band_violations({"m": 0.3}, {"m": None})
        assert gates.band_violations({"m": 0.3}, {})  # no band recorded


def test_budget_ceiling():
    assert not gates.budget_violations({"resources.turn_latency_p95_ms": 10.0})
    assert gates.budget_violations({"resources.turn_latency_p95_ms": 900.0})


def test_gate_catches_a_real_trust_regression(tmp_path: Path, recorded):
    # Tripling the persona's trust change rate is a one-line retune anyone
    # could make; it pins trust at the ceiling sooner, so the gate must fail
    # on the trust metrics it guards (F-010).
    with patch.object(Config, "PSYCH_DELTA", Config.PSYCH_DELTA * 3):
        mutated = asyncio.run(gates._trust(tmp_path))
    problems = gates.band_violations({**recorded, **mutated}, recorded)
    assert problems, "the gate did not notice trust saturating 3x faster"
    assert all(p.startswith("trust.") for p in problems), problems
    assert any("turns_to_ceiling" in p for p in problems), problems
