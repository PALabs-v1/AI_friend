#!/usr/bin/env python3
"""Headline tables for a BrainBench baseline, straight from its report.json files.

    python3 scripts/research/baseline_digest.py docs/brain-research-v3/results/home-gpu/v2base

Reads <root>/{A,B,C}/report.json (the output of `python -m evals.brainbench
report`) and prints Markdown. Nothing here is estimated: every number is a
field of a report, or (per-day rates) pooled exactly as evals/brainbench/
gates.py pools them, total events over total simulated days, so the numbers
compare directly with the gate bands and with the workstream ADRs.

Cells are persona seeds (12 per group in the architecture_only runs). A
summary value is shown as `mean (min-max)` across cells; a group metric as
`mean [95% CI]`, CI clustered by persona.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HORIZON_ORDER = ["1w", "1m", "6m", "1y", "3y", "10y"]


def load(root: Path, run: str) -> dict:
    return json.loads((root / run / "report.json").read_text())


def horizon_key(group: str) -> int:
    h = group.rsplit("/", 1)[-1]
    return HORIZON_ORDER.index(h) if h in HORIZON_ORDER else 99


def fmt(x: float | None, digits: int = 3) -> str:
    if x is None:
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:.{digits}f}"


def summ(
    report: dict, group: str, key: str, digits: int = 3, spread: bool = True
) -> str:
    v = report["summaries"].get(group, {}).get(key)
    if not v or v.get("mean") is None:
        return "n/a"
    if not spread or v.get("n_cells", 1) <= 1 or v["min"] == v["max"]:
        return fmt(v["mean"], digits)
    return f"{fmt(v['mean'], digits)} ({fmt(v['min'], digits)}-{fmt(v['max'], digits)})"


def summ_mean(report: dict, group: str, key: str) -> float | None:
    v = report["summaries"].get(group, {}).get(key)
    return None if not v else v.get("mean")


def metric(report: dict, group: str, key: str, digits: int = 3) -> str:
    v = report["groups"].get(group, {}).get("metrics", {}).get(key)
    if not v or v.get("mean") is None:
        return "n/a"
    lo, hi = v.get("ci95") or (None, None)
    if lo is None or lo == hi:
        return fmt(v["mean"], digits)
    return f"{fmt(v['mean'], digits)} [{fmt(lo, digits)}, {fmt(hi, digits)}]"


def per_day(report: dict, group: str, summary_key: str) -> str:
    """Pooled events per simulated day, as gates.py computes it."""
    s = report["summaries"].get(group, {}).get(summary_key)
    gap = report["groups"].get(group, {}).get("metrics", {}).get("gap_hours")
    if not s or not gap or not gap.get("n") or s.get("mean") is None:
        return "n/a"
    events = s["mean"] * s["n_cells"]
    days = gap["mean"] * gap["n"] / 24
    return fmt(events / days if days else None, 3)


def groups(report: dict, prefix: str) -> list[str]:
    return sorted(
        (g for g in report["groups"] if g.startswith(prefix)),
        key=lambda g: (g.rsplit("/", 1)[0], horizon_key(g)),
    )


def table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def proactive(b: dict) -> str:
    rows = []
    for g in groups(b, "proactive/"):
        _, sync, order, h = g.split("/")
        rows.append(
            [
                f"{sync}/{order}",
                h,
                summ(b, g, "initiation_timing.initiations_per_simulated_day", 2),
                summ(b, g, "initiation_usefulness.useful_rate"),
                per_day(b, g, "initiation_usefulness.annoyance_count"),
                per_day(b, g, "cooldown_integrity.cooldown_violations"),
                summ(
                    b,
                    g,
                    "cooldown_integrity.last_proactive_attempt_resets",
                    1,
                    spread=False,
                ),
                summ(b, g, "initiation_timing.collision_rate", spread=False),
                summ(b, g, "initiation_timing.night_fraction", spread=False),
            ]
        )
    return table(
        [
            "sync/tick order",
            "horizon",
            "initiations/day",
            "useful rate",
            "annoyance/day",
            "cooldown violations/day",
            "cooldown resets",
            "collision rate",
            "night fraction",
        ],
        rows,
    )


def bargein(b: dict) -> str:
    rows = []
    for g in groups(b, "bargein/"):
        rows.append(
            [
                g.rsplit("/", 1)[-1],
                metric(b, g, "replies_with_zero_terminal_outcomes"),
                metric(b, g, "started_replies_without_terminal"),
                metric(b, g, "history_matches_heard_violation"),
                metric(b, g, "terminal_outcomes_per_reply_violation"),
                metric(b, g, "hung_violation"),
                metric(b, g, "stale_stop_applied_violation"),
                metric(b, g, "current_turn_harmed_violation"),
            ]
        )
    return table(
        [
            "horizon",
            "zero-terminal replies / scenario",
            "started replies without terminal / scenario",
            "history != heard",
            "terminal-count violation",
            "hung",
            "stale stop applied",
            "current turn harmed",
        ],
        rows,
    )


def attention(a: dict) -> str:
    rows = [
        [
            g.rsplit("/", 1)[-1],
            summ(a, g, "collision_rate"),
            summ(a, g, "cliffs_delta"),
            summ(a, g, "fresh_mean"),
            summ(a, g, "repeated_mean"),
        ]
        for g in groups(a, "attention/")
    ]
    return table(
        [
            "horizon",
            "interference collision rate",
            "repetition Cliff's delta",
            "fresh novelty",
            "repeated novelty",
        ],
        rows,
    )


def trust(a: dict) -> str:
    rows = [
        [
            g.rsplit("/", 1)[-1],
            (
                f"{summ(a, g, 'hostility_response.n_hostile', 1, spread=False)} / "
                f"{summ(a, g, 'hostility_response.n_masked', 1, spread=False)}"
            ),
            summ(a, g, "hostility_response.hostile_trust_rise_rate"),
            summ(a, g, "background_drift.mean_trust_delta", 4),
            summ(a, g, "background_drift.saturated_fraction"),
            summ(a, g, "background_drift.turns_to_ceiling.competence", 1),
            summ(a, g, "background_drift.turns_to_ceiling.integrity", 1),
            summ(a, g, "competence_warmth_separation.competence_leak", 4),
        ]
        for g in groups(a, "trust/")
    ]
    return table(
        [
            "horizon",
            "hostile turns / masked by ceiling",
            "hostile trust-rise rate",
            "background mean trust delta",
            "saturated fraction",
            "turns to competence ceiling",
            "turns to integrity ceiling",
            "competence leak (warmth-only)",
        ],
        rows,
    )


def resources(r: dict, prefix: str = "resources/") -> str:
    rows = []
    for g in groups(r, prefix):
        rows.append(
            [
                g.rsplit("/", 1)[-1],
                summ(
                    r,
                    g,
                    "latency_percentiles.foreground_latency_ms.p50",
                    1,
                    spread=False,
                ),
                summ(
                    r,
                    g,
                    "latency_percentiles.foreground_latency_ms.p95",
                    1,
                    spread=False,
                ),
                summ(
                    r,
                    g,
                    "latency_percentiles.foreground_latency_ms.p99",
                    1,
                    spread=False,
                ),
                summ(r, g, "latency_percentiles.turn_latency_ms.p95", 1, spread=False),
                summ(r, g, "growth_curves.rss_peak_mib.last", 0),
                summ(r, g, "growth_curves.rows:memory.db:memories.last", 0),
                summ(r, g, "growth_curves.db_bytes:memory.db.last", 0, spread=False),
                summ(
                    r,
                    g,
                    "growth_curves.rows:workspace.db:workspace_transitions.last",
                    0,
                    spread=False,
                ),
                summ(
                    r, g, "unbounded_structures.still_increasing.count", 1, spread=False
                ),
            ]
        )
    return table(
        [
            "horizon",
            "foreground p50 ms",
            "foreground p95 ms",
            "foreground p99 ms",
            "turn p95 ms",
            "peak RSS MiB",
            "memories (rows)",
            "memory.db bytes",
            "workspace_transitions rows",
            "uncapped structures still growing",
        ],
        rows,
    )


def llm_reference(c: dict) -> str:
    picks = [
        ("memory/default/1m", "score_probe.hit@5.mean", "memory hit@5"),
        ("memory/default/1m", "score_probe.mrr.mean", "memory MRR"),
        ("memory/default/1m", "score_probe.abstained.mean", "memory abstained"),
        (
            "affect/default/1m",
            "user_valence_reaches_mood.cliffs_delta",
            "user valence -> mood (Cliff's delta)",
        ),
        (
            "affect/default/1m",
            "user_valence_reaches_mood.positive_mean_delta",
            "mood delta after positive user turns",
        ),
        (
            "affect/default/1m",
            "user_valence_reaches_mood.negative_mean_delta",
            "mood delta after negative user turns",
        ),
        (
            "affect/default/1m",
            "system2_completion_rate.completion_rate",
            "System 2 completion rate",
        ),
    ]
    rows = []
    for g, k, label in picks:
        rows.append([label, summ(c, g, k, spread=False), g])
    for g in sorted(x for x in c["summaries"] if x.startswith("personality/")):
        variant = g.split("/")[1]
        for k, label in [
            ("evolution_throughput.reflections_completed", "reflections completed"),
            ("evolution_throughput.applied", "personality changes applied"),
            (
                "evolution_throughput.queued",
                "personality changes queued, never applied",
            ),
            ("tier_integrity.immutable_violations", "immutable-tier violations"),
            (
                "tier_integrity.constitutional_violations",
                "constitutional-tier violations",
            ),
        ]:
            rows.append([f"{label} ({variant})", summ(c, g, k, spread=False), g])
    meta = sorted(x for x in c["summaries"] if x.startswith("metacognition/"))
    for g in meta:
        variant = g.split("/")[1]
        for k, label in [
            ("outage_propagation.failure_visible_rate", "outage visible to the brain"),
            ("outage_propagation.turn_error_rate", "turn error rate"),
            (
                "retrieval_freshness.surfaced_carried_over_fraction",
                "surfaced memories carried over",
            ),
            ("uncertainty_calibration.answerability_auroc", "answerability AUROC"),
            (
                "contradiction_recording.tagged_recorded_rate",
                "tagged contradictions recorded",
            ),
        ]:
            if summ_mean(c, g, k) is not None:
                rows.append([f"{label} ({variant})", summ(c, g, k, spread=False), g])
    return table(["metric", "value", "group"], rows)


def main() -> None:
    root = Path(sys.argv[1])
    a, b, c = load(root, "A"), load(root, "B"), load(root, "C")
    errors = {name: len(r.get("errors", [])) for name, r in zip("ABC", (a, b, c))}
    print(f"Errored cells: {errors}\n")
    for title, body in [
        ("Proactive initiation (run B)", proactive(b)),
        ("Barge-in lifecycle (run B)", bargein(b)),
        ("Attention (run A)", attention(a)),
        ("Trust (run A)", trust(a)),
        ("Resources, architecture_only (run A)", resources(a)),
        (
            "llm_augmented reference (run C: steady_professional, seed 1000, 1m)",
            llm_reference(c),
        ),
        ("Resources, llm_augmented (run C)", resources(c)),
    ]:
        print(f"### {title}\n\n{body}\n")


if __name__ == "__main__":
    main()
