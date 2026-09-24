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
    # Absolute relevance (clipped cosine; the query vector equals the fact's),
    # separate from the pool-relative ranking score.
    assert results[0]["relevance"] == pytest.approx(1.0, abs=1e-4)
    assert all(0.0 <= r["relevance"] <= 1.0 for r in results)
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
    # HNSW returns at most hnsw.ef_search rows (default 40): raised to the pool.
    settings = [c.args[0] for c in conn.execute.await_args_list]
    assert any("SET hnsw.ef_search = 60" in q for q in settings)
    # Absolute relevance for downstream thresholds, separate from the
    # pool-relative ranking score.
    assert results[0]["relevance"] == pytest.approx(0.9)


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


# --- adversarial-review findings on the vector index --------------------------


@pytest.mark.asyncio
async def test_index_detects_rowid_reuse_after_delete_and_reinsert(sqlite_conn):
    """TEXT-keyed rows reuse rowids: delete all + insert the same count leaves
    count and max(rowid) unchanged. The cached ids must not survive."""
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    for i in range(5):
        await _insert(sqlite_conn, f"old{i}", _vec(i, 8))
    await index.top_k(sqlite_conn, "personal", _vec(0, 8), 5)
    await sqlite_conn.execute("DELETE FROM memories")
    for i in range(5):
        await _insert(sqlite_conn, f"new{i}", _vec(i, 8))
    ids = [m for m, _ in await index.top_k(sqlite_conn, "personal", _vec(0, 8), 5)]
    assert ids and all(m.startswith("new") for m in ids)


@pytest.mark.asyncio
async def test_index_does_not_mistake_reuse_plus_growth_for_an_append(sqlite_conn):
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    for i in range(4):
        await _insert(sqlite_conn, f"m{i}", _vec(i, 8))
    await index.top_k(sqlite_conn, "personal", _vec(0, 8), 4)
    await sqlite_conn.execute("DELETE FROM memories WHERE id IN (?, ?)", "m2", "m3")
    for i in range(4):  # reuses rowids 3,4 then grows to 6
        await _insert(sqlite_conn, f"x{i}", _vec(i + 4, 8))
    got = {m for m, _ in await index.top_k(sqlite_conn, "personal", _vec(0, 8), 10)}
    assert got == {"m0", "m1", "x0", "x1", "x2", "x3"}


@pytest.mark.asyncio
async def test_concurrent_ensure_never_duplicates_rows(sqlite_conn):
    import asyncio

    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    for i in range(3):
        await _insert(sqlite_conn, f"r{i}", _vec(i, 8))
    await index.ensure(sqlite_conn, "personal")
    await _insert(sqlite_conn, "late", _vec(5, 8))
    await asyncio.gather(*(index.ensure(sqlite_conn, "personal") for _ in range(4)))
    await _insert(sqlite_conn, "later", _vec(6, 8))
    built = await index.ensure(sqlite_conn, "personal")
    assert sorted(built.ids) == ["late", "later", "r0", "r1", "r2"]


@pytest.mark.asyncio
async def test_index_cap_evicts_least_recently_touched_first(sqlite_conn):
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=3)
    for name, when, v in (
        ("old", "2026-01-01 00:00:00", 0),
        ("mid", "2026-01-02 00:00:00", 1),
        ("recent", "2026-01-03 00:00:00", 2),
        ("oldest", "2025-12-01 00:00:00", 3),
    ):
        await _insert(sqlite_conn, name, _vec(v, 8), when=when)
    built = await index.ensure(sqlite_conn, "personal")
    assert built.ids == ["old", "mid", "recent"]  # 3 most recent, oldest-first
    await _insert(sqlite_conn, "new", _vec(4, 8), when="2026-01-04 00:00:00")
    built = await index.ensure(sqlite_conn, "personal")
    assert built.ids == ["mid", "recent", "new"]  # "old" evicted, not "recent"


@pytest.mark.asyncio
async def test_index_follows_an_embedding_model_change(sqlite_conn):
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    for i in range(3):
        await _insert(sqlite_conn, f"d4_{i}", _vec(i, 4))
    await index.top_k(sqlite_conn, "personal", _vec(0, 4), 3)
    for i in range(5):
        await _insert(sqlite_conn, f"d6_{i}", _vec(i, 6))
    ids = [m for m, _ in await index.top_k(sqlite_conn, "personal", _vec(2, 6), 1)]
    assert ids == ["d6_2"]


@pytest.mark.asyncio
async def test_index_filters_room_before_ranking(sqlite_conn):
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    for i in range(5):
        await sqlite_conn.execute(
            "INSERT INTO memories (id, content, wing, room, embedding, last_recalled_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            f"m{i}",
            "c",
            "personal",
            "a" if i < 4 else "b",
            str(_vec(i, 8)),
            "2026-01-01 00:00:00",
        )
    hits = await index.top_k(sqlite_conn, "personal", _vec(0, 8), 3, room="b")
    assert [m for m, _ in hits] == ["m4"]


@pytest.mark.asyncio
async def test_dimension_mismatch_is_reported_not_silent(sqlite_store, monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    await sqlite_store.add_memory("stored with an old model", embedding=_vec(0, 16))
    sqlite_store.get_embedding = AsyncMock(return_value=_vec(0, 768))
    assert await sqlite_store.search_memories("anything", refresh_on_recall=False) == []
    assert "dimension" in (sqlite_store.last_search_error or "")


# ---------------------------------------------------------------------------
# Second adversarial review (docs/brain-research/01-problems.md, R2-*).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_warmed_on_an_empty_wing_learns_its_dimension_from_appends(
    sqlite_conn,
):
    """R2-1 (HIGH): the startup warm-up on a fresh store built the index with
    dim=None; every later write took the append path, which never set it, so
    `top_k` answered [] forever with no error."""
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    index = SQLiteVectorIndex(scan_limit=100)
    await index.ensure(sqlite_conn, "personal")  # warm-up, no rows yet
    for i in range(3):
        await _insert(sqlite_conn, f"m{i}", _vec(i, 8))
    hits = await index.top_k(sqlite_conn, "personal", _vec(1, 8), 2)
    assert hits[0] == ("m1", pytest.approx(1.0))
    assert (index.rebuilds, index.appends) == (1, 1)


@pytest.mark.asyncio
async def test_first_conversation_after_a_fresh_start_is_recallable(
    sqlite_store, monkeypatch
):
    """R2-1 end to end: agent start (warm-up on an empty store), first
    memory written, first question about it."""
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    await sqlite_store.warm_retrieval_index("personal")
    await sqlite_store.add_memory(
        "my sister Priya was born on March 14", embedding=_vec(3)
    )
    sqlite_store.get_embedding = AsyncMock(return_value=_vec(3))
    results = await sqlite_store.search_memories(
        "when was Priya born", refresh_on_recall=False
    )
    assert [r["content"] for r in results] == ["my sister Priya was born on March 14"]
    assert sqlite_store.last_search_error is None


class _WriteDuringRebuild:
    """A concurrent writer lands between the index reading its staleness key
    and fetching rows (another coroutine or process)."""

    def __init__(self, inner):
        self.inner = inner
        self.fired = False

    async def fetch(self, query, *args):
        if "ORDER BY last_recalled_at DESC" in query and not self.fired:
            self.fired = True
            await _insert(self.inner, "raced", _vec(5, 8))
        return await self.inner.fetch(query, *args)

    async def execute(self, query, *args):
        return await self.inner.execute(query, *args)


@pytest.mark.asyncio
async def test_write_during_rebuild_is_not_indexed_twice(sqlite_conn):
    """R2-3: the raced row is loaded by the rebuild but newer than its key,
    so the next append re-fetched it and the id sat in the index twice."""
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    for i in range(3):
        await _insert(sqlite_conn, f"r{i}", _vec(i, 8))
    index = SQLiteVectorIndex(scan_limit=100)
    await index.ensure(_WriteDuringRebuild(sqlite_conn), "personal")
    await _insert(sqlite_conn, "next", _vec(6, 8))
    built = await index.ensure(sqlite_conn, "personal")
    assert sorted(built.ids) == ["next", "r0", "r1", "r2", "raced"]
    assert len(built.ids) == built.matrix.shape[0]
    hits = await index.top_k(sqlite_conn, "personal", _vec(5, 8), 3)
    assert [m for m, _ in hits].count("raced") == 1


@pytest.mark.asyncio
async def test_invalidate_one_wing_forces_only_that_rebuild(sqlite_conn):
    """R2-6: a row reinserted under the same id and rowid with a new
    embedding (archive promotion that re-embeds) is invisible to the key;
    promotion invalidates the wing."""
    from app.state.sqlite_vector_index import SQLiteVectorIndex

    await _insert(sqlite_conn, "a", _vec(0, 8))
    await _insert(sqlite_conn, "w", _vec(0, 8), wing="work")
    await _insert(sqlite_conn, "top", None)  # no stored embedding: not indexed
    index = SQLiteVectorIndex(scan_limit=100)
    await index.ensure(sqlite_conn, "personal")
    await index.ensure(sqlite_conn, "work")
    await sqlite_conn.execute("DELETE FROM memories WHERE id = ?", "top")
    await _insert(sqlite_conn, "top", _vec(4, 8))  # same id, same rowid
    assert "top" not in [
        m for m, _ in await index.top_k(sqlite_conn, "personal", _vec(4, 8), 1)
    ]
    index.invalidate("personal")
    assert (await index.top_k(sqlite_conn, "personal", _vec(4, 8), 1))[0][0] == "top"
    assert index.rebuilds == 3  # personal twice, work once
    await index.top_k(sqlite_conn, "work", _vec(0, 8), 1)
    assert index.rebuilds == 3


@pytest.mark.asyncio
async def test_promotion_that_re_embeds_invalidates_the_index(sqlite_store):
    sqlite_store._sqlite_vector_index.invalidate = MagicMock()
    sqlite_store.get_embeddings = AsyncMock(return_value=[_vec(2)])
    sqlite_store._promote_one_archived_row = AsyncMock(return_value={"content": "x"})
    row = {"id": "a1", "content": "x", "wing": "personal", "embedding": None}
    await sqlite_store._promote_archived_rows(
        [(1.0, 1.0, 0.9, row)],
        [],
        threshold=0.0,
        current_valence=0.0,
        current_arousal=0.5,
        current_cortisol=0.0,
        current_time=None,
    )
    sqlite_store._sqlite_vector_index.invalidate.assert_called_once_with("personal")


@pytest.mark.asyncio
async def test_dimension_outage_is_not_served_from_cache_as_nothing_found(
    sqlite_store, monkeypatch
):
    """R2-4: the empty result was cached for 15 s and a cache hit clears
    `last_search_error`, so a repeat query reported the outage as silence."""
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    await sqlite_store.add_memory("stored with an old model", embedding=_vec(0, 16))
    sqlite_store.get_embedding = AsyncMock(return_value=_vec(0, 768))
    for _ in range(2):
        assert await sqlite_store.search_memories("same", refresh_on_recall=False) == []
        assert "dimension" in (sqlite_store.last_search_error or "")


@pytest.mark.asyncio
async def test_partial_dimension_loss_is_traced_and_logged(
    sqlite_store, monkeypatch, caplog
):
    """R2-4: when most rows are on an old model the query still returns the
    few new ones, so no error fires; the loss must still be visible."""
    import logging

    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "hybrid")
    for i in range(3):
        await sqlite_store.add_memory(f"old model {i}", embedding=_vec(i, 16))
    await sqlite_store.add_memory("new model", embedding=_vec(1))
    sqlite_store.get_embedding = AsyncMock(return_value=_vec(1))
    with caplog.at_level(logging.WARNING, logger="app.state.sqlite_vector_index"):
        results = await sqlite_store.search_memories("q", refresh_on_recall=False)
    assert [r["content"] for r in results] == ["new model"]
    assert sqlite_store.last_search_trace["skipped_dimension"] == 3
    assert any("3 of 4 stored embeddings skipped" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_v1_results_also_carry_absolute_relevance(sqlite_store, monkeypatch):
    """R2-5: surfacing publishes `relevance`; under `actr_v1` it fell back to
    the unbounded ACT-R score (0.575 for a query orthogonal to every memory),
    so a rollback would reintroduce R-7."""
    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "actr_v1")
    await sqlite_store.add_memory("an unrelated memory", embedding=_vec(0))
    sqlite_store.get_embedding = AsyncMock(return_value=_vec(1))  # orthogonal
    results = await sqlite_store.search_memories(
        "unrelated query", threshold=-99, refresh_on_recall=False
    )
    assert results and all(r["relevance"] == 0.0 for r in results)
