"""Verify MemoryStore's cross-store invariants against the LIVE stack.

Phase 4b of the Brain V3 research cycle. `evals.cognitive` proves the ranking
math is correct against a synthetic embedder and a store built for testing;
nothing proves the real write path keeps three real backing stores (Postgres,
Qdrant, Neo4j) in sync under real network calls, real failure modes and real
timing. This does, by writing through the actual `MemoryStore.add_memory`
(and, for the promotion check, the actual `_write_promoted_memory`) against
whatever Postgres/Qdrant/Neo4j `app.config.Config` points at, then reading
both sides back independently and comparing.

Every check tags its rows with `wing=CONSISTENCY_CHECK_WING` and cleans up
after itself (both stores, both success and failure paths) so a run never
leaves residue in a shared dev stack.

Usage (from backend/, against a running Postgres + Qdrant, Neo4j optional
for the entity check):
    PYTHONPATH=. python -m tools.memory_consistency --out results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field

CONSISTENCY_CHECK_WING = "_memory_consistency_check"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)


async def _build_store():
    from app.runtime_bootstrap import bootstrap_runtime
    from app.state.conversation_store import ConversationHistoryStore
    from app.state.graph_db import GraphDB
    from app.state.memory_store import MemoryStore

    await bootstrap_runtime()
    conversation_store = ConversationHistoryStore()
    await conversation_store.initialize()
    graph_db = GraphDB()
    await graph_db.initialize()
    store = MemoryStore(pool=conversation_store.pool, graph_db=graph_db)
    return store, conversation_store, graph_db


async def _cleanup_wing(store) -> None:
    """Deletes every row this module's checks may have written, in both
    stores, regardless of which check ran or failed. Idempotent."""
    async with store.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM memories WHERE wing = $1", CONSISTENCY_CHECK_WING
        )
        ids = [str(r["id"]) for r in rows]
        await conn.execute(
            "DELETE FROM memories WHERE wing = $1", CONSISTENCY_CHECK_WING
        )
        await conn.execute(
            "DELETE FROM archived_memories WHERE wing = $1", CONSISTENCY_CHECK_WING
        )
    if ids and store.qdrant_store.client:
        try:
            store.qdrant_store.client.delete(
                collection_name=store.qdrant_store.collection_name, points_selector=ids
            )
        except Exception:
            pass  # best-effort cleanup; a stray point in a dev collection is not evidence of anything


async def _pg_row(store, memory_id: str) -> dict | None:
    async with store.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", memory_id)
    return dict(row) if row else None


def _qdrant_point(store, memory_id: str) -> dict | None:
    if not store.qdrant_store.client:
        return None
    points = store.qdrant_store.client.retrieve(
        collection_name=store.qdrant_store.collection_name,
        ids=[memory_id],
        with_payload=True,
    )
    return points[0].payload if points else None


async def check_write_parity(store, n: int = 20) -> CheckResult:
    """N fresh writes through add_memory: does every one land a Postgres row
    AND a Qdrant point under the same UUID, with the scalar fields agreeing?"""
    written_ids = []
    mismatches = []
    for i in range(n):
        content = f"Consistency check memory {uuid.uuid4()} number {i}"
        ok = await store.add_memory(
            content=content,
            wing=CONSISTENCY_CHECK_WING,
            importance=0.42,
            valence=0.3,
        )
        if not ok:
            mismatches.append({"index": i, "reason": "add_memory returned False"})
            continue
        async with store.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM memories WHERE content = $1 AND wing = $2",
                content,
                CONSISTENCY_CHECK_WING,
            )
        if row is None:
            mismatches.append(
                {"index": i, "reason": "no Postgres row for written content"}
            )
            continue
        memory_id = str(row["id"])
        written_ids.append(memory_id)
        point = _qdrant_point(store, memory_id)
        if point is None:
            mismatches.append(
                {"id": memory_id, "reason": "no Qdrant point under the row's UUID"}
            )
            continue
        for field_name, pg_val in (
            ("content", row["content"]),
            ("wing", row["wing"]),
            ("importance_score", row["importance_score"]),
            ("valence", row["valence"]),
        ):
            qdrant_key = (
                "importance_score" if field_name == "importance_score" else field_name
            )
            if point.get(qdrant_key) != pg_val:
                mismatches.append(
                    {
                        "id": memory_id,
                        "field": field_name,
                        "postgres": pg_val,
                        "qdrant": point.get(qdrant_key),
                    }
                )
    return CheckResult(
        name="write_parity",
        passed=not mismatches,
        detail=f"{len(written_ids)}/{n} writes produced a matching row+point pair"
        + ("" if not mismatches else f"; {len(mismatches)} mismatches"),
        evidence={"n": n, "written": len(written_ids), "mismatches": mismatches[:10]},
    )


async def check_no_duplicate_on_repeat(store) -> CheckResult:
    """Writing identical content+wing twice should reinforce the existing row
    (MemoryStore's documented repeat-detection), not create a second
    row/point. Verifies both stores stay at exactly one representation."""
    content = f"Duplicate check {uuid.uuid4()}"
    await store.add_memory(content=content, wing=CONSISTENCY_CHECK_WING)
    await store.add_memory(content=content, wing=CONSISTENCY_CHECK_WING)
    async with store.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, recall_count FROM memories WHERE content = $1 AND wing = $2",
            content,
            CONSISTENCY_CHECK_WING,
        )
    if len(rows) != 1:
        return CheckResult(
            name="no_duplicate_on_repeat",
            passed=False,
            detail=f"expected exactly 1 Postgres row after 2 identical writes, found {len(rows)}",
            evidence={"row_ids": [str(r["id"]) for r in rows]},
        )
    memory_id = str(rows[0]["id"])
    point = _qdrant_point(store, memory_id)
    reinforced = rows[0]["recall_count"] >= 1
    return CheckResult(
        name="no_duplicate_on_repeat",
        passed=point is not None and reinforced,
        detail=(
            f"1 Postgres row (recall_count={rows[0]['recall_count']}), "
            f"{'1' if point else '0'} Qdrant point"
        ),
        evidence={"id": memory_id, "recall_count": rows[0]["recall_count"]},
    )


async def check_entity_metadata_parity(store, graph_db) -> CheckResult:
    """Seeds one Entity node, writes a memory whose text names it, and checks
    that the pre-linked entity list add_memory computes ends up identical in
    the Postgres row's metadata and the Qdrant payload -- the two writes
    happen from the same in-memory list but through separate code paths
    (_insert_memory_row vs _upsert_qdrant_memory), so nothing guarantees they
    stay identical except both reading the same variable, which is exactly
    what this checks rather than assumes."""
    entity_name = f"ConsistencyCheckEntity{uuid.uuid4().hex[:8]}"
    # execute_query swallows connection failures and returns [] rather than
    # raising (graph_db.py's own connectivity-probe pattern in initialize()),
    # so an empty probe result, not an exception, is the unreachable signal.
    probe = await graph_db.execute_query("RETURN 1 AS ok")
    if not probe:
        return CheckResult(
            name="entity_metadata_parity",
            passed=False,
            detail="Neo4j unreachable -- skipped, not passed",
            evidence={},
        )
    await graph_db.execute_query(
        "CREATE (e:Entity {name: $name})", {"name": entity_name}, write=True
    )
    try:
        content = f"I talked to {entity_name} about the consistency check today."
        await store.add_memory(content=content, wing=CONSISTENCY_CHECK_WING)
        async with store.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, metadata FROM memories WHERE content = $1 AND wing = $2",
                content,
                CONSISTENCY_CHECK_WING,
            )
        if row is None:
            return CheckResult(
                name="entity_metadata_parity",
                passed=False,
                detail="write did not produce a Postgres row",
                evidence={},
            )
        pg_meta = row["metadata"]
        pg_entities = (
            json.loads(pg_meta) if isinstance(pg_meta, str) else (pg_meta or {})
        ).get("entities", [])
        point = _qdrant_point(store, str(row["id"]))
        qdrant_entities = point.get("entities", []) if point else None
        return CheckResult(
            name="entity_metadata_parity",
            passed=(
                entity_name in pg_entities
                and qdrant_entities is not None
                and set(pg_entities) == set(qdrant_entities)
            ),
            detail=f"postgres entities={pg_entities} qdrant entities={qdrant_entities}",
            evidence={"postgres": pg_entities, "qdrant": qdrant_entities},
        )
    finally:
        await graph_db.execute_query(
            "MATCH (e:Entity {name: $name}) DETACH DELETE e",
            {"name": entity_name},
            write=True,
        )


async def check_promotion_consistency(store) -> CheckResult:
    """Exercises `_write_promoted_memory` directly -- the exact code path
    whose own docstring flags the known risk: SQL commits the promotion
    first, and a subsequent Qdrant upsert failure leaves the vector index
    stale for that row (logged, not raised). This seeds a synthetic archived
    row, promotes it, and verifies both stores agree afterward on a live
    stack rather than trusting the docstring's account of its own behavior."""
    from datetime import UTC, datetime

    mem_id = str(uuid.uuid4())
    content = f"Archived promotion check {mem_id}"
    now = datetime.now(UTC)
    vector = [0.01] * 768
    async with store.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO archived_memories
                (id, content, wing, importance_score, emotional_weight,
                 valence, certainty, source, recall_count, last_recalled_at,
                 created_at, metadata)
            VALUES ($1, $2, $3, 0.6, 0.0, 0.0, 1.0, 'user', 3, $4, $4, '{}'::jsonb)
            """,
            mem_id,
            content,
            CONSISTENCY_CHECK_WING,
            now,
        )
        row = await conn.fetchrow(
            "SELECT * FROM archived_memories WHERE id = $1", mem_id
        )
    row_dict = dict(row)
    payload_meta = store._build_promotion_payload(row_dict, {})
    await store._write_promoted_memory(
        mem_id,
        content,
        row_dict,
        vector,
        row_dict["recall_count"],
        now,
        payload_meta,
        {},
    )
    async with store.pool.acquire() as conn:
        active = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem_id)
        still_archived = await conn.fetchrow(
            "SELECT 1 FROM archived_memories WHERE id = $1", mem_id
        )
    point = _qdrant_point(store, mem_id)
    passed = (
        active is not None
        and still_archived is None
        and point is not None
        and point.get("content") == content
    )
    return CheckResult(
        name="promotion_consistency",
        passed=passed,
        detail=(
            f"active_row={'yes' if active else 'no'} "
            f"still_in_archive={'yes' if still_archived else 'no'} "
            f"qdrant_point={'yes' if point else 'no'}"
        ),
        evidence={"id": mem_id},
    )


async def run_all(n: int) -> dict:
    store, conversation_store, graph_db = await _build_store()
    started = time.time()
    try:
        results = [
            await check_write_parity(store, n),
            await check_no_duplicate_on_repeat(store),
            await check_entity_metadata_parity(store, graph_db),
            await check_promotion_consistency(store),
        ]
    finally:
        await _cleanup_wing(store)
        await conversation_store.close()
        await graph_db.close()
    return {
        "suite": "memory-consistency",
        "seconds": round(time.time() - started, 1),
        "checks": [asdict(r) for r in results],
        "all_passed": all(r.passed for r in results),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20, help="writes for the parity check")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(run_all(args.n))
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1)
    for check in report["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"[{mark}] {check['name']}: {check['detail']}")
    print(args.out)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
