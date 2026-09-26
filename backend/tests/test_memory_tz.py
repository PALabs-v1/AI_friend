"""
Mixed timezone-awareness regression tests for MemoryStore recency arithmetic.

Timestamps reach the scoring/decay paths from naive sources (SQLite
CURRENT_TIMESTAMP, strptime) and aware sources (Postgres timestamptz,
datetime.now(timezone.utc), a caller-supplied current_time). Subtracting a naive
and an aware datetime raises TypeError, which previously aborted apply_actr_decay
and silently emptied search results whenever the two sources were mixed. Every
recency subtraction now coerces both operands through _as_aware_utc first.
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.state.conversation_store import ConversationHistoryStore
from app.state.memory_store import MemoryStore


class TestAsAwareUtc:
    def test_naive_is_assumed_utc(self):
        dt = datetime(2026, 5, 1, 12, 0, 0)
        out = MemoryStore._as_aware_utc(dt)
        assert out.tzinfo is UTC
        assert out.hour == 12  # naive value read as-is, tagged UTC

    def test_aware_is_converted_to_utc(self):
        tz = timezone(timedelta(hours=5, minutes=30))  # +05:30
        dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=tz)
        out = MemoryStore._as_aware_utc(dt)
        assert out.tzinfo is UTC
        assert out.hour == 6 and out.minute == 30  # 12:00+05:30 -> 06:30 UTC

    def test_none_passes_through(self):
        assert MemoryStore._as_aware_utc(None) is None

    def test_subtraction_never_raises_after_coercion(self):
        naive = datetime(2026, 5, 1, 12, 0, 0)
        aware = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
        delta = MemoryStore._as_aware_utc(naive) - MemoryStore._as_aware_utc(aware)
        assert delta.total_seconds() == 2 * 3600


class TestParseQdrantCreatedAt:
    """Two write paths feed this field two different shapes:
    `_upsert_qdrant_memory` writes a numeric epoch string, but
    `_build_promotion_payload` (a promoted archived row) writes
    `created_at.isoformat()` -- an ISO-8601 string. Without handling both,
    every promoted memory's real creation time silently falls back to
    `current_time`/`now()`, corrupting `_spacing_hours` for exactly the
    memories old enough to have been promoted at all."""

    def test_numeric_epoch_string_is_parsed(self):
        dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        out = MemoryStore._parse_qdrant_created_at(
            str(dt.timestamp()), current_time=None
        )
        assert out == dt

    def test_iso_8601_string_from_the_promotion_path_is_parsed(self):
        dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        out = MemoryStore._parse_qdrant_created_at(dt.isoformat(), current_time=None)
        assert out == dt

    def test_missing_value_falls_back_to_current_time(self):
        current_time = datetime(2026, 6, 1, tzinfo=UTC)
        assert MemoryStore._parse_qdrant_created_at(None, current_time) == current_time

    def test_unparseable_value_surfaces_instead_of_corrupting_recency(self):
        with pytest.raises(ValueError, match="created_at"):
            MemoryStore._parse_qdrant_created_at(
                "not a timestamp", current_time=datetime(2026, 6, 1, tzinfo=UTC)
            )


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, content TEXT, raw_content TEXT, wing TEXT, room TEXT,
        embedding TEXT, importance_score REAL, emotional_weight REAL, valence REAL,
        certainty REAL, source TEXT, metadata TEXT, recall_count INTEGER,
        last_recalled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        lifespan_stage TEXT, crisis TEXT, virtue TEXT, relations TEXT,
        relation_circles TEXT, modality TEXT
    )
"""


class TestApplyDecayMixedTz:
    @pytest.mark.asyncio
    async def test_aware_current_time_against_naive_storage_decays(self):
        # SQLite stores naive (UTC) timestamps; passing an aware current_time
        # used to raise "can't subtract offset-naive and offset-aware
        # datetimes", abort the decay, and leave importance untouched.
        store = ConversationHistoryStore()
        await store.initialize()
        async with store.pool.acquire() as conn:
            await conn.execute(_SCHEMA)
            await conn.execute("DELETE FROM memories;")

        mock_graph = MagicMock()
        mock_graph.execute_query = AsyncMock(return_value=[])
        mem = MemoryStore(pool=store.pool, graph_db=mock_graph)
        mem.qdrant_store.client = None
        mem.get_embedding = AsyncMock(return_value=[0.1] * 768)

        try:
            await mem.add_memory("decay me", importance=0.6)
            # aware current_time ~ now, so the memory is recent (not pruned) and
            # its importance decays by the 0.8 factor.
            await mem.apply_actr_decay(["decay me"], current_time=datetime.now(UTC))
            async with store.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT importance_score FROM memories WHERE content = 'decay me'"
                )
            assert row["importance_score"] == pytest.approx(0.48, abs=1e-6)
        finally:
            await store.close()
            await mem.close()


def _make_row_aware(content, similarity, hours_ago=1, recall_count=1):
    now = datetime.now(UTC)  # aware last_recalled_at / created_at
    return {
        "content": content,
        "raw_content": content,
        "wing": "personal",
        "room": None,
        "importance_score": 0.5,
        "emotional_weight": 0.0,
        "valence": 0.0,
        "recall_count": recall_count,
        "last_recalled_at": now - timedelta(hours=hours_ago),
        "created_at": now - timedelta(hours=hours_ago),
        "metadata": "{}",
        "similarity": similarity,
    }


class TestSearchMixedTz:
    def test_naive_current_time_against_aware_rows_returns_results(self):
        # Rows carry aware timestamps; a naive current_time previously mixed
        # naive/aware in (now - last_recall), threw, and the outer handler
        # swallowed it into an empty result set.
        pool = MagicMock()
        pool.acquire = MagicMock()
        conn = AsyncMock()
        pool.acquire.return_value.__aenter__.return_value = conn
        conn.fetch.return_value = [_make_row_aware("aware memory", similarity=1.0)]

        mem = MemoryStore(pool, MagicMock())
        mem.graph_db.execute_query = AsyncMock(return_value=[])
        mem.qdrant_store.client = None

        with patch.object(mem, "get_embedding", return_value=[0.1] * 768):
            results = asyncio.run(
                mem.search_memories(
                    "query",
                    threshold=-5.0,
                    current_time=datetime(2026, 6, 1, 12, 0, 0),  # naive
                )
            )
        assert any(r["content"] == "aware memory" for r in results)
