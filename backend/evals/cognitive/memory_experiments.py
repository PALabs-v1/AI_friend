"""Memory-retrieval experiment matrix: baseline, ablations, alternatives, sweeps.

Every experiment is a set of (policy, candidate generator) arms evaluated on
the same scenarios, so arm-vs-arm differences are paired. Weight sweeps run on
TUNE seeds; the chosen configuration is then re-scored on HELD-OUT seeds and
on embedding profiles it was not tuned on, so a reported gain cannot be an
artifact of fitting the benchmark.

    python -m evals.cognitive memory --out /tmp/brainv2/memory.json

Reports carry the git SHA, seeds, profiles, regimes and arm definitions.
"""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, is_dataclass

from .embedder import LatentTopicEmbedder
from .lab import (
    GenerativeAgentsPolicy,
    HybridPolicy,
    RRFPolicy,
    V1Policy,
    all_rows,
    pg_actr_topn,
    run_lab,
    sqlite_recency,
    vector_topn,
    with_validity_oracle,
)
from .metrics import ProbeResult, aggregate, paired_delta
from .scenarios import build_history, register_embeddings

TUNE_SEEDS = tuple(range(1, 21))
HELDOUT_SEEDS = tuple(range(101, 131))
PROFILES = ("nomic_like", "minilm_like", "hard")

V1_TERMS = (
    "recency",
    "frequency",
    "spacing",
    "importance",
    "emo_proximity",
    "emo_distance",
)

GENERATORS = {
    "sqlite": sqlite_recency,
    "pg": pg_actr_topn,
    "vec20": lambda s, c: vector_topn(s, c, 20),
    "vec60": lambda s, c: vector_topn(s, c, 60),
    "all": all_rows,
    # Upper bound: a perfect write-time supersession detector (see lab.py).
    "vec60+validity": with_validity_oracle(lambda s, c: vector_topn(s, c, 60)),
}


def _policy(name: str):
    """Arm registry: name -> fresh policy object (policies hold per-run state)."""
    if name == "v1":
        return V1Policy()
    if name.startswith("v1-"):
        term = name[3:]
        if term == "cue":
            return V1Policy(name=name, cue_boost=0.0)
        if term == "goal_buffer":
            return V1Policy(name=name, goal_boost=False)
        return V1Policy(name=name, drop=frozenset({term}))
    if name == "cosine":
        return V1Policy(
            name=name, drop=frozenset(V1_TERMS), cue_boost=0.0, goal_boost=False
        )
    if name.startswith("v1_retuned"):
        # v1_retuned:sim_weight:cue_boost -- the "just retune two constants" arm
        _, w, c = name.split(":")
        return V1Policy(name=name, sim_weight=float(w), cue_boost=float(c))
    if name == "gen_agents":
        return GenerativeAgentsPolicy()
    if name == "rrf":
        return RRFPolicy()
    if name.startswith("hybrid"):
        # hybrid[:w_lex:w_act:tau:w_emo][/pool]  ("/pool" = candidate-pool IDF)
        pool_idf = name.endswith("/pool")
        parts = name.removesuffix("/pool").split(":")
        kwargs = {"pool_idf": pool_idf}
        if len(parts) > 1:
            kwargs["w_lex"] = float(parts[1])
            kwargs["w_act"] = float(parts[2])
            kwargs["tau"] = None if parts[3] == "none" else float(parts[3])
            if len(parts) > 4:
                kwargs["w_emo"] = float(parts[4])
        return HybridPolicy(name=name, **kwargs)
    raise KeyError(name)


def _run_cell(args) -> dict[str, list[dict]]:
    """One (profile, regime, seed) scenario, every requested arm.

    A regime may carry a distress share as ``summary+distress0.4``.
    """
    profile, regime, seed, arms = args
    base, _, distress = regime.partition("+distress")
    scenario = build_history(seed, regime=base, distress_share=float(distress or 0.0))
    embedder = LatentTopicEmbedder(profile, seed=seed)
    register_embeddings(scenario, embedder)
    out: dict[str, list[dict]] = {}
    for policy_name, gen_name in arms:
        results = run_lab(
            scenario, embedder, _policy(policy_name), GENERATORS[gen_name]
        )
        out[f"{policy_name}@{gen_name}"] = [asdict(r) for r in results]
    return out


def run_arms(arms, profiles, regimes, seeds, workers: int | None = None):
    """-> {(profile, regime): {arm: [ProbeResult]}}"""
    cells = [(p, r, s, tuple(arms)) for p in profiles for r in regimes for s in seeds]
    grouped: dict = defaultdict(lambda: defaultdict(list))
    workers = workers or min(8, os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for cell, out in zip(cells, pool.map(_run_cell, cells, chunksize=1)):
            for arm, rows in out.items():
                grouped[(cell[0], cell[1])][arm].extend(ProbeResult(**r) for r in rows)
    return grouped


CATEGORIES = (
    "paraphrase",
    "keyword",
    "old",
    "important",
    "emotional",
    "updated",
    "mood_negative",
)


def summarize(grouped, baseline_arm: str | None = None, metric: str = "hit@3") -> dict:
    report: dict = {}
    for (profile, regime), arms in grouped.items():
        cell: dict = {}
        for arm, results in arms.items():
            entry = {"aggregate": aggregate(results, CATEGORIES)}
            if baseline_arm and arm != baseline_arm and baseline_arm in arms:
                entry["paired_vs_baseline"] = {
                    m: paired_delta(arms[baseline_arm], results, m)
                    for m in (metric, "mrr", "obsolete_win", "trap_in_top")
                }
            cell[arm] = entry
        report[f"{profile}/{regime}"] = cell
    return report


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _jsonable(obj):
    if is_dataclass(obj):
        return asdict(obj)
    return str(obj)


# ---------------------------------------------------------------------------
# Named experiments
# ---------------------------------------------------------------------------

HYBRID_WEIGHT_GRID = [
    f"hybrid:{wl}:{wa}:none"
    for wl in (0.0, 0.5, 1.0, 2.0)
    for wa in (0.0, 0.15, 0.3, 0.6, 1.0)
]
TAU_GRID = ("none", "0.65", "0.7", "0.75", "0.8", "0.85")
V1_RETUNE_GRID = [f"v1_retuned:{w}:{c}" for w in (1, 3, 5, 8, 12) for c in (0, 1, 2, 5)]


def experiment_definitions(
    chosen_hybrid: str = "hybrid:1.5:0.2:none", chosen_retune: str = "v1_retuned:12:2"
) -> dict:
    return {
        "E1_baseline_paths": {
            "question": "How does production V1 perform on each candidate path?",
            "arms": [("v1", g) for g in ("sqlite", "pg", "vec20", "all")],
            "baseline": "v1@sqlite",
        },
        "E2_v1_ablation": {
            "question": "Which V1 terms carry weight when every memory is scored?",
            "arms": [("v1", "all")]
            + [(f"v1-{t}", "all") for t in V1_TERMS + ("cue", "goal_buffer")]
            + [("cosine", "all")],
            "baseline": "v1@all",
        },
        "E3_alternatives": {
            "question": "Published and hybrid alternatives vs production, same inputs.",
            "arms": [
                ("v1", "sqlite"),
                ("v1", "vec60"),
                ("cosine", "vec60"),
                ("gen_agents", "vec60"),
                ("rrf", "vec60"),
                (chosen_retune, "vec60"),
                (chosen_hybrid, "vec20"),
                (chosen_hybrid, "vec60"),
                (chosen_hybrid, "all"),
                (chosen_hybrid, "sqlite"),
            ],
            "baseline": "v1@sqlite",
        },
        "E4a_hybrid_weights": {
            "question": "Sensitivity of the hybrid to w_lex and w_act (tune seeds only).",
            "arms": [(h, "vec60") for h in HYBRID_WEIGHT_GRID],
            "baseline": "hybrid:1.0:0.3:none@vec60",
        },
        "E4b_supersession_tau": {
            "question": "Near-duplicate collapse threshold, per embedding profile.",
            "arms": [
                (":".join(chosen_hybrid.split(":")[:3] + [t]), "vec60")
                for t in TAU_GRID
            ],
            "baseline": ":".join(chosen_hybrid.split(":")[:3] + ["none"]) + "@vec60",
        },
        "E6_hybrid_ablation": {
            "question": "Does each hybrid component earn its place (held-out)?",
            "arms": [
                (chosen_hybrid + "/pool", "vec60"),
                ("hybrid:0.0:0.2:none/pool", "vec60"),  # - lexical
                ("hybrid:1.5:0.0:none/pool", "vec60"),  # - activation
                ("cosine", "vec60"),  # - both (z-score is rank-neutral)
                (chosen_hybrid + "/pool", "sqlite"),  # right scorer, wrong pool
            ],
            "baseline": chosen_hybrid + "/pool@vec60",
        },
        "E7_validity_value": {
            "question": "How much would perfect write-time supersession add (upper bound)?",
            "arms": [
                (chosen_hybrid + "/pool", "vec60"),
                (chosen_hybrid + "/pool", "vec60+validity"),
                ("v1", "vec60"),
                ("v1", "vec60+validity"),
            ],
            "baseline": chosen_hybrid + "/pool@vec60",
        },
        "E5_v1_retune_sweep": {
            "question": "Can retuning V1's two scale constants match the hybrid?",
            "arms": [(v, "vec60") for v in V1_RETUNE_GRID],
            "baseline": "v1_retuned:1:5@vec60",
        },
    }
