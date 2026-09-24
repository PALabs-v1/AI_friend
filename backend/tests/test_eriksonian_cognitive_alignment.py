"""
Tests for Eriksonian Cognitive Alignment, 3-state Mind Activation Boundaries,
and Python-based Spreading Activation/Cue Boosts.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.state.memory_store import DIRECT_CUE_BOOST, MemoryStore
from app.state.sqlite_fallback import SQLitePool


@pytest.fixture
def temp_store():
    # SQLitePool with :memory: provides an isolated, full-schema SQLite database for each test.
    pool = SQLitePool(":memory:")
    mock_graph = MagicMock()

    async def mock_execute_query(query, *args, **kwargs):
        if "MATCH (e:Entity)" in query or "MATCH (seed:Entity)" in query:
            return [{"name": "Kolkata"}, {"name": "Priya"}]
        return []

    mock_graph.execute_query = mock_execute_query
    store = MemoryStore(pool, mock_graph)
    store.qdrant_store.client = None
    return store


def test_eriksonian_db_schema_attributes(temp_store):
    """Verify that Eriksonian attributes are correctly stored and retrieved from the SQLite database."""
    # Check that 3-state thresholds are defined correctly
    assert temp_store.recall_threshold == -1.5
    assert temp_store.subconscious_threshold == -2.5
    assert temp_store.pruning_threshold == -3.5

    # Insert a memory with all Eriksonian columns
    with patch.object(temp_store, "get_embedding", return_value=[0.1] * 768):
        success = asyncio.run(
            temp_store.add_memory(
                content="I spent my childhood in Kolkata, learning cognitive architectures.",
                wing="personal",
                lifespan_stage="School Age",
                crisis="Industry vs Inferiority",
                virtue="Competence",
                relations="Neighborhood, School",
                relation_circles="Peers, teachers",
                modality="To complete, to make together",
            )
        )
    assert success is True

    # Retrieve memory using search_memories
    with patch.object(temp_store, "get_embedding", return_value=[0.1] * 768):
        results = asyncio.run(
            temp_store.search_memories("childhood in Kolkata", threshold=-5.0, limit=1)
        )
        assert len(results) == 1
        mem = results[0]
        assert (
            mem["content"]
            == "I spent my childhood in Kolkata, learning cognitive architectures."
        )
        assert mem["lifespan_stage"] == "School Age"
        assert mem["crisis"] == "Industry vs Inferiority"
        assert mem["virtue"] == "Competence"
        assert mem["relations"] == "Neighborhood, School"
        assert mem["relation_circles"] == "Peers, teachers"
        assert mem["modality"] == "To complete, to make together"


@pytest.mark.usefixtures("actr_v1_ranking")
def test_cue_and_spreading_activation_boosts(temp_store):
    """Validate cue-boost and spreading-activation *behaviour*.

    Deliberately asserts ordering/sign relationships rather than exact magic
    numbers. The previous version pinned +5.45 / +0.6, which were emergent from
    a PPR damping factor reverse-engineered to make the spreading boost land on
    exactly 0.6 — the test and the constant validated each other rather than the
    retrieval mechanism. These invariants hold under any sane tuning:

      A (direct cue match)      -> boost of at least DIRECT_CUE_BOOST
      B (entity-linked to A)    -> smaller, strictly positive spreading boost
      C (unrelated)             -> no boost at all
    """
    # Insert three test memories
    # Memory A (Contains entities 'priya' and 'kolkata')
    # Memory B (Contains entity 'kolkata')
    # Memory C (Unrelated)
    with patch.object(temp_store, "get_embedding", return_value=[0.1] * 768):
        asyncio.run(
            temp_store.add_memory(
                content="Priya is my partner, she lives in Kolkata.", wing="personal"
            )
        )
        asyncio.run(
            temp_store.add_memory(
                content="Kolkata is a beautiful city.", wing="personal"
            )
        )
        asyncio.run(
            temp_store.add_memory(content="An unrelated memory.", wing="personal")
        )

    # 1. Search with NO cues (baseline)
    with patch.object(temp_store, "get_embedding", return_value=[0.1] * 768):
        baseline_results = asyncio.run(
            temp_store.search_memories(
                "completely different query",
                threshold=-5.0,
                limit=5,
                refresh_on_recall=False,
            )
        )
        # Store baseline scores
        baseline_scores = {r["content"]: r["score"] for r in baseline_results}
        assert "Priya is my partner, she lives in Kolkata." in baseline_scores
        assert "Kolkata is a beautiful city." in baseline_scores
        assert "An unrelated memory." in baseline_scores

    # 2. Search with "Priya" (matches cue 'priya')
    with patch.object(temp_store, "get_embedding", return_value=[0.1] * 768):
        cued_results = asyncio.run(
            temp_store.search_memories(
                "Query about Priya", threshold=-5.0, limit=5, refresh_on_recall=False
            )
        )
        cued_scores = {r["content"]: r["score"] for r in cued_results}

    # Memory A contains the cue 'priya' -> direct cue boost dominates.
    actual_a_boost = (
        cued_scores["Priya is my partner, she lives in Kolkata."]
        - baseline_scores["Priya is my partner, she lives in Kolkata."]
    )
    assert actual_a_boost >= DIRECT_CUE_BOOST

    # Memory B shares the entity 'Kolkata' with directly-cued Memory A, so it
    # receives associative spreading activation — positive, but well below a
    # direct cue hit.
    actual_b_boost = (
        cued_scores["Kolkata is a beautiful city."]
        - baseline_scores["Kolkata is a beautiful city."]
    )
    assert actual_b_boost > 0.0
    assert actual_b_boost < actual_a_boost

    # Memory C shares no cues or entities -> no boost.
    actual_c_boost = (
        cued_scores["An unrelated memory."] - baseline_scores["An unrelated memory."]
    )
    assert math.isclose(actual_c_boost, 0.0, abs_tol=1e-4)


def test_pruning_threshold_decay(temp_store):
    """Verify that memories are pruned if base activation drops below self.pruning_threshold (-3.5)."""
    now = datetime.now(UTC)

    # In SQLite pool, insert two memories manually with different created_at dates
    # so we can control the exact hours_since decay calculation.
    # Memory A (Created 1000 hours ago) -> Activation: ln(1) - 0.5 * ln(1000 + 1) = -3.454 (Preserved)
    # Memory B (Created 2000 hours ago) -> Activation: ln(1) - 0.5 * ln(2000 + 1) = -3.800 (Pruned)

    time_a = (now - timedelta(hours=1000)).strftime("%Y-%m-%d %H:%M:%S")
    time_b = (now - timedelta(hours=2000)).strftime("%Y-%m-%d %H:%M:%S")

    import uuid

    async def insert_helper():
        async with temp_store.pool.acquire() as conn:
            # Insert Memory A
            await conn.execute(
                "INSERT INTO memories (id, content, recall_count, created_at, last_recalled_at, wing, importance_score) VALUES (?, ?, 1, ?, ?, 'personal', 0.4)",
                str(uuid.uuid4()),
                "Memory A",
                time_a,
                time_a,
            )
            # Insert Memory B
            await conn.execute(
                "INSERT INTO memories (id, content, recall_count, created_at, last_recalled_at, wing, importance_score) VALUES (?, ?, 1, ?, ?, 'personal', 0.4)",
                str(uuid.uuid4()),
                "Memory B",
                time_b,
                time_b,
            )

    asyncio.run(insert_helper())

    # Run apply_actr_decay on both memories
    asyncio.run(temp_store.apply_actr_decay(["Memory A", "Memory B"]))

    # Fetch remaining memories
    async def fetch_remaining():
        async with temp_store.pool.acquire() as conn:
            rows = await conn.fetch("SELECT content FROM memories")
            return [row["content"] for row in rows]

    remaining = asyncio.run(fetch_remaining())

    # Memory A should be preserved, Memory B should be pruned
    assert "Memory A" in remaining
    assert "Memory B" not in remaining
