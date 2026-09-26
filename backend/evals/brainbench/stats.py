"""Statistics for BrainBench suite results (06-benchmark-plan.md's "Statistics"
section: clustered by persona seed, 95% CIs, paired deltas against the V2
lifesim baseline, win/loss/tie, Cliff's delta / Cohen's d, Holm correction
across ablation families).

Reuses `evals.cognitive.metrics`'s cluster-bootstrap primitives directly
(clustering by persona seed here, by scenario seed there -- same math, a
different independent unit) instead of re-deriving bootstrap resampling.
`ProbeResult`/`aggregate`/`paired_delta` in that module are retrieval-specific
(ranked/relevant/obsolete/k), which none of BrainBench's other eight suites
have, so this module defines its own generic `SuiteOutcome` and the
aggregation/delta functions around it, but calls into the same
`_bootstrap_ci`/`_cluster_bootstrap_ci` for the actual interval math.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from evals.cognitive.metrics import _cluster_bootstrap_ci


@dataclass
class SuiteOutcome:
    """One probe/turn-level measurement from a BrainBench suite run.

    Generic across all 9 suites: a suite supplies whatever named metric
    values it computed for that probe in `metrics`, rather than the fixed
    ranked/relevant/obsolete shape that only fits memory retrieval.
    """

    probe_key: str
    persona_seed: int
    suite: str
    categories: tuple[str, ...]
    metrics: dict[str, float] = field(default_factory=dict)
    mode: str = "architecture_only"
    error: str | None = None


def aggregate(
    results: list[SuiteOutcome], categories: tuple[str, ...] | None = None
) -> dict:
    """Per-category mean and 95% cluster-bootstrap interval for every metric,
    clustered by `persona_seed`: probes drawn from one simulated persona
    share a world and a conversation history, so they are not independent
    the way probes from different personas are.
    """
    buckets: dict[str, list[SuiteOutcome]] = defaultdict(list, {"all": []})
    for r in results:
        buckets["all"].append(r)
        for c in r.categories:
            buckets[c].append(r)
    wanted = ("all",) + tuple(categories) if categories else tuple(sorted(buckets))

    out: dict[str, dict] = {}
    for name in wanted:
        rows = buckets.get(name, [])
        if not rows:
            out[name] = {"n": 0, "metrics": {}}
            continue
        metric_names = sorted({k for r in rows for k in r.metrics})
        per_metric = {}
        for metric in metric_names:
            values = [r.metrics[metric] for r in rows if metric in r.metrics]
            clusters = [r.persona_seed for r in rows if metric in r.metrics]
            if not values:
                continue
            lo, hi = _cluster_bootstrap_ci(values, clusters)
            per_metric[metric] = {
                "mean": round(sum(values) / len(values), 4),
                "ci95": [round(lo, 4), round(hi, 4)],
                "n": len(values),
            }
        out[name] = {"n": len(rows), "metrics": per_metric}
    return out


def paired_delta(
    a: list[SuiteOutcome],
    b: list[SuiteOutcome],
    metric: str,
    seed: int = 99,
    n: int = 2000,
) -> dict:
    """Paired cluster bootstrap of mean(b - a) over probes present in both
    runs (e.g. `baseline` vs `+temporal`), resampling persona seeds, plus
    Cliff's delta and Cohen's d so a significant delta can still be judged
    for practical size, not just direction.
    """
    index_a = {
        (r.persona_seed, r.probe_key): r.metrics[metric]
        for r in a
        if metric in r.metrics
    }
    pairs = [
        (r.persona_seed, index_a[(r.persona_seed, r.probe_key)], r.metrics[metric])
        for r in b
        if metric in r.metrics and (r.persona_seed, r.probe_key) in index_a
    ]
    if not pairs:
        return {"n": 0}
    diffs = [y - x for _, x, y in pairs]
    clusters = [s for s, _, _ in pairs]
    lo, hi = _cluster_bootstrap_ci(diffs, clusters, seed=seed, n=n)
    before = [x for _, x, _ in pairs]
    after = [y for _, _, y in pairs]
    return {
        "n": len(diffs),
        "delta": round(sum(diffs) / len(diffs), 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "wins": sum(1 for d in diffs if d > 0),
        "losses": sum(1 for d in diffs if d < 0),
        "ties": sum(1 for d in diffs if d == 0),
        "p_value": round(bootstrap_p_value(diffs, clusters, seed=seed, n=n), 4),
        "cliffs_delta": round(cliffs_delta(before, after), 4),
        "cohens_d": round(cohens_d(before, after), 4),
    }


def _resample_cluster_means(
    values: list[float], clusters: list, seed: int, n: int
) -> np.ndarray:
    """The same cluster-resampling `_cluster_bootstrap_ci` does internally,
    exposed here because a p-value needs the full resampled distribution,
    not just its two quantiles. Mirrors that function's algorithm exactly
    (whole-cluster resampling with replacement) so the p-value and the CI
    it accompanies are computed the same way.
    """
    labels = sorted(set(clusters))
    if len(labels) < 2:
        arr = np.asarray(values, dtype=float)
        rng = np.random.default_rng(seed)
        return arr[rng.integers(0, len(arr), size=(n, len(arr)))].mean(axis=1)
    position = {c: i for i, c in enumerate(labels)}
    sums = np.zeros(len(labels))
    counts = np.zeros(len(labels))
    for value, cluster in zip(values, clusters, strict=True):
        sums[position[cluster]] += value
        counts[position[cluster]] += 1
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(labels), size=(n, len(labels)))
    return sums[idx].sum(axis=1) / counts[idx].sum(axis=1)


def bootstrap_p_value(
    values: list[float], clusters: list, seed: int = 99, n: int = 2000
) -> float:
    """Two-sided percentile-bootstrap p-value for H0: mean(values) == 0.

    `p = 2 * min(P(resampled mean <= 0), P(resampled mean >= 0))`, capped at
    1.0 -- the standard nonparametric bootstrap significance test, used here
    on paired differences so `holm_correction` has real p-values to correct
    across an ablation family instead of only a CI's implicit yes/no.
    """
    if not values:
        return 1.0
    means = _resample_cluster_means(values, clusters, seed, n)
    p_low = float(np.mean(means <= 0))
    p_high = float(np.mean(means >= 0))
    return min(1.0, 2 * min(p_low, p_high))


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Cliff's delta: P(b_i > a_i) - P(b_i < a_i) over all pairs, in [-1, 1].

    A non-parametric effect size, robust to the non-normal distributions most
    of BrainBench's metrics have (hit@k and obsolete_win are 0/1; trust and
    valence are bounded; only latency is close to continuous).
    """
    if not a or not b:
        return 0.0
    # Counted by binary search over sorted b: O((n + m) log m) instead of
    # comparing every pair, which took hours on full-panel barge-in groups.
    # A NaN compares neither greater nor less than anything, so it adds to
    # neither count but stays in the denominator, exactly as pairwise did.
    xs = np.asarray(a, dtype=float)
    ys = np.sort(np.asarray(b, dtype=float))
    xs = xs[~np.isnan(xs)]
    ys = ys[~np.isnan(ys)]
    gt = int((len(ys) - np.searchsorted(ys, xs, side="right")).sum())
    lt = int(np.searchsorted(ys, xs, side="left").sum())
    return (gt - lt) / (len(a) * len(b))


def cohens_d(a: list[float], b: list[float]) -> float:
    """Cohen's d with the pooled standard deviation (Cohen, 1988), for
    metrics closer to normal (latency, continuous scores) where a parametric
    effect size is informative alongside Cliff's delta.
    """
    if len(a) < 2 or len(b) < 2:
        return 0.0
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1)
    pooled_n = len(a) + len(b) - 2
    if pooled_n <= 0:
        return 0.0
    pooled_sd = math.sqrt(((len(a) - 1) * var_a + (len(b) - 1) * var_b) / pooled_n)
    if pooled_sd == 0:
        return 0.0
    return (mean_b - mean_a) / pooled_sd


def holm_correction(p_values: dict[str, float], alpha: float = 0.05) -> dict[str, dict]:
    """Holm-Bonferroni step-down correction (Holm, 1979) across a family of
    comparisons -- e.g. every ablation arm's p-value against baseline in one
    workstream. Controls the family-wise error rate without Bonferroni's full
    conservatism: each remaining comparison's threshold loosens as smaller
    p-values are rejected in order, and the step-down stops at the first
    comparison that fails its threshold (every later one, having a larger
    p-value, is rejected too).
    """
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    result: dict[str, dict] = {}
    still_significant = True
    for i, (name, p) in enumerate(ordered):
        threshold = alpha / (m - i)
        significant = still_significant and p <= threshold
        if not significant:
            still_significant = False
        result[name] = {
            "p": p,
            "threshold": round(threshold, 6),
            "significant": significant,
        }
    return result
