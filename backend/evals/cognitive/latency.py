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
