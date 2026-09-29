from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.cognitive.learning import ReflectionService
from app.cognitive.memory_activation import memories_to_activations
from app.cognitive.pipeline import CognitivePipeline
from app.config import Config
from app.state.memory_records import BeliefRecord
from app.state.memory_store import MemoryStore
from app.state.temporal_detector import classify_e8_temporal_relation
from app.state.temporal_store import TemporalMemoryStore
from evals.cognitive.temporal_adversarial import run_production_false_closure


class _StubLLM:
    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        return self.response


class _StubVector:
    async def get_embedding(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _record(record_id: str, object_value: str, valid_from: float) -> BeliefRecord:
    return BeliefRecord(
        record_id=record_id,
        subject="Ari",
        predicate="lives_in",
        object=object_value,
        valid_from=valid_from,
        recorded_at=valid_from,
    )


@pytest.mark.asyncio
async def test_ambiguous_classifier_uses_only_top_three_neighbors(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)
    llm = _StubLLM('{"relation":"UPDATE"}')
    service = ReflectionService.__new__(ReflectionService)
    service.llm = llm
    neighbors = [_record(f"old-{i}", f"city-{i}", i) for i in range(5)]

    relation = await service._classify_temporal_relation(
        neighbors[2:], _record("new", "Tokyo", 20), "moved there recently"
    )

    assert relation == "UPDATE"
    assert len(llm.prompts) == 1
    assert "old-0" not in llm.prompts[0]
    assert all(f"city-{i}" in llm.prompts[0] for i in range(2, 5))


@pytest.mark.asyncio
async def test_ambiguous_classifier_fails_closed_when_feature_is_disabled(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", False)
    llm = _StubLLM('{"relation":"UPDATE"}')
    service = ReflectionService.__new__(ReflectionService)
    service.llm = llm

    relation = await service._classify_temporal_relation(
        [_record("old", "Lisbon", 10)], _record("new", "Seoul", 20), "context"
    )

    assert relation == "CONFLICT"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_e8_cosine_negation_detector_runs_offline():
    service = ReflectionService.__new__(ReflectionService)
    service.vector = _StubVector()
    neighbors = [_record("old", "vegetarian", 10)]
    incoming = _record("new", "not vegetarian now", 10)

    relation = await service._classify_temporal_e8(
        neighbors, incoming, "changed preference"
    )

    assert relation == "UPDATE"


def test_temporal_cues_do_not_close_a_high_similarity_restatement():
    old = _record("old", "Acme Corporation", 10)
    new = _record("new", "Acme", 20)

    assert (
        classify_e8_temporal_relation(
            old, new, similarity=0.95, context="Ari still works there now."
        )
        is None
    )


@pytest.mark.asyncio
async def test_later_dated_restatement_does_not_supersede_prior_belief():
    store = TemporalMemoryStore(":memory:")
    prior = _record("prior", "Acme Corporation", 10)
    restatement = _record("restatement", "Acme", 20)
    try:
        await store.store_belief(prior)
        assert await store.record_assertion(restatement) == "ELABORATION"
        assert (await store.get_belief("prior")).status == "ACTIVE"
        assert await store.get_belief("restatement") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_production_classifier_routes_later_same_value_to_elaboration(
    monkeypatch,
):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)
    store = TemporalMemoryStore(":memory:")
    service = ReflectionService.__new__(ReflectionService)
    service.vector = _StubVector()
    service.llm = _StubLLM('{"relation":"ELABORATION"}')
    prior = _record("prior", "Acme Corporation", 10)
    restatement = _record("restatement", "Acme", 20)
    try:
        await store.store_belief(prior)
        relation = await store.record_assertion(
            restatement,
            classifier_context="Ari still works there now.",
            classify_deterministic=service._classify_temporal_e8,
            classify_ambiguous=service._classify_temporal_relation,
        )
        assert relation == "ELABORATION"
        assert (await store.get_belief("prior")).status == "ACTIVE"
        assert service.llm.prompts == []
    finally:
        await store.close()


def test_production_false_closure_suite_covers_adversarial_still_true_cases():
    report = run_production_false_closure(seeds=range(1, 3))

    assert report["still_true"] == 16
    assert report["false_closed"] == 0
    assert report["false_closure_rate"] == 0.0
    assert set(report["categories"]) == {
        "paraphrase",
        "abbreviation",
        "still",
        "now",
        "these_days",
        "hedging",
        "dated_restatement",
        "second_person",
    }


@pytest.mark.asyncio
async def test_pipeline_retrieval_separates_current_from_historical(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)
    store = TemporalMemoryStore(":memory:")
    pipeline = CognitivePipeline.__new__(CognitivePipeline)
    pipeline.temporal_memory_store = store
    await store.store_belief(_record("old", "Lisbon", 10))
    await store.record_assertion(
        _record("new", "Seoul", 20).model_copy(update={"recorded_at": 21})
    )
    memories = [
        {"content": "Ari lives in Lisbon", "relevance": 1.0},
        {"content": "Ari lived in Lisbon", "relevance": 0.95},
        {"content": "Ari used to live in Lisbon", "relevance": 1.0},
    ]
    try:
        current, obsolete, ambiguous = await pipeline._temporal_belief_memories(
            "Where do I live now?"
        )
        filtered = pipeline._filter_superseded_memories(memories, obsolete)
        (
            past,
            past_obsolete,
            historical_ambiguous,
        ) = await pipeline._temporal_belief_memories("Where did I used to live?")
        assert [item["content"] for item in current] == ["Ari lives in Seoul"]
        assert [item["content"] for item in filtered] == []
        assert len(memories) == 3
        assert [item["content"] for item in past] == [
            "Ari lives in Lisbon",
            "Ari lives in Seoul",
        ]
        assert past_obsolete == []
        assert ambiguous is False
        assert historical_ambiguous is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_first_person_query_fails_closed_for_multiple_subjects(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)
    monkeypatch.setattr(Config, "TEMPORAL_MEMORY_SUBJECT", None)
    store = TemporalMemoryStore(":memory:")
    pipeline = CognitivePipeline.__new__(CognitivePipeline)
    pipeline.temporal_memory_store = store
    await store.store_belief(_record("ari-home", "Lisbon", 10))
    await store.store_belief(
        _record("bob-home", "Reykjavik", 10).model_copy(update={"subject": "Bob"})
    )
    try:
        memories, obsolete, ambiguous = await pipeline._temporal_belief_memories(
            "Where do I live now?"
        )
        assert memories == []
        assert obsolete == []
        assert ambiguous is True
        legacy = [{"content": "Bob lives in Reykjavik", "relevance": 1.0}]
        supplied = memories_to_activations(legacy)
        safe_legacy, safe_activations = pipeline._scope_memory_candidates(
            legacy, supplied, ambiguous
        )
        assert safe_legacy == []
        assert safe_activations == []
        (
            _plural_memories,
            _plural_obsolete,
            plural_ambiguous,
        ) = await pipeline._temporal_belief_memories("Where do we live?")
        assert plural_ambiguous is True
        (
            _named_memories,
            _named_obsolete,
            named_ambiguous,
        ) = await pipeline._temporal_belief_memories("Where do Ari and Bob live?")
        assert named_ambiguous is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_named_subject_overrides_configured_default(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)
    monkeypatch.setattr(Config, "TEMPORAL_MEMORY_SUBJECT", "Ari")
    store = TemporalMemoryStore(":memory:")
    pipeline = CognitivePipeline.__new__(CognitivePipeline)
    pipeline.temporal_memory_store = store
    await store.store_belief(_record("ari-home", "Lisbon", 10))
    await store.store_belief(
        _record("bob-home", "Seoul", 10).model_copy(update={"subject": "Bob"})
    )
    try:
        memories, _obsolete, ambiguous = await pipeline._temporal_belief_memories(
            "Where does Bob live now?"
        )
        assert ambiguous is False
        assert [item["content"] for item in memories] == ["Bob lives in Seoul"]
    finally:
        await store.close()


def test_legacy_and_temporal_memories_remain_peer_activation_inputs():
    legacy = {"content": "Ari likes hiking", "relevance": 0.7}
    typed = {"id": "belief-1", "content": "Ari lives in Seoul", "relevance": 0.9}
    pipeline = CognitivePipeline.__new__(CognitivePipeline)
    legacy_activations = memories_to_activations([legacy])
    typed_activations = memories_to_activations([typed])
    activations = pipeline._merge_memory_activations(
        legacy_activations, typed_activations
    )

    assert [item.structured_value["content"] for item in activations or []] == [
        "Ari likes hiking",
        "Ari lives in Seoul",
    ]


def test_memory_store_filters_candidate_by_linked_belief_status(monkeypatch):
    candidate = {
        "id": "memory-1",
        "metadata": {
            "temporal_belief": {"belief_id": "belief-1", "status": "SUPERSEDED"}
        },
    }
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)

    assert (
        MemoryStore._filter_temporal_candidates([candidate], "Where do I live?") == []
    )
    assert MemoryStore._filter_temporal_candidates(
        [candidate], "Where did I used to live?"
    ) == [candidate]
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", False)
    assert MemoryStore._filter_temporal_candidates([candidate], "Where do I live?") == [
        candidate
    ]


def test_linked_temporal_candidates_are_scoped_without_store_lookup(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)
    monkeypatch.setattr(Config, "TEMPORAL_MEMORY_SUBJECT", None)
    ari = {
        "content": "Ari lives in Seoul",
        "metadata": {"temporal_belief": {"subject": "Ari"}},
    }
    bob = {
        "content": "Bob lives in Lisbon",
        "metadata": {"temporal_belief": {"subject": "Bob"}},
    }
    unlinked = {"content": "I like hiking", "metadata": {}}

    scoped, _ = CognitivePipeline._scope_linked_temporal(
        "Where do I live?", [ari, bob, unlinked], None
    )
    assert scoped == [unlinked]
    named, _ = CognitivePipeline._scope_linked_temporal(
        "Where does Ari live?", [ari, bob, unlinked], None
    )
    assert named == [ari, unlinked]


def test_temporal_candidates_merge_into_precomputed_activations():
    existing = memories_to_activations(
        [{"content": "Ari likes hiking", "relevance": 0.7}]
    )
    typed = memories_to_activations(
        [{"id": "belief-1", "content": "Ari lives in Seoul", "score": 0.9}]
    )
    pipeline = CognitivePipeline.__new__(CognitivePipeline)

    activations = pipeline._merge_memory_activations(existing, typed)

    assert [item.record_id for item in activations or []] == [
        "legacy-0",
        "belief-1",
    ]


@pytest.mark.asyncio
async def test_reflection_fact_write_runs_e8_before_ambiguous_llm(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_TEMPORAL_TRUTH_ENABLED", True)
    store = TemporalMemoryStore(":memory:")
    service = ReflectionService.__new__(ReflectionService)
    service.temporal_memory_store = store
    service.vector = _StubVector()
    service.llm = _StubLLM('{"relation":"CONFLICT"}')
    service.graph = type("Graph", (), {"create_triplet": AsyncMock()})()
    try:
        await service._resolve_one_fact(
            {
                "subject": "Ari",
                "relation": "drinks",
                "object": "coffee",
                "confidence": 0.95,
                "category": "social",
                "reason": "Ari drinks coffee daily",
            }
        )
        await service._resolve_one_fact(
            {
                "subject": "Ari",
                "relation": "drinks",
                "object": "green tea",
                "confidence": 0.95,
                "category": "social",
                "reason": "Ari actually switched drinks recently",
            }
        )

        current = await store.query_current_beliefs("Ari")
        history = await store.query_historical_beliefs("Ari")
        assert [item.object for item in current] == ["green tea"]
        assert [item.object for item in history] == ["coffee", "green tea"]
        assert history[0].status == "SUPERSEDED"
        assert service.llm.prompts == []
    finally:
        await store.close()
