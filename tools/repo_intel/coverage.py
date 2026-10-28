"""Coverage accounting. Every number comes from SQL over the index; nothing is estimated.

A stage counts only while its stored hash equals the file's current sha256, so an edited
file drops out of every stage count until it is processed again.
"""

from __future__ import annotations

import sqlite3

EM = chr(0x2014)  # the owner asked for this character in the console progress line

STAGES = ("classified", "parsed", "indexed", "read", "reviewed", "verified")


def collect(conn: sqlite3.Connection) -> dict:
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    snap = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if snap is None:
        raise RuntimeError("no inventory yet: run `repo-intel inventory` first")
    present = "present=1"
    scope = "present=1 AND in_scope=1"
    n = q(f"SELECT COUNT(*) FROM files WHERE {scope}")
    stages = {
        s: q(f"SELECT COUNT(*) FROM files WHERE {scope} AND {s}_hash = sha256")
        for s in STAGES
    }
    failed = q(
        f"SELECT COUNT(*) FROM files WHERE {scope} AND failure_stage IS NOT NULL AND (failure_hash = sha256 OR failure_stage='inventory')"
    )
    stale = q(
        f"SELECT COUNT(*) FROM files WHERE {scope} AND ("
        + " OR ".join(
            f"({s}_hash IS NOT NULL AND {s}_hash != sha256)"
            for s in STAGES
            if s != "classified"
        )
        + ")"
    )
    excluded = [
        dict(r)
        for r in conn.execute(
            f"SELECT category, scope_rule, exclusion_reason AS reason, COUNT(*) AS files, SUM(size) AS bytes FROM files "
            f"WHERE {present} AND in_scope=0 GROUP BY category, scope_rule, exclusion_reason ORDER BY files DESC, category"
        )
    ]
    scope_cats = [
        dict(r)
        for r in conn.execute(
            f"SELECT category, COUNT(*) AS files, SUM(size) AS bytes FROM files WHERE {scope} GROUP BY category ORDER BY files DESC"
        )
    ]
    ignored = [
        dict(r)
        for r in conn.execute(
            "SELECT path, is_dir, file_count, bytes, reason FROM ignored_paths WHERE snapshot_id=? ORDER BY file_count DESC",
            (snap["id"],),
        )
    ]
    removed = q("SELECT COUNT(*) FROM files WHERE present=0")
    return {
        "snapshot": dict(zip(snap.keys(), tuple(snap), strict=True)),
        "discovered": q(f"SELECT COUNT(*) FROM files WHERE {present}"),
        "in_scope": n,
        "excluded_total": q(
            f"SELECT COUNT(*) FROM files WHERE {present} AND in_scope=0"
        ),
        "stages": stages,
        "unread": n - stages["read"],
        "failed": failed,
        "changed_since_analysis": stale,
        "removed_from_tree": removed,
        "open_batches": q("SELECT COUNT(*) FROM batches WHERE status='open'"),
        "in_scope_by_category": scope_cats,
        "excluded": excluded,
        "ignored": ignored,
    }


def headline(c: dict, *, repo_doc: bool = False) -> str:
    """The progress line in the owner's format. Committed docs use a hyphen (repo style bans em dashes)."""
    n, r = c["in_scope"], c["stages"]["read"]
    pct = (100.0 * r / n) if n else 0.0
    dash = "-" if repo_doc else EM
    return f"Codebase Coverage: {r:,} / {n:,} files read ({pct:.2f}%) {dash} {n - r:,} remaining."


def render_text(c: dict) -> str:
    s, st = c["snapshot"], c["stages"]
    lines = [
        headline(c),
        f"  revision      {s['head_commit'][:12]} on {s['branch']}  ({'DIRTY working tree' if s['dirty'] else 'clean'})  snapshot #{s['id']}",
        f"  tree finger   {s['tree_fingerprint'][:16]}  scope rules {s['scope_hash'][:12]}",
        f"  discovered    {c['discovered']:,} files in the working tree ({c['excluded_total']:,} excluded, {c['in_scope']:,} in scope)",
        f"  classified    {st['classified']:,} / {c['in_scope']:,}",
        f"  parsed        {st['parsed']:,} / {c['in_scope']:,}   (structural phase, not started)"
        if not st["parsed"]
        else f"  parsed        {st['parsed']:,} / {c['in_scope']:,}",
        f"  indexed       {st['indexed']:,} / {c['in_scope']:,}   (structural phase, not started)"
        if not st["indexed"]
        else f"  indexed       {st['indexed']:,} / {c['in_scope']:,}",
        f"  read          {st['read']:,} / {c['in_scope']:,}   (delivered in full and acknowledged with checked evidence)",
        f"  reviewed      {st['reviewed']:,} / {c['in_scope']:,}   (structured note, every listed symbol found in the file)",
        f"  verified      {st['verified']:,} / {c['in_scope']:,}   (independent second reader agrees)",
        f"  failed        {c['failed']:,}   changed since analysis {c['changed_since_analysis']:,}   open claims {c['open_batches']:,}",
        "",
        "In scope by category:",
        *[
            f"  {r['category']:<22}{r['files']:>6,} files {r['bytes'] or 0:>12,} bytes"
            for r in c["in_scope_by_category"]
        ],
        "",
        "Excluded from the read count (still hashed and listed):",
        *[
            f"  {r['category']:<22}{r['files']:>6,} files {r['bytes'] or 0:>12,} bytes  [{r['scope_rule']}] {r['reason']}"
            for r in c["excluded"]
        ],
        "",
        "Ignored by git (names and sizes only, contents never opened):",
        *[
            f"  {r['path']:<46}{r['file_count']:>8,} files {r['bytes']:>14,} bytes  {r['reason']}"
            for r in c["ignored"][:25]
        ],
    ]
    if len(c["ignored"]) > 25:
        lines.append(f"  ... {len(c['ignored']) - 25} more")
    lines.append("")
    lines.append(
        "Reading all files does not mean the code is understood; see REVIEWED and VERIFIED above."
    )
    return "\n".join(lines) + "\n"


def render_md(c: dict) -> str:
    s = c["snapshot"]
    out = [
        "# Codebase coverage",
        "",
        "Generated by `python -m tools.repo_intel coverage --md`. Do not edit by hand.",
        "",
        f"**{headline(c, repo_doc=True)}**",
        "",
        f"Revision `{s['head_commit']}` on `{s['branch']}` ({'dirty working tree' if s['dirty'] else 'clean'}), tree fingerprint `{s['tree_fingerprint']}`, snapshot {s['id']} at {s['created_at']}.",
        "",
        "| Stage | Files | Meaning |",
        "|---|---:|---|",
    ]
    meaning = {
        "classified": "scope and category assigned",
        "parsed": "structural parser ran (not started)",
        "indexed": "symbols and edges stored (not started)",
        "read": "delivered in full, acknowledged with literal evidence",
        "reviewed": "structured note whose symbols all occur in the file",
        "verified": "independent second reader agrees",
    }
    for k in STAGES:
        out.append(f"| {k} | {c['stages'][k]:,} / {c['in_scope']:,} | {meaning[k]} |")
    out += [
        "",
        f"Failed: {c['failed']:,}. Changed since analysis: {c['changed_since_analysis']:,}. Open claims: {c['open_batches']:,}.",
        "",
        "## Excluded from the read count",
        "",
        "| Category | Files | Bytes | Rule | Reason |",
        "|---|---:|---:|---|---|",
    ]
    for r in c["excluded"]:
        out.append(
            f"| {r['category']} | {r['files']:,} | {r['bytes'] or 0:,} | {r['scope_rule']} | {r['reason']} |"
        )
    out += [
        "",
        "## Ignored by git",
        "",
        "| Path | Files | Bytes | Reason |",
        "|---|---:|---:|---|",
    ]
    for r in c["ignored"]:
        out.append(
            f"| `{r['path']}` | {r['file_count']:,} | {r['bytes']:,} | {r['reason']} |"
        )
    out += [
        "",
        "Reading a file does not guarantee understanding it. Only REVIEWED and VERIFIED carry weight about meaning.",
    ]
    return "\n".join(out) + "\n"
