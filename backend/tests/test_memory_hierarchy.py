"""
Hierarchical Memory Tests — Scoping and Verbatim Integrity.

Validates that the memory store correctly filters by 'wing' and 'room'
and preserves the 'raw_content' verbatim truth.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.contracts import MemorySurfaced
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


def _make_row(content, raw_content=None, wing="personal", room=None, similarity=1.0):
    now = datetime.now(UTC)
    return {
        "content": content,
        "raw_content": raw_content or content,
        "wing": wing,
        "room": room,
        "importance_score": 0.5,
        "emotional_weight": 0.0,
        "valence": 0.0,
        "recall_count": 1,
        "last_recalled_at": now,
        "created_at": now,
        "metadata": "{}",
        "similarity": similarity,
    }


@pytest.mark.asyncio
async def test_memory_contract_validation():
    """Verify that the new memory contracts validate correctly."""
    data = {
        "memories": [
            {
                "content": "Narrative version",
                "raw_content": "Verbatim truth",
                "scope": {"wing": "personal", "room": "session_1"},
                "score": 0.9,
                "valence": 0.1,
            }
        ],
        "source": "episodic",
        "provenance": "pgvector_actr",
        "context": "test context",
    }
    msg = MemorySurfaced.model_validate(data)
    assert msg.memories[0].raw_content == "Verbatim truth"
    assert msg.memories[0].scope.wing == "personal"
    assert msg.provenance == "pgvector_actr"


@pytest.mark.usefixtures("actr_v1_ranking")
@pytest.mark.asyncio
async def test_scoped_search_query_generation(memory_store, mock_pool):
    """Verify that search_memories routes queries using the correct function and parameters."""
    _pool, conn = mock_pool
    conn.fetch.return_value = []

    with patch.object(memory_store, "get_embedding", return_value=[0.1] * 768):
        # 1. Test search in specific room
        await memory_store.search_memories("query", wing="identity", room="reflections")

        # Capture the call to fetch containing surface_actr_memories
        found_call = None
        for call in conn.fetch.call_args_list:
            if "surface_actr_memories" in call[0][0]:
                found_call = call
                break

        assert found_call is not None
        query_sql = found_call[0][0]
        params = found_call[0][1:]

        assert "surface_actr_memories" in query_sql
        assert "identity" in params
        assert "reflections" in params


@pytest.mark.asyncio
async def test_verbatim_storage_integrity(memory_store, mock_pool):
    """Verify that add_memory stores both processed and raw content."""
    _pool, conn = mock_pool

    with patch.object(memory_store, "get_embedding", return_value=[0.1] * 768):
        await memory_store.add_memory(
            content="Processed narrative",
            raw_content="Raw verbatim transcript",
            wing="personal",
        )

        # add_memory also teaches the MentalLexicon (extra execute calls after
        # the insert), so locate the memories INSERT specifically rather than
        # assuming it is the last execute.
        insert_call = None
        for call in conn.execute.call_args_list:
            if "INSERT INTO memories" in call[0][0]:
                insert_call = call
                break

        assert insert_call is not None
        params = insert_call[0][1:]
        assert params[1] == "Processed narrative"
        assert params[2] == "Raw verbatim transcript"
        assert params[3] == "personal"


@pytest.mark.asyncio
async def test_search_returns_hierarchical_metadata(memory_store, mock_pool):
    """Verify that search results include wing and room info."""
    _pool, conn = mock_pool
    conn.fetch.return_value = [
        _make_row("Content", "Raw", wing="identity", room="private")
    ]

    with patch.object(memory_store, "get_embedding", return_value=[0.1] * 768):
        results = await memory_store.search_memories("query")
        assert len(results) == 1
        assert results[0]["wing"] == "identity"
        assert results[0]["room"] == "private"
        assert results[0]["raw_content"] == "Raw"
