#!/usr/bin/env python3
"""Collect AI_friend's research results and reports into one showcase archive.

    python3 scripts/research/collect_research_data.py
    AIF_DATA_DIR=/path python3 scripts/research/collect_research_data.py
    SKIP_REMOTE=1 python3 scripts/research/collect_research_data.py   # Mac only

Only what someone would be shown, plus what a future run needs to be
compared against. No logs, transcripts, CI output, dev/aborted runs, caches.

  reports/   readable write-ups (markdown, PDF, charts)
  results/   the numbers behind them (JSON/CSV reports; BrainBench runs with
             the files `evals.brainbench report`/`compare` read)

Copy-only and incremental: a file is copied when it is missing or its size or
mtime differs; nothing in a source is modified or deleted. Files a previous
collection put in the archive that the rules no longer select are listed, not
removed. Every source is spelled out in RULES below, so what gets included
is reviewable in one place. The guide (README.md, versioned here as
research_data_README.md) is installed on every run, then MANIFEST.tsv and
INDEX.md are regenerated (data_manifest.py).
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = Path(
    os.environ.get(
        "AIF_DATA_DIR", Path.home() / "Projects/PALabs/AI_friend-research-data"
    )
)
REMOTE = os.environ.get("AIF_REMOTE", "home-gpu")
RDIR = os.environ.get("AIF_REMOTE_DIR", "/data/aif-v3")
SHARED = Path(
    subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
).parent

READABLE = ["*.md", "*.pdf", "*.png", "*.svg", "*.jpg"]
NUMBERS = ["*.json", "*.jsonl", "*.csv"]
# The files `evals.brainbench report` and `compare` read. outcome-cells/ is
# left out: the runner rebuilds outcomes.jsonl from it, so it is a duplicate.
RUN_FILES = [
    "report.md",
    "report.json",
    "manifest.json",
    "plan.json",
    "cells.jsonl",
    "outcomes.jsonl",
]

# (source dir relative to REPO or SHARED, include patterns, destination)
LOCAL_RULES: list[tuple[Path, list[str], str]] = [
    # Brain V3 (this research cycle): the write-ups...
    (
        REPO / "docs/brain-research-v3",
        ["README.md", "0[89]-*.md", "1[0-9]-*.md", "findings.md"],
        "reports/brain-v3",
    ),
    (
        REPO / "docs/brain-research-v3/baseline",
        ["RESULTS.md"],
        "reports/brain-v3/phase3-baseline",
    ),
    (REPO / "docs/brain-research-v3/adr", READABLE, "reports/brain-v3/adr"),
    # ...and their numbers.
    (
        REPO / "docs/brain-research-v3/results/home-gpu/v2base",
        ["*/report.md"],
        "reports/brain-v3/v2-lifesim-baseline",
    ),
    (
        REPO / "docs/brain-research-v3/results/home-gpu/gpu-experiments",
        NUMBERS,
        "results/brain-v3/gpu-experiments",
    ),
    (
        REPO / "docs/brain-research-v3/results/home-gpu",
        ["*.json", "barge_in_mutations.txt", "README.md"],
        "results/brain-v3/phase3-baseline/home-gpu",
    ),
    (
        REPO / "docs/brain-research-v3/results/mac",
        ["*.json", "barge_in_mutations.txt", "README.md"],
        "results/brain-v3/phase3-baseline/mac",
    ),
    (
        REPO / "docs/brain-research-v3/baseline",
        ["manifest.json"],
        "results/brain-v3/phase3-baseline",
    ),
    (
        REPO / "backend/evals/brainbench/baseline",
        NUMBERS,
        "results/brain-v3/gate-bands",
    ),
    (REPO / "backend/tools/measure/out", NUMBERS, "results/measurements"),
    # Brain V2 (the previous cycle).
    (REPO / "docs/brain-research", READABLE, "reports/brain-v2"),
    (REPO / "docs/brain-research/adr", READABLE, "reports/brain-v2/adr"),
    # Earlier benchmarks and evidence packs.
    (REPO / "scripts/results", READABLE, "reports/earlier/benchmarks"),
    (REPO / "scripts/results", NUMBERS, "results/earlier/benchmarks"),
    (
        REPO / "academic_benchmarks/documentation",
        READABLE,
        "reports/earlier/academic-benchmarks",
    ),
    (
        REPO / "academic_benchmarks/datasets",
        NUMBERS,
        "results/earlier/academic-benchmarks",
    ),
    (REPO / "evidence", READABLE, "reports/earlier/evidence"),
    # Never committed: the earlier orchestration cycle's phase reports and the
    # probe runs in the shared checkout.
    (
        SHARED / "orchestration",
        [
            "PHASE_*/BENCHMARK_RESULTS.md",
            "PHASE_*/PHASE_GATE.md",
            "FINAL_SYSTEM_AUDIT/*.md",
        ],
        "reports/earlier/orchestration",
    ),
    (SHARED / "orchestration", ["PHASE_*/*.json"], "results/earlier/orchestration"),
    (SHARED / "backend/evals/out", NUMBERS, "results/earlier/evals-probes"),
]

copied = unchanged = 0
selected: set[str] = set()


def matches(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in patterns)


def copy_file(src: Path, dest_rel: str) -> None:
    global copied, unchanged
    dest = DATA / dest_rel
    selected.add(dest_rel)
    st = src.stat()
    if dest.exists():
        dt = dest.stat()
        if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
            unchanged += 1
            return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    copied += 1


def collect_local() -> None:
    for root, patterns, dest in LOCAL_RULES:
        if not root.is_dir():
            print(f"[collect] missing, skipped: {root}")
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            # A rule's patterns name files directly under it unless they
            # contain a slash, so a flat rule never pulls in subdirectories.
            if "/" not in "".join(patterns) and "/" in rel:
                continue
            if matches(rel, patterns):
                copy_file(path, f"{dest}/{rel}")


def ssh(cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", REMOTE, cmd],
        capture_output=True,
        text=True,
        check=False,
    )


def rsync(src: str, dest: Path, includes: list[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    args = ["rsync", "-a", "-m", "--include=*/"]
    args += [f"--include={p}" for p in includes] + [
        "--exclude=*",
        f"{REMOTE}:{src}/",
        f"{dest}/",
    ]
    if subprocess.run(args, check=False).returncode != 0:
        sys.exit(f"[collect] rsync failed: {REMOTE}:{src}")


def collect_remote() -> None:
    if ssh("true").returncode != 0:
        print(f"[collect] {REMOTE} unreachable: remote results not refreshed")
        return
    listing = ssh(f"ls {RDIR}/runs 2>/dev/null")
    for run in sorted(listing.stdout.split()):
        # Aborted runs stopped on harness bugs; their numbers are not valid.
        if run.startswith("_aborted"):
            continue
        has_manifest = ssh(f"test -f {RDIR}/runs/{run}/manifest.json").returncode == 0
        if not has_manifest:
            continue
        dest = DATA / "results/brainbench" / run
        rsync(f"{RDIR}/runs/{run}", dest, RUN_FILES)
        selected.update(
            p.relative_to(DATA).as_posix() for p in dest.rglob("*") if p.is_file()
        )
        report = dest / "report.md"
        if report.exists():
            copy_file(report, f"reports/brainbench/{run}.md")
    dest = DATA / "results/brain-v3/phase3-baseline/home-gpu"
    # Only the Phase 3 memory runs: affect/latency are committed and come
    # from the repo rule above.
    rsync(f"{RDIR}/baseline-results/baseline", dest, ["memory_*.json"])
    selected.update(
        p.relative_to(DATA).as_posix()
        for p in dest.glob("memory_*.json")
        if p.is_file()
    )


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"[collect] archive: {DATA}")
    collect_local()
    if os.environ.get("SKIP_REMOTE") != "1":
        collect_remote()
    shutil.copy2(REPO / "scripts/research/research_data_README.md", DATA / "README.md")
    generated = {"README.md", "INDEX.md", "MANIFEST.tsv"}
    stray = sorted(
        rel
        for rel in (
            p.relative_to(DATA).as_posix() for p in DATA.rglob("*") if p.is_file()
        )
        if rel not in selected and rel not in generated
    )
    if stray:
        print(
            f"[collect] {len(stray)} file(s) in the archive not selected by the rules (left in place):"
        )
        for rel in stray[:20]:
            print(f"   {rel}")
    subprocess.run(
        [sys.executable, str(REPO / "scripts/research/data_manifest.py"), str(DATA)],
        check=True,
    )
    print(f"[collect] copied {copied}, unchanged {unchanged}")


if __name__ == "__main__":
    main()
