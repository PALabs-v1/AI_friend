from __future__ import annotations

import pytest

from tools.repo_intel import inventory, structure

from .conftest import git


@pytest.fixture
def mini(tmp_path):
    root = tmp_path / "mini"
    (root / "backend/app").mkdir(parents=True)
    (root / "backend/tests").mkdir(parents=True)
    (root / "backend/crates/foo/src").mkdir(parents=True)
    (root / "backend/app/__init__.py").write_text("")
    (root / "backend/app/contracts.py").write_text(
        'class Topics:\n    CHAT = "chat.input"\n    PLAY = "audio.play"\n'
    )
    (root / "backend/app/util.py").write_text(
        "def helper():\n    return 1\n\ndef other():\n    return 2\n\ndef never_called_anywhere():\n    return 3\n"
    )
    (root / "backend/app/core.py").write_text(
        "import asyncio, importlib, os\n"
        "from app.contracts import Topics\n"
        "from app import util\n"
        "from app.util import helper\n\n"
        "class Svc:\n"
        "    async def go(self):\n"
        "        await self.agent.publish(Topics.CHAT, {})\n"
        "        await self.agent.subscribe('audio.play', self.step)\n"
        "        self.step()\n"
        "        helper()\n"
        "        util.other()\n"
        "        asyncio.create_task(self.step())\n"
        "        os.getenv('X_KEY')\n"
        "        importlib.import_module(self.name)\n"
        "        getattr(self, self.name)\n"
        "        self.db.execute('INSERT INTO memories (a) VALUES (1)')\n\n"
        "    def step(self):\n"
        "        return 1\n\n"
        "@router.get('/health')\n"
        "def health():\n"
        "    return 'ok'\n"
    )
    (root / "backend/tests/test_core.py").write_text(
        "from app.core import Svc\n\ndef test_svc():\n    assert Svc\n"
    )
    (root / "backend/crates/foo/src/main.rs").write_text(
        "mod bar;\nuse crate::bar::thing;\n\nfn main() {\n    run();\n    // run_commented();\n}\n\n"
        'fn run() {\n    let _ = "run_in_string()";\n    client.publish("x.y", 1);\n}\n'
    )
    (root / "backend/crates/foo/src/bar.rs").write_text("pub fn thing() {}\n")
    (root / "README.md").write_text(
        "# Title\n\nSee [core](backend/app/core.py) and [gone](docs/missing.md) and `backend/app/util.py`.\n"
    )
    root.mkdir(exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def idx(mini, conn):
    inventory.scan(mini, conn)
    res = structure.build_index(conn, mini)
    return conn, res


def edges(conn, kind):
    return [dict(r) for r in conn.execute("SELECT * FROM edges WHERE kind=?", (kind,))]


def test_every_parseable_file_is_parsed_and_stage_is_recorded(idx):
    conn, res = idx
    assert not res.failed and not res.skipped_stale
    unparsed = conn.execute(
        "SELECT path FROM files WHERE in_scope=1 AND present=1 AND parsed_hash IS NOT sha256"
    ).fetchall()
    assert unparsed == []


def test_cross_module_import_and_call_resolve_with_source_lines(idx):
    conn, _ = idx
    imp = [e for e in edges(conn, "IMPORTS") if e["confidence"] == "proven"]
    assert any(e["dst"] == "backend/app/contracts.py" and e["line"] == 2 for e in imp)
    calls = {(e["src"], e["dst"], e["confidence"]) for e in edges(conn, "CALLS")}
    assert ("Svc.go", "helper", "proven") in calls
    assert ("Svc.go", "other", "proven") in calls
    assert ("Svc.go", "Svc.step", "proven") in calls


def test_nats_subject_constant_resolves_through_class_attribute(idx):
    conn, _ = idx
    m = structure.subject_matrix(conn)
    assert any(p[3] == "proven" for p in m["chat.input"]["PUBLISHES"])
    assert m["audio.play"]["SUBSCRIBES"][0][3] == "proven"


def test_test_to_component_edge(idx):
    conn, _ = idx
    t = edges(conn, "TEST_VALIDATES")
    assert [(e["src"], e["dst"]) for e in t] == [
        ("backend/tests/test_core.py", "backend/app/core.py")
    ]


def test_decorator_route_env_storage_and_task_edges(idx):
    conn, _ = idx
    assert any(e["dst"] == "GET /health" for e in edges(conn, "REGISTERS_ROUTE"))
    assert any(e["dst"] == "X_KEY" for e in edges(conn, "READS_ENV"))
    assert any(e["dst"] == "table:memories" for e in edges(conn, "WRITES_STORAGE"))
    assert any(e["dst"] == "Svc.step" for e in edges(conn, "STARTS_TASK"))


def test_dynamic_constructs_are_flagged_unresolved_never_dropped(idx):
    conn, _ = idx
    dyn = edges(conn, "DYNAMIC")
    assert {e["dst"] for e in dyn} == {"dynamic import", "getattr with a computed name"}
    assert all(e["confidence"] == "unresolved" for e in dyn)


def test_unreferenced_lists_the_uncalled_function_and_warns_about_dynamic_code(idx):
    conn, _ = idx
    rows, dyn = structure.unreferenced(conn)
    assert "never_called_anywhere" in [r["qualname"] for r in rows]
    assert "helper" not in [r["qualname"] for r in rows]
    assert dyn == 2


def test_rust_lexical_ignores_comments_and_strings_and_marks_edges_lexical(idx):
    conn, _ = idx
    rc = [e for e in edges(conn, "CALLS") if e["src"] == "main"]
    assert [(e["dst"], e["confidence"]) for e in rc] == [("run", "lexical")]
    mods = [e for e in edges(conn, "IMPORTS") if e["evidence"] == "mod bar;"]
    assert (
        mods
        and mods[0]["dst"].endswith("bar.rs")
        and mods[0]["confidence"] == "lexical"
    )
    assert any(
        e["dst"] == "x.y" and e["confidence"] == "lexical"
        for e in edges(conn, "PUBLISHES")
    )


def test_markdown_links_flag_missing_targets(idx):
    conn, _ = idx
    links = {e["dst"]: e["confidence"] for e in edges(conn, "DOC_LINKS")}
    assert links["backend/app/core.py"] == "proven"
    assert links["docs/missing.md"] == "unresolved"


def test_syntax_error_is_recorded_as_a_failure_not_skipped(mini, conn):
    (mini / "backend/app/broken.py").write_text("def f(:\n    pass\n")
    git(mini, "add", "-A")
    inventory.scan(mini, conn)
    res = structure.build_index(conn, mini)
    assert [p for p, _ in res.failed] == ["backend/app/broken.py"]
    row = conn.execute(
        "SELECT failure_stage, parsed_hash FROM files WHERE path='backend/app/broken.py'"
    ).fetchone()
    assert row["failure_stage"] == "parse" and row["parsed_hash"] is None


def test_editing_a_file_after_inventory_is_refused_not_indexed(mini, conn):
    inventory.scan(mini, conn)
    (mini / "backend/app/util.py").write_text("def helper():\n    return 99\n")
    res = structure.build_index(conn, mini)
    assert res.skipped_stale == ["backend/app/util.py"]


def test_repair_notes_prunes_missing_symbols_and_records_it(mini, conn):
    import json

    from tools.repo_intel import renote

    inventory.scan(mini, conn)
    f = conn.execute(
        "SELECT id, sha256 FROM files WHERE path='backend/app/util.py'"
    ).fetchone()
    conn.execute("UPDATE files SET read_hash=sha256 WHERE id=?", (f["id"],))
    conn.execute(
        "INSERT INTO notes (file_id, sha256, batch_id, reader, tool_family, role, summary, symbols, depends_on, uncertainties, evidence, symbols_ok, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f["id"],
            f["sha256"],
            "b",
            "r",
            "claude",
            "helpers",
            "Two small helper functions used by the core service module.",
            json.dumps(["helper", "Util.nonexistent"]),
            "[]",
            "[]",
            "[]",
            0,
            "now",
        ),
    )
    out = renote.repair(conn, mini, "r2", "claude")
    assert out == [("backend/app/util.py", "reviewed; dropped 1 of 2 symbols")]
    n = conn.execute(
        "SELECT symbols, uncertainties, symbols_ok FROM notes WHERE batch_id='repair'"
    ).fetchone()
    assert (
        json.loads(n["symbols"]) == ["helper"]
        and "Util.nonexistent" in n["uncertainties"]
        and n["symbols_ok"] == 1
    )
    assert (
        conn.execute(
            "SELECT reviewed_hash=sha256 FROM files WHERE id=?", (f["id"],)
        ).fetchone()[0]
        == 1
    )
