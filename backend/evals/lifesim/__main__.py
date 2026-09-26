"""CLI: ``python -m evals.lifesim <command>`` (run from ``backend/``).

generate --seed 1000 --archetype socialite --horizon 1y --out /tmp/ls/1000
panel    --split dev --count 12 --horizon 6m --out /tmp/ls/panel
stats    /tmp/ls/1000
banks    verify | freeze
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import banks
from .personas import PANEL
from .splits import SPLITS


def _cmd_generate(a) -> int:
    from .generate import generate

    m = generate(a.seed, a.archetype, a.horizon, Path(a.out), final_run=a.final_run)
    print(
        json.dumps(
            {
                k: m[k]
                for k in (
                    "seed",
                    "split",
                    "archetype",
                    "horizon",
                    "start",
                    "end",
                    "counts",
                )
            },
            indent=1,
        )
    )
    return 0


def _cmd_panel(a) -> int:
    from .generate import generate

    seeds = list(SPLITS[a.split])[: a.count]
    for i, seed in enumerate(seeds):
        archetype = PANEL[i % len(PANEL)] if i < a.count - a.random else "random"
        m = generate(
            seed,
            archetype,
            a.horizon,
            Path(a.out) / f"{seed}-{archetype}",
            final_run=a.final_run,
        )
        print(
            f"{seed} {archetype:22s} turns={m['counts']['turns']:7d} probes={m['counts']['probes']:5d}"
        )
    return 0


def _cmd_stats(a) -> int:
    from .stats import describe

    print(json.dumps(describe(Path(a.dir)), indent=1, sort_keys=True))
    return 0


def _cmd_banks(a) -> int:
    if a.action == "freeze":
        print(json.dumps(banks.freeze(), indent=1))
    else:
        print(json.dumps(banks.verify_manifest(), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m evals.lifesim",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="one persona, one horizon")
    g.add_argument("--seed", type=int, required=True)
    g.add_argument(
        "--archetype", default="random", help=f"one of {', '.join(PANEL)} or random"
    )
    g.add_argument(
        "--horizon",
        default="1y",
        help="100t, 500t, 1000t, 1w, 1m, 6m, 1y, 3y, 5y, 10y, ...",
    )
    g.add_argument("--out", required=True)
    g.add_argument(
        "--final-run", action="store_true", help="required for held-out seeds; logged"
    )
    g.set_defaults(fn=_cmd_generate)

    pn = sub.add_parser(
        "panel", help="the archetype panel over consecutive seeds of one split"
    )
    pn.add_argument("--split", default="dev", choices=sorted(SPLITS))
    pn.add_argument("--count", type=int, default=len(PANEL))
    pn.add_argument(
        "--random", type=int, default=0, help="how many of --count are random draws"
    )
    pn.add_argument("--horizon", default="6m")
    pn.add_argument("--out", required=True)
    pn.add_argument("--final-run", action="store_true")
    pn.set_defaults(fn=_cmd_panel)

    st = sub.add_parser(
        "stats", help="coverage, gaps and persona effects for one generated directory"
    )
    st.add_argument("dir")
    st.set_defaults(fn=_cmd_stats)

    b = sub.add_parser("banks", help="verify or freeze the template banks")
    b.add_argument("action", choices=("verify", "freeze"))
    b.set_defaults(fn=_cmd_banks)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
