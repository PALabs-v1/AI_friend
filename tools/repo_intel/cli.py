"""repo-intel command line.

Exit codes: 0 ok; 1 work remains (coverage --strict) or a request failed; 2 the index
disagrees with the working tree or is corrupt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import batch, coverage, gitinfo, inventory
from .db import CorruptIndex, connect

EXIT_OK, EXIT_INCOMPLETE, EXIT_DRIFT = 0, 1, 2


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    root = gitinfo.repo_root(Path(args.root).resolve() if args.root else Path.cwd())
    db = Path(
        args.db or os.environ.get("REPO_INTEL_DB") or root / ".repo-intel" / "index.db"
    )
    return root, db


def _summary_dict(s: inventory.Summary) -> dict:
    return {
        "snapshot": s.snapshot_id,
        "head": s.head,
        "branch": s.branch,
        "dirty": s.dirty,
        "fingerprint": s.fingerprint,
        "present": s.present,
        "in_scope": s.in_scope,
        "added": s.added,
        "removed": s.removed,
        "modified": s.modified,
        "scope_changed": s.scope_changed,
        "failures": s.failures,
        "applied": s.applied,
    }


def _print_drift(s: inventory.Summary, limit: int = 15) -> None:
    for label, items in (
        ("added", s.added),
        ("removed", s.removed),
        ("modified", s.modified),
        ("scope flipped", s.scope_changed),
    ):
        if items:
            shown = ", ".join(items[:limit]) + (
                f" ... (+{len(items) - limit})" if len(items) > limit else ""
            )
            print(f"  {label:<14}{len(items):>5}  {shown}")


def cmd_inventory(a: argparse.Namespace) -> int:
    root, db = _paths(a)
    s = inventory.scan(root, connect(db), actor=a.actor)
    if a.json:
        print(json.dumps(_summary_dict(s), indent=2))
    else:
        print(
            f"inventory: snapshot #{s.snapshot_id} {s.head[:12]} {'dirty' if s.dirty else 'clean'}: {s.present:,} files present, {s.in_scope:,} in scope"
        )
        _print_drift(s)
        for f in s.failures:
            print(f"  FAILED {f}")
    return EXIT_OK if not s.failures else EXIT_INCOMPLETE


def cmd_reconcile(a: argparse.Namespace) -> int:
    root, db = _paths(a)
    s = inventory.scan(root, connect(db), apply=False, actor=a.actor)
    if a.json:
        print(json.dumps(_summary_dict(s), indent=2))
    elif s.drift:
        print(
            "DRIFT: the index no longer matches the working tree (run `inventory` to accept it):"
        )
        _print_drift(s)
    else:
        print(
            f"reconciled: index matches the working tree ({s.present:,} files, fingerprint {s.fingerprint[:16]})"
        )
    return EXIT_DRIFT if s.drift else EXIT_OK


def cmd_coverage(a: argparse.Namespace) -> int:
    root, db = _paths(a)
    conn = connect(db)
    c = coverage.collect(conn)
    if a.md:
        Path(a.md).write_text(coverage.render_md(c), encoding="utf-8")
    print(
        json.dumps(c, indent=2, default=str) if a.json else coverage.render_text(c),
        end="",
    )
    if a.strict:
        if inventory.scan(root, conn, apply=False, actor=a.actor).drift:
            print(
                "STRICT: the index is out of date against the working tree; run `inventory`.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        if c["unread"] or c["failed"]:
            return EXIT_INCOMPLETE
    return EXIT_OK


def cmd_claim(a: argparse.Namespace) -> int:
    root, db = _paths(a)
    bid, text, warnings = batch.claim(
        connect(db),
        root,
        reader=a.reader,
        tool_family=a.family,
        max_bytes=a.max_bytes,
        max_files=a.max_files,
        prefix=a.prefix,
        ttl_min=a.ttl,
    )
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if bid is None:
        print(
            "nothing left to claim" + (f" under {a.prefix!r}" if a.prefix else ""),
            file=sys.stderr,
        )
        return EXIT_OK if not warnings else EXIT_DRIFT
    print(text, end="")
    print(
        f"claimed {bid}; acknowledge with: ack {bid} --reader {a.reader} --notes <file.jsonl>",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_ack(a: argparse.Namespace) -> int:
    root, db = _paths(a)
    raw = (
        sys.stdin.read()
        if a.notes == "-"
        else Path(a.notes).read_text(encoding="utf-8")
    )
    notes = [json.loads(line) for line in raw.splitlines() if line.strip()]
    try:
        report = batch.ack(
            connect(db), root, batch_id=a.batch, reader=a.reader, notes=notes
        )
    except ValueError as exc:
        print(f"ack refused: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
    bad = {p: r for p, r in report.items() if not r["accepted"]}
    for p, r in report.items():
        flag = "ok " if r["accepted"] else "REJECTED"
        extra = r["reason"] or (
            "read"
            if r["read"]
            else f"{r.get('chunks_remaining', 0)} chunk(s) still unread"
        )
        print(
            f"  {flag:<9}{p}  {extra}{'  reviewed' if r.get('reviewed') else ''}{'  SYMBOL NOT FOUND IN FILE' if r['accepted'] and not r.get('symbols_ok', True) else ''}"
        )
    print(
        f"accepted {len(report) - len(bad)}/{len(report)}; rejected files return to the unread pool"
    )
    return EXIT_OK if not bad else EXIT_INCOMPLETE


def cmd_release(a: argparse.Namespace) -> int:
    _, db = _paths(a)
    print(f"expired {batch.release_expired(connect(db), a.ttl)} stale claim(s)")
    return EXIT_OK


def cmd_files(a: argparse.Namespace) -> int:
    _, db = _paths(a)
    conn = connect(db)
    where = {
        "unread": "present=1 AND in_scope=1 AND (read_hash IS NULL OR read_hash != sha256)",
        "stale": "present=1 AND in_scope=1 AND read_hash IS NOT NULL AND read_hash != sha256",
        "unreviewed": "present=1 AND in_scope=1 AND (reviewed_hash IS NULL OR reviewed_hash != sha256)",
        "failed": "present=1 AND failure_stage IS NOT NULL",
        "excluded": "present=1 AND in_scope=0",
        "all": "present=1",
    }[a.state]
    rows = conn.execute(
        f"SELECT path, category, size, exclusion_reason FROM files WHERE {where} AND path LIKE ? ORDER BY path_b",
        (a.prefix + "%",),
    ).fetchall()
    for r in rows:
        print(
            f"{r['category']:<22}{r['size']:>10}  {r['path']}"
            + (f"  [{r['exclusion_reason'][:60]}]" if a.state == "excluded" else "")
        )
    print(f"{len(rows):,} file(s)", file=sys.stderr)
    return EXIT_OK


def cmd_show(a: argparse.Namespace) -> int:
    _, db = _paths(a)
    conn = connect(db)
    f = conn.execute("SELECT * FROM files WHERE path=?", (a.path,)).fetchone()
    if f is None:
        print(f"{a.path}: not in the index", file=sys.stderr)
        return EXIT_INCOMPLETE
    stage = {
        s: (f[f"{s}_hash"] == f["sha256"]) if f[f"{s}_hash"] else None
        for s in coverage.STAGES
    }
    row = {k: v for k, v in zip(f.keys(), tuple(f), strict=True) if k != "path_b"}
    print(json.dumps(row | {"stages_current": stage}, indent=2, default=str))
    for n in conn.execute(
        "SELECT reader, tool_family, role, summary, symbols, symbols_ok, created_at FROM notes WHERE file_id=? ORDER BY id",
        (f["id"],),
    ):
        print(json.dumps(dict(n), indent=2))
    return EXIT_OK


def cmd_resume(a: argparse.Namespace) -> int:
    """Compact grounding for a new session: where the index stands and the next action."""
    root, db = _paths(a)
    conn = connect(db)
    c = coverage.collect(conn)
    s = inventory.scan(root, conn, apply=False, actor=a.actor)
    print(coverage.headline(c))
    print(
        f"index snapshot #{c['snapshot']['id']} at {c['snapshot']['head_commit'][:12]}; working tree now {gitinfo.head_commit(root)[:12]} ({'drift' if s.drift else 'no drift'})"
    )
    if s.drift:
        _print_drift(s, 8)
    print(
        f"failed {c['failed']}, changed since analysis {c['changed_since_analysis']}, open claims {c['open_batches']}"
    )
    nxt = (
        "run `inventory` to absorb drift"
        if s.drift
        else "claim the next batch: `claim --reader NAME --family FAMILY`"
        if c["unread"]
        else "all in-scope files read: review notes, then verify with a second reader"
    )
    print(f"next: {nxt}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="repo-intel", description=__doc__.splitlines()[0])
    p.add_argument(
        "--root", help="repository path (default: git root of the current directory)"
    )
    p.add_argument(
        "--db",
        help="index path (default: <root>/.repo-intel/index.db or $REPO_INTEL_DB)",
    )
    p.add_argument(
        "--actor", default="repo-intel", help="name recorded in the audit log"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add(
        "inventory", cmd_inventory, "scan the working tree and update the index"
    ).add_argument("--json", action="store_true")
    add(
        "reconcile",
        cmd_reconcile,
        "report drift between index and working tree, change nothing",
    ).add_argument("--json", action="store_true")
    sp = add("coverage", cmd_coverage, "print the coverage report")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--md", help="also write a Markdown snapshot to this path")
    sp.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if files are unread or failed, 2 on drift",
    )
    sp = add("claim", cmd_claim, "reserve and print the next batch of unread files")
    sp.add_argument("--reader", required=True)
    sp.add_argument(
        "--family", required=True, help="tool family: claude, codex, agy, human"
    )
    sp.add_argument("--max-bytes", type=int, default=150_000)
    sp.add_argument("--max-files", type=int, default=60)
    sp.add_argument("--prefix", default="", help="only paths starting with this")
    sp.add_argument(
        "--ttl",
        type=int,
        default=batch.DEFAULT_TTL_MIN,
        help="minutes before an unacked claim expires",
    )
    sp = add("ack", cmd_ack, "acknowledge a batch with per-file notes (JSONL)")
    sp.add_argument("batch")
    sp.add_argument("--reader", required=True)
    sp.add_argument("--notes", required=True, help="JSONL path or - for stdin")
    add("release-expired", cmd_release, "expire stale claims").add_argument(
        "--ttl", type=int, default=batch.DEFAULT_TTL_MIN
    )
    sp = add("files", cmd_files, "list files by state")
    sp.add_argument(
        "state", choices=["unread", "stale", "unreviewed", "failed", "excluded", "all"]
    )
    sp.add_argument("--prefix", default="")
    add("show", cmd_show, "one file's row and notes").add_argument("path")
    add("resume", cmd_resume, "compact grounding for a new session")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except CorruptIndex as exc:
        print(f"index problem: {exc}", file=sys.stderr)
        return EXIT_DRIFT
    except (gitinfo.GitError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
