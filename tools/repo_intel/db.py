"""SQLite persistence. WAL mode, explicit transactions, corruption check on open."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    head_commit TEXT NOT NULL,
    branch TEXT NOT NULL,
    dirty INTEGER NOT NULL,
    tree_fingerprint TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    files_present INTEGER NOT NULL,
    in_scope INTEGER NOT NULL,
    added INTEGER NOT NULL,
    removed INTEGER NOT NULL,
    modified INTEGER NOT NULL,
    scope_changed INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path_b BLOB NOT NULL UNIQUE,
    path TEXT NOT NULL,
    present INTEGER NOT NULL,
    tracked INTEGER NOT NULL,
    git_blob TEXT,
    wt_state TEXT NOT NULL,
    kind TEXT NOT NULL,
    symlink_target TEXT,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    is_binary INTEGER NOT NULL,
    utf8_ok INTEGER NOT NULL,
    line_count INTEGER NOT NULL,
    category TEXT NOT NULL,
    language TEXT NOT NULL,
    parser TEXT NOT NULL,
    in_scope INTEGER NOT NULL,
    exclusion_reason TEXT NOT NULL,
    scope_rule TEXT NOT NULL,
    first_snapshot INTEGER NOT NULL,
    last_snapshot INTEGER NOT NULL,
    -- Each stage stores the sha256 it was done against. A stage is current only
    -- while its hash equals sha256; any edit makes every stage stale at once.
    classified_hash TEXT,
    parsed_hash TEXT,
    indexed_hash TEXT,
    read_hash TEXT,
    reviewed_hash TEXT,
    verified_hash TEXT,
    failure_stage TEXT,
    failure_hash TEXT,
    failure_msg TEXT
);
CREATE INDEX IF NOT EXISTS files_scope ON files (present, in_scope);

CREATE TABLE IF NOT EXISTS ignored_paths (
    snapshot_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    is_dir INTEGER NOT NULL,
    file_count INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, path)
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    reader TEXT NOT NULL,
    tool_family TEXT NOT NULL,
    status TEXT NOT NULL,           -- open | acked | partial | expired
    created_at TEXT NOT NULL,
    closed_at TEXT,
    snapshot_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL REFERENCES batches (id),
    file_id INTEGER NOT NULL REFERENCES files (id),
    sha256 TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    chunk_total INTEGER NOT NULL,
    acked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (batch_id, file_id, chunk_idx)
);

CREATE TABLE IF NOT EXISTS chunk_reads (
    file_id INTEGER NOT NULL REFERENCES files (id),
    sha256 TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    chunk_total INTEGER NOT NULL,
    batch_id TEXT NOT NULL,
    reader TEXT NOT NULL,
    acked_at TEXT NOT NULL,
    PRIMARY KEY (file_id, sha256, chunk_idx, reader)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files (id),
    sha256 TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    reader TEXT NOT NULL,
    tool_family TEXT NOT NULL,
    role TEXT NOT NULL,
    summary TEXT NOT NULL,
    symbols TEXT NOT NULL,           -- JSON list
    depends_on TEXT NOT NULL,        -- JSON list of repo paths
    uncertainties TEXT NOT NULL,
    evidence TEXT NOT NULL,          -- JSON list
    symbols_ok INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_file ON notes (file_id, sha256);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class CorruptIndex(RuntimeError):
    pass


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        ok = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise CorruptIndex(f"{path} is unreadable: {exc}") from exc
    if ok != "ok":
        raise CorruptIndex(f"{path} failed PRAGMA quick_check: {ok}")
    conn.executescript(DDL)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
    elif int(row[0]) != SCHEMA_VERSION:
        raise CorruptIndex(
            f"index schema {row[0]} != tool schema {SCHEMA_VERSION}; rebuild with a fresh --db path"
        )
    return conn


@contextmanager
def txn(conn: sqlite3.Connection):
    """One writer at a time; commit on success, roll everything back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def log_event(
    conn: sqlite3.Connection, ts: str, actor: str, action: str, detail: str
) -> None:
    conn.execute(
        "INSERT INTO events (ts, actor, action, detail) VALUES (?,?,?,?)",
        (ts, actor, action, detail),
    )
