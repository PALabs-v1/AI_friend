"""Attacker regressions for malformed memory rows and their metadata."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.state.conversation_store import ConversationHistoryStore
from app.state.memory_store import MemoryStore


@pytest.fixture
async def isolated_memory_store(monkeypatch):
    class OfflineQdrant:
        client = None

    monkeypatch.setattr(
        "app.state.semantic_recall_store.SemanticRecallStore", OfflineQdrant
    )
    conversation = ConversationHistoryStore()
    conversation.dsn = "sqlite:///:memory:"
    await conversation.initialize()
    graph = MagicMock()
    graph.execute_query = AsyncMock(return_value=[])
    store = MemoryStore(pool=conversation.pool, graph_db=graph)
    store.qdrant_store.client = None
    store.get_embedding = AsyncMock(return_value=[0.1] * 768)
    yield store, conversation
    await store.close()
    await conversation.close()


_bad_metadata = st.one_of(
    st.integers(),
    st.sampled_from([float("nan"), float("inf"), -float("inf"), 1e300]),
    st.text(max_size=400),
    st.lists(st.integers(), min_size=257, max_size=300),
    st.dictionaries(st.text(max_size=100), st.integers(), min_size=129, max_size=140),
    st.just({"nested": [[[[[[[[[["too deep"]]]]]]]]]]}),
)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(metadata=_bad_metadata)
@pytest.mark.asyncio
async def test_invalid_metadata_is_rejected_before_write(
    isolated_memory_store, metadata
):
    store, conversation = isolated_memory_store

    result = await store.add_memory("A bounded test memory", metadata=metadata)

    assert result is False
    async with conversation.pool.acquire() as conn:
        row_count = await conn.fetchval("SELECT count(*) FROM memories")
    assert row_count == 0


@pytest.mark.asyncio
async def test_corrupt_stored_metadata_surfaces_as_search_error(isolated_memory_store):
    store, conversation = isolated_memory_store
    assert await store.add_memory("Alice likes tea", embedding=[0.1] * 768)
    async with conversation.pool.acquire() as conn:
        await conn.execute("UPDATE memories SET metadata = ?", "{not-json")

    results = await store.search_memories("Alice tea", refresh_on_recall=False)

    assert results == []
    assert "metadata" in (store.last_search_error or "").lower()

    async with conversation.pool.acquire() as conn:
        await conn.execute(
            "UPDATE memories SET metadata = ?",
            json.dumps({"oversized": "x" * 1_000_001}),
        )
    results = await store.search_memories("Alice tea", refresh_on_recall=False)
    assert results == []
    assert "too large" in (store.last_search_error or "")

    async with conversation.pool.acquire() as conn:
        await conn.execute(
            "UPDATE memories SET metadata = ?, created_at = ?", "{}", "yesterday-ish"
        )
    results = await store.search_memories("Alice tea", refresh_on_recall=False)
    assert results == []
    assert "created_at" in (store.last_search_error or "")


@pytest.mark.asyncio
async def test_invalid_embedding_timestamp_and_nonfinite_scores_do_not_write(
    isolated_memory_store,
):
    store, conversation = isolated_memory_store
    assert not await store.add_memory("bad dimension", embedding=[0.1] * 767)
    assert not await store.add_memory("bad timestamp", valid_from="yesterday-ish")
    assert not await store.add_memory("bad score", importance=float("inf"))
    assert not await store.add_memory("bad valence", valence=float("nan"))
    async with conversation.pool.acquire() as conn:
        row_count = await conn.fetchval("SELECT count(*) FROM memories")
    assert row_count == 0


def test_consolidation_rejects_corrupt_or_unbounded_decay_metadata():
    with pytest.raises(ValueError, match="valid JSON"):
        MemoryStore._extract_actr_decay_rate("{broken", 0.1)
    with pytest.raises(ValueError, match="finite"):
        MemoryStore._extract_actr_decay_rate({"decay_rate": float("inf")}, 0.1)
    with pytest.raises(ValueError, match="out of range"):
        MemoryStore._extract_actr_decay_rate({"decay_rate": 11}, 0.1)
