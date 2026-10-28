from __future__ import annotations

import pytest

from tools.repo_intel import inventory, knowledge

from .conftest import git
from .test_structure import idx, mini  # noqa: F401  (fixtures)


@pytest.fixture
def kb(idx, mini):  # noqa: F811
    conn, _ = idx
    knowledge.ensure_schema(conn)
    return conn, mini


def add(kb, **spec):
    conn, root = kb
    return knowledge.add_claim(conn, root, spec, "author-a", "claude")


UTIL_EV = {
    "path": "backend/app/util.py",
    "start": 7,
    "end": 7,
    "quote": "def never_called_anywhere",
}


def test_claim_with_a_quote_that_is_not_in_the_cited_lines_is_rejected(kb):
    with pytest.raises(knowledge.ClaimError, match="does not contain"):
        add(
            kb,
            text="the helper function is defined here",
            evidence=[{**UTIL_EV, "quote": "def invented_name"}],
        )
    with pytest.raises(knowledge.ClaimError, match="outside the file"):
        add(
            kb,
            text="the helper function is defined here",
            evidence=[{**UTIL_EV, "start": 99, "end": 99}],
        )


def test_claim_needs_evidence_or_a_check(kb):
    with pytest.raises(knowledge.ClaimError):
        add(kb, text="this statement has nothing behind it at all")


def test_seeded_false_existence_claim_is_refuted_and_true_one_supported(kb):
    conn, root = kb
    bad = add(
        kb,
        text="function ghost_function is defined in util.py",
        check={"type": "symbol_defined", "name": "ghost_function"},
    )
    good = add(
        kb,
        text="function helper is defined in util.py",
        check={
            "type": "symbol_defined",
            "name": "helper",
            "path": "backend/app/util.py",
        },
    )
    assert knowledge.verify_claim(conn, root, bad, "v") == "refuted"
    assert knowledge.verify_claim(conn, root, good, "v") == "supported"


def test_missed_implementation_is_caught_by_the_index(kb):
    """The author claims nothing calls helper(). Svc.go calls it, so the claim is refuted with the call site."""
    conn, root = kb
    cid = add(
        kb,
        text="nothing in the repo calls helper() so it is dead code",
        check={"type": "no_static_callers", "name": "helper"},
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "refuted"
    detail = conn.execute(
        "SELECT status_detail FROM claims WHERE id=?", (cid,)
    ).fetchone()[0]
    assert "backend/app/core.py" in detail


def test_true_negative_claim_stops_at_supported_and_mentions_dynamic_blind_spot(kb):
    conn, root = kb
    cid = add(
        kb,
        text="never_called_anywhere has no static caller in this repo",
        check={"type": "no_static_callers", "name": "never_called_anywhere"},
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "supported"
    # even with an agreeing independent reader, a static-only negative claim is never VERIFIED
    assert (
        knowledge.review(
            conn,
            root,
            cid,
            "reader-b",
            "codex",
            "agree",
            "searched the repo with ripgrep, no caller",
        )
        == "supported"
    )
    assert (
        "dynamic constructs"
        in conn.execute(
            "SELECT status_detail FROM claims WHERE id=?", (cid,)
        ).fetchone()[0]
    )


def test_positive_claim_becomes_verified_only_with_check_and_independent_reader(kb):
    conn, root = kb
    cid = add(
        kb,
        text="the audio.play subject has a subscriber in core.py",
        check={"type": "subject_wired", "subject": "audio.play", "min_subscribers": 1},
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "supported"
    with pytest.raises(knowledge.ClaimError, match="author cannot review"):
        knowledge.review(
            conn,
            root,
            cid,
            "author-a",
            "claude",
            "agree",
            "I checked my own claim carefully today",
        )
    assert (
        knowledge.review(
            conn,
            root,
            cid,
            "reader-b",
            "codex",
            "agree",
            "read core.py and found the subscribe call",
        )
        == "verified"
    )


def test_disagreement_between_layers_is_disputed(kb):
    conn, root = kb
    cid = add(
        kb,
        text="the audio.play subject has a subscriber in core.py",
        check={"type": "subject_wired", "subject": "audio.play", "min_subscribers": 1},
    )
    knowledge.verify_claim(conn, root, cid, "v")
    assert (
        knowledge.review(
            conn,
            root,
            cid,
            "reader-b",
            "codex",
            "disagree",
            "the subscribe call is in dead code I believe",
        )
        == "disputed"
    )


def test_evidence_only_claim_goes_stale_when_its_quote_disappears(kb):
    conn, root = kb
    cid = add(
        kb,
        text="never_called_anywhere is the last function in util.py",
        evidence=[UTIL_EV],
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "supported"
    (root / "backend/app/util.py").write_text("def helper():\n    return 1\n")
    git(root, "add", "-A")
    inventory.scan(root, conn)
    out = knowledge.recheck(conn, root, "v")
    assert out["stale"] == 1
    assert (
        conn.execute("SELECT status FROM claims WHERE id=?", (cid,)).fetchone()[0]
        == "stale"
    )
    assert (
        "quote no longer found"
        in conn.execute(
            "SELECT status_detail FROM claims WHERE id=?", (cid,)
        ).fetchone()[0]
    )


def test_moved_quote_is_rebound_not_staled(kb):
    conn, root = kb
    cid = add(
        kb, text="never_called_anywhere is defined in util.py", evidence=[UTIL_EV]
    )
    p = root / "backend/app/util.py"
    p.write_text("# new header line\n" + p.read_text())
    git(root, "add", "-A")
    inventory.scan(root, conn)
    out = knowledge.recheck(conn, root, "v")
    assert out["rebound"] == 1 and out["stale"] == 0
    ev = conn.execute(
        "SELECT line_start FROM claim_evidence WHERE claim_id=?", (cid,)
    ).fetchone()
    assert ev[0] == 8


def test_check_goes_stale_when_a_file_it_read_changes(kb):
    conn, root = kb
    cid = add(
        kb,
        text="util.py contains the helper function",
        check={"type": "contains", "path": "backend/app/util.py", "text": "def helper"},
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "supported"
    (root / "backend/app/util.py").write_text("def helper():\n    return 2\n")
    git(root, "add", "-A")
    inventory.scan(root, conn)
    assert knowledge.recheck(conn, root, "v")["stale"] == 1


def test_history_records_every_transition(kb):
    conn, root = kb
    cid = add(
        kb,
        text="function ghost_function is defined in util.py",
        check={"type": "symbol_defined", "name": "ghost_function"},
    )
    knowledge.verify_claim(conn, root, cid, "v")
    h = [
        (r["from_status"], r["to_status"])
        for r in conn.execute(
            "SELECT * FROM claim_history WHERE claim_id=? ORDER BY id", (cid,)
        )
    ]
    assert h == [(None, "unverified"), ("unverified", "refuted")]


def test_runtime_checks_are_opt_in_allowlisted_and_shell_free(kb):
    conn, root = kb
    cid = add(
        kb,
        text="pytest is installed and reports its version",
        check={
            "type": "runtime",
            "argv": ["python", "-m", "pytest", "--version"],
            "stdout_contains": "pytest",
        },
    )
    assert (
        knowledge.verify_claim(conn, root, cid, "v") == "unverified"
    )  # not run without --runtime
    assert knowledge.verify_claim(conn, root, cid, "v", runtime=True) == "supported"


@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", "x"],
        ["python3", "-c", "import os; os.system('touch /tmp/pwned')"],
        ["python", "script.py"],
        ["git", "reset", "--hard"],
        ["git", "branch", "-D", "main"],
        ["git", "diff", "--output=README.md"],
        ["git", "grep", "-Ovim", "x"],
        ["git", "-c", "core.pager=sh", "log"],
        ["cargo", "install", "x"],
        ["ruff", "check", "--fix", "."],
        ["python", "-m", "pytest", "a\x00b"],
    ],
)
def test_unsafe_runtime_commands_are_refused_when_the_claim_is_added(kb, argv):
    with pytest.raises(knowledge.ClaimError):
        add(
            kb,
            text="a claim whose check tries to run something unsafe",
            check={"type": "runtime", "argv": argv},
        )


def test_malformed_checks_are_refused_at_add_and_never_abort_a_verify_run(kb):
    conn, root = kb
    with pytest.raises(knowledge.ClaimError, match="needs"):
        add(
            kb,
            text="contains check with no text field at all",
            check={"type": "contains", "path": "README.md"},
        )
    with pytest.raises(knowledge.ClaimError, match="invalid regex"):
        add(
            kb,
            text="regex check with a broken pattern here",
            check={"type": "regex", "path": "README.md", "pattern": "("},
        )
    # a spec that slips past add (older ledger rows) must degrade to unverified, not raise
    cid = add(
        kb,
        text="function helper is defined in util.py",
        check={"type": "symbol_defined", "name": "helper"},
    )
    conn.execute(
        "UPDATE claims SET check_json=? WHERE id=?",
        ('{"type": "regex", "path": "README.md", "pattern": "("}', cid),
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "unverified"


def test_recheck_never_revives_or_overwrites_a_superseded_claim(kb):
    conn, root = kb
    old = add(
        kb,
        text="util.py contains the helper function",
        check={"type": "contains", "path": "backend/app/util.py", "text": "def helper"},
    )
    new = add(
        kb,
        text="util.py defines helper returning one",
        check={"type": "contains", "path": "backend/app/util.py", "text": "def helper"},
    )
    knowledge.verify_claim(conn, root, old, "v")
    knowledge.supersede(conn, old, new, "v")
    (root / "backend/app/util.py").write_text("def helper():\n    return 2\n")
    git(root, "add", "-A")
    inventory.scan(root, conn)
    knowledge.recheck(conn, root, "v")
    assert (
        conn.execute("SELECT status FROM claims WHERE id=?", (old,)).fetchone()[0]
        == "superseded"
    )


def test_symbol_checks_see_a_definition_even_when_the_name_is_very_common(kb):
    conn, root = kb
    rows = [
        (1, "sha", "function", "__init__", f"C{i}.__init__", 1, 1, "")
        for i in range(250)
    ]
    fid = conn.execute(
        "SELECT id FROM files WHERE path='backend/app/util.py'"
    ).fetchone()[0]
    conn.executemany(
        "INSERT INTO symbols (file_id, sha256, kind, name, qualname, line, end_line, decorators) VALUES (?,?,?,?,?,?,?,?)",
        [(fid, *r[1:]) for r in rows],
    )
    cid = add(
        kb,
        text="util.py defines an __init__ method somewhere",
        check={
            "type": "symbol_defined",
            "name": "__init__",
            "path": "backend/app/util.py",
        },
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "supported"


def test_review_packet_hides_the_authors_reasoning(kb):
    conn, root = kb
    cid = add(
        kb,
        text="never_called_anywhere is defined in util.py",
        evidence=[UTIL_EV],
        severity="low",
    )
    p = knowledge.packet(conn, root, cid)
    assert "def never_called_anywhere" in p and "author-a" not in p


def test_audit_reports_are_derived_from_index_and_ledger(kb):
    from tools.repo_intel import audit

    conn, root = kb
    add(
        kb,
        text="the audio.play subject has a subscriber in core.py",
        check={"type": "subject_wired", "subject": "audio.play", "min_subscribers": 1},
        classification="still_supported",
        source="OLD_AUDIT.md",
    )
    add(
        kb,
        text="function ghost_function is defined in util.py",
        check={"type": "symbol_defined", "name": "ghost_function"},
        classification="contradicted",
        source="OLD_AUDIT.md",
        kind="debt:correctness",
        severity="high",
    )
    for i in range(1, 3):
        knowledge.verify_claim(conn, root, i, "v")
    amap = audit.architecture_map(conn, "abc123")
    assert "| chat.input |" in amap and "| audio.play |" in amap
    assert "docs/missing.md" in amap, "dead documentation citation must be reported"
    disc = audit.discrepancy_report(conn)
    assert (
        "Contradicted (1)" in disc
        and "Still supported (1)" in disc
        and "refuted" in disc
    )
    debt = audit.debt_registry(conn)
    assert "ghost_function" in debt and "high" in debt
