"""Phase 2 commands: build and query the structural index."""

from __future__ import annotations

import argparse
import json

from . import structure
from .db import connect


def _conn(a: argparse.Namespace):
    from .cli import _paths

    root, db = _paths(a)
    return root, connect(db)


def cmd_index(a: argparse.Namespace) -> int:
    root, conn = _conn(a)
    res = structure.build_index(conn, root)
    st = structure.stats(conn)
    if a.json:
        print(
            json.dumps(
                {
                    "parsed": res.parsed,
                    "failed": res.failed,
                    "stale": res.skipped_stale,
                    **st,
                },
                indent=2,
            )
        )
    else:
        print(
            f"parsed {res.parsed} / {st['parseable']} parseable files ({st['in_scope']} in scope)"
        )
        print(f"symbols {res.symbols:,}   edges {res.edges:,}")
        for p, msg in res.failed[:20]:
            print(f"  FAILED {p}: {msg}")
        for p in res.skipped_stale[:20]:
            print(f"  STALE (changed since inventory, run inventory first) {p}")
        print(
            "symbols by kind:",
            ", ".join(f"{k} {v}" for k, v in st["symbols_by_kind"].items()),
        )
        conf: dict[str, int] = {}
        for k, v in st["edges_by_kind_confidence"].items():
            conf[k.split("/")[1]] = conf.get(k.split("/")[1], 0) + v
        print(
            "edges by confidence:",
            ", ".join(f"{k} {v}" for k, v in sorted(conf.items())),
        )
    return 1 if res.failed or res.skipped_stale else 0


def cmd_symbols(a: argparse.Namespace) -> int:
    _, conn = _conn(a)
    rows = structure.find_symbols(conn, a.name, a.limit)
    for r in rows:
        dec = f"  @{r['decorators']}" if r["decorators"] else ""
        print(
            f"{r['path']}:{r['line']}-{r['end_line']}  {r['kind']} {r['qualname']}{dec}"
        )
    if not rows:
        print(
            "no symbol with that name (it may still exist behind a dynamic construct; see `unreferenced`)"
        )
    return 0


def cmd_refs(a: argparse.Namespace) -> int:
    _, conn = _conn(a)
    rows = structure.refs_to(conn, a.name, a.limit)
    for r in rows:
        print(
            f"[{r['confidence']:<10}] {r['src_path']}:{r['line']}  {r['kind']} {r['src']} -> {r['dst']}"
        )
    if not rows:
        print(
            "no static reference found. That does not mean unused: dynamic imports, decorators, "
            "config-selected classes and message topics are not all visible to static analysis."
        )
    return 0


def cmd_deps(a: argparse.Namespace) -> int:
    _, conn = _conn(a)
    for r in structure.file_deps(conn, a.path, a.reverse):
        print(f"[{r['confidence']:<10}] {r['other']}  (line {r['line']}, {r['kind']})")
    return 0


def cmd_subjects(a: argparse.Namespace) -> int:
    _, conn = _conn(a)
    m = structure.subject_matrix(conn)
    for subj, d in sorted(m.items()):
        if a.filter and a.filter not in subj:
            continue
        print(subj)
        for kind in ("PUBLISHES", "SUBSCRIBES"):
            for path, src, line, conf in d[kind]:
                print(f"   {kind:<10} [{conf}] {path}:{line} {src}")
    return 0


def cmd_unreferenced(a: argparse.Namespace) -> int:
    _, conn = _conn(a)
    rows, dyn = structure.unreferenced(conn, a.kind)
    for r in rows[: a.limit]:
        print(f"{r['path']}:{r['line']}  {r['qualname']}")
    print(
        f"-- {len(rows)} {a.kind}s with no static call or import edge. NOT dead code: {dyn} dynamic "
        "constructs (importlib, getattr, plugin discovery) and all decorator, bus and config wiring are invisible to this list."
    )
    return 0


def cmd_bench(a: argparse.Namespace) -> int:
    from . import bench

    rep = bench.run()
    text = bench.render(rep)
    print(text)
    if a.out:
        from pathlib import Path

        Path(a.out).write_text(text + "\n")
    return 0


def cmd_audit(a: argparse.Namespace) -> int:
    from pathlib import Path

    from . import audit, bench, gitinfo, knowledge

    root, conn = _conn(a)
    knowledge.ensure_schema(conn)
    rev = (
        gitinfo.head_commit(root)[:12] if hasattr(gitinfo, "head_commit") else "unknown"
    )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ARCHITECTURE_MAP.md").write_text(audit.architecture_map(conn, rev))
    (out / "DISCREPANCY_REPORT.md").write_text(audit.discrepancy_report(conn))
    (out / "DEBT_REGISTRY.md").write_text(audit.debt_registry(conn))
    (out / "NOTE_QUALITY.md").write_text(audit.note_quality(conn))
    if a.bench:
        (out / "BENCHMARK_TABLE.md").write_text(bench.render(bench.run()) + "\n")
    print(f"wrote reports to {out}")
    return 0


def cmd_renote(a: argparse.Namespace) -> int:
    from . import renote

    root, conn = _conn(a)
    res = renote.repair(conn, root, a.reader, a.family)
    for path, msg in res:
        print(f"  {path}: {msg}")
    print(
        f"{sum(1 for _, m in res if m.startswith('reviewed'))} of {len(res)} repaired"
    )
    return 0


def register(add) -> None:
    sp = add(
        "repair-notes",
        cmd_renote,
        "mark read files REVIEWED by pruning symbols that do not occur in the file",
    )
    sp.add_argument("--reader", default="claude-main")
    sp.add_argument("--family", default="claude")
    sp = add(
        "audit",
        cmd_audit,
        "regenerate the architecture map, discrepancy report and debt registry",
    )
    sp.add_argument("--out", default="docs/engineering/repo-intel")
    sp.add_argument(
        "--bench",
        action="store_true",
        help="also run the benchmark and write its table",
    )
    sp = add(
        "bench",
        cmd_bench,
        "run the intelligence-system benchmark on fixtures with known ground truth",
    )
    sp.add_argument("--out")
    sp = add(
        "index",
        cmd_index,
        "parse every in-scope file; build the symbol and relationship index",
    )
    sp.add_argument("--json", action="store_true")
    sp = add("symbols", cmd_symbols, "where a function, class or type is defined")
    sp.add_argument("name")
    sp.add_argument("--limit", type=int, default=40)
    sp = add("refs", cmd_refs, "static references to a symbol, with confidence tiers")
    sp.add_argument("name")
    sp.add_argument("--limit", type=int, default=60)
    sp = add(
        "deps",
        cmd_deps,
        "files a file imports (or, with --reverse, files that import it)",
    )
    sp.add_argument("path")
    sp.add_argument("--reverse", action="store_true")
    sp = add(
        "subjects", cmd_subjects, "NATS subject matrix: publishers and subscribers"
    )
    sp.add_argument("--filter", default="")
    sp = add(
        "unreferenced",
        cmd_unreferenced,
        "symbols with no static reference (not proof of dead code)",
    )
    sp.add_argument("--kind", default="function")
    sp.add_argument("--limit", type=int, default=80)
