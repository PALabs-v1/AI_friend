"""Content-check the V3 ownership and status claims that 02-v3-foundation.md makes.

tools/check_v3_paths.py checks that named paths exist. This script checks what
02 says about them: which V3 owner holds each register item, decision and NF-n
row (read from docs/brain-research-v3/work/COVERAGE.md, which V3's own test
keeps complete), plus a few status facts read from the repo.

Usage: python3 tools/check_v3_claims.py <path to an AI_friend checkout on brain-v3>
Exit 0 when every claim holds, 1 otherwise. Prints one line per claim.
"""

import re
import sys
from pathlib import Path

# (02 table row, item id, owner 02 states). Keep in sync with 02's table.
OWNER_CLAIMS = [
    ("B", "M-5", "W1"),
    ("B", "NF-6", "W1"),
    ("B", "DR-007", "W7"),
    ("B", "DR-034", "W7"),
    ("B", "M-10", "W8"),
    ("B", "C1-1", "W8"),
    ("D", "NF-26", "W8"),
    ("E", "A-1", "W2"),
    ("E", "NF-12", "W2"),
    ("E", "NF-16", "W2"),
    ("E", "NF-17", "W2"),
    ("E", "DR-008", "W2"),
    ("E", "DR-009", "W2"),
    ("E", "DR-010", "W2"),
    ("F", "NF-18", "W2"),
    ("F", "NF-21", "W2"),
    ("F", "NF-33", "W2"),
    ("F", "NF-34", "W8"),
    ("G", "DR-019", "W9"),
    ("H", "DR-011", "W11"),
    ("H", "DR-020", "W11"),
    ("H", "DR-021", "W11"),
    ("H", "DR-023", "W11"),
    ("H", "DR-033", "W11"),
    ("H", "DR-038", "W11"),
    ("J", "DR-027", "W5"),
    ("J", "DR-028", "W5"),
    ("J", "DR-030", "W5"),
    ("K", "NF-36", "W2"),
    ("N", "NF-38", "W12"),
]

ROW = re.compile(r"^\|\s*([A-Z0-9]+-[A-Za-z0-9-]+)\s*\|\s*([A-Za-z0-9]+)\s*\|")


def coverage_owners(repo: Path) -> dict[str, str]:
    owners = {}
    for line in (
        (repo / "docs/brain-research-v3/work/COVERAGE.md").read_text().splitlines()
    ):
        m = ROW.match(line)
        if m:
            owners[m.group(1)] = m.group(2)
    return owners


def status_claims(repo: Path) -> list[tuple[str, bool, str]]:
    """Facts 02 states that are not ownership rows."""
    out = []
    v3 = repo / "docs/brain-research-v3"
    readme = (v3 / "README.md").read_text()
    for ws in ("W4", "W9", "W10a"):
        ok = (
            re.search(rf"\b{ws}\b[^\n]*merged|merged[^\n]*\b{ws}\b", readme) is not None
        )
        out.append((f"README says {ws} merged", ok, "docs/brain-research-v3/README.md"))
    fid = (v3 / "research/neuro/fidelity-table.md").read_text()
    nf_rows = [ln for ln in fid.splitlines() if ln.startswith("| NF-")]
    vision_rows = [
        ln.split("|")[1].strip()
        for ln in nf_rows
        if re.search(r"\bvision\b|\bcamera\b|\bfacial\b", ln, re.IGNORECASE)
    ]
    out.append(
        (
            "no NF-n row covers vision",
            not vision_rows,
            f"fidelity-table.md rows matching vision: {vision_rows or 'none'}",
        )
    )
    specs = sorted(
        p.name
        for p in (v3 / "work").glob("W*.md")
        if re.search(r"app/vision/", p.read_text())
    )
    out.append(
        (
            "no V3 workstream spec targets app/vision/",
            not specs,
            f"specs naming app/vision/: {specs or 'none'}",
        )
    )
    ps = repo / "backend/evals/brainbench/personality_suite.py"
    out.append(("personality suite exists", ps.exists(), str(ps.relative_to(repo))))
    reflex = (repo / "backend/app/vision/reflex.py").read_text()
    agent = (repo / "backend/app/vision/agent.py").read_text()
    conditional = "mediapipe" in (reflex + agent).lower()
    out.append(
        (
            "facial reflex is conditional on MediaPipe",
            conditional,
            "backend/app/vision/{reflex,agent}.py mention mediapipe",
        )
    )
    return out


def main() -> int:
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    owners = coverage_owners(repo)
    bad = 0
    for row, item, claimed in OWNER_CLAIMS:
        actual = owners.get(item)
        ok = actual == claimed
        bad += not ok
        print(
            f"{'OK  ' if ok else 'FAIL'} 02 row {row}: {item} owned by {claimed} (COVERAGE.md: {actual})"
        )
    for label, ok, where in status_claims(repo):
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {label} ({where})")
    print(f"{len(OWNER_CLAIMS)} ownership claims, {bad} failing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
