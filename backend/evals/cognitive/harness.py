"""Drive the real `MemoryStore` through a scenario, the way production does.

This is the production path, not a model of it: `add_memory` (duplicate
reinforcement, lexicon learning, contradiction linking) and `search_memories`
(L1 cache, MRL gating, the Rust ACT-R kernel, cue boost, PageRank, goal buffer,
threshold, archive promotion) all run unmodified against an in-memory SQLite
store. Only three things are substituted, each for a stated reason:

* the embedder -- `LatentTopicEmbedder`, because no model server exists here;
* Qdrant -- disabled, so the SQLite fallback is the path under test (this is
  also the path a deployment without Qdrant/Postgres runs);
* Neo4j -- a stub returning no rows, because the relation leg is not reached
  in production either (see docs/brain-research/01-problems.md, M-4).

Each probe runs with the goal buffer and L1 cache cleared, so a probe's result
cannot depend on which probe happened to run before it. Probe order therefore
does not matter and two policies see identical inputs.

Retrieval parameters default to what the two production callers pass
(`action.py:_surface_fallback_memories`, `surfacing_agent.py`): limit=3,
refresh_on_recall=False. The ranking policy is whatever
`Config.MEMORY_RANKING_POLICY` says unless `run_production` pins one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from .metrics import ProbeResult
from .scenarios import Scenario

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _QuietLogs:
    """Production logs one INFO line per write/recall; thousands per run."""

    def __enter__(self):
        self._previous = logging.root.manager.disable
        logging.disable(logging.WARNING)
        return self

    def __exit__(self, *exc):
        logging.disable(self._previous)
        return False


async def build_store(embedder):
    from app.state.conversation_store import ConversationHistoryStore
    from app.state.memory_store import MemoryStore

    conversation = ConversationHistoryStore()
    conversation.dsn = "sqlite:///:memory:"
    await conversation.initialize()
    graph = MagicMock()
    graph.execute_query = AsyncMock(return_value=[])
    store = MemoryStore(pool=conversation.pool, graph_db=graph)
    store.qdrant_store.client = None
    store.get_embedding = embedder.aembed
    store.get_embeddings = embedder.aembed_many
    return store, conversation


async def load_scenario(store, scenario: Scenario, embedder) -> int:
    """Replay every event through `add_memory` on the simulated clock."""
    failures = 0
    for event in scenario.events:
        ok = await store.add_memory(
            event.text,
            wing="personal",
            importance=event.importance,
            emotion=event.emotion,
            valence=event.valence,
            source="user",
            current_time=T0 + timedelta(hours=event.t_hours),
            embedding=embedder.embed(event.text),
        )
        failures += 0 if ok else 1
    return failures


async def run_probes(
    store,
    scenario: Scenario,
    *,
    limit: int = 3,
    refresh_on_recall: bool = False,
    full_candidate_pool: bool | None = None,
) -> list[ProbeResult]:
    key_of = scenario.memory_keys()
    results = []
    for probe in scenario.probes:
        store.goal_buffer.flush()
        store._last_query_vector = None
        store._l1_cache.clear()
        valence, arousal, cortisol = probe.affect
        found = await store.search_memories(
            probe.query,
            wing="personal",
            limit=limit,
            refresh_on_recall=refresh_on_recall,
            full_candidate_pool=full_candidate_pool,
            current_valence=valence,
            current_arousal=arousal,
            current_cortisol=cortisol,
            current_time=T0 + timedelta(hours=probe.t_hours),
        )
        if refresh_on_recall and store._background_tasks:
            await asyncio.gather(*list(store._background_tasks), return_exceptions=True)
        ranked = tuple(key_of.get(m.get("content"), "?") for m in found)
        results.append(
            ProbeResult(
                probe_key=probe.key,
                scenario_seed=scenario.seed,
                categories=tuple(sorted(probe.categories)),
                ranked=ranked,
                relevant=tuple(sorted(probe.relevant)),
                obsolete=tuple(sorted(probe.obsolete)),
                k=limit,
                error=store.last_search_error,
            )
        )
    return results


async def run_production(
    scenario: Scenario, embedder, ranking_policy: str | None = None, **probe_kwargs
) -> list[ProbeResult]:
    """`ranking_policy` pins `Config.MEMORY_RANKING_POLICY` ("hybrid" or
    "actr_v1") for this run and restores it afterwards."""
    from app.config import Config

    previous = Config.MEMORY_RANKING_POLICY
    if ranking_policy is not None:
        Config.MEMORY_RANKING_POLICY = ranking_policy
    try:
        return await _run_production(scenario, embedder, **probe_kwargs)
    finally:
        Config.MEMORY_RANKING_POLICY = previous


async def _run_production(
    scenario: Scenario, embedder, **probe_kwargs
) -> list[ProbeResult]:
    with _QuietLogs():
        store, conversation = await build_store(embedder)
        try:
            failures = await load_scenario(store, scenario, embedder)
            if failures:
                raise RuntimeError(
                    f"{failures} add_memory calls failed in {scenario.name}"
                )
            return await run_probes(store, scenario, **probe_kwargs)
        finally:
            await store.close()
            await conversation.close()
