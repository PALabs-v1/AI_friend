"""Power for the realism eval's blind pairwise tier.

A rater sees two transcripts (arm A vs arm B, order randomized) and picks the
one that seems more like a real someone. H0: p = 0.5, exact two-sided test.

The eval's pass rule is joint: the test must reject AND the observed
preference must reach a threshold (default 0.60). Power for the joint rule is
lower than power for the test alone, so both are printed. Judgments from one
rater are correlated; the design-effect table shows how many raw judgments
are needed for a given effective n at an intraclass correlation (ICC) and a
number of judgments per rater, Kish's deff = 1 + (m - 1) * ICC.

Usage (from brain-v4/): python3 tools/power_pairwise.py
"""

from math import ceil, comb, sqrt


def pmf(n: int, k: int, p: float) -> float:
    return comb(n, k) * p**k * (1 - p) ** (n - k)


def rejection_region(n: int, alpha: float) -> set[int]:
    null = [pmf(n, k, 0.5) for k in range(n + 1)]
    region = set()
    for k in range(n + 1):
        # two-sided: sum of outcomes no more likely than k under H0
        p_value = sum(q for q in null if q <= null[k] * (1 + 1e-12))
        if p_value <= alpha:
            region.add(k)
    return region


def power(
    n: int, p_true: float, alpha: float = 0.05, min_observed: float = 0.0
) -> float:
    """P(pass) at true preference p_true: reject H0 and k/n >= min_observed."""
    return sum(
        pmf(n, k, p_true)
        for k in rejection_region(n, alpha)
        if k > n / 2 and k / n >= min_observed
    )


def min_n(
    p_true: float, min_observed: float = 0.0, target: float = 0.8, cap: int = 3000
) -> int:
    for n in range(10, cap + 1):
        if power(n, p_true, min_observed=min_observed) >= target:
            return n
    return -1


def normal_approx(p_true: float) -> float:
    z_a, z_b = 1.959964, 0.841621  # alpha .05 two-sided, power .8
    return ((z_a * 0.5 + z_b * sqrt(p_true * (1 - p_true))) / (p_true - 0.5)) ** 2


if __name__ == "__main__":
    print("A. Test alone (reject p = 0.5 in favor of the better arm), 80% power")
    print("true preference | exact n | normal approx")
    for p in (0.55, 0.60, 0.65, 0.70, 0.75):
        print(f"{p:.2f}            | {min_n(p):>7} | {normal_approx(p):.1f}")

    print("\nB. Joint pass rule: reject AND observed preference >= threshold")
    print("at n = 200 judgments:")
    for thr in (0.55, 0.60):
        cells = ", ".join(
            f"true {p:.2f}: {power(200, p, min_observed=thr):.3f}"
            for p in (0.60, 0.65, 0.70)
        )
        print(f"  threshold {thr:.2f} -> {cells}")
    print("smallest n for 80% power under the joint rule:")
    for thr in (0.55, 0.60):
        cells = ", ".join(
            f"true {p:.2f}: {min_n(p, min_observed=thr)}" for p in (0.65, 0.70)
        )
        print(f"  threshold {thr:.2f} -> {cells}")

    print("\nC. Raw judgments needed for an effective n, with correlated raters")
    print("effective n | ICC | judgments per rater | raw judgments")
    for n_eff in (90, 200):
        for icc in (0.05, 0.10, 0.20):
            for m in (10, 40):
                deff = 1 + (m - 1) * icc
                print(f"{n_eff:>11} | {icc:.2f} | {m:>19} | {ceil(n_eff * deff)}")
