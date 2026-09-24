"""Brain V2 hybrid memory retrieval (ADR-001): ranking contract and wiring.

The contract, in order of importance:
1. Relevance decides. A clearly more relevant memory wins regardless of age
   or how often a less relevant one was repeated.
2. History breaks ties. Among equally relevant memories, recent and often
   reinforced ones rank first (ACT-R base level).
3. Lexical evidence is whole-word and bounded; it cannot outvote meaning by a
   substring accident.
4. Rankings do not depend on the embedding model's cosine baseline.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Config
from app.state.memory_ranking import bm25_scores, hybrid_rank, query_terms, zscores

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _cand(content, sim, hours_ago=1.0, recall_count=1, importance=0.6):
    return {
        "content": content,
        "similarity": sim,
        "recall_count": recall_count,
        "last_recalled_at": NOW - timedelta(hours=hours_ago),
        "importance_score": importance,
    }


def _filler(n, sim=0.40):
    return [
        _cand(f"small talk number {i}", sim + 0.001 * i, hours_ago=2) for i in range(n)
    ]


def _rank(cands, query="unrelated words here", limit=3):
    return hybrid_rank(cands, query, set(), NOW, limit=limit)


def test_relevant_old_memory_beats_recent_irrelevant_ones():
    old = _cand("my sister was born in march", 0.82, hours_ago=24 * 120)
    out = _rank(_filler(30) + [old])
    assert out[0]["content"] == old["content"]


def test_relevant_rare_memory_beats_frequently_repeated_irrelevant_one():
    rare = _cand("allergic to peanuts", 0.80, recall_count=1)
    repeated = _cand("had pasta again", 0.45, recall_count=40)
    out = _rank(_filler(20) + [repeated, rare])
    assert out[0]["content"] == rare["content"]


def test_recency_breaks_a_relevance_tie():
    old = _cand("I drink coffee every morning", 0.80, hours_ago=24 * 40)
    new = _cand("I switched to green tea", 0.80, hours_ago=24 * 2)
    out = _rank(_filler(20) + [old, new], limit=2)
    assert [c["content"] for c in out] == [new["content"], old["content"]]


def test_frequency_breaks_a_relevance_tie():
    once = _cand("the trek is in december", 0.75, recall_count=1)
    often = _cand("the trek is in the mountains", 0.75, recall_count=6)
    out = _rank(_filler(20) + [once, often], limit=2)
    assert out[0]["content"] == often["content"]


def test_lexical_match_is_whole_word_not_substring():
    # V1's substring cue matched "art" inside "party" for +5.0.
    assert query_terms("any art plans?", set()) == ["any", "art", "plans"]
    scores = bm25_scores(["we went to a party", "an art exhibition"], ["art"])
    assert scores[0] == 0.0 and scores[1] > 0.0


def test_lexical_term_is_bounded_and_cannot_outvote_a_large_relevance_gap():
    keyword_only = _cand("saw a meme about coffee", 0.40)
    meaningful = _cand("quit caffeine and drink tea now", 0.85)
    out = _rank(
        _filler(20) + [keyword_only, meaningful], query="how much coffee", limit=1
    )
    assert out[0]["content"] == meaningful["content"]
    assert (
        0.0
        <= max(
            c["score_terms"]["lexical"]
            for c in _rank(_filler(5) + [keyword_only], "coffee", None)
        )
        <= 1.5
    )


def test_ranking_is_invariant_to_the_embedding_models_cosine_baseline():
    base = _filler(15) + [_cand("target fact", 0.70), _cand("near miss", 0.62)]
    shifted = [
        {**c, "similarity": c["similarity"] + 0.35} for c in base
    ]  # anisotropic model
    assert [c["content"] for c in _rank(base)] == [c["content"] for c in _rank(shifted)]


def test_every_result_explains_itself_and_limit_is_respected():
    out = _rank(_filler(10), limit=4)
    assert len(out) == 4
    for c in out:
        assert set(c["score_terms"]) == {"relevance", "lexical", "activation"}
    assert hybrid_rank([], "q", set(), NOW, limit=3) == []


def test_zscores_handle_constant_input():
    assert zscores([0.5, 0.5, 0.5]) == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# MemoryStore wiring (real SQLite + Rust kernel)
# ---------------------------------------------------------------------------


def _vec(i: int, dim: int = 768) -> list[float]:
    v = [0.0] * dim
    v[i % dim] = 1.0
    return v


@pytest.fixture
async def sqlite_store():
    from app.state.conversation_store import ConversationHistoryStore
    from app.state.memory_store import MemoryStore

    conversation = ConversationHistoryStore()
    conversation.dsn = "sqlite:///:memory:"
    await conversation.initialize()
    graph = MagicMock()
    graph.execute_query = AsyncMock(return_value=[])
    store = MemoryStore(pool=conversation.pool, graph_db=graph)
    store.qdrant_store.client = None
    yield store
    await store.close()
    await conversation.close()


async def _seed_old_fact_then_newer_filler(store, n_filler=40):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    target = [0.0] * 768
    target[0] = 1.0
    await store.add_memory(
        "my sister Priya was born on March 14",
        importance=0.6,
        current_time=t0,
        embedding=target,
    )
    for i in range(n_filler):
        await store.add_memory(
            f"filler line {i} about nothing",
            importance=0.3,
            current_time=t0 + timedelta(days=1, hours=i),
            embedding=_vec(i + 5),
        )
    store.get_embedding = AsyncMock(return_value=target)
    return t0 + timedelta(days=30)


@pytest.mark.asyncio
async def test_hybrid_recalls_a_memory_older_than_the_twenty_newest(
    sqlite_store, monkeypatch
):
    """V1 on SQLite scored only `ORDER BY last_recalled_at DESC LIMIT 20`, so
    this fact was invisible once 20 newer memories existed."""
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    now = await _seed_old_fact_then_newer_filler(sqlite_store)
    results = await sqlite_store.search_memories(
        "when is my sibling's birthday",
        limit=3,
        refresh_on_recall=False,
        current_time=now,
    )
    assert results and results[0]["content"] == "my sister Priya was born on March 14"
    trace = sqlite_store.last_search_trace
    assert trace["policy"] == "hybrid" and trace["source"] == "sqlite"
    assert (
        trace["pool"] == 41
    )  # the whole (small) store: pool = min(store, MEMORY_CANDIDATE_POOL)
    assert "content" not in str(trace["results"])  # ids and terms only, never text


@pytest.mark.asyncio
async def test_v1_policy_still_available_and_still_has_the_recency_cap(
    sqlite_store, monkeypatch
):
    """Documents the defect the hybrid fixes, and that rollback works."""
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "actr_v1")
    now = await _seed_old_fact_then_newer_filler(sqlite_store)
    results = await sqlite_store.search_memories(
        "when is my sibling's birthday",
        limit=3,
        refresh_on_recall=False,
        current_time=now,
    )
    assert all(r["content"] != "my sister Priya was born on March 14" for r in results)


@pytest.mark.asyncio
async def test_hybrid_respects_exclude_contents(sqlite_store, monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    now = await _seed_old_fact_then_newer_filler(sqlite_store, n_filler=5)
    results = await sqlite_store.search_memories(
        "birthday",
        limit=3,
        refresh_on_recall=False,
        current_time=now,
        exclude_contents=["my sister Priya was born on March 14"],
    )
    assert all(r["content"] != "my sister Priya was born on March 14" for r in results)


@pytest.mark.asyncio
async def test_hybrid_embedding_outage_is_recorded_not_silent(
    sqlite_store, monkeypatch
):
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    sqlite_store.get_embedding = AsyncMock(return_value=None)
    assert await sqlite_store.search_memories("anything") == []
    assert sqlite_store.last_search_error == "embedding service returned no vector"


@pytest.mark.asyncio
async def test_hybrid_postgres_path_selects_candidates_by_vector_distance(monkeypatch):
    """Postgres V1 selected its pool with the full ACT-R score, where
    similarity is a minor term; the hybrid path orders by `<=>` distance
    (which is also what lets pgvector use the HNSW index)."""
    from app.state.memory_store import MemoryStore

    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    conn = AsyncMock()
    rows = [
        {
            "id": "a",
            "content": "relevant",
            "raw_content": "relevant",
            "wing": "personal",
            "room": None,
            "importance_score": 0.6,
            "emotional_weight": 0.0,
            "valence": 0.0,
            "recall_count": 1,
            "last_recalled_at": NOW - timedelta(days=90),
            "created_at": NOW - timedelta(days=90),
            "metadata": "{}",
            "similarity": 0.9,
        },
        {
            "id": "b",
            "content": "recent noise",
            "raw_content": "recent noise",
            "wing": "personal",
            "room": None,
            "importance_score": 0.6,
            "emotional_weight": 0.0,
            "valence": 0.0,
            "recall_count": 3,
            "last_recalled_at": NOW,
            "created_at": NOW,
            "metadata": "{}",
            "similarity": 0.4,
        },
    ]
    conn.fetch = AsyncMock(return_value=rows)
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    graph = MagicMock()
    graph.execute_query = AsyncMock(return_value=[])
    store = MemoryStore(pool, graph)
    store.qdrant_store.client = None
    store.get_embedding = AsyncMock(return_value=[0.1] * 768)
    store._fetch_archive_rows = AsyncMock(return_value=[])
    try:
        results = await store.search_memories(
            "q", limit=2, refresh_on_recall=False, current_time=NOW
        )
    finally:
        await store.close()
    calls = [c for c in conn.fetch.await_args_list if "FROM memories" in c.args[0]]
    assert len(calls) == 1
    sql = calls[0].args[0]
    assert "ORDER BY embedding <=> $1::vector" in sql and "LIMIT $4" in sql
    assert calls[0].args[4] == Config.MEMORY_CANDIDATE_POOL
    assert [r["content"] for r in results] == ["relevant", "recent noise"]
    assert store.last_search_trace["source"] == "postgres"


@pytest.mark.asyncio
async def test_archive_rows_are_promoted_only_when_they_win_a_slot(
    sqlite_store, monkeypatch
):
    """V1 wrote up to five archived rows back to the active tier even when the
    result limit then discarded them. Only winners are promoted now."""
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    now = await _seed_old_fact_then_newer_filler(sqlite_store, n_filler=5)
    winner = [0.0] * 768
    winner[0] = 0.99
    winner[1] = 0.14
    loser = _vec(700)
    archived = [
        {
            "id": "arch-win",
            "content": "birthday party for Priya at the lake",
            "embedding": str(winner),
            "similarity_arch": None,
            "importance_score": 0.6,
            "recall_count": 1,
            "last_recalled_at": now - timedelta(days=200),
            "created_at": now - timedelta(days=200),
            "wing": "personal",
            "metadata": "{}",
        },
        {
            "id": "arch-lose",
            "content": "birthday card shop was closed",
            "embedding": str(loser),
            "similarity_arch": None,
            "importance_score": 0.3,
            "recall_count": 1,
            "last_recalled_at": now - timedelta(days=200),
            "created_at": now - timedelta(days=200),
            "wing": "personal",
            "metadata": "{}",
        },
    ]
    sqlite_store._fetch_archive_rows = AsyncMock(return_value=archived)
    promoted_ids = []

    async def _fake_promote(scored_rows, *_a, **_k):
        out = []
        for _rs, _s, _sim, row in scored_rows:
            promoted_ids.append(row["id"])
            out.append({"content": row["content"], "score": 0.0})
        return out

    sqlite_store._promote_archived_rows = _fake_promote
    results = await sqlite_store.search_memories(
        "Priya birthday", limit=2, refresh_on_recall=False, current_time=now
    )
    contents = [r["content"] for r in results]
    assert "birthday party for Priya at the lake" in contents
    assert promoted_ids == ["arch-win"]


# ---------------------------------------------------------------------------
# SQLite vector index (latency fix: p50 580 ms -> 4 ms at 5,000 memories)
# ---------------------------------------------------------------------------


async def _insert(conn, mem_id, vec, wing="personal", when="2026-01-01 00:00:00"):
    await conn.execute(
        "INSERT INTO memories (id, content, wing, embedding, last_recalled_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        mem_id,
        f"content {mem_id}",
        wing,
        str(vec) if vec is not None else None,
        when,
        when,
    )


@pytest.fixture
def sqlite_conn():
    from app.state.sqlite_fallback import SQLiteConnection

    return SQLiteConnection(":memory:")


@pytest.mark.asyncio
async def test_vector_index_appends_incrementally_and_rebuilds_on_delete(sqlite_conn):
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    for i in range(5):
        await _insert(sqlite_conn, f"m{i}", _vec(i, 8))
    assert [
        m for m, _ in await index.top_k(sqlite_conn, "personal", _vec(3, 8), 1)
    ] == ["m3"]
    assert (index.rebuilds, index.appends) == (1, 0)

    await _insert(sqlite_conn, "new", _vec(6, 8))  # e.g. another process wrote
    assert (await index.top_k(sqlite_conn, "personal", _vec(6, 8), 1))[0][0] == "new"
    assert (index.rebuilds, index.appends) == (1, 1)

    await sqlite_conn.execute("DELETE FROM memories WHERE id = ?", "m3")
    top = await index.top_k(sqlite_conn, "personal", _vec(3, 8), 5)
    assert "m3" not in [m for m, _ in top]
    assert index.rebuilds == 2

    await index.top_k(sqlite_conn, "personal", _vec(1, 8), 1)  # unchanged store
    assert (index.rebuilds, index.appends) == (2, 1)


@pytest.mark.asyncio
async def test_vector_index_skips_corrupt_rows_without_rebuilding_every_query(
    sqlite_conn,
):
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    await _insert(sqlite_conn, "ok", _vec(0, 8))
    await _insert(sqlite_conn, "bad", "not a vector")
    await _insert(sqlite_conn, "none", None)
    for _ in range(3):
        hits = await index.top_k(sqlite_conn, "personal", _vec(0, 8), 5)
    assert [m for m, _ in hits] == ["ok"]
    assert index.rebuilds == 1


@pytest.mark.asyncio
async def test_vector_index_is_per_wing_and_rejects_wrong_dimension(sqlite_conn):
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    await _insert(sqlite_conn, "p", _vec(0, 8), wing="personal")
    await _insert(sqlite_conn, "w", _vec(0, 8), wing="work")
    assert [m for m, _ in await index.top_k(sqlite_conn, "work", _vec(0, 8), 5)] == [
        "w"
    ]
    assert await index.top_k(sqlite_conn, "personal", _vec(0, 16), 5) == []
    assert await index.top_k(sqlite_conn, "empty", _vec(0, 8), 5) == []


@pytest.mark.asyncio
async def test_warm_retrieval_index_builds_once_and_never_raises(
    sqlite_store, monkeypatch
):
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    await _seed_old_fact_then_newer_filler(sqlite_store, n_filler=3)
    await sqlite_store.warm_retrieval_index()
    assert sqlite_store._sqlite_vector_index.rebuilds == 1
    sqlite_store.pool = None  # broken pool: warm-up must log, not raise
    await sqlite_store.warm_retrieval_index()


def test_zero_importance_is_not_promoted_to_mid_importance():
    low = _cand("decayed memory", 0.60, importance=0.0)
    mid = _cand("ordinary memory", 0.60, importance=0.5)
    out = _rank(_filler(10) + [low, mid], limit=None)
    order = [c["content"] for c in out]
    assert order.index("ordinary memory") < order.index("decayed memory")


def test_candidate_builder_tolerates_text_timestamps():
    """The SQLite converter hands back text for an unparseable stored value;
    candidate building must degrade that field, not fail the search."""
    from app.state.memory_store import MemoryStore

    store = object.__new__(MemoryStore)
    store.decay_rate = 0.5
    store.spread_weight = 1.0
    row = {
        "content": "x",
        "similarity": 0.5,
        "recall_count": 1,
        "importance_score": 0.6,
        "created_at": "garbled",
        "last_recalled_at": "also garbled",
        "metadata": "{}",
    }
    cand = store._build_candidate_from_row(row, NOW, 0.0, 0.5, 0.0, float("-inf"))
    assert cand is not None and cand["created_at"] is None
