import asyncio
import json
import math
import sys
import types
from datetime import UTC, datetime

import pytest

from app import clock
from app.agents.subconscious_agent import SubconsciousAgent
from app.cognitive import trace
from app.cognitive.action_candidate import ActionCandidate, CandidateSelector
from app.cognitive.behavior_contracts import BehaviorDecision, CommunicativeIntent
from app.cognitive.decision import DecisionService
from app.cognitive.perception import CognitiveEvent
from app.config import Config
from app.state import memory_store as memory_store_module
from app.state.agent_state import StateService
from app.state.semantic_recall_store import SemanticRecallStore
from evals.brainbench.adapters import build_cognitive_service, build_memory_store
from evals.brainbench.traces import write_jsonl


def test_no_sink_skips_validation(monkeypatch):
    def fail_validation(*_args, **_kwargs):
        raise AssertionError("validation ran without a sink")

    monkeypatch.setattr(trace, "_validated", fail_validation)
    trace.emit("unknown.kind", unsafe="a whole sentence")


def test_enabled_and_optional_schema_fields():
    assert trace.enabled() is False
    with trace.collect() as events:
        assert trace.enabled() is True
        trace.emit("proactive.decision", fired=False, reason="disabled")
    assert events[0]["reason"] == "disabled"
    assert "idle_s" not in events[0]


def test_strict_rejects_unknown_schema_and_unsafe_values():
    invalid_values = (
        "this sentence has whitespace",
        "x" * 81,
        math.nan,
        (1, 2),
        {"a": {"b": {"c": {"d": 1}}}},
        list(range(201)),
    )
    for value in invalid_values:
        with trace.collect() as events, pytest.raises(trace.TraceSchemaError):
            trace.emit("affect.update", cause="test", inputs={"value": value})
        assert events == []
    with trace.collect(), pytest.raises(trace.TraceSchemaError):
        trace.emit("unknown.kind")
    with trace.collect(), pytest.raises(trace.TraceSchemaError):
        trace.emit([], cause="test")
    with trace.collect(), pytest.raises(trace.TraceSchemaError):
        trace.emit("affect.update", cause="test", inputs={}, surprise=1)


def test_non_strict_drops_counts_and_warns_once_per_kind(caplog):
    dropped_before = trace.dropped_count()
    with trace.collect(strict=False) as events:
        trace.emit("affect.update", cause="unsafe", inputs={"text": "not safe"})
        trace.emit("affect.update", cause="unsafe", inputs={"text": "still unsafe"})
    assert events == []
    assert trace.dropped_count() == dropped_before + 2
    assert (
        sum(
            "Dropped invalid cognitive trace kind=affect.update" in r.message
            for r in caplog.records
        )
        <= 1
    )


@pytest.mark.asyncio
async def test_nested_scopes_and_async_task_inheritance():
    with trace.collect() as outer:
        with trace.collect() as inner:
            trace.emit("affect.update", cause="nested", inputs={})

            async def child():
                trace.emit("affect.update", cause="child", inputs={})

            await asyncio.create_task(child())
    assert [event["cause"] for event in outer] == ["nested", "child"]
    assert [event["cause"] for event in inner] == ["nested", "child"]


def test_timestamp_uses_manual_clock():
    manual = clock.ManualClock(datetime(2030, 1, 2, tzinfo=UTC))
    with clock.use_clock(manual), trace.collect() as events:
        trace.emit("affect.update", cause="clock", inputs={})
        manual.advance(5)
        trace.emit("affect.update", cause="clock", inputs={})
    assert events[0]["t"] == manual.time() - 5
    assert events[1]["t"] == manual.time()


def test_jsonl_writer_revalidates_events(tmp_path):
    path = tmp_path / "events.jsonl"
    with pytest.raises(trace.TraceSchemaError):
        write_jsonl(
            [{"kind": "affect.update", "t": 1.0, "cause": "unsafe sentence"}],
            path,
        )
    with trace.collect() as events:
        trace.emit("affect.update", cause="safe", inputs={})
    write_jsonl(events, path)
    assert json.loads(path.read_text()) == events[0]


@pytest.mark.asyncio
async def test_real_memory_search_traces_hybrid_actr_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        SemanticRecallStore, "_ensure_collection_exists", lambda _self: None
    )
    store = build_memory_store(tmp_path)

    async def local_embedding(_text):
        return [1.0, 0.0]

    monkeypatch.setattr(store, "get_embedding", local_embedding)
    await store.add_memory("cobalt-marker-memory", embedding=[1.0, 0.0])
    with trace.collect() as events:
        results = await store.search_memories(
            "cobalt-marker-query", refresh_on_recall=False
        )
    assert results
    hybrid = next(event for event in events if event["kind"] == "memory.search")
    assert hybrid["policy"] == "hybrid"
    assert hybrid["results"][0]["id"]
    assert "terms" in hybrid["results"][0]

    materialize = store._materialize_hybrid_results

    async def invalid_terms(*args, **kwargs):
        rows = await materialize(*args, **kwargs)
        rows[0]["score_terms"] = {"unsafe term": 1.0}
        return rows

    monkeypatch.setattr(store, "_materialize_hybrid_results", invalid_terms)
    with trace.collect(), pytest.raises(trace.TraceSchemaError):
        await store.search_memories("strict-marker-query", refresh_on_recall=False)
    monkeypatch.setattr(store, "_materialize_hybrid_results", materialize)

    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "actr_v1")
    rust_stub = types.ModuleType("cognitive_rust")
    rust_stub.score_memories_actr_sqlite = lambda *_args: [(0, 1.0, 0.8)]
    monkeypatch.setitem(sys.modules, "cognitive_rust", rust_stub)

    async def empty_graph_query(*_args, **_kwargs):
        return []

    monkeypatch.setattr(store.graph_db, "execute_query", empty_graph_query)
    with trace.collect() as events:
        await store.search_memories("actr-marker-query", refresh_on_recall=False)
    actr_event = next(event for event in events if event["kind"] == "memory.search")
    assert actr_event["policy"] == "actr_v1"
    assert actr_event["cache_hit"] is False
    assert actr_event["error"] is False
    assert actr_event["results"][0]["id"]

    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")

    async def failed_embedding(_text):
        raise RuntimeError("private marker exception")

    monkeypatch.setattr(store, "get_embedding", failed_embedding)
    with trace.collect() as events:
        assert (
            await store.search_memories("failure-marker-query", refresh_on_recall=False)
            == []
        )
    failure = next(event for event in events if event["kind"] == "memory.search")
    assert failure["error"] is True
    assert failure["error_code"] == "RuntimeError"
    assert "private marker" not in json.dumps(events)
    await store.close()


@pytest.mark.asyncio
async def test_no_sink_skips_state_snapshots_and_search_trace_arguments(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        SemanticRecallStore, "_ensure_collection_exists", lambda _self: None
    )
    state = StateService(db_path=str(tmp_path / "state.db"), redis_host="127.0.0.1")

    async def no_persist():
        return None

    monkeypatch.setattr(state, "persist_state", no_persist)

    def fail_snapshot(*_args, **_kwargs):
        raise AssertionError("state snapshot constructed with no trace sink")

    monkeypatch.setattr(state, "_affect_snapshot", fail_snapshot)
    monkeypatch.setattr(state, "_trust_snapshot", fail_snapshot)
    appraisal = type(
        "Appraisal",
        (),
        {
            "goal_congruence": 0.4,
            "relationship_impact": 0.2,
            "novelty": 0.3,
            "relevance": 0.5,
            "agency": 0.4,
            "norm_alignment": 0.6,
        },
    )()
    await state.update_from_appraisal(appraisal)

    store = build_memory_store(tmp_path / "memory")

    async def local_embedding(_text):
        return [1.0, 0.0]

    monkeypatch.setattr(store, "get_embedding", local_embedding)
    await store.add_memory("no-sink-search-marker", embedding=[1.0, 0.0])

    def fail_emit(*_args, **_kwargs):
        raise AssertionError("trace arguments constructed with no trace sink")

    monkeypatch.setattr(memory_store_module, "_emit_trace", fail_emit)
    assert trace.enabled() is False
    assert await store.search_memories("no-sink-search-query", refresh_on_recall=False)
    await store.close()


@pytest.mark.asyncio
async def test_hybrid_trace_ids_do_not_collide_on_duplicate_content(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        SemanticRecallStore, "_ensure_collection_exists", lambda _self: None
    )
    store = build_memory_store(tmp_path)
    candidates = [
        {"id": "memory-one", "content": "same private memory", "similarity": 0.9},
        {"id": "memory-two", "content": "same private memory", "similarity": 0.8},
    ]

    async def local_embedding(_text):
        return [1.0, 0.0]

    async def fetch_similarity(*_args, **_kwargs):
        return [dict(candidate) for candidate in candidates], "sqlite"

    async def fetch_archive(*_args, **_kwargs):
        return []

    async def materialize(ranked, **_kwargs):
        return [
            {
                "id": row["id"],
                "content": row["content"],
                "score": row["score"],
                "score_terms": row["score_terms"],
            }
            for row in ranked
        ]

    monkeypatch.setattr(store, "get_embedding", local_embedding)
    monkeypatch.setattr(store, "_fetch_similarity_candidates", fetch_similarity)
    monkeypatch.setattr(store, "_fetch_archive_candidates", fetch_archive)
    monkeypatch.setattr(store, "_materialize_hybrid_results", materialize)
    monkeypatch.setattr(store, "_resolve_dynamic_stop_words", lambda _user: set())

    async def refresh_stop_words(*_args):
        return None

    monkeypatch.setattr(store, "_refresh_stop_words_if_stale", refresh_stop_words)

    with trace.collect() as events:
        results = await store.search_memories(
            "same private memory", refresh_on_recall=False
        )
    event = next(item for item in events if item["kind"] == "memory.search")
    expected_ids = [row["id"] for row in store.last_search_trace["results"]]
    assert [row["id"] for row in results] == expected_ids
    assert [row["id"] for row in event["results"]] == expected_ids
    assert expected_ids == ["memory-one", "memory-two"]
    assert event["error"] is False
    await store.close()


def test_arbitration_trace_uses_selector_scores(monkeypatch):
    service = object.__new__(DecisionService)
    service._candidate_selector = CandidateSelector()
    candidates = [
        ActionCandidate(candidate_id="speak", kind="SPEAK", source="policy", score=0.7),
        ActionCandidate(candidate_id="wait", kind="WAIT", source="reflex", score=0.2),
    ]
    monkeypatch.setattr(service, "_immutable_core", lambda: {"boundaries": []})
    monkeypatch.setattr(service, "_build_candidates", lambda *_args: candidates)
    behavior = BehaviorDecision(intent=CommunicativeIntent(act="CHAT", goal="ENGAGE"))
    with trace.collect() as baseline_events:
        service._select_action_candidate(behavior, "ENGAGE", [], "hello")
    baseline = next(
        event for event in baseline_events if event["kind"] == "arbitration.decision"
    )
    eligible = [
        candidate for candidate in baseline["candidates"] if candidate["eligible"]
    ]
    winner = next(
        candidate for candidate in eligible if candidate["code"] == baseline["chosen"]
    )
    assert winner["score"] == max(candidate["score"] for candidate in eligible)

    from app.cognitive import action_candidate

    original_modulation = action_candidate._control_modulation
    bump = 0.125
    monkeypatch.setattr(
        action_candidate,
        "_control_modulation",
        lambda candidate, controls: original_modulation(candidate, controls) + bump,
    )
    with trace.collect() as bumped_events:
        service._select_action_candidate(behavior, "ENGAGE", [], "hello")
    bumped = next(
        event for event in bumped_events if event["kind"] == "arbitration.decision"
    )
    assert [item["score"] for item in bumped["candidates"]] == pytest.approx(
        [item["score"] + bump for item in baseline["candidates"]]
    )


@pytest.mark.asyncio
async def test_real_affect_trust_proactive_and_decision_emit(tmp_path, monkeypatch):
    state = StateService(db_path=str(tmp_path / "state.db"), redis_host="127.0.0.1")

    async def no_persist():
        return None

    monkeypatch.setattr(state, "persist_state", no_persist)
    appraisal = type(
        "Appraisal",
        (),
        {
            "goal_congruence": 0.4,
            "relationship_impact": 0.2,
            "novelty": 0.3,
            "relevance": 0.5,
            "agency": 0.4,
            "norm_alignment": 0.6,
        },
    )()
    mood_before = state.current_state.mood
    trust_before = state.current_state.trust_benevolence
    with trace.collect() as events:
        await state.update_from_appraisal(appraisal)
    affect_event = next(event for event in events if event["kind"] == "affect.update")
    trust_event = next(event for event in events if event["kind"] == "trust.update")
    assert affect_event["cause"] == "appraisal"
    assert affect_event["before"]["mood"] == mood_before
    assert affect_event["after"]["mood"] == state.current_state.mood
    assert trust_event["cause"] == "appraisal"
    assert trust_event["before"]["benevolence"] == trust_before
    assert trust_event["after"]["benevolence"] == state.current_state.trust_benevolence

    with trace.collect() as events:
        await state.update_active_person_reliance(True, stake_weight=0.5)
    person_sync = next(
        event for event in reversed(events) if event["kind"] == "trust.update"
    )
    assert person_sync["cause"] == "reliance_update"
    assert person_sync["inputs"]["stake_weight"] == 0.5
    assert person_sync["after"]["benevolence"] == state.current_state.trust_benevolence

    with trace.collect() as events:
        await state.record_active_person_rupture_repair(
            "repair", 0.2, notes="private marker note"
        )
    rupture = next(
        event for event in reversed(events) if event["kind"] == "trust.update"
    )
    assert rupture["cause"] == "rupture_repair"
    assert rupture["inputs"] == {"kind": "repair", "magnitude": 0.2}
    assert "private marker note" not in json.dumps(events)

    monkeypatch.setattr(Config, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(Config, "PROACTIVE_IDLE_THRESHOLD_SECONDS", 1.0)
    monkeypatch.setattr(Config, "PROACTIVE_COOLDOWN_SECONDS", 1.0)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_ENERGY", 0.0)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.0)
    state.current_state.last_user_interaction = clock.time() - 100
    state.current_state.last_proactive_attempt = 0.0
    with trace.collect() as events:
        assert state.check_proactive_eligibility() is True
    proactive = next(event for event in events if event["kind"] == "proactive.decision")
    assert proactive["fired"] is True
    assert proactive["reason"] == "eligible"
    assert proactive["idle_s"] >= proactive["idle_threshold_s"]

    subconscious = object.__new__(SubconsciousAgent)
    subconscious._last_benchmark_time = clock.time() - 10.0
    with trace.collect() as events:
        await subconscious._on_system_tick({})
    benchmark = next(event for event in events if event["kind"] == "proactive.decision")
    assert benchmark["reason"] == "benchmark_active"
    assert benchmark["benchmark_elapsed_s"] < benchmark["benchmark_threshold_s"]
    assert all(value is not None for value in benchmark.values())

    monkeypatch.setattr(
        SemanticRecallStore, "_ensure_collection_exists", lambda _self: None
    )
    service = build_cognitive_service("architecture_only", tmp_path / "cognitive")
    try:
        monkeypatch.setattr(Config, "MEMORY_TRUTH_ENABLED", True)
        event = CognitiveEvent(
            event_id="turn-1",
            event_type="USER_MESSAGE",
            raw_content="Tell me about cobalt-marker-user-text",
            metadata={},
        )
        with trace.collect() as events:
            async for _ in service.cognitive.process_event(
                {"id": "turn-1", "type": "USER_MESSAGE", "content": event.raw_content}
            ):
                pass
        arbitration = next(
            event for event in events if event["kind"] == "arbitration.decision"
        )
        assert arbitration["chosen"]
        assert arbitration["candidates"]
        chosen = next(
            candidate
            for candidate in arbitration["candidates"]
            if candidate["code"] == arbitration["chosen"]
        )
        assert chosen["eligible"] is True
        assert chosen["reason"] == "chosen"
    finally:
        service.cognitive.close()
        await service.memory_store.close()


@pytest.mark.asyncio
async def test_privacy_end_to_end_for_memory_and_real_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(
        SemanticRecallStore, "_ensure_collection_exists", lambda _self: None
    )
    service = build_cognitive_service("architecture_only", tmp_path)

    async def local_embedding(_text):
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(service.memory_store, "get_embedding", local_embedding)
    await service.memory_store.add_memory(
        "violet-marker-private-memory", embedding=[1.0] + [0.0] * 767
    )
    markers = {"violet-marker", "amber-marker"}
    try:
        with trace.collect() as events:
            await service.memory_store.search_memories("violet-marker query")
            async for _ in service.cognitive.process_event(
                {
                    "id": "privacy-turn-1",
                    "type": "USER_MESSAGE",
                    "content": "Please remember amber-marker in this private sentence.",
                }
            ):
                pass
        serialized = json.dumps(events)
        assert not any(marker in serialized for marker in markers)
        assert all(event["kind"] for event in events)
    finally:
        service.cognitive.close()
        await service.memory_store.close()
