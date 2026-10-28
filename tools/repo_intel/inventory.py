"""Deterministic repository inventory and reconciliation.

One scan = one transaction. If anything raises, the index is exactly as it was,
so an interrupted scan can simply be run again.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import gitinfo
from .db import log_event, txn
from .scope import Rule, classify, load_rules

TOOL_VERSION = "0.1.0"
IGNORED_WALK_CAP = 500_000

IGNORED_REASONS = (
    (".env", "Local environment file. May hold secrets; never opened."),
    (".venv", "Python virtual environment (third-party dependencies)."),
    ("venv", "Python virtual environment (third-party dependencies)."),
    ("node_modules", "Node dependency directory."),
    ("target", "Rust build output."),
    ("__pycache__", "Python bytecode cache."),
    (".pytest_cache", "Tool cache."),
    (".ruff_cache", "Tool cache."),
    (".mypy_cache", "Tool cache."),
    (".benchmarks", "Benchmark cache."),
    (".repo-intel", "This tool's own index."),
    (
        ".claude/worktrees",
        "Nested git worktrees of other sessions; not part of this checkout.",
    ),
    (".claude/settings", "Local agent settings. May hold secrets; never opened."),
    (".vercel", "Deploy tool cache."),
    (".vscode", "Editor settings."),
    (".DS_Store", "macOS metadata."),
)


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def display(path_b: bytes) -> str:
    return path_b.decode("utf-8", "backslashreplace")


@dataclass
class Observation:
    path_b: bytes
    tracked: bool
    git_blob: str | None
    wt_state: str
    kind: str = "file"
    present: bool = True
    symlink_target: str | None = None
    symlink_in_repo: bool = True
    size: int = 0
    sha256: str = ""
    is_binary: bool = False
    utf8_ok: bool = True
    line_count: int = 0
    failure: str | None = None


def observe(
    root: Path, path_b: bytes, *, tracked: bool, mode: str, blob: str | None, xy: str
) -> Observation:
    """Read one file from the working tree. Never follows symlinks, never executes anything."""
    obs = Observation(
        path_b, tracked, blob, xy.strip() or ("untracked" if not tracked else "clean")
    )
    full = Path(os.fsdecode(path_b))
    full = root / full
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        obs.present, obs.kind, obs.wt_state = False, "file", "missing"
        return obs
    except OSError as exc:
        obs.failure = f"lstat failed: {exc}"
        return obs
    if mode == "160000":
        obs.kind = "submodule"
        obs.sha256 = hashlib.sha256((blob or "").encode()).hexdigest()
        return obs
    if stat.S_ISLNK(st.st_mode):
        obs.kind = "symlink"
        target = os.readlink(full)
        obs.symlink_target = target
        obs.sha256 = hashlib.sha256(
            target.encode("utf-8", "surrogateescape")
        ).hexdigest()
        resolved = (full.parent / target).resolve()
        obs.symlink_in_repo = (
            resolved.is_relative_to(root.resolve()) and resolved.exists()
        )
        return obs
    if not stat.S_ISREG(st.st_mode):
        obs.failure = f"not a regular file (mode {oct(st.st_mode)})"
        return obs
    obs.size = st.st_size
    try:
        data = full.read_bytes()
    except OSError as exc:
        obs.failure = f"unreadable: {exc}"
        return obs
    obs.sha256 = hashlib.sha256(data).hexdigest()
    obs.size = len(data)
    obs.is_binary = b"\0" in data[:8192]
    if not obs.is_binary:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            obs.utf8_ok = False
        obs.line_count = data.count(b"\n") + (
            1 if data and not data.endswith(b"\n") else 0
        )
    return obs


def gather(root: Path) -> list[Observation]:
    status = gitinfo.status(root)
    seen: dict[bytes, Observation] = {}
    for t in gitinfo.tracked(root):
        seen[t.path_b] = observe(
            root,
            t.path_b,
            tracked=True,
            mode=t.mode,
            blob=t.blob,
            xy=status.get(t.path_b, ""),
        )
    for p in gitinfo.untracked(root):
        if p not in seen:
            seen[p] = observe(root, p, tracked=False, mode="100644", blob=None, xy="")
    return [seen[k] for k in sorted(seen)]


def _walk_count(path: Path) -> tuple[int, int, bool]:
    files = size = 0
    capped = False
    for dirpath, _dirs, names in os.walk(path, followlinks=False):
        for n in names:
            files += 1
            try:
                size += os.lstat(os.path.join(dirpath, n)).st_size
            except OSError:
                pass
            if files >= IGNORED_WALK_CAP:
                return files, size, True
    return files, size, capped


def ignored_buckets(root: Path) -> list[tuple[str, bool, int, int, str]]:
    """Ignored paths with counts and sizes. Names and sizes only; contents are never opened."""
    rows = []
    for p in gitinfo.ignored(root):
        text = display(p)
        is_dir = text.endswith("/")
        rel = text.rstrip("/")
        full = root / os.fsdecode(p.rstrip(b"/"))
        reason = "Ignored by .gitignore or .git/info/exclude."
        for needle, why in IGNORED_REASONS:
            if (
                rel.endswith(needle)
                or f"/{needle}" in f"/{rel}"
                or rel.startswith(needle)
            ):
                reason = why
                break
        if is_dir:
            n, size, capped = _walk_count(full)
            if capped:
                reason += f" Count stopped at {IGNORED_WALK_CAP:,} files."
        else:
            try:
                n, size = 1, os.lstat(full).st_size
            except OSError:
                n, size = 1, 0
        rows.append((rel, is_dir, n, size, reason))
    rows.append((".git", True, *_walk_count(root / ".git")[:2], "Git metadata."))
    return rows


@dataclass
class Summary:
    snapshot_id: int
    head: str
    branch: str
    dirty: bool
    fingerprint: str
    present: int
    in_scope: int
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    scope_changed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    applied: bool = True

    @property
    def drift(self) -> bool:
        return bool(self.added or self.removed or self.modified or self.scope_changed)


def scan(
    root: Path,
    conn: sqlite3.Connection,
    *,
    apply: bool = True,
    actor: str = "repo-intel",
    rules: list[Rule] | None = None,
    scope_hash: str | None = None,
) -> Summary:
    """Reconcile the index against the filesystem. apply=False reports drift and changes nothing."""
    if rules is None or scope_hash is None:
        rules, scope_hash = load_rules()
    observations = gather(root)
    head, branch = gitinfo.head_commit(root), gitinfo.branch(root)
    dirty = bool(gitinfo.status(root)) or any(not o.tracked for o in observations)
    ignored = ignored_buckets(root)
    now = utcnow()

    class _Rollback(Exception):
        pass

    summary: Summary | None = None
    try:
        with txn(conn):
            existing = {
                bytes(r["path_b"]): r for r in conn.execute("SELECT * FROM files")
            }
            snap_id = conn.execute(
                "INSERT INTO snapshots (created_at, head_commit, branch, dirty, tree_fingerprint, scope_hash, tool_version,"
                " files_present, in_scope, added, removed, modified, scope_changed) VALUES (?,?,?,?,?,?,?,0,0,0,0,0,0)",
                (now, head, branch, int(dirty), "", scope_hash, TOOL_VERSION),
            ).lastrowid
            summary = Summary(snap_id, head, branch, dirty, "", 0, 0, applied=apply)
            h = hashlib.sha256()
            for o in observations:
                verdict = classify(
                    display(o.path_b),
                    kind=o.kind,
                    is_binary=o.is_binary,
                    present=o.present,
                    rules=rules,
                    symlink_target_in_repo=o.symlink_in_repo,
                )
                name = display(o.path_b)
                if o.failure:
                    summary.failures.append(f"{name}: {o.failure}")
                if o.present:
                    h.update(o.path_b + b"\0" + o.sha256.encode() + b"\n")
                    summary.present += 1
                    summary.in_scope += int(verdict.in_scope)
                prev = existing.pop(o.path_b, None)
                _upsert(conn, summary, o, verdict, prev, snap_id, name)
            for path_b, prev in existing.items():
                if prev["present"]:
                    summary.removed.append(display(path_b))
                    conn.execute(
                        "UPDATE files SET present=0, wt_state='missing', last_snapshot=? WHERE id=?",
                        (snap_id, prev["id"]),
                    )
            summary.fingerprint = h.hexdigest()
            conn.execute("DELETE FROM ignored_paths WHERE snapshot_id < ?", (snap_id,))
            conn.executemany(
                "INSERT INTO ignored_paths VALUES (?,?,?,?,?,?)",
                [(snap_id, p, int(d), n, b, r) for p, d, n, b, r in ignored],
            )
            conn.execute(
                "UPDATE snapshots SET tree_fingerprint=?, files_present=?, in_scope=?, added=?, removed=?, modified=?, scope_changed=? WHERE id=?",
                (
                    summary.fingerprint,
                    summary.present,
                    summary.in_scope,
                    len(summary.added),
                    len(summary.removed),
                    len(summary.modified),
                    len(summary.scope_changed),
                    snap_id,
                ),
            )
            log_event(
                conn,
                now,
                actor,
                "inventory" if apply else "reconcile-dry-run",
                f"head={head} dirty={int(dirty)} present={summary.present} in_scope={summary.in_scope} +{len(summary.added)} -{len(summary.removed)} ~{len(summary.modified)}",
            )
            if not apply:
                raise _Rollback
    except _Rollback:
        pass
    assert summary is not None
    return summary


def _upsert(
    conn: sqlite3.Connection,
    s: Summary,
    o: Observation,
    v,
    prev: sqlite3.Row | None,
    snap_id: int,
    name: str,
) -> None:
    present = int(o.present)
    cols = {
        "path": name,
        "present": present,
        "tracked": int(o.tracked),
        "git_blob": o.git_blob,
        "wt_state": o.wt_state,
        "kind": o.kind,
        "symlink_target": o.symlink_target,
        "size": o.size,
        "sha256": o.sha256,
        "is_binary": int(o.is_binary),
        "utf8_ok": int(o.utf8_ok),
        "line_count": o.line_count,
        "category": v.category,
        "language": v.language,
        "parser": v.parser,
        "in_scope": int(v.in_scope),
        "exclusion_reason": "" if v.in_scope else v.reason,
        "scope_rule": v.rule,
        "last_snapshot": snap_id,
    }
    if prev is None:
        if present:
            s.added.append(name)
        cols.update(
            first_snapshot=snap_id, path_b=o.path_b, classified_hash=o.sha256 or None
        )
        _apply_failure(cols, o)
        keys = ", ".join(cols)
        conn.execute(
            f"INSERT INTO files ({keys}) VALUES ({', '.join('?' * len(cols))})",
            list(cols.values()),
        )
        return
    if present and not prev["present"]:
        s.added.append(name)
    elif present and prev["sha256"] != o.sha256:
        s.modified.append(name)
    elif not present and prev["present"]:
        s.removed.append(name)  # still in git's index but deleted from the working tree
    if present and bool(prev["in_scope"]) != v.in_scope:
        s.scope_changed.append(name)
    cols["classified_hash"] = o.sha256 or None
    _apply_failure(cols, o, prev)
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE files SET {sets} WHERE id=?", [*cols.values(), prev["id"]])


def _apply_failure(cols: dict, o: Observation, prev: sqlite3.Row | None = None) -> None:
    if o.failure:
        cols.update(
            failure_stage="inventory", failure_hash=o.sha256, failure_msg=o.failure
        )
    elif prev is not None and prev["failure_stage"] == "inventory":
        cols.update(failure_stage=None, failure_hash=None, failure_msg=None)
