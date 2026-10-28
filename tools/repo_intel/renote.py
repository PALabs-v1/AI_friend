"""Repair path for files that were read but whose note failed the symbol check.

`ack` marks a file READ once its evidence strings check out, but REVIEWED only
if every listed symbol occurs in the file. A note with one bad symbol therefore
leaves a read file unreviewed, and re-claiming it would re-deliver the whole
text. `repair` fixes the note, not the reading: it takes the latest note for the
file's current hash, keeps only the symbols that literally occur in the file,
and records the pruning in the note's uncertainties so it is never silent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIN_SUMMARY = 40


def repair(
    conn: sqlite3.Connection, root: Path, reader: str, family: str
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    rows = conn.execute(
        "SELECT id, path, sha256 FROM files WHERE in_scope=1 AND present=1 AND read_hash=sha256 "
        "AND (reviewed_hash IS NULL OR reviewed_hash!=sha256) ORDER BY path"
    ).fetchall()
    for f in rows:
        try:
            data = (root / f["path"]).read_bytes()
        except OSError as exc:
            out.append((f["path"], f"unreadable: {exc}"))
            continue
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            out.append((f["path"], "changed since inventory; run inventory first"))
            continue
        text = data.decode("utf-8", "replace")
        n = conn.execute(
            "SELECT * FROM notes WHERE file_id=? AND sha256=? ORDER BY id DESC LIMIT 1",
            (f["id"], f["sha256"]),
        ).fetchone()
        if n is None:
            out.append((f["path"], "no note to repair; claim the file again"))
            continue
        if not n["role"].strip() or len(n["summary"].strip()) < MIN_SUMMARY:
            out.append((f["path"], "role or summary too short; write a new note"))
            continue
        symbols = json.loads(n["symbols"])
        kept = [s for s in symbols if s in text]
        dropped = [s for s in symbols if s not in text]
        unc = n["uncertainties"]
        try:
            unc_list = json.loads(unc)
        except ValueError:
            unc_list = [unc] if unc else []
        if dropped:
            unc_list.append(
                f"repair: dropped symbols not found literally in the file: {', '.join(dropped)}"
            )
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO notes (file_id, sha256, batch_id, reader, tool_family, role, summary, symbols, depends_on, uncertainties, evidence, symbols_ok, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f["id"],
                    f["sha256"],
                    "repair",
                    reader,
                    family,
                    n["role"],
                    n["summary"],
                    json.dumps(kept),
                    n["depends_on"],
                    json.dumps(unc_list),
                    n["evidence"],
                    1,
                    now,
                ),
            )
            conn.execute(
                "UPDATE files SET reviewed_hash=? WHERE id=?", (f["sha256"], f["id"])
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        out.append(
            (f["path"], f"reviewed; dropped {len(dropped)} of {len(symbols)} symbols")
        )
    return out
