"""Phases 3 and 4: evidence-backed claims, staleness, and independent verification.

A claim is a statement about the repository ("X has no caller", "the default
is 4500"). It earns a status only through evidence bound to file hashes:

  unverified  recorded, nothing has checked it yet
  supported   its deterministic check passed (or its evidence quotes are intact)
  refuted     its deterministic check failed against the current code
  stale       a file it depends on changed and the evidence can no longer be found
  disputed    two layers disagree (check vs reviewer, or reviewer vs reviewer)
  verified    deterministic check passed AND an independent reader agreed

Layers: 1 deterministic checks (this file), 2 independent reader (`review`;
the reader must not be the author), 3 runtime commands (`--runtime` only, no
shell, allowlisted programs), 4 reconciliation (`disputed`).

Negative claims ("no caller", "not used") can never be `verified` by static
analysis alone: the index cannot see dynamic dispatch. They stop at `supported`
unless a runtime check backs them.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import structure

DDL = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    classification TEXT NOT NULL DEFAULT 'n/a',
    source TEXT NOT NULL DEFAULT '',
    check_json TEXT NOT NULL DEFAULT '',
    negative INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    status_detail TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL,
    author_family TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    inputs_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS claim_evidence (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims (id),
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    quote TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_history (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims (id),
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims (id),
    reader TEXT NOT NULL,
    family TEXT NOT NULL,
    verdict TEXT NOT NULL,
    note TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

CLASSIFICATIONS = {
    "n/a",
    "still_supported",
    "superseded",
    "contradicted",
    "incomplete",
    "unverifiable",
}
NEGATIVE_CHECKS = {
    "no_static_callers",
    "file_missing",
    "not_contains",
    "symbol_missing",
}
RUNTIME_ALLOW = {
    "python",
    "python3",
    "pytest",
    "cargo",
    "grep",
    "ls",
    "wc",
    "git",
    "ruff",
}
GIT_READONLY = {
    "log",
    "show",
    "diff",
    "status",
    "rev-parse",
    "ls-files",
    "cat-file",
    "grep",
    "blame",
    "check-ignore",
    "merge-base",
}


GIT_DENIED_ARGS = (
    "--output",
    "-O",
    "--open-files-in-pager",
    "--ext-diff",
    "--textconv",
    "--exec",
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
)
REQUIRED_KEYS = {
    "file_exists": ["path"],
    "file_missing": ["path"],
    "contains": ["path", "text"],
    "not_contains": ["path", "text"],
    "regex": ["path", "pattern"],
    "symbol_defined": ["name"],
    "symbol_missing": ["name"],
    "no_static_callers": ["name"],
    "called_from_tests": ["name"],
    "edge_exists": ["kind"],
    "edge_count": ["kind"],
    "subject_wired": ["subject"],
    "runtime": ["argv"],
}


def validate_runtime_argv(argv) -> None:
    """Runtime checks may read and run the repo's own tests; they may not run arbitrary code or change the repo."""
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(a, str) and "\x00" not in a for a in argv)
    ):
        raise ClaimError("runtime argv must be a non-empty list of strings")
    prog, rest = argv[0], argv[1:]
    if prog not in RUNTIME_ALLOW:
        raise ClaimError(f"runtime program must be one of {sorted(RUNTIME_ALLOW)}")
    if prog in ("python", "python3") and rest[:2] != ["-m", "pytest"]:
        raise ClaimError(
            "runtime python is limited to `-m pytest ...`; arbitrary code is not allowed"
        )
    if prog == "cargo" and (not rest or rest[0] not in ("test", "check")):
        raise ClaimError("runtime cargo is limited to test and check")
    if prog == "ruff" and (
        not rest
        or rest[0] != "check"
        or any(a.startswith("--fix") or a == "--unsafe-fixes" for a in rest)
    ):
        raise ClaimError("runtime ruff is limited to `check` without fixes")
    if prog == "git":
        if not rest or rest[0] not in GIT_READONLY:
            raise ClaimError("runtime git is limited to read-only subcommands")
        if any(
            a == d
            or a.startswith(d + "=")
            or (len(d) == 2 and a.startswith(d) and a != "--")
            for a in rest[1:]
            for d in GIT_DENIED_ARGS
        ):
            raise ClaimError(
                "runtime git argument is not allowed (it could write files or run commands)"
            )


class ClaimError(ValueError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


NEW_CLAIM_COLUMNS = {
    "subsystem": "TEXT NOT NULL DEFAULT ''",
    "revision": "TEXT NOT NULL DEFAULT ''",
    "last_verified_rev": "TEXT NOT NULL DEFAULT ''",
    "depends_on_claims": "TEXT NOT NULL DEFAULT '[]'",
    "contradicting": "TEXT NOT NULL DEFAULT ''",
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    have = {r[1] for r in conn.execute("PRAGMA table_info(claims)")}
    for col, decl in NEW_CLAIM_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE claims ADD COLUMN {col} {decl}")
    structure.ensure_schema(conn)


def _head(root: Path) -> str:
    try:
        from . import gitinfo

        return gitinfo.head_commit(root)[:12]
    except Exception:  # not a git checkout: record that, do not fail the claim
        return "unknown"


def _subsystem(spec: dict) -> str:
    if spec.get("subsystem"):
        return str(spec["subsystem"])
    paths = [e["path"] for e in spec.get("evidence") or []] + [
        (spec.get("check") or {}).get(k, "") for k in ("path", "src_path")
    ]
    for p in paths:
        if p:
            parts = p.split("/")
            if parts[0] == "backend" and len(parts) > 2:
                deep = parts[1] in ("app", "crates") and len(parts) > 3
                return "/".join(parts[:3] if deep else parts[:2])
            return parts[0]
    return (spec.get("check") or {}).get("subject", "") or "repo"


def _history(conn, cid, actor, old, new, reason):
    conn.execute(
        "INSERT INTO claim_history (claim_id, ts, actor, from_status, to_status, reason) VALUES (?,?,?,?,?,?)",
        (cid, now(), actor, old, new, reason[:500]),
    )


def _set_status(conn, cid, actor, new, detail=""):
    old = conn.execute("SELECT status FROM claims WHERE id=?", (cid,)).fetchone()[0]
    conn.execute(
        "UPDATE claims SET status=?, status_detail=?, updated_at=? WHERE id=?",
        (new, detail[:500], now(), cid),
    )
    if old != new:
        _history(conn, cid, actor, old, new, detail or "status change")


def _file(conn, path):
    return conn.execute(
        "SELECT id, sha256, present FROM files WHERE path=?", (path,)
    ).fetchone()


def _read(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------- checks


@dataclass
class CheckResult:
    ok: bool
    detail: str
    inputs: dict[str, str]


def _in_tests(path: str) -> bool:
    return (
        path.startswith("tests/")
        or "/tests/" in path
        or path.rsplit("/", 1)[-1].startswith("test_")
    )


def check_paths(spec: dict) -> list[str]:
    return [spec[k] for k in ("path", "src_path") if isinstance(spec.get(k), str)]


def code_fingerprint(conn) -> str:
    """Hash of every in-scope code file. Index-wide checks (callers, edges, subjects) depend on code,
    not on documents, so a documentation edit or a regenerated report must not stale them."""
    h = hashlib.sha256()
    for r in conn.execute(
        "SELECT path, sha256 FROM files WHERE in_scope=1 AND present=1 "
        "AND language IN ('python','rust','javascript','typescript','sql') ORDER BY path"
    ):
        h.update(f"{r[0]}\0{r[1]}\n".encode())
    return h.hexdigest()


def _input_hashes(conn, spec) -> dict[str, str]:
    out = {}
    for p in check_paths(spec):
        r = _file(conn, p)
        out[p] = r["sha256"] if r and r["present"] else "<missing>"
    if spec["type"] in (
        "symbol_defined",
        "symbol_missing",
        "no_static_callers",
        "called_from_tests",
        "edge_exists",
        "edge_count",
        "subject_wired",
    ) or (
        spec["type"] in ("contains", "not_contains", "regex") and not spec.get("path")
    ):
        out["*index*"] = code_fingerprint(conn)
    return out


def run_check(
    conn: sqlite3.Connection, root: Path, spec: dict, runtime: bool = False
) -> CheckResult:
    t = spec.get("type")
    inputs = _input_hashes(conn, spec)
    ok, detail = False, ""
    if t in ("file_exists", "file_missing"):
        r = _file(conn, spec["path"])
        present = bool(r and r["present"]) or (root / spec["path"]).exists()
        ok = present if t == "file_exists" else not present
        detail = f"{spec['path']} {'exists' if present else 'does not exist'}"
    elif t in ("contains", "not_contains", "regex"):
        txt = _read(root, spec["path"])
        if txt is None:
            return CheckResult(False, f"cannot read {spec['path']}", inputs)
        if t == "regex":
            n = len(re.findall(spec["pattern"], txt, re.MULTILINE))
            lo, hi = spec.get("min", 1), spec.get("max")
            ok = n >= lo and (hi is None or n <= hi)
            detail = f"pattern matched {n} time(s) in {spec['path']} (need {lo}..{hi if hi is not None else 'any'})"
        else:
            n = txt.count(spec["text"])
            ok = n > 0 if t == "contains" else n == 0
            detail = f"{spec['text']!r} occurs {n} time(s) in {spec['path']}"
    elif t in ("symbol_defined", "symbol_missing"):
        rows = structure.find_symbols(conn, spec["name"], 1_000_000)
        if spec.get("path"):
            rows = [r for r in rows if r["path"] == spec["path"]]
        if spec.get("kind"):
            rows = [r for r in rows if r["kind"] == spec["kind"]]
        ok = bool(rows) if t == "symbol_defined" else not rows
        detail = f"{len(rows)} definition(s) of {spec['name']}" + (
            f" e.g. {rows[0]['path']}:{rows[0]['line']}" if rows else ""
        )
    elif t == "no_static_callers":
        refs = [
            r
            for r in structure.refs_to(conn, spec["name"], 1_000_000)
            if r["kind"]
            in ("CALLS", "STARTS_TASK", "REFERENCES", "INHERITS", "DECORATED_BY")
            and not (spec.get("exclude_tests", True) and _in_tests(r["src_path"]))
            and not (spec.get("exclude_path") and r["src_path"] == spec["exclude_path"])
        ]
        dyn = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='DYNAMIC'"
        ).fetchone()[0]
        ok = not refs
        detail = (
            f"no static caller of {spec['name']} (index cannot see {dyn} dynamic constructs)"
            if ok
            else f"{len(refs)} static reference(s), first {refs[0]['src_path']}:{refs[0]['line']} [{refs[0]['confidence']}]"
        )
    elif t == "called_from_tests":
        refs = [
            r
            for r in structure.refs_to(conn, spec["name"], 1_000_000)
            if r["kind"] in ("CALLS", "STARTS_TASK") and _in_tests(r["src_path"])
        ]
        ok = bool(refs)
        detail = (
            f"{len(refs)} call(s) from tests, first {refs[0]['src_path']}:{refs[0]['line']}"
            if ok
            else f"no test calls {spec['name']} (importing its module is not coverage)"
        )
    elif t in ("edge_exists", "edge_count"):
        q = "SELECT COUNT(*) FROM edges e JOIN files f ON f.id=e.file_id WHERE e.kind=?"
        args: list = [spec["kind"]]
        if spec.get("src_path"):
            q += " AND f.path=?"
            args.append(spec["src_path"])
        if spec.get("dst"):
            q += " AND e.dst=?"
            args.append(spec["dst"])
        if spec.get("dst_contains"):
            q += " AND e.dst LIKE ?"
            args.append(f"%{spec['dst_contains']}%")
        if spec.get("confidence"):
            q += " AND e.confidence=?"
            args.append(spec["confidence"])
        n = conn.execute(q, args).fetchone()[0]
        lo, hi = spec.get("min", 1), spec.get("max")
        ok = n >= lo and (hi is None or n <= hi)
        detail = f"{n} matching {spec['kind']} edge(s) (need {lo}..{hi if hi is not None else 'any'})"
    elif t == "subject_wired":
        m = structure.subject_matrix(conn).get(
            spec["subject"], {"PUBLISHES": [], "SUBSCRIBES": []}
        )
        npub, nsub = len(m["PUBLISHES"]), len(m["SUBSCRIBES"])
        ok = npub >= spec.get("min_publishers", 0) and nsub >= spec.get(
            "min_subscribers", 0
        )
        if "max_publishers" in spec:
            ok = ok and npub <= spec["max_publishers"]
        if "max_subscribers" in spec:
            ok = ok and nsub <= spec["max_subscribers"]
        detail = f"{spec['subject']}: {npub} publisher(s), {nsub} subscriber(s)"
    elif t == "runtime":
        if not runtime:
            return CheckResult(False, "runtime check skipped (pass --runtime)", inputs)
        argv = spec["argv"]
        validate_runtime_argv(argv)
        try:
            p = subprocess.run(
                argv,
                cwd=root,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=spec.get("timeout", 120),
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult(False, f"runtime failed to run: {exc}", inputs)
        want = spec.get("expect_exit", 0)
        ok = (
            p.returncode == want
            and spec.get("stdout_contains", "") in p.stdout
            and not (
                spec.get("stdout_not_contains")
                and spec["stdout_not_contains"] in p.stdout
            )
        )
        detail = f"exit {p.returncode} (want {want})" + (
            "" if ok else f"; stdout tail: {p.stdout[-120:]!r}"
        )
    else:
        raise ClaimError(f"unknown check type {t!r}")
    return CheckResult(ok, detail, inputs)


# ---------------------------------------------------------------- claims


def add_claim(conn, root: Path, spec: dict, author: str, family: str) -> int:
    """Record a claim. Every evidence quote must be present in the cited lines of the current file."""
    text = (spec.get("text") or "").strip()
    if len(text) < 15:
        raise ClaimError("claim text must be a full statement (at least 15 characters)")
    if spec.get("classification", "n/a") not in CLASSIFICATIONS:
        raise ClaimError(f"classification must be one of {sorted(CLASSIFICATIONS)}")
    check = spec.get("check")
    if check:
        if check.get("type") not in _KNOWN:
            raise ClaimError(f"unknown check type {check.get('type')!r}")
        missing = [k for k in REQUIRED_KEYS.get(check["type"], []) if k not in check]
        if missing:
            raise ClaimError(f"check {check['type']} needs {missing}")
        if check["type"] == "regex":
            try:
                re.compile(check["pattern"])
            except re.error as exc:
                raise ClaimError(f"invalid regex: {exc}") from exc
        if check["type"] == "runtime":
            validate_runtime_argv(check["argv"])
    evidence = spec.get("evidence") or []
    if not evidence and not check:
        raise ClaimError("a claim needs evidence quotes, a check, or both")
    bound = []
    for ev in evidence:
        f = _file(conn, ev["path"])
        body = _read(root, ev["path"])
        if f is None or body is None:
            raise ClaimError(f"evidence file {ev['path']} is not in the inventory")
        if hashlib.sha256((root / ev["path"]).read_bytes()).hexdigest() != f["sha256"]:
            raise ClaimError(
                f"{ev['path']} changed since the inventory; run inventory first"
            )
        lines = body.split("\n")
        a, b = int(ev["start"]), int(ev.get("end", ev["start"]))
        if not (1 <= a <= b <= len(lines)):
            raise ClaimError(
                f"{ev['path']}: lines {a}-{b} are outside the file ({len(lines)} lines)"
            )
        quote = ev["quote"]
        if len(quote) < 5 or quote not in "\n".join(lines[a - 1 : b]):
            raise ClaimError(
                f"{ev['path']}:{a}-{b} does not contain the quoted text {quote!r}"
            )
        bound.append((ev["path"], f["sha256"], a, b, quote))
    negative = int(
        bool(check and check["type"] in NEGATIVE_CHECKS) or bool(spec.get("negative"))
    )
    ts = now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO claims (text, kind, severity, classification, source, check_json, negative, status, author, author_family, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                text,
                spec.get("kind", "behavior"),
                spec.get("severity", "info"),
                spec.get("classification", "n/a"),
                spec.get("source", ""),
                json.dumps(check) if check else "",
                negative,
                "unverified",
                author,
                family,
                ts,
                ts,
            ),
        )
        cid = cur.lastrowid
        conn.execute(
            "UPDATE claims SET subsystem=?, revision=?, depends_on_claims=? WHERE id=?",
            (
                _subsystem(spec),
                _head(root),
                json.dumps(spec.get("depends_on_claims") or []),
                cid,
            ),
        )
        conn.executemany(
            "INSERT INTO claim_evidence (claim_id, path, sha256, line_start, line_end, quote) VALUES (?,?,?,?,?,?)",
            [(cid, *e) for e in bound],
        )
        _history(conn, cid, author, None, "unverified", "claim recorded")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return cid


_KNOWN = {
    "file_exists",
    "file_missing",
    "contains",
    "not_contains",
    "regex",
    "symbol_defined",
    "symbol_missing",
    "no_static_callers",
    "called_from_tests",
    "edge_exists",
    "edge_count",
    "subject_wired",
    "runtime",
}


def verify_claim(conn, root: Path, cid: int, actor: str, runtime: bool = False) -> str:
    row = conn.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
    if row is None:
        raise ClaimError(f"no claim {cid}")
    if row["status"] == "superseded":
        return "superseded"  # kept for the audit trail; the corrected claim is the live one
    if row["check_json"]:
        spec = json.loads(row["check_json"])
        try:
            res = run_check(conn, root, spec, runtime)
        except Exception as exc:  # one malformed claim must not abort a verify run
            _set_status(
                conn,
                cid,
                actor,
                "unverified",
                f"check error: {type(exc).__name__}: {exc}",
            )
            return "unverified"
        if spec["type"] == "runtime" and not runtime:
            # The command is not re-run here, but a reviewer's disagreement must
            # still dispute the claim: Codex disagreed with two runtime claims
            # and both stayed "supported" until this branch looked at reviews.
            disagreed = conn.execute(
                "SELECT 1 FROM reviews WHERE claim_id=? AND verdict='disagree'", (cid,)
            ).fetchone()
            if disagreed and row["status"] in ("supported", "verified"):
                _set_status(conn, cid, actor, "disputed", "reviewer disagrees")
                return "disputed"
            return row["status"]
        conn.execute(
            "UPDATE claims SET inputs_json=?, last_verified_rev=?, contradicting=? WHERE id=?",
            (
                json.dumps(res.inputs),
                _head(root),
                "" if res.ok else res.detail[:300],
                cid,
            ),
        )
        new = "supported" if res.ok else "refuted"
        reviews = conn.execute(
            "SELECT verdict FROM reviews WHERE claim_id=?", (cid,)
        ).fetchall()
        verdicts = {r[0] for r in reviews}
        if (
            res.ok
            and "agree" in verdicts
            and "disagree" not in verdicts
            and (not row["negative"] or spec["type"] == "runtime")
        ):
            new = "verified"
        elif res.ok and "disagree" in verdicts or not res.ok and "agree" in verdicts:
            new = "disputed"
        _set_status(conn, cid, actor, new, res.detail)
        return new
    # evidence-only claim: supported while every quote is intact in the current file
    bad = _evidence_problems(conn, root, cid)
    new = "stale" if bad else "supported"
    _set_status(conn, cid, actor, new, bad or "all evidence quotes intact")
    return new


def _evidence_problems(conn, root, cid) -> str:
    for ev in conn.execute("SELECT * FROM claim_evidence WHERE claim_id=?", (cid,)):
        body = _read(root, ev["path"])
        if body is None:
            return f"{ev['path']} no longer exists"
        if ev["quote"] not in body:
            return f"quote no longer found in {ev['path']}"
    return ""


def recheck(conn, root: Path, actor: str) -> dict[str, int]:
    """Invalidate claims whose files changed. A moved quote is re-bound; a vanished quote makes the claim stale."""
    counts = {"unchanged": 0, "rebound": 0, "stale": 0}
    for c in conn.execute(
        "SELECT id, status, inputs_json FROM claims WHERE status!='superseded'"
    ).fetchall():
        cid, changed, lost = c["id"], False, ""
        for ev in conn.execute(
            "SELECT * FROM claim_evidence WHERE claim_id=?", (cid,)
        ).fetchall():
            p = root / ev["path"]
            if not p.exists():
                lost = f"{ev['path']} was deleted"
                break
            data = p.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha == ev["sha256"]:
                continue
            changed = True
            body = data.decode("utf-8", errors="replace")
            if ev["quote"] in body:
                first = body[: body.index(ev["quote"])].count("\n") + 1
                last = first + ev["quote"].count("\n")
                conn.execute(
                    "UPDATE claim_evidence SET sha256=?, line_start=?, line_end=? WHERE id=?",
                    (sha, first, last, ev["id"]),
                )
                _history(
                    conn,
                    cid,
                    actor,
                    None,
                    c["status"],
                    f"{ev['path']} changed; quote re-bound to lines {first}-{last}",
                )
            else:
                lost = f"quote no longer found in {ev['path']} after it changed"
                break
        if not lost:
            inputs = json.loads(c["inputs_json"] or "{}")
            for path, sha in inputs.items():
                if path == "*index*":
                    if code_fingerprint(conn) != sha:
                        lost = "the repository changed since this index-wide check ran; re-verify"
                        changed = True
                        break
                    continue
                cur = _file(conn, path)
                now_sha = cur["sha256"] if cur and cur["present"] else "<missing>"
                if now_sha != sha:
                    lost = f"{path} changed since the check ran"
                    changed = True
                    break
        if lost:
            _set_status(conn, cid, actor, "stale", lost)
            counts["stale"] += 1
        elif changed:
            counts["rebound"] += 1
        else:
            counts["unchanged"] += 1
    # propagate: a claim that depends on a stale claim cannot stay current
    again = True
    while again:
        again = False
        stale_ids = {
            r[0] for r in conn.execute("SELECT id FROM claims WHERE status='stale'")
        }
        for r in conn.execute(
            "SELECT id, depends_on_claims FROM claims WHERE status NOT IN ('stale','superseded')"
        ).fetchall():
            deps = set(json.loads(r["depends_on_claims"] or "[]"))
            if deps & stale_ids:
                _set_status(
                    conn,
                    r["id"],
                    actor,
                    "stale",
                    f"depends on stale claim(s) {sorted(deps & stale_ids)}",
                )
                counts["stale"] += 1
                again = True
    return counts


def packet(conn, root: Path, cid: int, context: int = 3) -> str:
    """What an independent reader gets: the claim and the cited source, never the author's reasoning."""
    c = conn.execute("SELECT text, kind FROM claims WHERE id=?", (cid,)).fetchone()
    if c is None:
        raise ClaimError(f"no claim {cid}")
    out = [
        f"CLAIM {cid} [{c['kind']}]: {c['text']}",
        "Decide from the source below whether the claim is true. Open any other file you need.",
        "",
    ]
    for ev in conn.execute("SELECT * FROM claim_evidence WHERE claim_id=?", (cid,)):
        body = (_read(root, ev["path"]) or "").split("\n")
        a, b = (
            max(1, ev["line_start"] - context),
            min(len(body), ev["line_end"] + context),
        )
        out.append(f"--- {ev['path']} lines {a}-{b}")
        out += [f"{i:>5}| {body[i - 1]}" for i in range(a, b + 1)]
    return "\n".join(out)


def review(
    conn, root: Path, cid: int, reader: str, family: str, verdict: str, note: str
) -> str:
    if verdict not in ("agree", "disagree", "unsure"):
        raise ClaimError("verdict must be agree, disagree or unsure")
    c = conn.execute(
        "SELECT author, author_family FROM claims WHERE id=?", (cid,)
    ).fetchone()
    if c is None:
        raise ClaimError(f"no claim {cid}")
    if reader == c["author"]:
        raise ClaimError("the author cannot review their own claim")
    if len(note.strip()) < 20:
        raise ClaimError(
            "a review needs a note of at least 20 characters saying what you checked"
        )
    ev = [
        (e["path"], e["sha256"])
        for e in conn.execute(
            "SELECT path, sha256 FROM claim_evidence WHERE claim_id=?", (cid,)
        )
    ]
    conn.execute(
        "INSERT INTO reviews (claim_id, reader, family, verdict, note, inputs_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (cid, reader, family, verdict, note, json.dumps(dict(ev)), now()),
    )
    _history(
        conn,
        cid,
        reader,
        None,
        conn.execute("SELECT status FROM claims WHERE id=?", (cid,)).fetchone()[0],
        f"review: {verdict}",
    )
    return (
        verify_claim(conn, root, cid, reader)
        if conn.execute("SELECT check_json FROM claims WHERE id=?", (cid,)).fetchone()[
            0
        ]
        else _review_only(conn, root, cid, reader)
    )


def _review_only(conn, root, cid, actor) -> str:
    verdicts = [
        r[0]
        for r in conn.execute("SELECT verdict FROM reviews WHERE claim_id=?", (cid,))
    ]
    if "disagree" in verdicts:
        new = "disputed"
    elif "agree" in verdicts and not _evidence_problems(conn, root, cid):
        new = "verified"
    else:
        new = conn.execute("SELECT status FROM claims WHERE id=?", (cid,)).fetchone()[0]
    _set_status(conn, cid, actor, new, "reconciled with reviews")
    return new


def supersede(conn, old: int, new: int, actor: str) -> None:
    """Keep a wrong or overstated claim in the ledger, marked as replaced by a corrected one."""
    for cid in (old, new):
        if conn.execute("SELECT 1 FROM claims WHERE id=?", (cid,)).fetchone() is None:
            raise ClaimError(f"no claim {cid}")
    if old == new:
        raise ClaimError("a claim cannot supersede itself")
    _set_status(conn, old, actor, "superseded", f"replaced by corrected claim #{new}")


def list_claims(conn, status: str | None = None):
    q = "SELECT id, status, severity, kind, text, status_detail, negative FROM claims"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    return conn.execute(q + " ORDER BY id", args).fetchall()


def summary(conn) -> dict[str, int]:
    return {
        r[0]: r[1]
        for r in conn.execute("SELECT status, COUNT(*) FROM claims GROUP BY status")
    }
