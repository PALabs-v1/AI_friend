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


# ---- regressions for defects found by the independent code review


def _open_repo(files):
    from tools.repo_intel import bench, knowledge

    root = bench._repo(files)
    conn = bench._open(root)
    knowledge.ensure_schema(conn)
    return root, conn


def _verdict(conn, root, spec):
    from tools.repo_intel import knowledge

    cid = knowledge.add_claim(conn, root, spec, "a", "claude")
    return knowledge.verify_claim(conn, root, cid, "v")


def test_rust_self_and_path_calls_are_recorded_so_methods_are_not_caller_less():
    src = "struct A;\nimpl A {\n    fn go(&self) { self.step(); A::other(); }\n    fn step(&self) {}\n    fn other() {}\n}\n"
    root, conn = _open_repo({"crates/c/src/main.rs": src, "crates/c/src/lib.rs": ""})
    for name in ("step", "other"):
        spec = {
            "text": f"nothing calls {name} in the rust crate at all",
            "check": {"type": "no_static_callers", "name": name},
        }
        assert _verdict(conn, root, spec) == "refuted", name


def test_rust_char_literals_and_comments_do_not_corrupt_symbols_or_bus_edges():
    src = (
        "fn a() { let c = '\"'; let d = '{'; }\n"
        'fn b() { nc.publish("p.q", 1); }\n'
        '// old: client.publish("audio.resume", b)\n'
        "fn lifetimes<'x>(s: &'x str) -> &'x str { s }\n"
    )
    _root, conn = _open_repo({"crates/c/src/main.rs": src, "crates/c/src/lib.rs": ""})
    names = {r[0] for r in conn.execute("SELECT name FROM symbols WHERE kind='fn'")}
    assert {"a", "b", "lifetimes"} <= names
    pubs = [
        (r["src"], r["dst"])
        for r in conn.execute("SELECT src, dst FROM edges WHERE kind='PUBLISHES'")
    ]
    assert pubs == [("b", "p.q")]


def test_http_request_calls_are_not_bus_publishes():
    _root, conn = _open_repo(
        {
            "a.py": "import httpx\n\ndef f():\n    httpx.request('GET', 'http://x')\n    bus.request('svc.rpc', b'')\n"
        }
    )
    dst = [r[0] for r in conn.execute("SELECT dst FROM edges WHERE kind='PUBLISHES'")]
    assert dst == ["svc.rpc"]


def test_functions_passed_by_reference_count_as_references():
    src = "import asyncio\n\nclass S:\n    async def run(self):\n        await asyncio.to_thread(self.worker)\n        self.bus.subscribe('a.b', self.on_msg)\n\n    def worker(self):\n        return 1\n\n    async def on_msg(self, m):\n        return m\n"
    root, conn = _open_repo({"s.py": src})
    for name in ("worker", "on_msg"):
        spec = {
            "text": f"nothing calls {name} anywhere in this repo",
            "check": {"type": "no_static_callers", "name": name},
        }
        assert _verdict(conn, root, spec) == "refuted", name


def test_a_file_that_stops_parsing_keeps_its_old_edges_and_is_reported(mini, conn):
    from tools.repo_intel import structure as st

    inventory.scan(mini, conn)
    st.build_index(conn, mini)
    before = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='CALLS'").fetchone()[0]
    (mini / "backend/app/core.py").write_text("def broken(:\n")
    git(mini, "add", "-A")
    inventory.scan(mini, conn)
    res = st.build_index(conn, mini)
    assert [p for p, _ in res.failed] == ["backend/app/core.py"]
    after = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='CALLS'").fetchone()[0]
    assert after == before, (
        "a parse failure must not erase the previous index for that file"
    )
    from tools.repo_intel import workflow

    assert workflow.gate(conn)[0] is False
