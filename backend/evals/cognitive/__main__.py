"""CLI: python -m evals.cognitive {memory,affect} ...

    python -m evals.cognitive memory --experiments E1_baseline_paths E3_alternatives \\
        --seeds tune --out /tmp/brainv2/memory_tune.json
    python -m evals.cognitive affect --out /tmp/brainv2/affect.json
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import time


def _cmd_memory(args) -> int:
    from . import memory_experiments as mx

    seeds = {"tune": mx.TUNE_SEEDS, "heldout": mx.HELDOUT_SEEDS}[args.seeds]
    if args.quick:
        seeds = seeds[:3]
    defs = mx.experiment_definitions(args.hybrid, args.retune)
    names = args.experiments or list(defs)
    report = {
        "suite": "memory",
        "git_sha": mx.git_sha(),
        "python": platform.python_version(),
        "seeds": list(seeds),
        "seed_set": args.seeds,
        "profiles": args.profiles,
        "regimes": args.regimes,
        "limit": 3,
        "chosen_hybrid": args.hybrid,
        "chosen_retune": args.retune,
        "experiments": {},
    }
    for name in names:
        spec = defs[name]
        t0 = time.time()
        grouped = mx.run_arms(
            spec["arms"], args.profiles, args.regimes, seeds, args.workers
        )
        arm_names = [f"{p}@{g}" for p, g in spec["arms"]]
        report["experiments"][name] = {
            "question": spec["question"],
            "arms": arm_names,
            "baseline": spec["baseline"],
            "seconds": round(time.time() - t0, 1),
            "results": mx.summarize(grouped, spec["baseline"]),
        }
        print(
            f"[{name}] {len(arm_names)} arms in {time.time() - t0:.0f}s",
            file=sys.stderr,
        )
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1, default=mx._jsonable)
    print(args.out)
    return 0


def _cmd_affect(args) -> int:
    from . import affect_sim
    from .memory_experiments import git_sha

    report = {
        "suite": "affect",
        "git_sha": git_sha(),
        "turns": args.turns,
        "seeds": args.seeds,
        "results": affect_sim.run_matrix(args.turns, tuple(args.seeds)),
    }
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1)
    print(args.out)
    return 0


def _cmd_latency(args) -> int:
    from . import latency
    from .memory_experiments import git_sha

    rows = latency.run(tuple(args.sizes), tuple(args.policies), args.queries)
    report = {
        "suite": "latency",
        "git_sha": git_sha(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "results": rows,
    }
    for row in rows:
        print(row, file=sys.stderr)
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1)
    print(args.out)
    return 0


def main(argv=None) -> int:
    logging.disable(logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m evals.cognitive")
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("memory")
    m.add_argument("--experiments", nargs="*")
    m.add_argument("--seeds", choices=("tune", "heldout"), default="tune")
    m.add_argument(
        "--profiles", nargs="*", default=["nomic_like", "minilm_like", "hard"]
    )
    m.add_argument("--regimes", nargs="*", default=["verbatim", "unique", "summary"])
    m.add_argument("--hybrid", default="hybrid:1.5:0.2:none")
    m.add_argument("--retune", default="v1_retuned:12:2")
    m.add_argument("--workers", type=int)
    m.add_argument("--quick", action="store_true", help="3 seeds, for smoke runs")
    m.add_argument("--out", required=True)
    m.set_defaults(func=_cmd_memory)
    a = sub.add_parser("affect")
    a.add_argument("--turns", type=int, default=60)
    a.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    a.add_argument("--out", required=True)
    a.set_defaults(func=_cmd_affect)
    lat = sub.add_parser("latency")
    lat.add_argument("--sizes", type=int, nargs="*", default=[200, 1000, 3000, 5000])
    lat.add_argument("--policies", nargs="*", default=["actr_v1", "hybrid"])
    lat.add_argument("--queries", type=int, default=40)
    lat.add_argument("--out", required=True)
    lat.set_defaults(func=_cmd_latency)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
