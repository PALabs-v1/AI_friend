"""Local Phase 04 micro-benchmarks (BM-LOC-P4-01, BM-LOC-P4-02).

Implements local benchmarks per orchestration/archive/phase_04/BENCHMARK_PLAN.md.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.cognitive.calibration import (
    CapabilityLimitationModel,
    DomainCalibration,
    MetacognitiveDirective,
)
from app.state.person_model import PersonModel


def run_bm_loc_p4_01() -> dict[str, Any]:
    """BM-LOC-P4-01: Calibration and Metacognitive Directive Evaluation Latency.

    10,000 sequential evaluations through DomainCalibration and CapabilityLimitationModel.
    Target: Mean latency < 0.05 ms (50 us).
    """
    print("\n--- Running BM-LOC-P4-01: Calibration & Directive Evaluation Latency ---")
    num_iterations = 10000

    calibration_model = CapabilityLimitationModel(
        known_limitations=[
            "execute arbitrary shell",
            "predict stock prices",
            "diagnose cancer",
        ],
        domain_calibrations={
            "general": DomainCalibration(
                domain="general", sample_count=10, brier_score=0.15
            ),
            "math": DomainCalibration(domain="math", sample_count=50, brier_score=0.08),
            "memory": DomainCalibration(
                domain="memory", sample_count=20, brier_score=0.25
            ),
        },
    )

    domains = ["general", "math", "memory"]
    queries = [
        "What is 2+2?",
        "Can you execute arbitrary shell script?",
        "Do you remember my birthday?",
        "Tell me a joke.",
        "Predict stock prices for next week.",
    ]

    latencies_us: list[float] = []

    for i in range(num_iterations):
        domain = domains[i % len(domains)]
        query = queries[i % len(queries)]
        raw_conf = 0.2 + (i % 80) / 100.0

        t0 = time.perf_counter_ns()
        directive, cal_conf = calibration_model.evaluate_directive(
            domain, raw_conf, query=query
        )
        t1 = time.perf_counter_ns()

        latencies_us.append((t1 - t0) / 1000.0)
        assert isinstance(directive, MetacognitiveDirective)
        assert 0.0 <= cal_conf <= 1.0

    latencies_us.sort()
    n = len(latencies_us)
    mean_us = sum(latencies_us) / n
    p50_us = latencies_us[int(n * 0.50)]
    p95_us = latencies_us[int(n * 0.95)]
    p99_us = latencies_us[int(n * 0.99)]
    min_us = latencies_us[0]
    max_us = latencies_us[-1]

    verdict = "PASS" if mean_us < 50.0 else "FAIL"

    print(f"Iterations: {num_iterations}")
    print(
        f"Mean: {mean_us:.3f} us | p50: {p50_us:.3f} us | p95: {p95_us:.3f} us | p99: {p99_us:.3f} us"
    )
    print(f"Verdict: {verdict} (Target: mean < 50.0 us)")

    return {
        "benchmark_id": "BM-LOC-P4-01",
        "title": "Calibration & Directive Evaluation Latency",
        "iterations": num_iterations,
        "mean_us": round(mean_us, 3),
        "p50_us": round(p50_us, 3),
        "p95_us": round(p95_us, 3),
        "p99_us": round(p99_us, 3),
        "min_us": round(min_us, 3),
        "max_us": round(max_us, 3),
        "target_mean_us": "< 50.0",
        "verdict": verdict,
    }


def run_bm_loc_p4_02() -> dict[str, Any]:
    """BM-LOC-P4-02: Multi-Person Privacy Isolation Verification.

    1,000 synthetic disclosure queries across 10 simulated persons with private vs public facts.
    Target: 0.0% cross-person private leakage.
    """
    print("\n--- Running BM-LOC-P4-02: Multi-Person Privacy Isolation ---")
    num_queries = 1000
    num_persons = 10

    persons = [
        PersonModel(
            person_id=f"person_{i}",
            name=f"User_{i}",
            current_knowledge={f"secret_{i}": f"Private details of {i}"},
        )
        for i in range(num_persons)
    ]

    leakage_count = 0

    for i in range(num_queries):
        target_idx = i % num_persons
        owner_idx = (i * 3 + 1) % num_persons
        is_private = (i % 2) == 0

        target_person = persons[target_idx]
        owner_person_id = persons[owner_idx].person_id

        allowed = target_person.can_disclose(
            target_person_id=target_person.person_id,
            fact_owner_id=owner_person_id,
            is_private=is_private,
        )

        if is_private and (owner_person_id != target_person.person_id):
            if allowed:
                leakage_count += 1
        elif is_private and (owner_person_id == target_person.person_id):
            assert allowed, "Self-disclosure of private facts must be allowed"
        elif not is_private:
            assert allowed, "Public facts must always be disclosable"

    leakage_rate = (leakage_count / num_queries) * 100.0
    verdict = "PASS" if leakage_count == 0 else "FAIL"

    print(f"Total queries: {num_queries} across {num_persons} persons")
    print(f"Leakage occurrences: {leakage_count} ({leakage_rate:.2f}%)")
    print(f"Verdict: {verdict} (Target: strictly 0.0%)")

    return {
        "benchmark_id": "BM-LOC-P4-02",
        "title": "Multi-Person Privacy Isolation Verification",
        "queries": num_queries,
        "persons": num_persons,
        "leakage_count": leakage_count,
        "leakage_rate_pct": round(leakage_rate, 4),
        "target_leakage_rate": "0.0%",
        "verdict": verdict,
    }


def main():
    print("=================================================================")
    print("PHASE 04 LOCAL BENCHMARKS (Apple Silicon)")
    print("=================================================================")

    r1 = run_bm_loc_p4_01()
    r2 = run_bm_loc_p4_02()

    results = {
        "BM-LOC-P4-01": r1,
        "BM-LOC-P4-02": r2,
    }

    out_dir = os.path.abspath(
        os.path.join(BACKEND_ROOT, "..", "orchestration", "PHASE_04")
    )
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "local_benchmark_results.json")

    with open(out_file, "w", encoding="ascii") as f:
        json.dump(results, f, indent=2)

    print("\n=================================================================")
    print(f"Saved benchmark results to {out_file}")
    all_pass = all(r["verdict"] == "PASS" for r in results.values())
    print(f"OVERALL LOCAL BENCHMARK VERDICT: {'PASS' if all_pass else 'FAIL'}")
    print("=================================================================")


if __name__ == "__main__":
    main()
