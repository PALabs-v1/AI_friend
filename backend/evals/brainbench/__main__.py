"""Command line entry point for the whole-brain BrainBench runner."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from evals.brainbench import runner
from evals.brainbench.switchboard import ArmUnavailable, arm_status


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.brainbench")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("arms", help="list registered ablation arms")
    for command in ("plan", "run"):
        sub = commands.add_parser(command)
        sub.add_argument(
            "--mode", choices=("architecture_only", "llm_augmented"), required=True
        )
        sub.add_argument("--arm", default="baseline")
        sub.add_argument("--suites", default="all")
        sub.add_argument("--variants")
        sub.add_argument("--archetypes", default="all")
        sub.add_argument("--seeds", required=True)
        sub.add_argument("--horizons", required=True)
        sub.add_argument("--pairing", choices=("zip", "cross"), default="zip")
        sub.add_argument("--suite-arg", action="append", default=[])
        if command == "run":
            sub.add_argument("--out", type=Path, required=True)
            sub.add_argument(
                "--llm-url",
                default=os.environ.get(
                    "BRAINBENCH_LLM_URL", "http://100.88.246.46:11434"
                ),
            )
            sub.add_argument("--force-new-plan", action="store_true")
            sub.add_argument(
                "--embeddings", choices=("deterministic", "model"), default=None
            )
            sub.add_argument(
                "--allow-model-embeddings-in-architecture-only", action="store_true"
            )
            sub.add_argument("--workers", type=int, default=1)
    report = commands.add_parser("report")
    report.add_argument("out", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("baseline_out", type=Path)
    compare.add_argument("arm_out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "arms":
            print(
                f"{'ARM':34} {'RUNNABLE':9} {'WORKSTREAM':11} OVERRIDES / MISSING FLAGS"
            )
            for row in arm_status():
                values = json.dumps(row["overrides"], sort_keys=True)
                if row["missing"]:
                    values += " missing=" + ", ".join(row["missing"])
                print(
                    f"{row['name']:34} {row['runnable']!s:9} {(row['workstream'] or '-'):11} {values}"
                )
            return 0
        if args.command in {"plan", "run"}:
            cells = runner.build_plan(
                mode=args.mode,
                arm=args.arm,
                suites=args.suites,
                archetypes=args.archetypes,
                seeds=args.seeds,
                horizons=args.horizons,
                pairing=args.pairing,
                variants=args.variants,
                suite_args=runner.parse_suite_args(args.suite_arg),
            )
            if args.command == "plan":
                print(
                    json.dumps(
                        {
                            "count": len(cells),
                            "cells": [cell.fields() for cell in cells],
                        },
                        indent=2,
                    )
                )
                return 0
            status = runner.run_plan(
                cells,
                args.out,
                llm_url=args.llm_url,
                argv=sys.argv
                if argv is None
                else ["python -m evals.brainbench", *argv],
                force_new_plan=args.force_new_plan,
                embeddings=args.embeddings,
                allow_model_embeddings_in_architecture_only=args.allow_model_embeddings_in_architecture_only,
                workers=args.workers,
            )
            return status
        if args.command == "report":
            _report, markdown = runner.report_run(args.out)
            print(markdown, end="")
            return 0
        _compare, markdown = runner.compare_runs(args.baseline_out, args.arm_out)
        print(markdown, end="")
        return 0
    except (ValueError, ArmUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
