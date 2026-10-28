"""Phase 3 and 4 commands: claims, evidence, staleness, verification, independent review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import knowledge
from .db import connect


def _conn(a):
    from .cli import _paths

    root, db = _paths(a)
    conn = connect(db)
    knowledge.ensure_schema(conn)
    return root, conn


def cmd_claims(a: argparse.Namespace) -> int:
    root, conn = _conn(a)
    act = a.action
    try:
        if act == "add":
            src = (
                sys.stdin
                if a.target == "-"
                else Path(a.target).read_text().splitlines()
            )
            n = 0
            for line in src:
                if line.strip():
                    cid = knowledge.add_claim(
                        conn, root, json.loads(line), a.author, a.family
                    )
                    print(f"claim {cid} recorded")
                    n += 1
            print(f"{n} claim(s) recorded")
        elif act == "list":
            for r in knowledge.list_claims(conn, a.status):
                neg = " (negative)" if r["negative"] else ""
                print(
                    f"#{r['id']:<4} {r['status']:<10} {r['severity']:<8} {r['kind']:<12}{neg} {r['text'][:110]}"
                )
            print(
                "-- "
                + ", ".join(
                    f"{k} {v}" for k, v in sorted(knowledge.summary(conn).items())
                )
            )
        elif act == "show":
            c = conn.execute(
                "SELECT * FROM claims WHERE id=?", (int(a.target),)
            ).fetchone()
            print(json.dumps(dict(c), indent=2))
            for h in conn.execute(
                "SELECT ts, actor, from_status, to_status, reason FROM claim_history WHERE claim_id=?",
                (c["id"],),
            ):
                print(
                    f"  {h['ts']} {h['actor']}: {h['from_status']} -> {h['to_status']} ({h['reason']})"
                )
        elif act == "verify":
            ids = (
                [r[0] for r in conn.execute("SELECT id FROM claims ORDER BY id")]
                if a.target in (None, "all")
                else [int(a.target)]
            )
            for cid in ids:
                print(
                    f"#{cid} -> {knowledge.verify_claim(conn, root, cid, a.author, a.runtime)}"
                )
        elif act == "supersede":
            knowledge.supersede(conn, int(a.target), int(a.by), a.author)
            print(f"claim {a.target} superseded by {a.by}")
        elif act == "recheck":
            print(knowledge.recheck(conn, root, a.author))
        elif act == "packet":
            print(knowledge.packet(conn, root, int(a.target)))
        elif act == "review":
            print(
                knowledge.review(
                    conn, root, int(a.target), a.reader, a.family, a.verdict, a.note
                )
            )
    except knowledge.ClaimError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def register(add) -> None:
    sp = add(
        "claims",
        cmd_claims,
        "evidence-backed claims: add, list, show, verify, recheck, packet, review",
    )
    sp.add_argument(
        "action",
        choices=[
            "add",
            "list",
            "show",
            "verify",
            "recheck",
            "packet",
            "review",
            "supersede",
        ],
    )
    sp.add_argument(
        "target", nargs="?", help="claim id, JSONL file for add (- for stdin), or all"
    )
    sp.add_argument("--file", help=argparse.SUPPRESS)
    sp.add_argument("--status")
    sp.add_argument("--by", default="", help="supersede: the corrected claim id")
    sp.add_argument("--author", default="repo-intel")
    sp.add_argument("--family", default="human")
    sp.add_argument("--reader", default="")
    sp.add_argument("--verdict", default="")
    sp.add_argument("--note", default="")
    sp.add_argument(
        "--runtime",
        action="store_true",
        help="run runtime checks (programs from an allowlist, no shell)",
    )
    sp.set_defaults(file=None)
