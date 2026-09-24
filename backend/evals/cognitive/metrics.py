"""Retrieval metrics over ranked memory keys, with bootstrap intervals.

All metrics are per probe, then averaged per category. A probe's `ranked` list
is what the retriever returned, best first, as scenario memory keys (unknown
contents map to ``"?"`` so they still count as intrusions).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

import numpy as np

TRIVIAL_PREFIXES = ("filler:", "trap:")


@dataclass
class ProbeResult:
    probe_key: str
    scenario_seed: int
    categories: tuple[str, ...]
    ranked: tuple[str, ...]
    relevant: tuple[str, ...]
    obsolete: tuple[str, ...]
    k: int
    error: str | None = None

    @property
    def first_relevant_rank(self) -> int | None:
        for i, key in enumerate(self.ranked):
            if key in self.relevant:
                return i + 1
        return None

    def metrics(self) -> dict[str, float]:
        top = self.ranked[: self.k]
        rank = self.first_relevant_rank
        hit_k = rank is not None and rank <= self.k
        obsolete_ranks = [
            i + 1 for i, key in enumerate(self.ranked) if key in self.obsolete
        ]
        obsolete_win = 0.0
        if self.obsolete:
            best_obsolete = min(obsolete_ranks) if obsolete_ranks else None
            if best_obsolete is not None and best_obsolete <= self.k:
                obsolete_win = 1.0 if rank is None or best_obsolete < rank else 0.0
        trivial = sum(
            1 for key in top if key.startswith(TRIVIAL_PREFIXES) or key == "?"
        )
        return {
            "hit@1": 1.0 if rank == 1 else 0.0,
            f"hit@{self.k}": 1.0 if hit_k else 0.0,
            "mrr": (1.0 / rank) if rank is not None and rank <= self.k else 0.0,
            "obsolete_win": obsolete_win,
            "trivia_share": trivial / self.k,
            "trap_in_top": 1.0 if any(key.startswith("trap:") for key in top) else 0.0,
            "empty": 1.0 if not self.ranked else 0.0,
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metrics"] = self.metrics()
        return d


def _bootstrap_ci(
    values: list[float], seed: int = 1234, n: int = 1000
) -> tuple[float, float]:
    """Percentile bootstrap 95% interval of the mean (seeded, reproducible)."""
    if not values:
        return (float("nan"), float("nan"))
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, len(arr), size=(n, len(arr)))].mean(axis=1)
    return (float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975)))


def _cluster_bootstrap_ci(
    values: list[float], clusters: list, seed: int = 1234, n: int = 1000
) -> tuple[float, float]:
    """95% interval of the mean, resampling whole clusters (scenario seeds).

    Probes within one scenario share a store, and the `mood_negative` probes
    repeat the paraphrase questions verbatim, so probes are not independent;
    the scenario seed is the independent unit. With fewer than two clusters
    there is nothing to resample between, so this falls back to probes.
    """
    labels = sorted(set(clusters))
    if len(labels) < 2:
        return _bootstrap_ci(values, seed=seed, n=n)
    position = {c: i for i, c in enumerate(labels)}
    sums = np.zeros(len(labels))
    counts = np.zeros(len(labels))
    for value, cluster in zip(values, clusters, strict=True):
        sums[position[cluster]] += value
        counts[position[cluster]] += 1
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(labels), size=(n, len(labels)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return (float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975)))


def aggregate(
    results: list[ProbeResult], categories: tuple[str, ...] | None = None
) -> dict:
    """Per-category mean and 95% cluster-bootstrap interval for every metric.

    ``obsolete_win`` is averaged only over probes that have an obsolete key,
    otherwise it would be diluted by probes where it cannot happen.
    ``trivia_share`` is the share of the k result *slots* filled by small
    talk or traps (an empty slot counts as not trivia).
    """
    buckets: dict[str, list[ProbeResult]] = defaultdict(list)
    for r in results:
        buckets["all"].append(r)
        for c in r.categories:
            buckets[c].append(r)
    wanted = ("all",) + tuple(categories) if categories else tuple(sorted(buckets))
    out: dict[str, dict] = {}
    for cat in wanted:
        rs = buckets.get(cat, [])
        if not rs:
            continue
        per_metric: dict[str, list[float]] = defaultdict(list)
        seeds: dict[str, list[int]] = defaultdict(list)
        for r in rs:
            for name, value in r.metrics().items():
                if name == "obsolete_win" and not r.obsolete:
                    continue
                per_metric[name].append(value)
                seeds[name].append(r.scenario_seed)
        out[cat] = {
            "n": len(rs),
            "scenarios": len({r.scenario_seed for r in rs}),
            **{
                name: {
                    "mean": round(sum(vals) / len(vals), 4),
                    "ci95": [
                        round(x, 4) for x in _cluster_bootstrap_ci(vals, seeds[name])
                    ],
                }
                for name, vals in per_metric.items()
            },
        }
    return out


def paired_delta(
    a: list[ProbeResult],
    b: list[ProbeResult],
    metric: str,
    seed: int = 99,
    n: int = 2000,
) -> dict:
    """Paired cluster bootstrap of mean(b - a) over probes present in both
    runs, resampling scenario seeds (see `_cluster_bootstrap_ci`)."""
    index_a = {(r.scenario_seed, r.probe_key): r.metrics()[metric] for r in a}
    pairs = [
        (r.scenario_seed, index_a[(r.scenario_seed, r.probe_key)], r.metrics()[metric])
        for r in b
        if (r.scenario_seed, r.probe_key) in index_a
    ]
    diffs = [y - x for _, x, y in pairs]
    if not diffs:
        return {"n": 0}
    lo, hi = _cluster_bootstrap_ci(diffs, [s for s, _, _ in pairs], seed=seed, n=n)
    return {
        "n": len(diffs),
        "delta": round(sum(diffs) / len(diffs), 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "wins": sum(1 for d in diffs if d > 0),
        "losses": sum(1 for d in diffs if d < 0),
    }
