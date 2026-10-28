"""Embedding provenance per memory row (V4 slice 14, D-035, R-12).

Every vector row records the embedder's model id and dimension. A scope that
holds vectors from a different embedder is refused on read, so a model change
cannot silently reinterpret old vectors. Rows from before provenance existed
(NULL) count as the default embedder and are never rewritten by a read.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.state.memory_store import EmbeddingMismatchError, MemoryStore
from app.state.sqlite_fallback import SQLitePool
from scripts import export_friend, import_friend


def _store():
    pool = SQLitePool(":memory:")
    store = MemoryStore(pool, MagicMock())
    store.qdrant_store.client = None
    store.get_embedding = AsyncMock(return_value=[0.1] * 768)
    return pool, store


async def _insert(conn, ident, *, model=None, dim=None, vector=None, wing="personal"):
    await conn.execute(
        "INSERT INTO memories (id, content, wing, embedding, embedding_model, embedding_dim) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ident,
        f"memory {ident}",
        wing,
        json.dumps(vector if vector is not None else [0.0] * 768),
        model,
        dim,
    )


def test_write_records_embedding_model_and_dimension():
    pool, store = _store()

    async def run():
        assert await store.add_memory("I live in Pune", embedding=[0.1] * 768) is True
        async with pool.acquire() as conn:
            return dict(await conn.fetchrow("SELECT * FROM memories"))

    row = asyncio.run(run())
    assert row["embedding_model"] == store.embedding_model == "nomic-embed-text"
    assert row["embedding_dim"] == store.embedding_dimension == 768


def test_a_wrong_dimension_write_is_refused_without_raising_into_the_turn():
    pool, store = _store()

    async def run():
        ok = await store.add_memory("short vector", embedding=[0.1] * 384)
        async with pool.acquire() as conn:
            n = (await conn.fetchrow("SELECT count(*) AS n FROM memories"))["n"]
        return ok, n

    ok, n = asyncio.run(run())
    assert ok is False and n == 0


@pytest.mark.parametrize(
    ("model", "dimension"), [("other-model", 768), ("nomic-embed-text", 384)]
)
def test_a_scope_with_another_embedders_vectors_is_refused(model, dimension):
    pool, store = _store()

    async def run():
        async with pool.acquire() as conn:
            await _insert(conn, "m1", model=model, dim=dimension)
            await store._assert_scope_embedding_provenance(conn, "memories", "personal")

    with pytest.raises(EmbeddingMismatchError, match="different embedder"):
        asyncio.run(run())


def test_legacy_null_rows_count_as_default_and_are_not_rewritten_by_a_read():
    pool, store = _store()

    async def run():
        async with pool.acquire() as conn:
            await _insert(conn, "legacy")  # NULL model, NULL dim
            await store._assert_scope_embedding_provenance(conn, "memories", "personal")
            return dict(await conn.fetchrow("SELECT * FROM memories WHERE id='legacy'"))

    row = asyncio.run(run())
    assert row["embedding_model"] is None and row["embedding_dim"] is None


def test_legacy_null_rows_are_refused_once_the_embedder_changes():
    pool, store = _store()
    store.embedding_model = "other-model"

    async def run():
        async with pool.acquire() as conn:
            await _insert(conn, "legacy")
            await store._assert_scope_embedding_provenance(conn, "memories", "personal")

    with pytest.raises(EmbeddingMismatchError):
        asyncio.run(run())


@pytest.mark.parametrize(
    ("model", "dim", "ok"),
    [
        (None, None, True),  # legacy row: counts as the default embedder
        ("nomic-embed-text", 768, True),
        ("other-model", 768, False),
        ("nomic-embed-text", 384, False),
    ],
)
def test_row_check_accepts_default_and_legacy_rows_and_rejects_others(model, dim, ok):
    _pool, store = _store()
    row = {"id": "r", "embedding_model": model, "embedding_dim": dim}
    if ok:
        store._check_row_embedding(row)
    else:
        with pytest.raises(EmbeddingMismatchError, match="different embedder"):
            store._check_row_embedding(row)


def test_row_check_reads_sqlite_rows_and_rows_without_the_columns():
    import sqlite3

    _pool, store = _store()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (id TEXT, embedding_model TEXT, embedding_dim INT)")
    conn.execute("INSERT INTO t VALUES ('a', 'other-model', 768)")
    conn.execute("INSERT INTO t VALUES ('b', NULL, NULL)")
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM t")}
    with pytest.raises(EmbeddingMismatchError):
        store._check_row_embedding(rows["a"])
    store._check_row_embedding(rows["b"])
    store._check_row_embedding({"id": "no-columns-at-all"})
    store._check_row_embedding(object())  # a row shape we cannot read counts as legacy


def test_old_callers_may_insert_without_the_provenance_arguments():
    import inspect

    sig = inspect.signature(MemoryStore._insert_memory_row)
    for name in ("embedding_model", "embedding_dim"):
        assert sig.parameters[name].default is None


def test_the_postgres_path_check_is_scoped_by_wing_and_runs_once_per_scope():
    pool, store = _store()

    async def run():
        async with pool.acquire() as conn:
            await _insert(conn, "other", model="other-model", dim=768, wing="work")
            # a clean wing is not blocked by another wing's foreign vectors
            await store._assert_scope_embedding_provenance(conn, "memories", "personal")
            assert ("memories", "personal", None) in store._embedding_scopes_verified
            with pytest.raises(EmbeddingMismatchError):
                await store._assert_scope_embedding_provenance(conn, "memories", "work")
            assert ("memories", "work", None) not in store._embedding_scopes_verified

    asyncio.run(run())


def test_search_reports_a_mismatch_through_last_search_error_instead_of_crashing():
    _pool, store = _store()

    async def run():
        assert await store.add_memory("I live in Pune", embedding=[0.1] * 768) is True
        store.embedding_model = "other-model"  # the embedder changed under old vectors
        store._embedding_scopes_verified.clear()
        found = await store.search_memories("where do I live", limit=3)
        return found, store.last_search_error

    found, err = asyncio.run(run())
    assert not found
    assert err and "different embedder" in err


def test_export_manifest_contains_embedding_configuration():
    manifest = export_friend._build_manifest({}, {}, {})
    assert manifest["embedding_model"] == "nomic-embed-text"
    assert manifest["embedding_dim"] == 768


def test_import_rejects_a_different_embedding_configuration():
    with pytest.raises(ValueError, match="does not match this embedder"):
        import_friend._verify_embedding_manifest(
            {"embedding_model": "other-model", "embedding_dim": 768}
        )


def test_import_accepts_an_archive_made_before_the_manifest_carried_the_pin():
    # Older exports have no embedding keys; they were all made with the default.
    import_friend._verify_embedding_manifest({"schema_version": 1})
