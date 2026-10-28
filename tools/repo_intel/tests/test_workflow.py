from __future__ import annotations

import json

import pytest

from tools.repo_intel import inventory, knowledge, structure, workflow

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


def edit_and_rescan(kb, path, text):
    conn, root = kb
    (root / path).write_text(text)
    git(root, "add", "-A")
    inventory.scan(root, conn)
    structure.build_index(conn, root)


def test_claim_records_subsystem_revision_and_last_verification(kb):
    conn, root = kb
    cid = add(
        kb,
        text="function helper is defined in util.py",
        check={
            "type": "symbol_defined",
            "name": "helper",
            "path": "backend/app/util.py",
        },
    )
    knowledge.verify_claim(conn, root, cid, "v")
    row = conn.execute(
        "SELECT subsystem, revision, last_verified_rev FROM claims WHERE id=?", (cid,)
    ).fetchone()
    assert (
        row["subsystem"] == "backend/app"
        and row["revision"]
        and row["last_verified_rev"] == row["revision"]
    )


def test_refuted_claim_keeps_the_contradicting_evidence(kb):
    conn, root = kb
    cid = add(
        kb,
        text="nothing in the repo calls helper() so it is dead code",
        check={"type": "no_static_callers", "name": "helper"},
    )
    knowledge.verify_claim(conn, root, cid, "v")
    assert (
        "backend/app/core.py"
        in conn.execute(
            "SELECT contradicting FROM claims WHERE id=?", (cid,)
        ).fetchone()[0]
    )


def test_negative_claim_goes_stale_when_an_unrelated_file_gains_a_caller(kb):
    """The directive's example: a function becomes reachable because its callers changed."""
    conn, root = kb
    cid = add(
        kb,
        text="never_called_anywhere has no static caller in this repo",
        check={"type": "no_static_callers", "name": "never_called_anywhere"},
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "supported"
    edit_and_rescan(
        kb,
        "backend/app/other.py",
        "from app.util import never_called_anywhere\n\ndef go():\n    return never_called_anywhere()\n",
    )
    assert knowledge.recheck(conn, root, "v")["stale"] == 1
    assert knowledge.verify_claim(conn, root, cid, "v") == "refuted"


def test_staleness_propagates_to_dependent_claims(kb):
    conn, root = kb
    base = add(
        kb,
        text="util.py contains the helper function",
        check={"type": "contains", "path": "backend/app/util.py", "text": "def helper"},
    )
    dep = add(
        kb,
        text="core.py relies on the helper defined in util.py",
        check={"type": "contains", "path": "backend/app/core.py", "text": "helper()"},
        depends_on_claims=[base],
    )
    for c in (base, dep):
        knowledge.verify_claim(conn, root, c, "v")
    edit_and_rescan(kb, "backend/app/util.py", "def helper():\n    return 2\n")
    knowledge.recheck(conn, root, "v")
    status = {
        r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM claims")
    }
    assert status[base] == "stale" and status[dep] == "stale"


def test_gate_closes_on_stale_or_disputed_and_opens_after_supersede(kb):
    conn, root = kb
    cid = add(
        kb,
        text="the audio.play subject has a subscriber in core.py",
        check={"type": "subject_wired", "subject": "audio.play", "min_subscribers": 1},
    )
    knowledge.verify_claim(conn, root, cid, "v")
    ok, _ = workflow.gate(conn)
    assert ok
    knowledge.review(
        conn,
        root,
        cid,
        "reader-b",
        "codex",
        "disagree",
        "I believe the subscribe call is in dead code",
    )
    ok, why = workflow.gate(conn)
    assert not ok and "disputed" in why[0]
    fixed = add(
        kb,
        text="audio.play is subscribed in core.py inside Svc.go",
        check={"type": "subject_wired", "subject": "audio.play", "min_subscribers": 1},
    )
    knowledge.verify_claim(conn, root, fixed, "v")
    knowledge.supersede(conn, cid, fixed, "v")
    assert workflow.gate(conn)[0]


def test_gate_closes_when_a_file_changed_after_analysis(kb):
    conn, root = kb
    (root / "backend/app/util.py").write_text("def helper():\n    return 5\n")
    git(root, "add", "-A")
    inventory.scan(
        root, conn
    )  # inventory sees the new hash; the parsed stage is now behind
    ok, why = workflow.gate(conn)
    assert not ok and any("not parsed" in w or "changed since" in w for w in why)


def test_impact_follows_importers_tests_and_subjects(kb):
    conn, _ = kb
    d = workflow.impact(conn, "backend/app/contracts.py")
    paths = [p for _, p in d["importers_by_distance"]]
    assert "backend/app/core.py" in paths and "backend/tests/test_core.py" in paths
    assert "backend/tests/test_core.py" in d["tests_reached"]
    assert any(s[1] == "chat.input" for s in d["bus_subjects_in_blast_radius"])
    with pytest.raises(ValueError):
        workflow.impact(conn, "does/not/exist.py")


def test_preflight_reports_existing_symbol_references_and_unknown_name(kb):
    conn, _ = kb
    d = workflow.preflight(conn, "helper")
    assert d["definitions"] and d["references_by_confidence"].get("proven", 0) >= 1
    ghost = workflow.preflight(conn, "completely_made_up_feature")
    assert ghost["name_exists_nowhere"] is True and any(
        "another name" in a for a in ghost["advice"]
    )
    f = workflow.preflight(conn, "backend/app/core.py")
    assert f["kind"] == "file" and "X_KEY" in f["reads_env"]


def test_changes_and_status_report_the_working_state(kb):
    conn, _ = kb
    s = workflow.status(conn)
    assert s["in_scope"] == s["parsed_current"] and s["failed"] == 0
    assert json.dumps(workflow.changes(conn))
    assert "claims:" in workflow.render_status(s)


def test_superseded_claims_are_not_revived_by_verify_all(kb):
    conn, root = kb
    old = add(
        kb,
        text="nothing in the repo calls helper() so it is dead code",
        check={"type": "no_static_callers", "name": "helper"},
    )
    new = add(
        kb,
        text="helper() has callers in core.py",
        check={"type": "edge_exists", "kind": "CALLS", "dst": "helper", "min": 1},
    )
    knowledge.verify_claim(conn, root, old, "v")
    knowledge.supersede(conn, old, new, "v")
    assert knowledge.verify_claim(conn, root, old, "v") == "superseded"
    assert (
        conn.execute("SELECT status FROM claims WHERE id=?", (old,)).fetchone()[0]
        == "superseded"
    )


def test_a_documentation_edit_does_not_stale_index_wide_claims_but_a_code_edit_does(kb):
    conn, root = kb
    cid = add(
        kb,
        text="never_called_anywhere has no static caller in this repo",
        check={"type": "no_static_callers", "name": "never_called_anywhere"},
    )
    assert knowledge.verify_claim(conn, root, cid, "v") == "supported"
    edit_and_rescan(kb, "README.md", "# Title\n\nreworded documentation only\n")
    assert knowledge.recheck(conn, root, "v")["stale"] == 0
    edit_and_rescan(
        kb,
        "backend/app/util.py",
        "def helper():\n    return 1\n\ndef never_called_anywhere():\n    return 4\n",
    )
    assert knowledge.recheck(conn, root, "v")["stale"] == 1
