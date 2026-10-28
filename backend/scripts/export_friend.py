"""Export a friend's full state to one portable archive (roadmap Phase 4.1).

State spans four stores that have to move together, or the restored friend
is missing part of itself:

- Postgres: 9 tables (`db/schema.sql` is the schema of record -- Prisma only
  covers 3 of the 9).
- Neo4j: the `:Agent` / `:Entity` subgraph.
- `backend/.identity_state/`: `personality.json`, `history.json` (carries
  the `persona_seeded_at` marker), `identity_core.db`.
- `state_cache.db`: the durable affect/trust state (`agent_state`,
  `adaptive_weights`) -- easy to miss under the blanket `*.db` gitignore.

Qdrant and Redis are deliberately not captured: Qdrant's memory vectors share
the same UUIDs as the Postgres `memories` rows (derivable), and both of
Redis's consumers already mirror to SQLite (also derivable). See the
roadmap's Phase 4.1 section for the full reasoning.

Usage (from backend/):
    ../.venv/bin/python scripts/export_friend.py --out my_friend.tar.gz
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("export_friend")

SCHEMA_VERSION = 1
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_EMBEDDING_DIM = 768


def _embedding_config() -> tuple[str, int]:
    return (
        getattr(Config, "EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        int(getattr(Config, "EMBEDDING_DIMENSION", DEFAULT_EMBEDDING_DIM)),
    )


def _build_manifest(pg_counts, neo4j_counts, state_present) -> dict:
    embedding_model, embedding_dim = _embedding_config()
    return {
        "schema_version": SCHEMA_VERSION,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "exported_at": time.time(),
        "postgres_row_counts": pg_counts,
        "neo4j_counts": neo4j_counts,
        "state_files_present": state_present,
    }


POSTGRES_TABLES = (
    "sessions",
    "messages",
    "agent_configs",
    "memories",
    "archived_memories",
    "visual_screen_traces",
    "vocabulary",
    "lexical_associations",
    "self_knowledge_gaps",
)

_GRAPH_LABELS = ("Agent", "Entity")


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


async def _export_postgres(dsn: str, out_dir: Path) -> dict[str, int]:
    pg_dir = out_dir / "postgres"
    pg_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    conn = await asyncpg.connect(dsn)
    try:
        for table in POSTGRES_TABLES:
            rows = await conn.fetch(f"SELECT * FROM {table}")
            with (pg_dir / f"{table}.jsonl").open("w") as f:
                for row in rows:
                    f.write(json.dumps(dict(row), default=_json_default))
                    f.write("\n")
            counts[table] = len(rows)
            logger.info("  postgres.%s: %d rows", table, len(rows))
    finally:
        await conn.close()
    return counts


async def _export_neo4j(out_dir: Path) -> dict[str, int]:
    from app.state.graph_db import GraphDB

    graph_dir = out_dir / "neo4j"
    graph_dir.mkdir(parents=True, exist_ok=True)
    counts = {"nodes": 0, "relationships": 0}

    db = GraphDB()
    try:
        async with db.driver.session() as session:
            node_result = await session.run(
                "MATCH (n) WHERE n:Agent OR n:Entity "
                "RETURN labels(n) AS labels, properties(n) AS props"
            )
            with (graph_dir / "nodes.jsonl").open("w") as f:
                async for record in node_result:
                    f.write(
                        json.dumps(
                            {
                                "labels": list(record["labels"]),
                                "props": dict(record["props"]),
                            },
                            default=_json_default,
                        )
                    )
                    f.write("\n")
                    counts["nodes"] += 1

            rel_result = await session.run(
                "MATCH (a)-[r]->(b) WHERE (a:Agent OR a:Entity) AND (b:Agent OR b:Entity) "
                "RETURN labels(a) AS a_labels, properties(a) AS a_props, "
                "labels(b) AS b_labels, properties(b) AS b_props, "
                "type(r) AS rel_type, properties(r) AS rel_props"
            )
            with (graph_dir / "relationships.jsonl").open("w") as f:
                async for record in rel_result:
                    f.write(
                        json.dumps(
                            {
                                "a_labels": list(record["a_labels"]),
                                "a_props": dict(record["a_props"]),
                                "b_labels": list(record["b_labels"]),
                                "b_props": dict(record["b_props"]),
                                "rel_type": record["rel_type"],
                                "rel_props": dict(record["rel_props"]),
                            },
                            default=_json_default,
                        )
                    )
                    f.write("\n")
                    counts["relationships"] += 1
    finally:
        await db.close()

    logger.info("  neo4j.nodes: %d", counts["nodes"])
    logger.info("  neo4j.relationships: %d", counts["relationships"])
    return counts


def _snapshot_sqlite(src_path: str, dest_path: Path) -> bool:
    """A consistent point-in-time copy via the SQLite Online Backup API,
    rather than a plain file copy -- `state_cache.db` and `identity_core.db`
    hold no exclusive lock against a concurrently running agent process, and
    a raw `shutil.copy2` of a live SQLite file can capture a torn write.
    """
    if not os.path.exists(src_path):
        return False
    src = sqlite3.connect(src_path)
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()
    return True


def _export_identity_and_state(out_dir: Path) -> dict[str, bool]:
    from app.cognitive.identity import _DEFAULT_IDENTITY_STATE_DIR

    identity_base = (
        getattr(Config, "IDENTITY_BASE_PATH", None) or _DEFAULT_IDENTITY_STATE_DIR
    )
    identity_out = out_dir / "identity_state"
    identity_out.mkdir(parents=True, exist_ok=True)

    present: dict[str, bool] = {}
    for name in ("personality.json", "history.json"):
        src = Path(identity_base) / name
        if src.exists():
            shutil.copy2(src, identity_out / name)
            present[name] = True
        else:
            present[name] = False
            logger.warning("  identity_state/%s: not found at %s, skipping", name, src)

    identity_core_db_path = getattr(
        Config, "IDENTITY_CORE_DB_PATH", None
    ) or os.path.join(identity_base, "identity_core.db")
    present["identity_core.db"] = _snapshot_sqlite(
        identity_core_db_path, identity_out / "identity_core.db"
    )
    if not present["identity_core.db"]:
        logger.warning(
            "  identity_state/identity_core.db: not found at %s, skipping",
            identity_core_db_path,
        )

    state_cache_path = getattr(Config, "STATE_CACHE_DB_PATH", None) or "state_cache.db"
    present["state_cache.db"] = _snapshot_sqlite(
        state_cache_path, out_dir / "state_cache.db"
    )
    if not present["state_cache.db"]:
        logger.warning("  state_cache.db: not found at %s, skipping", state_cache_path)

    return present


async def export_friend(out_path: Path, *, skip_neo4j: bool = False) -> None:
    dsn = Config.DATABASE_URL
    if not dsn:
        raise ValueError("DATABASE_URL is not set -- nothing to export from.")

    with tempfile.TemporaryDirectory(prefix="friend_export_") as tmp:
        staging = Path(tmp) / "friend_export"
        staging.mkdir()

        logger.info("Exporting Postgres...")
        pg_counts = await _export_postgres(dsn, staging)

        neo4j_counts = {"nodes": 0, "relationships": 0, "skipped": True}
        if not skip_neo4j:
            logger.info("Exporting Neo4j...")
            neo4j_counts = await _export_neo4j(staging)
            neo4j_counts["skipped"] = False
        else:
            logger.info("Skipping Neo4j (--skip-neo4j).")

        logger.info("Exporting identity state and durable affect state...")
        state_present = _export_identity_and_state(staging)

        manifest = _build_manifest(pg_counts, neo4j_counts, state_present)
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Archiving to %s...", out_path)
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(staging, arcname="friend_export")

    logger.info("Done: %s", out_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output archive path (e.g. my_friend.tar.gz)",
    )
    parser.add_argument(
        "--skip-neo4j",
        action="store_true",
        help="Skip the Neo4j subgraph (e.g. Neo4j isn't reachable right now).",
    )
    args = parser.parse_args()

    asyncio.run(export_friend(args.out, skip_neo4j=args.skip_neo4j))
    return 0


if __name__ == "__main__":
    sys.exit(main())
