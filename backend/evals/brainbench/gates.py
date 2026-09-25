"""BrainBench regression gates: a fixed dev slice, checked against V2's bands.

06-benchmark-plan.md: "a small fixed dev-seed slice runs in under 60 seconds
and fails if any capability drops below its recorded V2 baseline band". This
is what stands between a Phase 7 workstream improving one capability and
silently breaking another to do it.

What is gated, and what is not:

- Only the ``architecture_only`` suites run here (attention, trust,
  proactive, barge-in, resources): they need no model, so the slice is
  deterministic and fast. Memory, affect, personality and metacognition are
  ``llm_augmented`` only (DR-037) and are periodic evals, not gates. Memory
  retrieval quality has its own model-free gate already:
  ``tests/test_cognitive_bench.py::test_gate_hybrid_retrieval_beats_v1_by_a_wide_margin``.
- Each gated metric declares a direction. ``higher`` / ``lower`` fail only when
  the value moves the wrong way past the band; ``toward_zero`` fails when its
  magnitude grows. Moving the right way always passes, so a workstream that
  fixes, say, trust rising under hostility (A-3) clears the gate without
  re-recording anything. The bands stay at V2 on purpose: they are the floor
  every later change is measured against, not a moving target.
- Metrics with no defensible "better" direction (raw novelty level, mood
  level, vocabulary size) are not gated. Neither are timings, except one
  coarse absolute budget (``BUDGETS``) that only catches an order-of-magnitude
  slowdown: wall-clock numbers are not reproducible across machines, and a
  gate must never be flaky.

Every embedding in the slice is ``resources_suite.architecture_embedding``
(deterministic, production dimension), so the slice touches no network.

Re-recording the bands is a deliberate act, never a side effect of a test:
``python -m evals.brainbench.gates --record`` from ``backend/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.brainbench import (
    attention_suite,
    bargein_suite,
    proactive_suite,
    resources_suite,
    trust_suite,
)
from evals.brainbench.adapters import build_cognitive_service
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build

BANDS_PATH = Path(__file__).parent / "baseline" / "v2_gate_bands.json"

# Three contrasting archetypes on consecutive dev seeds. `emotional_caregiver`
# and `volatile_creative` carry the hostile and warm robot-directed turns the
# trust metrics need; `steady_professional` is the low-affect control.
SLICE: tuple[tuple[int, str], ...] = (
    (1000, "steady_professional"),
    (1001, "volatile_creative"),
    (1002, "emotional_caregiver"),
)
HORIZON = "1m"
# Proactive ticks once per simulated minute through every idle gap, so a month
# costs ~15 s per persona per arm; a week keeps the whole slice under a minute
# and still spans several >2 h idle gaps per persona.
PROACTIVE_HORIZON = "1w"
PROACTIVE_ARMS: tuple[proactive_suite.SyncArm, ...] = ("broadcast", "none")
BARGEIN_SCENARIOS_PER_FAMILY = 3

# Deterministic metrics can only differ by float noise across platforms (the
# Phase 3 baseline was byte-identical across arm64 and x86_64), so the band is
# narrow: any real behaviour change moves these metrics by far more than 1%.
REL_TOL = 0.01
ABS_TOL = 1e-6

Direction = Literal["higher", "lower", "toward_zero"]


@dataclass(frozen=True)
class GateMetric:
    name: str
    direction: Direction
    why: str


GATE_METRICS: tuple[GateMetric, ...] = (
    GateMetric(
        "attention.repetition_cliffs_delta",
        "higher",
        "fresh turns should score more novel than repeated ones",
    ),
    GateMetric(
        "attention.interference_collision_rate",
        "lower",
        "distinct same-kind events scored as near-duplicates",
    ),
    GateMetric(
        "trust.hostile_trust_rise_rate",
        "lower",
        "A-3: trust should not rise on hostile robot-directed turns",
    ),
    GateMetric(
        "trust.hostile_mean_trust_delta",
        "lower",
        "A-3: hostility should not raise trust on average",
    ),
    GateMetric(
        "trust.background_mean_trust_delta",
        "toward_zero",
        "trust should not drift on turns that say nothing about the agent",
    ),
    *(
        GateMetric(
            f"trust.{component}_turns_to_ceiling",
            "higher",
            f"F-010: {component} pins at the 1.0 clamp and trust goes deaf",
        )
        for component in ("integrity", "competence")
    ),
    GateMetric(
        "trust.saturated_fraction",
        "lower",
        "share of turns with a trust component pinned at its ceiling",
    ),
    GateMetric(
        "trust.competence_leak",
        "toward_zero",
        "warmth-only turns should not move competence",
    ),
    *(
        metric
        for arm in PROACTIVE_ARMS
        for metric in (
            GateMetric(
                f"proactive.{arm}.cooldown_violations_per_day",
                "lower",
                "V-4: outreach inside the cooldown window",
            ),
            GateMetric(
                f"proactive.{arm}.annoyance_per_day",
                "lower",
                "outreach unrelated to any upcoming commitment",
            ),
            GateMetric(
                f"proactive.{arm}.night_fraction",
                "lower",
                "share of outreach at night",
            ),
            GateMetric(
                f"proactive.{arm}.collision_rate",
                "lower",
                "outreach colliding with a user turn",
            ),
        )
    ),
    *(
        GateMetric(
            f"bargein.{invariant}_violation_rate",
            "lower",
            f"barge-in lifecycle invariant `{invariant}` (ADR-003)",
        )
        for invariant in bargein_suite._CLAIMED
    ),
    GateMetric(
        "bargein.zero_terminal_reply_rate",
        "lower",
        "replies that never reached a terminal outcome",
    ),
    GateMetric(
        "resources.background_task_timeouts",
        "lower",
        "background work that did not finish within its budget",
    ),
    GateMetric(
        "resources.still_increasing_structures",
        "lower",
        "in-memory structures with no cap still growing at the end",
    ),
    GateMetric(
        "resources.workspace_transitions_per_turn",
        "lower",
        "rows added per turn to a SQLite table nothing prunes",
    ),
)

# Gated metrics V2 cannot produce on this slice, and why. They stay in
# GATE_METRICS so a workstream that makes them measurable sees them appear;
# the test pins this set so it can only change on purpose.
UNMEASURABLE_AT_V2: dict[str, str] = {
    "trust.hostile_trust_rise_rate": (
        "F-010: every hostile turn in the slice lands after the ceiling, so "
        "each one is masked"
    ),
    "trust.hostile_mean_trust_delta": "F-010: same masking as the rise rate",
    "trust.competence_leak": (
        "F-010: competence is pinned at 1.0 on every warmth-only turn"
    ),
}

# Absolute ceilings for non-deterministic numbers. Architecture overhead is
# ~3.5 ms per turn on an M5; 250 ms only trips on a real regression, never on
# a slow CI runner.
BUDGETS: dict[str, float] = {"resources.turn_latency_p95_ms": 250.0}


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _values(outcomes: list[SuiteOutcome], metric: str) -> list[float]:
    return [o.metrics[metric] for o in outcomes if o.metrics.get(metric) is not None]


def _service(run_dir: Path):
    service = build_cognitive_service("architecture_only", run_dir)
    service.memory_store.get_embedding = resources_suite.architecture_embedding
    return service


async def _with_service(run_dir: Path, body: Callable[[Any], Any]):
    service = _service(run_dir)
    try:
        return await body(service)
    finally:
        service.cognitive.close()


async def _attention(root: Path) -> dict[str, float | None]:
    repeated_and_fresh: list[SuiteOutcome] = []
    pairs = collisions = 0
    for seed, archetype in SLICE:
        simulation = build(seed, archetype, HORIZON)
        sim, turns, annotations, _probes, _answers = simulation
        outcomes = await _with_service(
            root / f"attention-{seed}",
            lambda service, simulation=simulation, seed=seed: (
                attention_suite.run_attention_suite(
                    simulation, service, persona_seed=seed
                )
            ),
        )
        repeated_and_fresh.extend(outcomes)
        rate = attention_suite.interference_collision_rate(
            sim, outcomes, turns, annotations
        )
        pairs += rate["pairs_checked"]
        collisions += rate["collisions"]
    delta = attention_suite.repetition_novelty_delta(repeated_and_fresh)
    return {
        "attention.repetition_cliffs_delta": delta["cliffs_delta"],
        "attention.interference_collision_rate": collisions / pairs if pairs else None,
    }


async def _trust(root: Path) -> dict[str, float | None]:
    per_persona: list[list[SuiteOutcome]] = []
    for seed, archetype in SLICE:
        simulation = build(seed, archetype, HORIZON)
        per_persona.append(
            await _with_service(
                root / f"trust-{seed}",
                lambda service, simulation=simulation, seed=seed: (
                    trust_suite.run_trust_suite(simulation, service, persona_seed=seed)
                ),
            )
        )
    pooled = [o for outcomes in per_persona for o in outcomes]
    hostility = trust_suite.hostility_response(pooled)
    separation = trust_suite.competence_warmth_separation(pooled)
    # background_drift reads turns-to-ceiling from outcome order, so it runs
    # per persona; the gated numbers are then pooled by turn count.
    drifts = [trust_suite.background_drift(outcomes) for outcomes in per_persona]
    # Pool as total delta over unmasked background turns, not a mean of
    # per-persona means: a persona with 3 background turns must not weigh as
    # much as one with 300.
    total_background_delta = sum(
        d["total_trust_delta"] for d in drifts if d["total_trust_delta"] is not None
    )
    n_background = sum(d["n"] - d["n_masked"] for d in drifts)
    saturated = sum(
        d["saturated_fraction"] * len(outcomes)
        for d, outcomes in zip(drifts, per_persona, strict=True)
    )
    # A component that never hits the clamp survived every turn of its run:
    # count it as len(run) + 1, so "never" beats any turn that did. The slice
    # value is the earliest persona, the one the gate must protect.
    ceilings = {
        component: min(
            d["turns_to_ceiling"][component] or len(outcomes) + 1
            for d, outcomes in zip(drifts, per_persona, strict=True)
        )
        for component in ("integrity", "competence")
    }
    return {
        "trust.integrity_turns_to_ceiling": float(ceilings["integrity"]),
        "trust.competence_turns_to_ceiling": float(ceilings["competence"]),
        "trust.hostile_trust_rise_rate": hostility["hostile_trust_rise_rate"],
        "trust.hostile_mean_trust_delta": hostility["mean_trust_delta"],
        "trust.background_mean_trust_delta": (
            total_background_delta / n_background if n_background else None
        ),
        "trust.saturated_fraction": saturated / len(pooled) if pooled else None,
        "trust.competence_leak": separation["competence_leak"],
    }


async def _proactive(root: Path) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for arm in PROACTIVE_ARMS:
        pooled: list[SuiteOutcome] = []
        for seed, archetype in SLICE:
            simulation = build(seed, archetype, PROACTIVE_HORIZON)
            pooled.extend(
                await _with_service(
                    root / f"proactive-{arm}-{seed}",
                    lambda service, simulation=simulation, seed=seed, arm=arm: (
                        proactive_suite.run_proactive_suite(
                            simulation,
                            service,
                            persona_seed=seed,
                            sync=arm,
                            run_dir=root / f"proactive-{arm}-{seed}-state",
                        )
                    ),
                )
            )
        days = sum(_values(pooled, "gap_hours")) / 24
        timing = proactive_suite.initiation_timing(pooled)
        cooldown = proactive_suite.cooldown_integrity(pooled)
        usefulness = proactive_suite.initiation_usefulness(pooled)
        result |= {
            f"proactive.{arm}.cooldown_violations_per_day": (
                cooldown["cooldown_violations"] / days if days else None
            ),
            f"proactive.{arm}.annoyance_per_day": (
                usefulness["annoyance_count"] / days if days else None
            ),
            f"proactive.{arm}.night_fraction": timing["night_fraction"],
            f"proactive.{arm}.collision_rate": timing["collision_rate"],
        }
    return result


async def _bargein() -> dict[str, float | None]:
    pooled: list[SuiteOutcome] = []
    for seed, archetype in SLICE:
        pooled.extend(
            await asyncio.to_thread(
                bargein_suite.run_bargein_suite_for_seed,
                seed,
                archetype,
                HORIZON,
                n_scenarios_per_family=BARGEIN_SCENARIOS_PER_FAMILY,
            )
        )
    result: dict[str, float | None] = {
        f"bargein.{invariant}_violation_rate": _mean(
            _values(pooled, f"{invariant}_violation")
        )
        for invariant in bargein_suite._CLAIMED
    }
    replies = sum(_values(pooled, "terminal_outcome_replies_eligible"))
    zero = sum(_values(pooled, "replies_with_zero_terminal_outcomes"))
    result["bargein.zero_terminal_reply_rate"] = zero / replies if replies else None
    return result


async def _resources(root: Path) -> tuple[dict[str, float | None], dict[str, float]]:
    timeouts = 0.0
    still_increasing = 0
    transitions = turns = 0.0
    latencies: list[float] = []
    for seed, archetype in SLICE:
        simulation = build(seed, archetype, HORIZON)
        outcomes = await _with_service(
            root / f"resources-{seed}",
            lambda service, simulation=simulation, seed=seed: (
                resources_suite.run_resources_suite(
                    simulation, service, persona_seed=seed
                )
            ),
        )
        timeouts += sum(_values(outcomes, "background_task_timeouts"))
        still_increasing += len(
            resources_suite.unbounded_structures(outcomes).get("still_increasing", {})
        )
        samples = [o for o in outcomes if "sample" in o.categories]
        final = samples[-1].metrics
        transitions += final.get("rows:workspace.db:workspace_transitions", 0.0)
        turns += len(simulation[1])
        latencies.extend(_values(outcomes, "turn_latency_ms"))
    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1)]
    return (
        {
            "resources.background_task_timeouts": timeouts,
            "resources.still_increasing_structures": float(still_increasing),
            "resources.workspace_transitions_per_turn": (
                transitions / turns if turns else None
            ),
        },
        {"resources.turn_latency_p95_ms": p95},
    )


async def measure() -> tuple[dict[str, float | None], dict[str, float]]:
    """Run the fixed slice once; returns (gated metrics, budgeted numbers)."""
    with tempfile.TemporaryDirectory(prefix="brainbench-gates-") as tmp:
        root = Path(tmp)
        metrics: dict[str, float | None] = {}
        metrics |= await _attention(root)
        metrics |= await _trust(root)
        metrics |= await _proactive(root)
        metrics |= await _bargein()
        gated, budgets = await _resources(root)
        metrics |= gated
    expected = {m.name for m in GATE_METRICS}
    if set(metrics) != expected:
        raise RuntimeError(
            f"gate slice produced {sorted(set(metrics) ^ expected)} "
            "outside/missing from GATE_METRICS"
        )
    return metrics, budgets


def band_violations(
    current: dict[str, float | None], recorded: dict[str, float | None]
) -> list[str]:
    """Every gated metric that moved the wrong way past its V2 band."""
    problems: list[str] = []
    for metric in GATE_METRICS:
        if metric.name not in recorded:
            problems.append(f"{metric.name}: no recorded V2 band (re-record)")
            continue
        base, now = recorded[metric.name], current.get(metric.name)
        if base is None:
            # Not measurable on the V2 slice; recorded as such, never as 0.
            continue
        if now is None:
            problems.append(f"{metric.name}: was {base:.6g} at V2, now unmeasurable")
            continue
        tol = max(ABS_TOL, REL_TOL * abs(base))
        worse = {
            "higher": now < base - tol,
            "lower": now > base + tol,
            "toward_zero": abs(now) > abs(base) + tol,
        }[metric.direction]
        if worse:
            problems.append(
                f"{metric.name}: {now:.6g} vs V2 {base:.6g} "
                f"({metric.direction} is better; {metric.why})"
            )
    return problems


def budget_violations(budgets: dict[str, float]) -> list[str]:
    return [
        f"{name}: {budgets[name]:.1f} > ceiling {ceiling:.1f}"
        for name, ceiling in BUDGETS.items()
        if budgets[name] > ceiling
    ]


def load_bands(path: Path = BANDS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def record(path: Path = BANDS_PATH) -> dict[str, Any]:
    started = time.perf_counter()
    metrics, budgets = asyncio.run(measure())
    payload = {
        "description": "V2 BrainBench gate bands (evals/brainbench/gates.py)",
        "git_sha": _git("rev-parse", "HEAD"),
        # Writing this file is what makes the tree dirty; anything else is not.
        "dirty": any(
            not line.endswith(BANDS_PATH.name)
            for line in _git("status", "--porcelain", "--", ".").splitlines()
        ),
        "slice": {
            "personas": [list(p) for p in SLICE],
            "horizon": HORIZON,
            "proactive_horizon": PROACTIVE_HORIZON,
            "proactive_arms": list(PROACTIVE_ARMS),
            "bargein_scenarios_per_family": BARGEIN_SCENARIOS_PER_FAMILY,
        },
        "tolerance": {"relative": REL_TOL, "absolute": ABS_TOL},
        "metrics": {
            m.name: {"value": metrics[m.name], "direction": m.direction, "why": m.why}
            for m in GATE_METRICS
        },
        "observed_budgets": budgets,
        "wall_seconds": round(time.perf_counter() - started, 1),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.brainbench.gates")
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"re-record the V2 bands into {BANDS_PATH}",
    )
    args = parser.parse_args(argv)
    if args.record:
        payload = record()
        print(json.dumps(payload["metrics"], indent=2))
        print(f"wrote {BANDS_PATH} in {payload['wall_seconds']} s")
        return 0
    metrics, budgets = asyncio.run(measure())
    recorded = {k: v["value"] for k, v in load_bands()["metrics"].items()}
    problems = band_violations(metrics, recorded) + budget_violations(budgets)
    for name in sorted(metrics):
        print(f"{name}: {metrics[name]} (V2 {recorded.get(name)})")
    for problem in problems:
        print("FAIL", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
