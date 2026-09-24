"""
Memory Store Tests — ACT-R Retrieval Scoring (Phase 2).

Tests validate that the ACT-R activation equation correctly ranks memories
based on frequency, recency, cosine similarity, and emotional alignment.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.state.memory_store import MemoryStore


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.acquire = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, conn


@pytest.fixture
def memory_store(mock_pool):
    pool, _ = mock_pool
    mock_graph = MagicMock()
    mock_graph.execute_query = AsyncMock(return_value=[])
    store = MemoryStore(pool, mock_graph)
    store.qdrant_store.client = None
    return store


def _make_row(
    content,
    similarity,
    raw_content=None,
    wing="personal",
    room=None,
    hours_ago=0,
    recall_count=1,
    valence=0.0,
    importance=0.5,
    emotion=0.0,
):
    """Helper to build a mock DB row with all ACT-R fields."""
    now = datetime.now(UTC)
    return {
        "content": content,
        "raw_content": raw_content or content,
        "wing": wing,
        "room": room,
        "importance_score": importance,
        "emotional_weight": emotion,
        "valence": valence,
        "recall_count": recall_count,
        "last_recalled_at": now - timedelta(hours=hours_ago),
        "created_at": now - timedelta(hours=hours_ago),
        "metadata": "{}",
        "similarity": similarity,
    }


@pytest.mark.usefixtures("actr_v1_ranking")
def test_calculate_utility_no_emotion(memory_store, mock_pool):
    """Basic ACT-R retrieval: single memory should pass threshold."""
    _pool, conn = mock_pool
    rows = [_make_row("Old memory", similarity=1.0, hours_ago=10, recall_count=3)]
    conn.fetch.return_value = rows

    with patch.object(memory_store, "get_embedding", return_value=[0.1] * 768):
        results = asyncio.run(memory_store.search_memories("test query", threshold=0.1))
        assert len(results) == 1
        assert results[0]["content"] == "Old memory"
        assert results[0]["score"] > 0.1


@pytest.mark.usefixtures("actr_v1_ranking")
def test_emotional_boost(memory_store, mock_pool):
    """Mood-congruent recall: emotionally aligned memory should rank higher."""
    _pool, conn = mock_pool
    rows = [
        _make_row("Mundane Fact", similarity=0.7, valence=0.0),
        _make_row("Emotional Memory", similarity=0.6, valence=0.5, emotion=0.8),
    ]
    conn.fetch.return_value = rows

    with patch.object(memory_store, "get_embedding", return_value=[0.1] * 768):
        # With current_valence=0.5, emotional memory should align better
        results = asyncio.run(
            memory_store.search_memories(
                "test query", threshold=0.1, limit=1, current_valence=0.5
            )
        )
        assert results[0]["content"] == "Emotional Memory"


@pytest.mark.usefixtures("actr_v1_ranking")
def test_time_decay_ranking(memory_store, mock_pool):
    """ACT-R base-level activation: recent memories should outrank stale ones."""
    _pool, conn = mock_pool
    rows = [
        _make_row("Recent memory", similarity=0.7, hours_ago=0, recall_count=2),
        _make_row("Ancient memory", similarity=0.9, hours_ago=2400, recall_count=1),
    ]
    conn.fetch.return_value = rows

    with patch.object(memory_store, "get_embedding", return_value=[0.1] * 768):
        results = asyncio.run(
            memory_store.search_memories("test query", threshold=-5.0, limit=1)
        )
        # Recent memory has higher base-level activation despite lower similarity
        assert results[0]["content"] == "Recent memory"


@pytest.mark.usefixtures("actr_v1_ranking")
def test_recall_frequency_boost(memory_store, mock_pool):
    """ACT-R: frequently recalled memories should have higher activation."""
    _pool, conn = mock_pool
    rows = [
        _make_row("Rarely recalled", similarity=0.8, recall_count=1),
        _make_row("Frequently recalled", similarity=0.7, recall_count=50),
    ]
    conn.fetch.return_value = rows

    with patch.object(memory_store, "get_embedding", return_value=[0.1] * 768):
        results = asyncio.run(
            memory_store.search_memories("test query", threshold=-5.0, limit=1)
        )
        # ln(50) >> ln(1), so frequent recall should win despite lower similarity
        assert results[0]["content"] == "Frequently recalled"


def test_neo4j_spreading_activation(mock_pool):
    pool, conn = mock_pool
    # First memory gets direct cue boost (contains "cricket"), second does not but mentions Priya
    rows = [
        _make_row("Aniket played cricket yesterday.", similarity=0.9, hours_ago=1),
        _make_row("Priya loves coding.", similarity=0.6, hours_ago=1),
    ]
    conn.fetch.return_value = rows

    mock_graph = MagicMock()

    # Mock Neo4j execute_query to return a relationship between Aniket and Priya
    async def mock_execute_query(query, *args, **kwargs):
        if "MATCH (e:Entity)" in query:
            return [{"name": "Aniket"}, {"name": "Priya"}]
        elif "MATCH (s:Entity)-[r]-(t:Entity)" in query:
            return [{"source": "Aniket", "target": "Priya"}]
        return []

    mock_graph.execute_query = mock_execute_query

    store = MemoryStore(pool, mock_graph)
    store.qdrant_store.client = None

    with patch.object(store, "get_embedding", return_value=[0.1] * 768):
        results = asyncio.run(
            store.search_memories("cricket", threshold=-10.0, limit=2)
        )
        assert len(results) == 2
        priya_mem = next(r for r in results if "Priya" in r["content"])
        assert "Priya" in priya_mem["content"]
