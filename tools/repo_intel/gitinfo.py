"""Read-only git queries. Never writes to the repository or its index.

GIT_OPTIONAL_LOCKS=0 stops `git status` from refreshing the index, so running
the audit cannot change repository state. Every list is NUL-separated so that
newlines, spaces, quotes and non-UTF-8 bytes in file names survive intact.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> bytes:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, env=env, check=False
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def repo_root(start: Path) -> Path:
    out = (
        _git(start, "rev-parse", "--show-toplevel")
        .decode("utf-8", "surrogateescape")
        .strip()
    )
    return Path(out)


def head_commit(root: Path) -> str:
    try:
        return _git(root, "rev-parse", "HEAD").decode().strip()
    except GitError:
        return "UNBORN"


def branch(root: Path) -> str:
    return _git(root, "branch", "--show-current").decode().strip() or "DETACHED"


@dataclass(frozen=True)
class Tracked:
    path_b: bytes
    mode: str
    blob: str


def tracked(root: Path) -> list[Tracked]:
    out = _git(root, "ls-files", "-s", "-z")
    entries = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, blob, stage = meta.decode().split(" ")
        if stage != "0":
            continue  # unmerged entries appear once per stage; stage 0 is the resolved one
        entries.append(Tracked(path, mode, blob))
    return entries


def untracked(root: Path) -> list[bytes]:
    out = _git(root, "ls-files", "-z", "--others", "--exclude-standard")
    return [p for p in out.split(b"\0") if p]


def ignored(root: Path) -> list[bytes]:
    """Ignored paths with untracked directories collapsed (a trailing / marks one)."""
    out = _git(
        root,
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
    )
    return [p for p in out.split(b"\0") if p]


def status(root: Path) -> dict[bytes, str]:
    """Map path -> two-letter XY code for every tracked change (untracked excluded)."""
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    recs = out.split(b"\0")
    result: dict[bytes, str] = {}
    i = 0
    while i < len(recs):
        rec = recs[i]
        i += 1
        if len(rec) < 4:
            continue
        xy, path = rec[:2].decode(), rec[3:]
        if xy[0] in "RC":
            i += 1  # the original path follows as its own NUL field
        result[path] = xy
    return result
