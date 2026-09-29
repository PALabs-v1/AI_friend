"""
Tests for the unified memory-insert writer (_insert_memory_row).

The writer collapses eight near-identical INSERTs across three axes: SQLite vs
PostgreSQL placeholders, timed vs untimed (created_at/last_recalled_at), and the
full Eriksonian column set vs a legacy fallback for un-migrated schemas. These
tests exercise each axis directly.
"""

import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from app.state.conversation_store import ConversationHistoryStore
from app.state.memory_records import BeliefRecord
from app.state.memory_store import MemoryStore

_FULL_SCHEMA = """
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

# Legacy schema: no Eriksonian columns -> the full insert must fail and the
# writer must fall back to the base column set.
_LEGACY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, content TEXT, raw_content TEXT, wing TEXT, room TEXT,
        embedding TEXT, importance_score REAL, emotional_weight REAL, valence REAL,
        certainty REAL, source TEXT, metadata TEXT, recall_count INTEGER,
        last_recalled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


async def _make_store(schema):
    store = ConversationHistoryStore()
    await store.initialize()
    async with store.pool.acquire() as conn:
        await conn.execute(schema)
        await conn.execute("DELETE FROM memories;")
    mock_graph = MagicMock()
    mock_graph.execute_query = AsyncMock(return_value=[])
    mem = MemoryStore(pool=store.pool, graph_db=mock_graph)
    mem.qdrant_store.client = None
    mem.get_embedding = AsyncMock(return_value=[0.1] * 768)
    return store, mem


class TestSqliteInsert:
    @pytest.mark.asyncio
    async def test_temporal_belief_link_is_written_and_updated(self):
        store, mem = await _make_store(_FULL_SCHEMA)
        belief = BeliefRecord(
            record_id="belief-acme",
            subject="Ari",
            predicate="works_at",
            object="Acme",
            valid_from=10.0,
            recorded_at=11.0,
        )
        try:
            assert await mem.index_temporal_belief(belief)
            assert (
                await mem.update_temporal_belief_link(
                    belief.record_id, status="SUPERSEDED", valid_until=20.0
                )
                == 1
            )
            async with store.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT metadata FROM memories WHERE content = 'Ari works at Acme'"
                )
            link = orjson.loads(row["metadata"])["temporal_belief"]
            assert link["belief_id"] == "belief-acme"
            assert link["status"] == "SUPERSEDED"
            assert link["valid_until"] == 20.0
        finally:
            await store.close()
            await mem.close()

    @pytest.mark.asyncio
    async def test_timed_full_insert_sets_created_at_and_eriksonian(self):
        store, mem = await _make_store(_FULL_SCHEMA)
        try:
            ts = datetime(2026, 3, 1, 9, 30, 0)
            ok = await mem.add_memory(
                "timed memory",
                wing="personal",
                importance=0.7,
                lifespan_stage="adulthood",
                modality="text",
                current_time=ts,
            )
            assert ok is True
            async with store.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT recall_count, created_at, last_recalled_at, "
                    "lifespan_stage, modality FROM memories WHERE content = 'timed memory'"
                )
            assert row["recall_count"] == 1
            assert str(row["created_at"]).startswith("2026-03-01 09:30:00")
            assert str(row["last_recalled_at"]).startswith("2026-03-01 09:30:00")
            assert row["lifespan_stage"] == "adulthood"
            assert row["modality"] == "text"
        finally:
            await store.close()
            await mem.close()

    @pytest.mark.asyncio
    async def test_untimed_insert_defaults_last_recalled_at(self):
        store, mem = await _make_store(_FULL_SCHEMA)
        try:
            ok = await mem.add_memory("untimed memory", wing="personal")
            assert ok is True
            async with store.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT recall_count, last_recalled_at FROM memories "
                    "WHERE content = 'untimed memory'"
                )
            assert row["recall_count"] == 1
            # CURRENT_TIMESTAMP default fired -> not NULL.
            assert row["last_recalled_at"] is not None
        finally:
            await store.close()
            await mem.close()

    @pytest.mark.asyncio
    async def test_legacy_schema_falls_back_and_still_inserts(self):
        store, mem = await _make_store(_LEGACY_SCHEMA)
        try:
            # Full insert references lifespan_stage etc. which do not exist here;
            # the writer must catch that and retry with the base columns.
            ok = await mem.add_memory(
                "legacy memory", wing="personal", lifespan_stage="childhood"
            )
            assert ok is True
            async with store.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT content, recall_count FROM memories "
                    "WHERE content = 'legacy memory'"
                )
            assert row is not None
            assert row["recall_count"] == 1
        finally:
            await store.close()
            await mem.close()


class TestPgPlaceholderGeneration:
    """The PG branch builds $n placeholders; assert they are well-formed and
    aligned with the parameter count on the AsyncMock (no real Postgres)."""

    def _make_pg_store(self):
        pool = MagicMock()  # not MockPGPool -> is_sqlite False -> PG branch
        store = MemoryStore(pool, MagicMock())
        store.qdrant_store.client = None
        return store

    async def _capture(self, current_time):
        store = self._make_pg_store()
        conn = AsyncMock()
        await store._insert_memory_row(
            conn,
            memory_id="id1",
            content="c",
            raw_val="c",
            wing="personal",
            room=None,
            vector_str="[0.1]",
            importance=0.5,
            emotion=0.0,
            valence=0.0,
            certainty=1.0,
            source="user",
            metadata_json="{}",
            lifespan_stage="s",
            crisis=None,
            virtue=None,
            relations=None,
            relation_circles=None,
            modality=None,
            current_time=current_time,
        )
        # include_eriksonian=True succeeds on AsyncMock (no exception), so the
        # first (and only) call is the full insert.
        sql, params = (
            conn.execute.await_args_list[0].args[0],
            conn.execute.await_args_list[0].args[1:],
        )
        return sql, params

    @pytest.mark.asyncio
    async def test_timed_placeholders_sequential_and_aligned(self):
        sql, params = await self._capture(datetime(2026, 1, 1))
        nums = [int(n) for n in re.findall(r"\$(\d+)", sql)]
        # 12 base + 5 provenance + 6 eriksonian + 2 time = 25 bound params.
        assert nums == list(range(1, 26))
        assert len(params) == 25
        assert " 1," in sql or ", 1," in sql  # recall_count literal present

    @pytest.mark.asyncio
    async def test_untimed_uses_current_timestamp_literal(self):
        sql, params = await self._capture(None)
        nums = [int(n) for n in re.findall(r"\$(\d+)", sql)]
        # 12 base + 5 provenance + 6 eriksonian = 23 bound params; time is a literal.
        assert nums == list(range(1, 24))
        assert len(params) == 23
        assert "CURRENT_TIMESTAMP" in sql
