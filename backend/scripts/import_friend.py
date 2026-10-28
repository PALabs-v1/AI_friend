"""Import a friend archive produced by export_friend.py (roadmap Phase 4.1).

Destructive on the Postgres side by design: TRUNCATEs all 9 tables and
reloads them from the archive, matching the roadmap's verification shape
("export, wipe, import, assert the friend remembers and its affect state is
identical"). Neo4j import is idempotent MERGE rather than wipe-then-load --
there is no equivalent "assert identical" requirement for the graph, and a
destructive wipe there risks losing relationships a partial/older export
simply never described. Local files (`.identity_state/`, `state_cache.db`)
refuse to overwrite an existing non-empty destination without --force,
mirroring `create_friend.py`'s persona-overwrite guard -- these are the files
a person may have been building an evolving friend in since the export was
taken, and a silent overwrite would erase that without warning.

Everything destructive requires --force, checked before any write happens.

Usage (from backend/):
    ../.venv/bin/python scripts/import_friend.py my_friend.tar.gz --force
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("import_friend")

SCHEMA_VERSION = 1
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_EMBEDDING_DIM = 768


def _embedding_config() -> tuple[str, int]:
    return (
        getattr(Config, "EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        int(getattr(Config, "EMBEDDING_DIMENSION", DEFAULT_EMBEDDING_DIM)),
    )


def _verify_embedding_manifest(manifest: dict) -> None:
    model, dimension = _embedding_config()
    # Archives made before the manifest carried the pin used the default embedder.
    exported_model = manifest.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    exported_dimension = manifest.get("embedding_dim", DEFAULT_EMBEDDING_DIM)
    if exported_model != model or exported_dimension != dimension:
        raise ValueError(
            "Archive embedding configuration "
            f"{exported_model!r}/{exported_dimension!r} does not match this "
            f"embedder {model!r}/{dimension}."
        )


# Dependency order for reload: `messages` FK-references `sessions`, so
# sessions must exist first. Every other table is independent.
POSTGRES_IMPORT_ORDER = (
    "sessions",
    "agent_configs",
    "memories",
    "archived_memories",
    "visual_screen_traces",
    "vocabulary",
    "lexical_associations",
    "self_knowledge_gaps",
    "messages",
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _pg_cast_for(data_type: str, udt_name: str) -> str | None:
    """Explicit `::type` cast for columns where a bare bound parameter would
    be ambiguous or wrong -- mirrors the `$1::vector(768)` pattern already
    used in memory_store.py's own hand-written queries. Everything else
    (integer, double precision, text, varchar, boolean) round-trips fine as
    a plain bound parameter since `json.loads` already gives back the right
    Python native type for those.
    """
    if data_type == "uuid":
        return "uuid"
    if data_type in ("json", "jsonb"):
        return udt_name
    if data_type.startswith("timestamp"):
        return "timestamptz" if "with time zone" in data_type else "timestamp"
    if udt_name in ("vector", "halfvec"):
        return udt_name
    return None


async def _column_casts(conn, table: str) -> dict[str, str | None]:
    rows = await conn.fetch(
        "SELECT column_name, data_type, udt_name FROM information_schema.columns "
        "WHERE table_name = $1",
        table,
    )
    return {r["column_name"]: _pg_cast_for(r["data_type"], r["udt_name"]) for r in rows}


async def _wipe_postgres(conn) -> None:
    tables = ", ".join(POSTGRES_IMPORT_ORDER)
    logger.info("Truncating: %s", tables)
    await conn.execute(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")


async def _import_postgres_table(conn, table: str, path: Path) -> int:
    if not path.exists():
        logger.warning("  postgres.%s: no export file, skipping", table)
        return 0

    casts = await _column_casts(conn, table)
    count = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cols = list(row.keys())
            for col in cols:
                if not _IDENTIFIER_RE.match(col):
                    raise ValueError(f"Unsafe column name in export: {col!r}")
            placeholders = [
                f"${i + 1}::{casts[c]}" if casts.get(c) else f"${i + 1}"
                for i, c in enumerate(cols)
            ]
            values = [row[c] for c in cols]
            sql = (
                f"INSERT INTO {table} ({', '.join(cols)}) "
                f"VALUES ({', '.join(placeholders)})"
            )
            await conn.execute(sql, *values)
            count += 1
    logger.info("  postgres.%s: %d rows", table, count)
    return count


async def _import_postgres(dsn: str, staging: Path) -> dict[str, int]:
    conn = await asyncpg.connect(dsn)
    counts: dict[str, int] = {}
    try:
        # Keep the destructive wipe and every insert in one transaction. A
        # malformed row must roll back instead of leaving a partially empty
        # friend behind.
        async with conn.transaction():
            await _wipe_postgres(conn)
            pg_dir = staging / "postgres"
            for table in POSTGRES_IMPORT_ORDER:
                counts[table] = await _import_postgres_table(
                    conn, table, pg_dir / f"{table}.jsonl"
                )
    finally:
        await conn.close()
    return counts


async def _import_neo4j(staging: Path) -> dict[str, int]:
    from app.state.graph_db import GraphDB

    graph_dir = staging / "neo4j"
    nodes_path = graph_dir / "nodes.jsonl"
    counts = {"nodes": 0, "relationships": 0}
    if not nodes_path.exists():
        logger.warning("  neo4j: no export files, skipping")
        return counts

    db = GraphDB()
    try:
        await db.bootstrap_constraints()

        async def _merge_node(tx, label: str, props: dict):
            await tx.run(
                f"MERGE (n:{label} {{name: $name}}) SET n += $props",
                name=props["name"],
                props=props,
            )

        async def _merge_rel(tx, a_label, a_name, b_label, b_name, rel_type, props):
            await tx.run(
                f"MATCH (a:{a_label} {{name: $a_name}}), (b:{b_label} {{name: $b_name}}) "
                f"MERGE (a)-[r:{rel_type}]->(b) SET r += $props",
                a_name=a_name,
                b_name=b_name,
                props=props,
            )

        async with db.driver.session() as session:
            with nodes_path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    label = next(
                        (lbl for lbl in rec["labels"] if lbl in ("Agent", "Entity")),
                        None,
                    )
                    if label is None or not rec["props"].get("name"):
                        continue
                    await session.execute_write(_merge_node, label, rec["props"])
                    counts["nodes"] += 1

            rels_path = graph_dir / "relationships.jsonl"
            if rels_path.exists():
                with rels_path.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        a_label = next(
                            (
                                lbl
                                for lbl in rec["a_labels"]
                                if lbl in ("Agent", "Entity")
                            ),
                            None,
                        )
                        b_label = next(
                            (
                                lbl
                                for lbl in rec["b_labels"]
                                if lbl in ("Agent", "Entity")
                            ),
                            None,
                        )
                        a_name = rec["a_props"].get("name")
                        b_name = rec["b_props"].get("name")
                        if not (a_label and b_label and a_name and b_name):
                            continue
                        try:
                            rel_type = GraphDB._safe_relation(rec["rel_type"])
                        except ValueError:
                            logger.warning(
                                "  skipping relationship with unsafe type %r",
                                rec["rel_type"],
                            )
                            continue
                        await session.execute_write(
                            _merge_rel,
                            a_label,
                            a_name,
                            b_label,
                            b_name,
                            rel_type,
                            rec["rel_props"],
                        )
                        counts["relationships"] += 1
    finally:
        await db.close()

    logger.info("  neo4j.nodes: %d", counts["nodes"])
    logger.info("  neo4j.relationships: %d", counts["relationships"])
    return counts


def _restore_sqlite(src_path: Path, dest_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    src = sqlite3.connect(str(src_path))
    try:
        dest = sqlite3.connect(dest_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


def _import_identity_and_state(staging: Path, *, force: bool) -> dict[str, bool]:
    import shutil

    from app.cognitive.identity import _DEFAULT_IDENTITY_STATE_DIR

    identity_base = (
        getattr(Config, "IDENTITY_BASE_PATH", None) or _DEFAULT_IDENTITY_STATE_DIR
    )
    identity_in = staging / "identity_state"
    os.makedirs(identity_base, exist_ok=True)

    written: dict[str, bool] = {}
    for name in ("personality.json", "history.json"):
        src = identity_in / name
        dest = Path(identity_base) / name
        if not src.exists():
            written[name] = False
            continue
        if dest.exists() and not force:
            logger.warning(
                "  identity_state/%s already exists at %s; pass --force to overwrite. Skipping.",
                name,
                dest,
            )
            written[name] = False
            continue
        shutil.copy2(src, dest)
        written[name] = True

    identity_core_src = identity_in / "identity_core.db"
    identity_core_dest = getattr(Config, "IDENTITY_CORE_DB_PATH", None) or os.path.join(
        identity_base, "identity_core.db"
    )
    if identity_core_src.exists():
        if os.path.exists(identity_core_dest) and not force:
            logger.warning(
                "  identity_state/identity_core.db already exists at %s; "
                "pass --force to overwrite. Skipping.",
                identity_core_dest,
            )
            written["identity_core.db"] = False
        else:
            _restore_sqlite(identity_core_src, identity_core_dest)
            written["identity_core.db"] = True
    else:
        written["identity_core.db"] = False

    state_cache_src = staging / "state_cache.db"
    state_cache_dest = getattr(Config, "STATE_CACHE_DB_PATH", None) or "state_cache.db"
    if state_cache_src.exists():
        if os.path.exists(state_cache_dest) and not force:
            logger.warning(
                "  state_cache.db already exists at %s; pass --force to overwrite. Skipping.",
                state_cache_dest,
            )
            written["state_cache.db"] = False
        else:
            _restore_sqlite(state_cache_src, state_cache_dest)
            written["state_cache.db"] = True
    else:
        written["state_cache.db"] = False

    return written


async def import_friend(
    archive_path: Path, *, force: bool, skip_neo4j: bool = False
) -> None:
    if not force:
        raise ValueError(
            "import_friend is destructive (it TRUNCATEs the Postgres tables "
            "and can overwrite local identity/state files) -- pass --force "
            "to confirm."
        )

    dsn = Config.DATABASE_URL
    if not dsn:
        raise ValueError("DATABASE_URL is not set -- nothing to import into.")

    with tempfile.TemporaryDirectory(prefix="friend_import_") as tmp:
        logger.info("Extracting %s...", archive_path)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        staging = Path(tmp) / "friend_export"

        manifest_path = staging / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("Not a friend export archive: manifest.json is missing.")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Archive schema_version {manifest.get('schema_version')!r} "
                f"!= this script's {SCHEMA_VERSION!r}."
            )
        _verify_embedding_manifest(manifest)

        logger.info("Importing Postgres...")
        pg_counts = await _import_postgres(dsn, staging)

        if not skip_neo4j:
            logger.info("Importing Neo4j...")
            await _import_neo4j(staging)
        else:
            logger.info("Skipping Neo4j (--skip-neo4j).")

        logger.info("Importing identity state and durable affect state...")
        state_written = _import_identity_and_state(staging, force=force)

    logger.info("Done. Postgres rows imported: %s", pg_counts)
    skipped = [name for name, ok in state_written.items() if not ok]
    if skipped:
        logger.warning(
            "Not overwritten (missing in archive, or already present without "
            "--force covering it): %s",
            skipped,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive", type=Path, help="Path to the exported .tar.gz archive"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required: confirms the destructive Postgres wipe and local-file overwrite.",
    )
    parser.add_argument(
        "--skip-neo4j", action="store_true", help="Skip restoring the Neo4j subgraph."
    )
    args = parser.parse_args()

    asyncio.run(
        import_friend(args.archive, force=args.force, skip_neo4j=args.skip_neo4j)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
