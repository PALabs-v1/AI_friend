"""Benchmark of the repository-intelligence system itself, on fixtures with known ground truth.

Each scenario builds a small repository, runs the real inventory, index and
claim code, and compares the outcome with a baseline workflow that has none of
this infrastructure: literal text search ("a name that appears in another file
is used; a claim is true if its words are in the file").

Scenarios are chosen from the failure modes the directive lists. Some are
expected misses: static analysis cannot see a worker started through
importlib, and no tool here understands unexpected naming. Those are reported as
misses, not tuned away.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import inventory, knowledge, structure
from .db import connect


def _git(root: Path, *a: str) -> None:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="b",
        GIT_AUTHOR_EMAIL="b@b",
        GIT_COMMITTER_NAME="b",
        GIT_COMMITTER_EMAIL="b@b",
    )
    subprocess.run(
        ["git", "-C", str(root), *a], check=True, capture_output=True, env=env
    )


def _repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-intel-bench-")) / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for p, t in files.items():
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_text(t)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "x")
    return root


def _open(root: Path):
    conn = connect(root.parent / "i.db")
    knowledge.ensure_schema(conn)
    inventory.scan(root, conn)
    structure.build_index(conn, root)
    return conn


def _grep_used(root: Path, name: str, defined_in: str) -> bool:
    """Baseline: the name appears in some other file."""
    for p in root.rglob("*"):
        if (
            p.is_file()
            and ".git" not in p.parts
            and str(p.relative_to(root)) != defined_in
        ) and re.search(rf"\b{re.escape(name)}\b", p.read_text(errors="replace")):
            return True
    return False


@dataclass
class Outcome:
    scenario: str
    metric: str
    truth: str
    intel: str
    baseline: str
    intel_ok: bool
    baseline_ok: bool
    note: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    def by_metric(self) -> dict[str, tuple[int, int, int]]:
        m: dict[str, list[Outcome]] = {}
        for o in self.outcomes:
            m.setdefault(o.metric, []).append(o)
        return {
            k: (len(v), sum(o.intel_ok for o in v), sum(o.baseline_ok for o in v))
            for k, v in m.items()
        }


def _claim(conn, root, spec):
    cid = knowledge.add_claim(conn, root, spec, "bench-author", "claude")
    return knowledge.verify_claim(conn, root, cid, "bench")


def run() -> Report:
    rep = Report()
    add = rep.outcomes.append

    # 1 alias: a function reached through `import ... as`
    root = _repo(
        {
            "pkg/__init__.py": "",
            "pkg/m.py": "def helper():\n    return 1\n",
            "pkg/use.py": "from pkg.m import helper as h\n\ndef go():\n    return h()\n",
        }
    )
    conn = _open(root)
    got = any(
        r["kind"] == "CALLS"
        and r["confidence"] == "proven"
        and r["src_path"] == "pkg/use.py"
        for r in structure.refs_to(conn, "helper")
    )
    add(
        Outcome(
            "alias",
            "dependency_recall",
            "use.py calls helper through alias h",
            f"call edge found: {got}",
            "name 'helper' appears in use.py (import line); the call h() is invisible",
            got,
            False,
        )
    )
    st = _claim(
        conn,
        root,
        {
            "text": "helper in pkg/m.py has no caller anywhere in the repo",
            "check": {"type": "no_static_callers", "name": "helper"},
        },
    )
    add(
        Outcome(
            "alias",
            "false_absence",
            "claim is false (alias call exists)",
            st,
            "grep sees 'helper' in use.py",
            st == "refuted",
            True,
            "baseline passes only because the import line mentions the name",
        )
    )

    # 2 dynamic worker: registered through importlib + getattr (expected miss)
    root = _repo(
        {
            "w/__init__.py": "",
            "w/job.py": "def run_cleanup():\n    return 1\n",
            "w/boot.py": "import importlib\n\ndef start(name):\n    m = importlib.import_module('w.' + name)\n    return getattr(m, 'run_' + name)()\n",
        }
    )
    conn = _open(root)
    st = _claim(
        conn,
        root,
        {
            "text": "run_cleanup in w/job.py has no caller and is dead code",
            "check": {"type": "no_static_callers", "name": "run_cleanup"},
        },
    )
    dyn = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='DYNAMIC'").fetchone()[0]
    detail = conn.execute(
        "SELECT status_detail FROM claims ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    add(
        Outcome(
            "dynamic_worker",
            "false_absence",
            "claim is false (job runs via importlib)",
            st,
            "grep: name 'run_cleanup' not used elsewhere, so 'dead'",
            False,
            False,
            f"static check wrongly supports the claim; {dyn} dynamic construct(s) flagged in the detail: {'dynamic constructs' in detail}",
        )
    )
    add(
        Outcome(
            "dynamic_worker",
            "risk_disclosed",
            "dynamic code present, claim must not be treated as proof",
            f"capped at {st}, dynamic warning present",
            "no warning",
            st != "verified" and "dynamic constructs" in detail,
            False,
        )
    )

    # 3 non-obvious subscriber, cross-language via a shared constant
    root = _repo(
        {
            "app/contracts.py": 'class Topics:\n    PLAY = "audio.play"\n',
            "app/pub.py": "from app.contracts import Topics\n\nasync def send(agent):\n    await agent.publish(Topics.PLAY, {})\n",
            "crates/c/src/lib.rs": 'pub mod topics { pub const PLAY: &str = "audio.play"; }\n',
            "crates/c/src/main.rs": "async fn main() {\n    let s = client.subscribe(topics::PLAY).await;\n}\n",
        }
    )
    conn = _open(root)
    m = structure.subject_matrix(conn).get(
        "audio.play", {"PUBLISHES": [], "SUBSCRIBES": []}
    )
    ok = bool(m["PUBLISHES"]) and bool(m["SUBSCRIBES"])
    add(
        Outcome(
            "cross_language_subscriber",
            "dependency_recall",
            "python publisher and rust subscriber linked by subject",
            f"pub {len(m['PUBLISHES'])} sub {len(m['SUBSCRIBES'])}",
            "grep 'audio.play' finds only the constant definition and Python; Rust uses topics::PLAY",
            ok,
            False,
        )
    )

    # 4 obsolete implementation still in source
    root = _repo(
        {
            "a/__init__.py": "",
            "a/old.py": "def legacy_scorer():\n    return 0\n",
            "a/new.py": "def scorer():\n    return 1\n",
            "a/main.py": "from a.new import scorer\n\ndef go():\n    return scorer()\n",
        }
    )
    conn = _open(root)
    rows, _ = structure.unreferenced(conn)
    listed = "legacy_scorer" in [r["qualname"] for r in rows] and "scorer" not in [
        r["qualname"] for r in rows
    ]
    add(
        Outcome(
            "obsolete_impl",
            "obsolete_detected",
            "legacy_scorer unreferenced, scorer used",
            f"unreferenced lists legacy_scorer: {listed}",
            f"grep: legacy_scorer used elsewhere = {_grep_used(root, 'legacy_scorer', 'a/old.py')}",
            listed,
            not _grep_used(root, "legacy_scorer", "a/old.py"),
        )
    )

    # 5 misleading outdated document: numeric claim vs code, and a link to a missing file
    root = _repo(
        {
            "cfg.py": "HALF_LIFE_S = 4500\n",
            "DOC.md": "The half-life is 600 s (see `docs/gone.md`).\n",
        }
    )
    conn = _open(root)
    st = _claim(
        conn,
        root,
        {
            "text": "the cortisol half-life constant is 600 seconds",
            "check": {
                "type": "contains",
                "path": "cfg.py",
                "text": "HALF_LIFE_S = 600",
            },
        },
    )
    add(
        Outcome(
            "outdated_doc",
            "contradiction_detected",
            "doc says 600, code says 4500",
            st,
            "no baseline check exists without reading both",
            st == "refuted",
            False,
        )
    )
    missing = [
        e
        for e in conn.execute(
            "SELECT dst, confidence FROM edges WHERE kind='DOC_MENTIONS'"
        )
    ]
    add(
        Outcome(
            "outdated_doc",
            "contradiction_detected",
            "doc cites docs/gone.md which does not exist",
            str([tuple(m) for m in missing]),
            "ls would find it only if someone looked",
            any(m["confidence"] == "unresolved" for m in missing),
            False,
        )
    )

    # 6 config-dependent behaviour
    root = _repo(
        {"s.py": "import os\n\ndef mode():\n    return os.getenv('FEATURE_X', 'off')\n"}
    )
    conn = _open(root)
    got = (
        conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='READS_ENV' AND dst='FEATURE_X'"
        ).fetchone()[0]
        == 1
    )
    add(
        Outcome(
            "config_switch",
            "dependency_recall",
            "FEATURE_X env var read by s.py:mode",
            f"READS_ENV edge: {got}",
            "grep FEATURE_X finds it",
            got,
            True,
        )
    )

    # 7 file modified after the audit
    root = _repo({"u.py": "def f():\n    return 1\n"})
    conn = _open(root)
    cid = knowledge.add_claim(
        conn,
        root,
        {
            "text": "u.py defines function f returning one",
            "evidence": [{"path": "u.py", "start": 1, "end": 2, "quote": "def f():"}],
        },
        "a",
        "claude",
    )
    knowledge.verify_claim(conn, root, cid, "b")
    (root / "u.py").write_text("def g():\n    return 1\n")
    _git(root, "add", "-A")
    inventory.scan(root, conn)
    out = knowledge.recheck(conn, root, "b")
    add(
        Outcome(
            "modified_after_audit",
            "staleness_detected",
            "claim must go stale after f was renamed",
            str(out),
            "a markdown note never notices",
            out["stale"] == 1,
            False,
        )
    )
    root2 = _repo({"u.py": "def f():\n    return 1\n"})
    conn2 = _open(root2)
    knowledge.add_claim(
        conn2,
        root2,
        {
            "text": "u.py defines function f returning one",
            "evidence": [{"path": "u.py", "start": 1, "end": 1, "quote": "def f():"}],
        },
        "a",
        "claude",
    )
    (root2 / "u.py").write_text("# header\n# another\ndef f():\n    return 1\n")
    _git(root2, "add", "-A")
    inventory.scan(root2, conn2)
    out2 = knowledge.recheck(conn2, root2, "b")
    add(
        Outcome(
            "modified_after_audit",
            "staleness_detected",
            "unrelated edit above a quote must re-bind, not stale",
            str(out2),
            "n/a",
            out2["stale"] == 0 and out2["rebound"] == 1,
            False,
        )
    )

    # 8 test that imports the module but never calls the claimed function
    root = _repo(
        {
            "p/__init__.py": "",
            "p/calc.py": "def add(a, b):\n    return a + b\n\ndef risky(a):\n    return a / 0\n",
            "tests/test_calc.py": "from p.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        }
    )
    conn = _open(root)
    st_bad = _claim(
        conn,
        root,
        {
            "text": "risky in p/calc.py is covered by tests/test_calc.py",
            "check": {"type": "called_from_tests", "name": "risky"},
        },
    )
    st_ok = _claim(
        conn,
        root,
        {
            "text": "add in p/calc.py is covered by tests/test_calc.py",
            "check": {"type": "called_from_tests", "name": "add"},
        },
    )
    add(
        Outcome(
            "test_does_not_cover",
            "contradiction_detected",
            "risky is imported-module only, never called by a test",
            st_bad,
            "grep 'calc' in tests finds the import and suggests coverage",
            st_bad == "refuted",
            False,
        )
    )
    add(
        Outcome(
            "test_does_not_cover",
            "claim_precision",
            "add is called from a test",
            st_ok,
            "grep finds 'add' in the test",
            st_ok == "supported",
            True,
        )
    )

    # 9 indirect dependency through an interface
    root = _repo(
        {
            "i/__init__.py": "",
            "i/base.py": "class Store:\n    def save(self):\n        raise NotImplementedError\n",
            "i/impl.py": "from i.base import Store\n\nclass SqlStore(Store):\n    def save(self):\n        return 1\n",
        }
    )
    conn = _open(root)
    base_edges = [
        r
        for r in conn.execute(
            "SELECT dst, dst_file_id, confidence FROM edges WHERE kind='INHERITS'"
        )
    ]
    got = (
        bool(base_edges)
        and base_edges[0]["confidence"] == "proven"
        and base_edges[0]["dst_file_id"] is not None
    )
    add(
        Outcome(
            "indirect_interface",
            "dependency_recall",
            "SqlStore depends on Store through inheritance",
            str([tuple(i) for i in base_edges]),
            "grep 'Store' matches both, direction unknown",
            got,
            False,
        )
    )

    # 10 false claims seeded into a 'historical audit'
    root = _repo(
        {
            "m/__init__.py": "",
            "m/a.py": "def real():\n    return 1\n",
            "m/b.py": "from m.a import real\n\ndef user():\n    return real()\n",
        }
    )
    conn = _open(root)
    seeded = [
        (
            {
                "text": "function imaginary exists in m/a.py",
                "check": {
                    "type": "symbol_defined",
                    "name": "imaginary",
                    "path": "m/a.py",
                },
            },
            "refuted",
        ),
        (
            {
                "text": "real in m/a.py is never called by anything",
                "check": {"type": "no_static_callers", "name": "real"},
            },
            "refuted",
        ),
        (
            {
                "text": "m/b.py contains the text from m.a import real",
                "check": {
                    "type": "contains",
                    "path": "m/b.py",
                    "text": "from m.a import real",
                },
            },
            "supported",
        ),
        (
            {
                "text": "function user exists in m/b.py",
                "check": {"type": "symbol_defined", "name": "user", "path": "m/b.py"},
            },
            "supported",
        ),
        (
            {
                "text": "m/a.py contains a function called fake_helper",
                "check": {
                    "type": "contains",
                    "path": "m/a.py",
                    "text": "def fake_helper",
                },
            },
            "refuted",
        ),
    ]
    for spec, truth in seeded:
        st = _claim(conn, root, spec)
        {"contains": True}.get(spec["check"]["type"])
        add(
            Outcome(
                "seeded_false_claims",
                "claim_precision" if truth == "supported" else "contradiction_detected",
                f"truth: {truth}",
                st,
                "baseline accepts any claim whose words appear in the repo",
                st == truth,
                (truth == "supported"),
            )
        )

    # 11 file inventory and parse coverage on a fixture with awkward files
    root = _repo(
        {
            "a.py": "x = 1\n",
            "b.rs": "fn main() {}\n",
            "c.md": "# t\n",
            "d.json": "{}",
            "e.toml": "a = 1\n",
            "broken.py": "def (:\n",
        }
    )
    conn = _open(root)
    n_scope = conn.execute(
        "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1"
    ).fetchone()[0]
    parsed = conn.execute(
        "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND parsed_hash=sha256"
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM files WHERE failure_stage='parse'"
    ).fetchone()[0]
    add(
        Outcome(
            "coverage",
            "inventory_parse_coverage",
            "5 of 6 files parse; the broken one is recorded as a failure, none vanish",
            f"in scope {n_scope}, parsed {parsed}, failed {failed}",
            "ls shows files but not which ones fail to parse",
            n_scope == 6 and parsed == 5 and failed == 1,
            False,
        )
    )
    return rep


def render(rep: Report) -> str:
    lines = [
        "| Scenario | Metric | Ground truth | Intel result | Intel | Baseline (text search) |",
        "|---|---|---|---|---|---|",
    ]
    for o in rep.outcomes:
        lines.append(
            f"| {o.scenario} | {o.metric} | {o.truth} | {o.intel[:70].replace('|', '/')} | {'ok' if o.intel_ok else 'MISS'} | {'ok' if o.baseline_ok else 'MISS'} |"
        )
    lines += [
        "",
        "| Metric | Cases | Intel correct | Baseline correct |",
        "|---|---:|---:|---:|",
    ]
    for k, (n, a, b) in sorted(rep.by_metric().items()):
        lines.append(f"| {k} | {n} | {a} | {b} |")
    notes = [f"- {o.scenario}: {o.note}" for o in rep.outcomes if o.note]
    return "\n".join(lines + ([""] + notes if notes else []))
