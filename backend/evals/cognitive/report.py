"""Render cognitive-benchmark JSON as markdown tables for the research docs.

    python -m evals.cognitive.report memory /tmp/brainv2/memory_heldout.json E3_alternatives
    python -m evals.cognitive.report latency /tmp/brainv2/latency_final.json
    python -m evals.cognitive.report affect /tmp/brainv2/affect.json

Numbers in docs/brain-research come from here, never from hand transcription.
"""

from __future__ import annotations

import json
import statistics
import sys

CATS = ("all", "paraphrase", "keyword", "old", "emotional", "updated", "mood_negative")


def _short(cell: str) -> str:
    return cell.replace("_like", "")


def memory_worst_case(report: dict, experiment: str) -> str:
    ex = report["experiments"][experiment]["results"]
    cells = list(ex)
    arms = list(ex[cells[0]])
    head = "| arm | " + " | ".join(_short(c) for c in cells) + " | worst | mean |"
    sep = "|---|" + "---:|" * (len(cells) + 2)
    rows = [head, sep]
    scored = []
    for arm in arms:
        vals = [ex[c][arm]["aggregate"]["all"]["hit@3"]["mean"] for c in cells]
        scored.append((arm, vals))
    for arm, vals in scored:
        rows.append(
            f"| `{arm}` | "
            + " | ".join(f"{v:.3f}" for v in vals)
            + f" | {min(vals):.3f} | {sum(vals) / len(vals):.3f} |"
        )
    return "\n".join(rows)


def memory_categories(report: dict, experiment: str, cell: str) -> str:
    arms = report["experiments"][experiment]["results"][cell]
    head = (
        "| arm | "
        + " | ".join(CATS)
        + " | obsolete wins | trap in top-3 | hit@3 95% CI | paired Δ vs baseline [95% CI] |"
    )
    rows = [head, "|---|" + "---:|" * (len(CATS) + 4)]
    for arm, v in arms.items():
        agg = v["aggregate"]
        pd = v.get("paired_vs_baseline", {}).get("hit@3")
        delta = (
            f"{pd['delta']:+.3f} [{pd['ci95'][0]:+.3f}, {pd['ci95'][1]:+.3f}]"
            if pd
            else "—"
        )
        obs = agg.get("updated", {}).get("obsolete_win", {}).get("mean")
        rows.append(
            f"| `{arm}` | "
            + " | ".join(
                f"{agg[c]['hit@3']['mean']:.2f}" if c in agg else "—" for c in CATS
            )
            + f" | {obs:.2f} | {agg['all']['trap_in_top']['mean']:.2f}"
            + f" | [{agg['all']['hit@3']['ci95'][0]:.3f}, {agg['all']['hit@3']['ci95'][1]:.3f}]"
            + f" | {delta} |"
        )
    return "\n".join(rows)


def latency(report: dict) -> str:
    rows = [
        "| memories | policy | writes | p50 ms | p95 ms | p99 ms |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for r in report["results"]:
        w = f"1 per {r['write_every']} queries" if r.get("write_every") else "none"
        rows.append(
            f"| {r['size']} | {r['policy']} | {w} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} |"
        )
    return "\n".join(rows)


def affect(report: dict, learn: str = "False", start: str = "None") -> str:
    res = report["results"]
    rows = [
        (
            "| appraisal input | script | r(user valence, mood) | final mood | max abs mood "
            "| turns saturated | final trust | w1 | loop gain |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, per_seed in res.items():
        source, script, lk, sk = key.split("/")
        if lk != f"learn={learn}" or sk != f"start={start}":
            continue

        def mean(field, seeds=per_seed):
            vals = [s[field] for s in seeds if s[field] is not None]
            return statistics.fmean(vals) if vals else None

        r = mean("corr_user_valence_vs_mood")
        rows.append(
            f"| {source} | {script} | {'—' if r is None else f'{r:+.2f}'} "
            f"| {mean('final_mood'):+.2f} | {mean('max_abs_mood'):.2f} "
            f"| {mean('turns_saturated'):.0f} | {mean('final_trust'):.2f} "
            f"| {mean('final_w1'):.2f} | {mean('loop_gain_final'):.3f} |"
        )
    return "\n".join(rows)


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    kind, path, *rest = argv
    with open(path) as fh:
        report = json.load(fh)
    if kind == "memory":
        experiment = rest[0]
        if len(rest) > 1:
            print(memory_categories(report, experiment, rest[1]))
        else:
            print(memory_worst_case(report, experiment))
    elif kind == "latency":
        print(latency(report))
    elif kind == "affect":
        print(affect(report, *(rest or [])))
    else:
        raise SystemExit(f"unknown report kind {kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
