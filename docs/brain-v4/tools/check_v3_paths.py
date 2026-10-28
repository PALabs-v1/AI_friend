"""Check that every AI_friend repo path named in the brain-v4 docs still exists.

Usage (from brain-v4/): python3 tools/check_v3_paths.py <path-to-AI_friend-checkout>
The checkout must be on branch brain-v3 (or main once PR #217 merges).

Two kinds of reference are checked:
- repo-root paths in backticks: `backend/...`, `docs/...`, `scripts/...`
- V3 doc names written without the docs/brain-research-v3/ prefix, such as
  `11-v2-lifesim-baseline.md` or `research/neuro/fidelity-table.md`, resolved
  against docs/brain-research-v3/. Names that are brain-v4's own files are
  skipped.
Exits 1 and lists the missing paths if any are gone.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO_ROOT_PATH = re.compile(r"`((?:backend|docs|scripts)/[A-Za-z0-9_./-]+?)`")
V3_DOC = re.compile(
    r"`((?:research/neuro|decisions|work|adr)/[A-Za-z0-9_.-]+\.md|\d\d-[A-Za-z0-9-]+\.md)`"
)
V3_DOCS_DIR = "docs/brain-research-v3"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    repo = Path(sys.argv[1]).expanduser().resolve()
    own = {p.name for p in HERE.glob("*.md")}
    missing: dict[str, set[str]] = {}
    checked: set[str] = set()
    for doc in sorted(HERE.glob("*.md")):
        text = doc.read_text()
        targets = [p.rstrip(".") for p in REPO_ROOT_PATH.findall(text)]
        targets += [
            f"{V3_DOCS_DIR}/{name}" for name in V3_DOC.findall(text) if name not in own
        ]
        for path in targets:
            checked.add(path)
            if not (repo / path).exists():
                missing.setdefault(path, set()).add(doc.name)
    print(f"checked {len(checked)} repo paths named in {HERE.name}/*.md against {repo}")
    for path, docs in sorted(missing.items()):
        print(f"MISSING {path}  (named in {', '.join(sorted(docs))})")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
