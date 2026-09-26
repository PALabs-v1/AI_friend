"""Seed partitions, enforced in code rather than by convention.

Held-out seeds exist to be run once, at the end. Generating them requires an
explicit ``final_run`` flag, and every held-out generation appends a line to
the held-out log so "we only ran it once" is checkable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

SPLITS: dict[str, range] = {
    "dev": range(1000, 1100),
    "tune": range(2000, 2200),
    "validation": range(3000, 3100),
    "heldout": range(9000, 9100),
}

DEFAULT_HELDOUT_LOG = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "brain-research-v3"
    / "results"
    / "HELDOUT_LOG.md"
)


class SplitError(ValueError):
    pass


def split_of(seed: int) -> str:
    for name, seeds in SPLITS.items():
        if seed in seeds:
            return name
    raise SplitError(
        f"seed {seed} is in no split; use one of "
        + ", ".join(f"{n} {r.start}-{r.stop - 1}" for n, r in SPLITS.items())
    )


def authorize(
    seed: int, *, final_run: bool = False, log_path: Path | None = None, note: str = ""
) -> str:
    """Return the seed's split, refusing held-out seeds unless this is the final run."""
    split = split_of(seed)
    if split != "heldout":
        return split
    if not final_run:
        raise SplitError(
            f"seed {seed} is held-out; pass --final-run only for the one final evaluation"
        )
    path = log_path or DEFAULT_HELDOUT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Held-out log\n\nOne line per held-out generation or evaluation. Never tune after a line appears here.\n\n"
        )
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a") as fh:
        fh.write(f"- {stamp} seed={seed} {note}".rstrip() + "\n")
    return split
