"""Engineering preflight, impact analysis, change tracking, status and the completion gate.

These turn the index and the claims ledger into the workflow the directive asks
for before any feature work: find what exists, trace who depends on it, see which
earlier conclusions it touches, and refuse to call a task complete while the
evidence is stale. Everything here is deterministic and read-only.
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque

from . import knowledge, structure


def _index_health(conn) -> dict:
    row = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    q = lambda sql: conn.execute(sql).fetchone()[0]
    in_scope = q("SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1")
    return {
        "snapshot": row["id"] if row else None,
        "head": row["head_commit"][:12] if row else None,
        "dirty": bool(row["dirty"]) if row else None,
        "in_scope": in_scope,
        "parsed_current": q(
            "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND parsed_hash=sha256"
        ),
        "read_current": q(
            "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND read_hash=sha256"
        ),
        "reviewed_current": q(
            "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND reviewed_hash=sha256"
        ),
        "verified_current": q(
            "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND verified_hash=sha256"
        ),
        "failed": q(
            "SELECT COUNT(*) FROM files WHERE failure_stage IS NOT NULL AND in_scope=1 AND present=1"
        ),
        "changed_since_analysis": q(
            "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND ("
            "(parsed_hash IS NOT NULL AND parsed_hash!=sha256) OR (read_hash IS NOT NULL AND read_hash!=sha256))"
        ),
    }


def _claims_summary(conn) -> dict:
    return {
        r[0]: r[1]
        for r in conn.execute("SELECT status, COUNT(*) FROM claims GROUP BY status")
    }


def status(conn) -> dict:
    knowledge.ensure_schema(conn)
    h = _index_health(conn)
    h["claims"] = _claims_summary(conn)
    h["stale_claims"] = [
        {"id": r[0], "text": r[1][:100], "why": r[2]}
        for r in conn.execute(
            "SELECT id, text, status_detail FROM claims WHERE status='stale' ORDER BY id"
        )
    ]
    h["disputed_claims"] = [
        {"id": r[0], "text": r[1][:100]}
        for r in conn.execute(
            "SELECT id, text FROM claims WHERE status='disputed' ORDER BY id"
        )
    ]
    h["unverified_claims"] = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE status='unverified'"
    ).fetchone()[0]
    return h


def render_status(s: dict) -> str:
    L = [
        f"revision {s['head']}  {'DIRTY working tree' if s['dirty'] else 'clean'}  snapshot #{s['snapshot']}",
        (
            f"files in scope {s['in_scope']}: parsed {s['parsed_current']}, read {s['read_current']}, "
            f"reviewed {s['reviewed_current']}, verified (second reader) {s['verified_current']}, "
            f"failed {s['failed']}, changed since analysis {s['changed_since_analysis']}"
        ),
        "claims: " + ", ".join(f"{k} {v}" for k, v in sorted(s["claims"].items())),
    ]
    for c in s["stale_claims"]:
        L.append(f"  STALE #{c['id']}: {c['text']}  ({c['why']})")
    for c in s["disputed_claims"]:
        L.append(f"  DISPUTED #{c['id']}: {c['text']}")
    return "\n".join(L)


def gate(conn, strict: bool = False) -> tuple[bool, list[str]]:
    """The completion gate: False with reasons while evidence is stale, disputed, failed or behind the tree."""
    s = status(conn)
    why = []
    if s["parsed_current"] != s["in_scope"]:
        why.append(
            f"{s['in_scope'] - s['parsed_current']} in-scope file(s) are not parsed at their current hash; run inventory and index"
        )
    if s["failed"]:
        why.append(f"{s['failed']} file(s) have an unresolved processing failure")
    if s["changed_since_analysis"]:
        why.append(
            f"{s['changed_since_analysis']} file(s) changed since they were analysed"
        )
    if s["stale_claims"]:
        why.append(
            f"{len(s['stale_claims'])} stale claim(s): "
            + ", ".join(f"#{c['id']}" for c in s["stale_claims"])
        )
    if s["disputed_claims"]:
        why.append(
            f"{len(s['disputed_claims'])} disputed claim(s): "
            + ", ".join(f"#{c['id']}" for c in s["disputed_claims"])
        )
    if strict and s["unverified_claims"]:
        why.append(f"{s['unverified_claims']} claim(s) never verified")
    return (not why, why)


def changes(conn) -> dict:
    rows = conn.execute(
        "SELECT path, sha256, parsed_hash, read_hash, reviewed_hash, first_snapshot FROM files WHERE in_scope=1 AND present=1"
    ).fetchall()
    latest = conn.execute("SELECT MAX(id) FROM snapshots").fetchone()[0]
    return {
        "added_in_latest_snapshot": [
            r["path"] for r in rows if r["first_snapshot"] == latest
        ],
        "changed_since_analysis": [
            r["path"]
            for r in rows
            if (r["parsed_hash"] and r["parsed_hash"] != r["sha256"])
            or (r["read_hash"] and r["read_hash"] != r["sha256"])
        ],
        "never_read": [r["path"] for r in rows if not r["read_hash"]],
        "stale_claims": [
            r[0] for r in conn.execute("SELECT id FROM claims WHERE status='stale'")
        ],
    }


def _file_row(conn, path):
    return conn.execute(
        "SELECT id, path, language FROM files WHERE path=? AND present=1", (path,)
    ).fetchone()


def impact(conn, path: str, depth: int = 4) -> dict:
    """Who is affected if this file changes: transitive importers, tests, subjects, claims."""
    f = _file_row(conn, path)
    if f is None:
        raise ValueError(f"{path} is not in the inventory")
    seen = {f["id"]: 0}
    q = deque([f["id"]])
    while q:
        cur = q.popleft()
        if seen[cur] >= depth:
            continue
        for r in conn.execute(
            "SELECT DISTINCT file_id FROM edges WHERE dst_file_id=? AND kind IN ('IMPORTS','TEST_VALIDATES') AND confidence IN ('proven','lexical')",
            (cur,),
        ):
            if r[0] not in seen:
                seen[r[0]] = seen[cur] + 1
                q.append(r[0])
    ids = list(seen)
    paths = {
        r["id"]: r["path"]
        for r in conn.execute(
            f"SELECT id, path FROM files WHERE id IN ({','.join('?' * len(ids))})", ids
        )
    }
    affected = sorted((seen[i], paths[i]) for i in ids if i != f["id"])
    tests = [
        p
        for _, p in affected
        if "/tests/" in p
        or p.startswith("tests/")
        or p.rsplit("/", 1)[-1].startswith("test_")
    ]
    subj = [
        (r["kind"], r["dst"], r["path"])
        for r in conn.execute(
            f"SELECT e.kind, e.dst, f.path FROM edges e JOIN files f ON f.id=e.file_id WHERE e.file_id IN ({','.join('?' * len(ids))}) "
            "AND e.kind IN ('PUBLISHES','SUBSCRIBES') AND e.confidence!='unresolved'",
            ids,
        )
    ]
    touched = set(paths.values())
    claims = [
        {"id": r["id"], "status": r["status"], "text": r["text"][:110]}
        for r in conn.execute(
            "SELECT id, status, text, check_json, inputs_json FROM claims ORDER BY id"
        )
        if touched & set(json.loads(r["inputs_json"] or "{}"))
        or any(
            ev[0] in touched
            for ev in conn.execute(
                "SELECT path FROM claim_evidence WHERE claim_id=?", (r["id"],)
            ).fetchall()
        )
    ]
    index_claims = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE inputs_json LIKE '%*index*%'"
    ).fetchone()[0]
    return {
        "target": path,
        "importers_by_distance": affected,
        "tests_reached": tests,
        "bus_subjects_in_blast_radius": sorted(set(subj))[:60],
        "claims_citing_these_files": claims,
        "index_wide_claims_to_reverify": index_claims,
        "caveat": "static imports only; dynamic loading, config-selected classes and bus consumers outside these files are not followed",
    }


def preflight(conn, target: str) -> dict:
    """Step B and C of the directive's preflight for one symbol or file."""
    out: dict = {"target": target, "index": _index_health(conn)}
    if _file_row(conn, target):
        out["kind"] = "file"
        out["impact"] = impact(conn, target)
        out["symbols"] = [
            f"{r['kind']} {r['qualname']}:{r['line']}"
            for r in conn.execute(
                "SELECT s.kind, s.qualname, s.line FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path=? "
                "AND s.kind IN ('class','function','method','struct','fn','impl') ORDER BY s.line LIMIT 40",
                (target,),
            )
        ]
        out["reads_env"] = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT e.dst FROM edges e JOIN files f ON f.id=e.file_id WHERE f.path=? AND e.kind='READS_ENV'",
                (target,),
            )
        ]
    else:
        out["kind"] = "symbol"
        defs = structure.find_symbols(conn, target, 20)
        out["definitions"] = [
            f"{d['path']}:{d['line']} {d['kind']} {d['qualname']}" for d in defs
        ]
        refs = structure.refs_to(conn, target, 200)
        by_conf: dict[str, int] = {}
        for r in refs:
            by_conf[r["confidence"]] = by_conf.get(r["confidence"], 0) + 1
        out["references_by_confidence"] = by_conf
        out["sample_references"] = [
            f"[{r['confidence']}] {r['src_path']}:{r['line']} {r['kind']}"
            for r in refs[:10]
        ]
        out["called_from_tests"] = sum(
            1
            for r in refs
            if r["kind"] in ("CALLS", "STARTS_TASK")
            and ("/tests/" in r["src_path"] or r["src_path"].startswith("tests/"))
        )
        out["subject_matches"] = [
            s for s in structure.subject_matrix(conn) if target in s
        ][:10]
        out["name_exists_nowhere"] = not defs and not out["subject_matches"]
        out["dynamic_constructs_in_repo"] = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='DYNAMIC'"
        ).fetchone()[0]
    out["related_claims"] = [
        {"id": r["id"], "status": r["status"], "text": r["text"][:120]}
        for r in conn.execute(
            "SELECT id, status, text FROM claims WHERE text LIKE ? ORDER BY id LIMIT 12",
            (f"%{target.rsplit('/', 1)[-1]}%",),
        )
    ]
    out["advice"] = [
        "A symbol that is not found may exist under another name, in Rust, or behind a dynamic construct; search synonyms before building.",
        "Read the confidence tag on every reference: inferred edges can be wrong.",
        "Record what you decide as a claim so a later change invalidates it.",
    ]
    return out


def render(d: dict) -> str:
    return json.dumps(d, indent=2, default=list)


def connect_readonly_check(conn: sqlite3.Connection) -> None:
    conn.execute("SELECT 1 FROM snapshots LIMIT 1")
