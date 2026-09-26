#!/usr/bin/env python3
"""Manifest and index for the research-data archive.

    python3 scripts/research/data_manifest.py ~/Projects/PALabs/AI_friend-research-data

Writes, at the archive root:
  MANIFEST.tsv  one row per file: path, bytes, sha256, mtime (UTC). A file
                whose size and mtime match the previous manifest keeps its
                hash, so reruns only hash what changed.
  INDEX.md      per-category totals, plus one row per BrainBench run with
                the provenance its own manifest.json records (git sha, mode,
                suites, start/end, cells ok/total).

Hand-written documentation (README.md) is never touched.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

GENERATED = {"MANIFEST.tsv", "INDEX.md", "README.md"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def previous(root: Path) -> dict[str, tuple[int, str, str]]:
    path = root / "MANIFEST.tsv"
    if not path.exists():
        return {}
    with path.open() as stream:
        return {
            row["path"]: (int(row["bytes"]), row["mtime_utc"], row["sha256"])
            for row in csv.DictReader(stream, delimiter="\t")
        }


def build_manifest(root: Path) -> list[dict[str, str]]:
    cache = previous(root)
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in GENERATED:
            continue
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        cached = cache.get(rel)
        digest = (
            cached[2]
            if cached and cached[:2] == (stat.st_size, mtime)
            else sha256(path)
        )
        rows.append(
            {
                "path": rel,
                "bytes": str(stat.st_size),
                "sha256": digest,
                "mtime_utc": mtime,
            }
        )
    with (root / "MANIFEST.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, ["path", "bytes", "sha256", "mtime_utc"], delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(rows)
    return rows


def run_row(run_dir: Path) -> list[str]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    argv = manifest.get("argv") or []

    def arg(flag: str) -> str:
        return argv[argv.index(flag) + 1] if flag in argv[:-1] else ""

    cells_ok = total = 0
    plan = run_dir / "plan.json"
    if plan.exists():
        total = len(json.loads(plan.read_text()).get("cells", []))
    cells = run_dir / "cells.jsonl"
    if cells.exists():
        latest = {}
        for line in cells.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                latest[row["cell_id"]] = row.get("status")
        cells_ok = sum(status == "ok" for status in latest.values())
    return [
        run_dir.name,
        (manifest.get("git_sha") or "")[:8]
        + (" dirty" if manifest.get("dirty") else ""),
        manifest.get("mode") or "",
        arg("--suites"),
        arg("--horizons"),
        (manifest.get("started_at") or "")[:16],
        (manifest.get("ended_at") or "")[:16] or "not recorded",
        f"{cells_ok}/{total}" if total else str(cells_ok),
        "yes" if (run_dir / "report.md").exists() else "no",
    ]


def build_index(root: Path, rows: list[dict[str, str]]) -> None:
    by_top: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        top = row["path"].split("/", 1)[0]
        by_top[top][0] += 1
        by_top[top][1] += int(row["bytes"])
    lines = [
        "# Research data index",
        "",
        (
            f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by "
            "`scripts/research/data_manifest.py`; do not edit (README.md is the "
            "hand-written guide). Every file and its sha256: `MANIFEST.tsv`."
        ),
        "",
        "| Category | Files | Size |",
        "|---|---:|---:|",
    ]
    for top, (count, size) in sorted(by_top.items()):
        lines.append(f"| `{top}/` | {count:,} | {human(size)} |")
    lines.append(
        f"| **total** | **{len(rows):,}** | **{human(sum(int(r['bytes']) for r in rows))}** |"
    )

    runs = sorted(p.parent for p in root.glob("results/brainbench/*/manifest.json"))
    if runs:
        lines += [
            "",
            "## BrainBench runs",
            "",
            "| Run | Code | Mode | Suites | Horizons | Started (UTC) | Ended (UTC) | Cells ok | Report |",
            "|---|---|---|---|---|---|---|---:|---|",
        ]
        for run_dir in runs:
            try:
                cols = run_row(run_dir)
            except (OSError, ValueError, KeyError) as exc:
                cols = [run_dir.name, f"unreadable manifest: {exc}"] + [""] * 7
            lines.append("| " + " | ".join(cols) + " |")
    (root / "INDEX.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    root = Path(sys.argv[1]).expanduser()
    rows = build_manifest(root)
    build_index(root, rows)
    print(f"[manifest] {len(rows):,} files indexed under {root}")


if __name__ == "__main__":
    main()
