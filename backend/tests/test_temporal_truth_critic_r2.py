"""W1 temporal truth: regressions for Codex cold critic round 2.

Each test fails on the change the critic reviewed (ADR-W1, "Cold critic,
round 2"). The critic's own reproducers asserted the filter-level fix it
suggested. Where the fix landed at a different layer (#3 in the pipeline,
#4 at the write boundary), the test drives that layer.
"""

from __future__ import annotations

import asyncio

import pytest

from app.cognitive.learning import TEMPORAL_INDEX_BATCH, ReflectionService
from app.cognitive.pipeline import CognitivePipeline
from app.config import Config
from app.state import temporal_intent
from app.state.memory_records import BeliefRecord
from app.state.memory_store import MemoryStore, _decode_memory_metadata
from app.state.temporal_detector import classify_e8_temporal_relation
from app.state.temporal_store import SLOT_CURRENT_LIMIT, TemporalMemoryStore
from evals.brainbench.adapters import build_memory_store


def _belief(rid, obj, start, *, subject="Ari", predicate="works_at", **extra):
    return BeliefRecord(
        record_id=rid,
        subject=subject,
        predicate=predicate,
        object=obj,
        valid_from=float(start),
        recorded_at=float(extra.pop("recorded", start)),
        confidence=0.9,
        **extra,
    )


@pytest.fixture
def temporal_on(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)


# --- #1 a change word unrelated to the new value closed a still-true fact ---


@pytest.mark.parametrize(
    ("context", "new"),
    [
        ("Ari moved house last month but still works at Acme", "Globex"),
        ("Maybe Ari switched jobs, but I am not certain", "Globex"),
        ("Ari did not move from Acme; he still works there", "not moved from Acme"),
        ("Ari didn't switch jobs after all", "Globex"),
        ("I moved the couch yesterday, and Ari works at Globex", "Globex"),
        ("Ari probably changed jobs", "Globex"),
    ],
)
def test_an_unrelated_hedged_or_continuity_cue_does_not_decide_an_update(context, new):
    old = _belief("old", "Acme", 1)
    incoming = _belief("new", new, 2)
    assert (
        classify_e8_temporal_relation(old, incoming, similarity=0.99, context=context)
        is None
    )


@pytest.mark.parametrize(
    ("context", "new"),
    [
        ("Ari switched to Globex", "Globex"),
        ("switched jobs recently", "Globex"),
        ("I got a new job, and Ari moved to Globex last week", "Globex"),
    ],
)
def test_a_change_word_about_the_new_value_still_decides_an_update(context, new):
    old = _belief("old", "Acme", 1)
    incoming = _belief("new", new, 2)
    relation = classify_e8_temporal_relation(
        old, incoming, similarity=0.99, context=context
    )
    assert relation == "UPDATE"


@pytest.mark.asyncio
async def test_the_production_write_never_closes_a_fact_on_an_unrelated_cue(tmp_path):
    class SameEmbedding:
        async def get_embedding(self, _text):
            return [1.0, 0.0]

    class ConservativeLLM:
        async def generate(self, _prompt, **_kwargs):
            return '{"relation":"CONFLICT"}'

    service = ReflectionService.__new__(ReflectionService)
    service.vector, service.llm = SameEmbedding(), ConservativeLLM()
    store = TemporalMemoryStore(str(tmp_path / "false-close.db"))
    try:
        await store.store_belief(_belief("old", "Acme Corp", 1))
        await store.record_assertion(
            _belief("new", "Globex", 2),
            classifier_context="Ari moved house last month but still works at Acme",
            classify_deterministic=service._classify_temporal_e8,
            classify_ambiguous=service._classify_temporal_relation,
        )
        # Held for review (DISPUTED), never closed as superseded.
        assert (await store.get_belief("old")).status != "SUPERSEDED"
    finally:
        await store.close()


# --- #2 a plain past-tense question hid every superseded fact --------------


def _linked(status, belief_id="real-old"):
    return {
        "id": "row-1",
        "content": "Ari drinks tea",
        "metadata": {
            "temporal_belief": {
                "belief_id": belief_id,
                "subject": "Ari",
                "predicate": "drinks",
                "status": status,
                "valid_from": 1.0,
                "valid_until": 2.0,
            }
        },
    }


@pytest.mark.parametrize(
    "query",
    [
        "What did I drink?",
        "What was I drinking?",
        "Where did Ari work?",
        "Did I drink tea?",
        "What was my old drink?",
    ],
)
def test_a_past_tense_question_reaches_superseded_history(temporal_on, query):
    old = _linked("SUPERSEDED")
    assert MemoryStore._filter_temporal_candidates([old], query) == [old]


@pytest.mark.parametrize(
    "query", ["What do I drink now?", "What do I drink?", "Did I say what I drink now?"]
)
def test_a_present_question_still_hides_superseded_history(temporal_on, query):
    assert MemoryStore._filter_temporal_candidates([_linked("SUPERSEDED")], query) == []


def test_both_stores_parse_temporal_intent_with_one_parser():
    from app.state import memory_store, temporal_store

    assert temporal_store._historical_intent is temporal_intent.historical_intent
    assert memory_store.historical_intent is temporal_intent.historical_intent
    assert TemporalMemoryStore.is_historical_query("What did I drink?")


# --- #3 memories with no belief link passed as current ---------------------


@pytest.mark.asyncio
async def test_an_unlinked_memory_naming_a_superseded_value_is_not_current(
    temporal_on, tmp_path
):
    store = TemporalMemoryStore(str(tmp_path / "projection.db"))
    try:
        await store.store_belief(
            _belief("acme", "Acme", 1, status="SUPERSEDED", valid_until=2.0)
        )
        await store.store_belief(_belief("globex", "Globex", 2))
        pipeline = CognitivePipeline.__new__(CognitivePipeline)
        pipeline.temporal_memory_store = store
        legacy = {"id": "legacy", "content": "Ari worked at Acme", "metadata": {}}
        other = {"id": "other", "content": "Ari likes long walks", "metadata": {}}

        now, _ = await pipeline._reconcile_temporal_memories(
            "Where does Ari work now?", [legacy, other], None
        )
        then, _ = await pipeline._reconcile_temporal_memories(
            "Where did Ari work before?", [legacy, other], None
        )

        now_ids = [m["id"] for m in now]
        assert "legacy" not in now_ids and "other" in now_ids
        assert "globex" in now_ids  # the typed current fact is supplied
        then_ids = [m["id"] for m in then]
        assert "legacy" in then_ids and "acme" in then_ids  # history reachable
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconciliation_is_off_with_the_flag_off(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", False)
    pipeline = CognitivePipeline.__new__(CognitivePipeline)
    pipeline.temporal_memory_store = TemporalMemoryStore(str(tmp_path / "off.db"))
    rows = [{"id": "legacy", "content": "Ari worked at Acme", "metadata": {}}]
    try:
        assert await pipeline._reconcile_temporal_memories(
            "Where now?", rows, None
        ) == (
            rows,
            None,
        )
    finally:
        await pipeline.temporal_memory_store.close()


# --- #4 caller metadata could forge a belief link --------------------------


@pytest.mark.asyncio
async def test_caller_metadata_cannot_forge_a_belief_link(tmp_path, monkeypatch):
    store = build_memory_store(tmp_path)

    async def embed(_text):
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(store, "get_embedding", embed)
    forged = {"temporal_belief": {"belief_id": "fake", "status": "SUPERSEDED"}}
    try:
        assert await store.add_memory("ordinary memory", metadata=forged)
        async with store.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT metadata FROM memories WHERE content = ?", "ordinary memory"
            )
        assert "temporal_belief" not in _decode_memory_metadata(row["metadata"])
    finally:
        await store.close()


def test_a_malformed_link_timestamp_never_raises(temporal_on):
    row = _linked("ACTIVE")
    row["metadata"]["temporal_belief"]["valid_until"] = "forged"
    assert MemoryStore._filter_temporal_candidates([row], "What did I drink in 2020?")


# --- #5 every fact write re-indexed the slot's whole history ---------------


@pytest.mark.asyncio
async def test_a_fact_write_indexes_a_bounded_backlog_once(temporal_on, tmp_path):
    store = TemporalMemoryStore(str(tmp_path / "bounded.db"))

    class Vector:
        def __init__(self):
            self.indexed: list[str] = []

        async def index_temporal_belief(self, belief):
            self.indexed.append(belief.record_id)
            return True

        async def get_embedding(self, _text):
            return [1.0, 0.0]

    loaded: list[int] = []
    real_query = store.query_slot_beliefs

    async def spy(subject, predicate, **kwargs):
        rows = await real_query(subject, predicate, **kwargs)
        loaded.append(len(rows))
        return rows

    store.query_slot_beliefs = spy
    vector = Vector()
    service = ReflectionService.__new__(ReflectionService)
    service.temporal_memory_store, service.vector, service.llm = store, vector, None
    try:
        for i in range(100):
            await store.store_belief(
                _belief(
                    f"old-{i}",
                    f"item-{i}",
                    i * 2,
                    status="SUPERSEDED",
                    valid_until=i * 2 + 1,
                )
            )
        await service._store_temporal_fact("Ari", "works_at", "new-1", 0.9, {})
        first = len(vector.indexed)
        await service._store_temporal_fact("Ari", "works_at", "new-2", 0.9, {})
        second = len(vector.indexed) - first

        assert first <= TEMPORAL_INDEX_BATCH + 1
        assert second <= TEMPORAL_INDEX_BATCH + 1
        assert len(set(vector.indexed)) == len(vector.indexed)  # each row once
        assert max(loaded) <= SLOT_CURRENT_LIMIT
    finally:
        await store.close()


def test_the_backlog_drains_and_then_only_the_new_fact_is_indexed(tmp_path):
    async def run():
        store = TemporalMemoryStore(str(tmp_path / "drain.db"))
        try:
            for i in range(20):
                await store.store_belief(
                    _belief(
                        f"old-{i}",
                        f"v{i}",
                        i * 2,
                        status="SUPERSEDED",
                        valid_until=i * 2 + 1,
                    )
                )
            drained = 0
            while batch := await store.unindexed_slot_beliefs(
                "Ari", "works_at", limit=8
            ):
                await store.mark_indexed([b.record_id for b in batch])
                drained += len(batch)
            assert drained == 20
            assert await store.unindexed_slot_beliefs("ari", "WORKS_AT", limit=8) == []
        finally:
            await store.close()

    asyncio.run(run())
