"""Retrieval latency of the real `MemoryStore` on the SQLite path, per policy.

    python -m evals.cognitive latency --sizes 200 1000 3000 5000

Stores are filled with random unit vectors (latency does not depend on what
the vectors mean) and queried with the production caller's arguments
(limit=3, refresh_on_recall=False). Reports p50/p95/p99 in milliseconds over
`queries` searches with the L1 cache cleared before each one, i.e. the cold
per-turn cost the foreground fallback path pays.
"""

from __future__ import annotations

import asyncio
import platform
import statistics
import time
from datetime import UTC, datetime, timedelta

import numpy as np

from .harness import _QuietLogs


def _pct(values, q):
    return float(np.percentile(values, q)) if values else float("nan")


async def measure(
    size: int, policy: str, queries: int = 40, seed: int = 0, write_every: int = 0
) -> dict:
    """`write_every` > 0 inserts a new memory before every n-th query, so the
    vector index's incremental-append path is part of the measurement."""
    from unittest.mock import AsyncMock, MagicMock

    from app.config import Config
    from app.state.conversation_store import ConversationHistoryStore
    from app.state.memory_store import MemoryStore

    rng = np.random.default_rng(seed)
    previous = Config.MEMORY_RANKING_POLICY
    Config.MEMORY_RANKING_POLICY = policy
    with _QuietLogs():
        conversation = ConversationHistoryStore()
        conversation.dsn = "sqlite:///:memory:"
        await conversation.initialize()
        graph = MagicMock()
        graph.execute_query = AsyncMock(return_value=[])
        store = MemoryStore(pool=conversation.pool, graph_db=graph)
        store.qdrant_store.client = None
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        try:
            for i in range(size):
                vec = rng.standard_normal(768)
                vec /= np.linalg.norm(vec)
                await store.add_memory(
                    f"memory number {i} about topic {i % 37} and detail {i % 11}",
                    importance=0.6,
                    current_time=t0 + timedelta(minutes=17 * i),
                    embedding=vec.tolist(),
                )
            now = t0 + timedelta(minutes=17 * size + 60)
            timings = []
            await store.warm_retrieval_index()  # as agents do at startup
            for q in range(queries):
                if write_every and q and q % write_every == 0:
                    vec = rng.standard_normal(768)
                    await store.add_memory(
                        f"late memory {q}",
                        importance=0.6,
                        current_time=now,
                        embedding=(vec / np.linalg.norm(vec)).tolist(),
                    )
                qvec = rng.standard_normal(768)
                qvec /= np.linalg.norm(qvec)
                store.get_embedding = AsyncMock(return_value=qvec.tolist())
                store._l1_cache.clear()
                started = time.perf_counter()
                await store.search_memories(
                    f"what about topic {q % 37}",
                    limit=3,
                    refresh_on_recall=False,
                    current_time=now,
                )
                timings.append((time.perf_counter() - started) * 1000.0)
            return {
                "size": size,
                "policy": policy,
                "queries": queries,
                "write_every": write_every,
                "p50_ms": round(_pct(timings, 50), 2),
                "p95_ms": round(_pct(timings, 95), 2),
                "p99_ms": round(_pct(timings, 99), 2),
                "mean_ms": round(statistics.fmean(timings), 2),
            }
        finally:
            Config.MEMORY_RANKING_POLICY = previous
            await store.close()
            await conversation.close()


async def measure_temporal_projection(size: int = 5000, queries: int = 40) -> dict:
    """Compare search with candidate-local filtering over linked belief rows."""
    from unittest.mock import AsyncMock, MagicMock

    import numpy as np

    from app.config import Config
    from app.state.conversation_store import ConversationHistoryStore
    from app.state.memory_store import MemoryStore

    rng = np.random.default_rng(0)
    previous = Config.MEMORY_RANKING_POLICY
    previous_temporal = Config.MEMORY_TEMPORAL_TRUTH_ENABLED
    Config.MEMORY_RANKING_POLICY = "hybrid"
    with _QuietLogs():
        conversation = ConversationHistoryStore()
        conversation.dsn = "sqlite:///:memory:"
        await conversation.initialize()
        graph = MagicMock()
        graph.execute_query = AsyncMock(return_value=[])
        memory = MemoryStore(pool=conversation.pool, graph_db=graph)
        memory.qdrant_store.client = None
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        try:
            for index in range(size):
                vector = rng.standard_normal(768)
                vector /= np.linalg.norm(vector)
                await memory.add_memory(
                    f"memory number {index} about topic {index % 37}",
                    importance=0.6,
                    # The internal link path (caller metadata cannot set it).
                    temporal_link={
                        "belief_id": f"temporal-{index}",
                        "subject": f"user-{index}",
                        "predicate": "lives_in" if index % 100 == 0 else "works_at",
                        "status": "ACTIVE",
                        "valid_from": t0.timestamp(),
                        "valid_until": None,
                    },
                    current_time=t0 + timedelta(minutes=17 * index),
                    embedding=vector.tolist(),
                )
            await memory.warm_retrieval_index()
            query_vector = rng.standard_normal(768)
            query_vector /= np.linalg.norm(query_vector)
            memory.get_embedding = AsyncMock(return_value=query_vector.tolist())
            query_times = []
            enabled_times = []
            now = t0 + timedelta(days=365)
            query = "Where do I live now?"
            for _ in range(queries):
                Config.MEMORY_TEMPORAL_TRUTH_ENABLED = False
                memory._l1_cache.clear()
                started = time.perf_counter()
                await memory.search_memories(
                    query, limit=3, refresh_on_recall=False, current_time=now
                )
                query_times.append((time.perf_counter() - started) * 1000.0)

                Config.MEMORY_TEMPORAL_TRUTH_ENABLED = True
                memory._l1_cache.clear()
                started = time.perf_counter()
                await memory.search_memories(
                    query, limit=3, refresh_on_recall=False, current_time=now
                )
                enabled_times.append((time.perf_counter() - started) * 1000.0)
            baseline_p95 = _pct(query_times, 95)
            temporal_p95 = _pct(enabled_times, 95)
            return {
                "size": size,
                "queries": queries,
                "baseline_policy": "hybrid",
                "baseline_p95_ms": round(baseline_p95, 2),
                "temporal_p95_ms": round(temporal_p95, 2),
                "p95_delta_pct": round(
                    100.0 * (temporal_p95 - baseline_p95) / baseline_p95, 2
                ),
                "machine": platform.machine(),
                "linked_rows": size,
                "method": "candidate-local belief_id/status filter; no projection lookup",
            }
        finally:
            Config.MEMORY_RANKING_POLICY = previous
            Config.MEMORY_TEMPORAL_TRUTH_ENABLED = previous_temporal
            await memory.close()
            await conversation.close()


async def measure_temporal_reconcile(
    slots: int = 500, versions: int = 10, queries: int = 40
) -> dict:
    """Per-turn cost of checking surfaced memories against the projection.

    `CognitivePipeline._reconcile_temporal_memories` runs in Stage 3 when
    the flag is on (W1 critic r2 #2, #3). It is outside `search_memories`,
    so the search comparison above does not see it; this reports it on its
    own, against a projection of `slots` x `versions` beliefs (one current
    value per slot, the rest superseded) in a file-backed store.
    """
    import tempfile
    from pathlib import Path

    from app.cognitive.pipeline import CognitivePipeline
    from app.config import Config
    from app.state.memory_records import BeliefRecord
    from app.state.temporal_store import TemporalMemoryStore

    previous = Config.MEMORY_TEMPORAL_TRUTH_ENABLED
    Config.MEMORY_TEMPORAL_TRUTH_ENABLED = True
    with tempfile.TemporaryDirectory() as tmp, _QuietLogs():
        store = TemporalMemoryStore(str(Path(tmp) / "projection.db"))
        try:
            for slot in range(slots):
                for version in range(versions):
                    current = version == versions - 1
                    await store.store_belief(
                        BeliefRecord(
                            record_id=f"b-{slot}-{version}",
                            subject=f"person{slot}",
                            predicate="works_at",
                            object=f"company{slot}x{version}",
                            valid_from=float(version * 10),
                            valid_until=None if current else float(version * 10 + 10),
                            recorded_at=float(version * 10),
                            confidence=0.9,
                            status="ACTIVE" if current else "SUPERSEDED",
                        )
                    )
            pipeline = CognitivePipeline.__new__(CognitivePipeline)
            pipeline.temporal_memory_store = store
            surfaced = [
                {
                    "id": f"m{i}",
                    "content": f"person7 worked at company7x{i}",
                    "metadata": {},
                }
                for i in range(3)
            ]
            present, historical = [], []
            for index in range(queries):
                slot = (index * 37) % slots
                for query, bucket in (
                    (f"Where does person{slot} work now?", present),
                    (f"Where did person{slot} work before?", historical),
                ):
                    started = time.perf_counter()
                    await pipeline._reconcile_temporal_memories(query, surfaced, None)
                    bucket.append((time.perf_counter() - started) * 1000.0)
            return {
                "beliefs": slots * versions,
                "queries": queries,
                "present_p50_ms": round(_pct(present, 50), 2),
                "present_p95_ms": round(_pct(present, 95), 2),
                "historical_p50_ms": round(_pct(historical, 50), 2),
                "historical_p95_ms": round(_pct(historical, 95), 2),
                "machine": platform.machine(),
            }
        finally:
            Config.MEMORY_TEMPORAL_TRUTH_ENABLED = previous
            await store.close()


def run_temporal_reconcile(queries: int = 40) -> dict:
    """Synchronous CLI entry point for the Stage 3 reconciliation cost."""
    return asyncio.run(measure_temporal_reconcile(queries=queries))


def run(
    sizes=(200, 1000, 3000, 5000),
    policies=("actr_v1", "hybrid"),
    queries=40,
    write_every=(0, 5),
) -> list[dict]:
    return [
        asyncio.run(measure(s, p, queries, write_every=w))
        for s in sizes
        for p in policies
        for w in write_every
    ]


def run_temporal_projection(size: int = 5000, queries: int = 40) -> dict:
    """Synchronous CLI entry point for the temporal projection comparison."""
    return asyncio.run(measure_temporal_projection(size, queries))
