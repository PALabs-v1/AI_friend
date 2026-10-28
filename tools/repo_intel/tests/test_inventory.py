from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest

from tools.repo_intel import inventory
from tools.repo_intel.coverage import collect, headline
from tools.repo_intel.scope import load_rules

from .conftest import git


def rows(conn):
    return {r["path"]: r for r in conn.execute("SELECT * FROM files")}


def test_every_git_visible_file_is_accounted_for(repo, conn):
    s = inventory.scan(repo, conn)
    r = rows(conn)
    expected = {
        "src/app.py",
        "src/util.py",
        "tests/test_app.py",
        "README.md",
        "with space.md",
        "-leading-dash.txt",
        "new\nline.txt",
        "blob.bin",
        "empty.py",
        "alias.md",
        ".gitignore",
        "untracked.py",
    }
    assert set(r) == expected
    assert s.present == len(expected)
    assert "ignored_dir/secret.env" not in r and "run.log" not in r, (
        "ignored files must not enter the inventory"
    )


def test_classification_and_exclusion_reasons(repo, conn):
    inventory.scan(repo, conn)
    r = rows(conn)
    assert r["blob.bin"]["category"] == "BINARY_ASSET" and not r["blob.bin"]["in_scope"]
    assert (
        r["alias.md"]["kind"] == "symlink"
        and r["alias.md"]["symlink_target"] == "README.md"
        and not r["alias.md"]["in_scope"]
    )
    assert r["tests/test_app.py"]["category"] == "TEST"
    assert (
        r["untracked.py"]["tracked"] == 0
        and r["untracked.py"]["wt_state"] == "untracked"
    )
    for f in r.values():
        if not f["in_scope"]:
            assert f["exclusion_reason"], f"{f['path']} excluded without a reason"
    assert r["empty.py"]["in_scope"] and r["empty.py"]["size"] == 0


def test_hashes_are_exact(repo, conn):
    inventory.scan(repo, conn)
    f = rows(conn)["src/app.py"]
    assert f["sha256"] == hashlib.sha256((repo / "src/app.py").read_bytes()).hexdigest()
    assert f["line_count"] == 5


def test_ignored_files_are_counted_never_opened(repo, conn, monkeypatch):
    opened = []
    real = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real(path, *a, **k)

    monkeypatch.setattr("builtins.open", spy)
    inventory.scan(repo, conn)
    assert not any("secret.env" in p for p in opened)
    ign = {r["path"]: r for r in conn.execute("SELECT * FROM ignored_paths")}
    assert ign["ignored_dir"]["file_count"] == 1 and ign["ignored_dir"]["is_dir"] == 1
    assert "run.log" in ign


def test_rescan_is_idempotent(repo, conn):
    a = inventory.scan(repo, conn)
    b = inventory.scan(repo, conn)
    assert a.fingerprint == b.fingerprint
    assert not b.drift and b.present == a.present


def test_dry_run_changes_nothing(repo, conn):
    inventory.scan(repo, conn)
    (repo / "src/app.py").write_text("changed\n")
    before = [
        tuple(r)
        for r in conn.execute("SELECT id, sha256, last_snapshot FROM files ORDER BY id")
    ]
    snaps = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    s = inventory.scan(repo, conn, apply=False)
    assert s.modified == ["src/app.py"] and not s.applied
    assert [
        tuple(r)
        for r in conn.execute("SELECT id, sha256, last_snapshot FROM files ORDER BY id")
    ] == before
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == snaps


def test_scan_never_touches_the_repository(repo, conn):
    index = repo / ".git" / "index"
    status = os.popen(
        f"git -C {repo} status --porcelain"
    ).read()  # a plain git status refreshes the index itself
    before = (index.read_bytes(), os.stat(index).st_mtime_ns)
    inventory.scan(repo, conn)
    inventory.scan(repo, conn, apply=False)
    assert (index.read_bytes(), os.stat(index).st_mtime_ns) == before, (
        "the scan must not write to .git"
    )
    assert os.popen(f"git -C {repo} status --porcelain").read() == status


def test_modification_added_removed_and_dirty_flag(repo, conn):
    inventory.scan(repo, conn)
    assert (
        conn.execute("SELECT dirty FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()[
            0
        ]
        == 1
    )  # untracked.py present
    (repo / "src/app.py").write_text("def handler(event):\n    return 0\n")
    (repo / "brand_new.py").write_text("x = 1\n")
    (repo / "src/util.py").unlink()
    s = inventory.scan(repo, conn)
    assert (
        s.modified == ["src/app.py"]
        and s.added == ["brand_new.py"]
        and s.removed == ["src/util.py"]
    )
    assert rows(conn)["src/util.py"]["present"] == 0
    (repo / "src/util.py").write_text("back\n")  # resurrection counts as added again
    assert inventory.scan(repo, conn).added == ["src/util.py"]


def test_clean_tree_is_reported_clean(tmp_path, conn):
    from .conftest import git as g

    root = tmp_path / "clean"
    root.mkdir()
    g(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text("x = 1\n")
    g(root, "add", "-A")
    g(root, "commit", "-q", "-m", "c")
    s = inventory.scan(root, conn)
    assert not s.dirty and s.head != "UNBORN"


def test_edit_makes_every_stage_stale(repo, conn):
    inventory.scan(repo, conn)
    fid = rows(conn)["src/app.py"]["id"]
    sha = rows(conn)["src/app.py"]["sha256"]
    conn.execute(
        "UPDATE files SET read_hash=?, reviewed_hash=?, parsed_hash=? WHERE id=?",
        (sha, sha, sha, fid),
    )
    assert collect(conn)["stages"]["read"] == 1
    (repo / "src/app.py").write_text("edited\n")
    inventory.scan(repo, conn)
    c = collect(conn)
    assert (
        c["stages"]["read"] == 0
        and c["stages"]["reviewed"] == 0
        and c["stages"]["parsed"] == 0
    )
    assert c["changed_since_analysis"] == 1


def test_interrupted_scan_leaves_the_index_unchanged(repo, conn, monkeypatch):
    inventory.scan(repo, conn)
    (repo / "src/app.py").write_text("v2\n")
    snapshot = [
        tuple(r) for r in conn.execute("SELECT id, sha256 FROM files ORDER BY id")
    ]
    calls = {"n": 0}
    real = inventory._upsert

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return real(*a, **k)

    monkeypatch.setattr(inventory, "_upsert", boom)
    with pytest.raises(KeyboardInterrupt):
        inventory.scan(repo, conn)
    assert [
        tuple(r) for r in conn.execute("SELECT id, sha256 FROM files ORDER BY id")
    ] == snapshot
    monkeypatch.setattr(inventory, "_upsert", real)
    assert inventory.scan(repo, conn).modified == ["src/app.py"]


def test_scope_flip_is_reported(repo, conn):
    inventory.scan(repo, conn)
    rules, h = load_rules()
    narrowed = [
        r for r in rules if r.name != "docs"
    ]  # without the docs rule, README falls to OTHER (still in scope)
    s = inventory.scan(repo, conn, rules=narrowed, scope_hash=h)
    assert s.scope_changed == []
    from dataclasses import replace

    flipped = [
        replace(r, in_scope=False, reason="test flip") if r.name == "docs" else r
        for r in rules
    ]
    s = inventory.scan(repo, conn, rules=flipped, scope_hash=h)
    assert "README.md" in s.scope_changed
    assert rows(conn)["README.md"]["exclusion_reason"] == "test flip"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_file_is_a_reported_failure_not_a_silent_skip(repo, conn):
    p = repo / "src/util.py"
    p.chmod(0)
    try:
        s = inventory.scan(repo, conn)
    finally:
        p.chmod(0o644)
    assert any("src/util.py" in f for f in s.failures)
    c = collect(conn)
    assert c["failed"] == 1
    assert (
        rows(conn)["src/util.py"]["in_scope"] == 1
    )  # still owed a read; not hidden by exclusion


def test_deleted_tracked_file_is_removed_not_failed(repo, conn):
    inventory.scan(repo, conn)
    (repo / "README.md").unlink()
    s = inventory.scan(repo, conn)
    assert s.removed == ["README.md"] and collect(conn)["failed"] == 0


def test_headline_format_matches_the_requested_one():
    c = {"in_scope": 1850, "stages": {"read": 245}}
    assert (
        headline(c)
        == f"Codebase Coverage: 245 / 1,850 files read (13.24%) {chr(0x2014)} 1,605 remaining."
    )
    assert chr(0x2014) not in headline(c, repo_doc=True)
    assert headline({"in_scope": 0, "stages": {"read": 0}}).startswith(
        "Codebase Coverage: 0 / 0 files read (0.00%)"
    )


def test_tracked_change_shows_wt_state(repo, conn):
    (repo / "src/util.py").write_text("modified in worktree\n")
    inventory.scan(repo, conn)
    assert rows(conn)["src/util.py"]["wt_state"] == "M"
    git(repo, "add", "src/util.py")
    inventory.scan(repo, conn)
    assert rows(conn)["src/util.py"]["wt_state"] == "M"


def test_corrupt_index_is_detected(tmp_path):
    from tools.repo_intel.db import CorruptIndex, connect

    p = tmp_path / "bad.db"
    p.write_bytes(b"this is not a sqlite database at all" * 100)
    with pytest.raises((CorruptIndex, sqlite3.DatabaseError)):
        connect(p)
