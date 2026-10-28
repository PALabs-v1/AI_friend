"""Embedding provenance (V4 slice 14) against a real Postgres with pgvector.

The pytest suite replaces asyncpg with a stub, so the Postgres SQL in
memory_store.py never runs there. This script runs it for real. Usage, with
a throwaway database (it deletes every row in memories and archived_memories):

    docker run -d --name pg_prov -e POSTGRES_USER=t -e POSTGRES_PASSWORD=t \\
        -e POSTGRES_DB=t -p 127.0.0.1:55433:5432 pgvector/pgvector:pg16
    docker exec -i pg_prov psql -U t -d t -q < backend/db/schema.sql
    cd backend && PROVENANCE_PG_DSN=postgresql://t:t@127.0.0.1:55433/t \\
        python3 scripts/verify_pg_provenance.py
    docker rm -f pg_prov
"""

import asyncio
import hashlib
import math
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.state.memory_store import MemoryStore

DSN = os.environ.get("PROVENANCE_PG_DSN")
if not DSN:
    sys.exit("set PROVENANCE_PG_DSN to a throwaway Postgres (see the module docstring)")


def vec(text, dim=768):
    seed = hashlib.sha256(text.encode()).digest()
    v = [((seed[i % 32] + i * 7) % 255) / 255.0 - 0.5 for i in range(dim)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def main():
    pool = await asyncpg.create_pool(DSN)
    store = MemoryStore(pool, None)
    store.qdrant_store.client = None

    async def fake_embed(text):
        return vec(text)

    store.get_embedding = fake_embed

    async with pool.acquire() as c:
        await c.execute("delete from memories; delete from archived_memories")

    # 1. writes record provenance
    for t in [
        "the cat sat on the mat",
        "quarterly tax filing is due friday",
        "she loves jazz piano",
    ]:
        await store.add_memory(t, wing="personal", importance=0.9)
    async with pool.acquire() as c:
        rows = await c.fetch(
            "select content, embedding_model, embedding_dim from memories order by id"
        )
    print(
        "1 writes:",
        [(r["content"][:12], r["embedding_model"], r["embedding_dim"]) for r in rows],
    )
    assert all(
        r["embedding_model"] == store.embedding_model and r["embedding_dim"] == 768
        for r in rows
    )

    # 2. pgvector search works and no error recorded
    res = await store.search_memories(
        "the cat sat on the mat", wing="personal", limit=3, refresh_on_recall=False
    )
    print(
        "2 search:",
        [r.get("content", r)[:20] if isinstance(r, dict) else str(r)[:20] for r in res],
        "err=",
        store.last_search_error,
    )
    assert res and store.last_search_error is None

    # 3. legacy NULL-provenance row accepted
    async with pool.acquire() as c:
        await c.execute("update memories set embedding_model=NULL, embedding_dim=NULL")
    store._embedding_scopes_verified.clear()
    store._l1_cache.clear()
    res = await store.search_memories(
        "she loves jazz piano", wing="personal", limit=3, refresh_on_recall=False
    )
    print("3 legacy NULL:", len(res), "err=", store.last_search_error)
    assert res and store.last_search_error is None

    # 4. foreign embedder row => refused, reported through last_search_error
    async with pool.acquire() as c:
        await c.execute(
            "update memories set embedding_model='mxbai-embed-large' where content like 'she loves%'"
        )
    store._embedding_scopes_verified.clear()
    store._l1_cache.clear()
    res = await store.search_memories(
        "she loves jazz piano", wing="personal", limit=3, refresh_on_recall=False
    )
    print("4 foreign:", res, "err=", (store.last_search_error or "")[:90])
    assert store.last_search_error and "different embedder" in store.last_search_error
    async with pool.acquire() as c:
        n = await c.fetchval(
            "select count(*) from memories where embedding_model='mxbai-embed-large'"
        )
    assert n == 1, "read path must not rewrite provenance"

    # 5. dimension mismatch on write is refused (add_memory logs and returns, as before)
    async with pool.acquire() as c:
        before = await c.fetchval("select count(*) from memories")
    await store.add_memory("bad dim", wing="personal", embedding=[0.1] * 384)
    async with pool.acquire() as c:
        after = await c.fetchval("select count(*) from memories")
    print("5 dim guard: rows", before, "->", after)
    assert before == after

    # 6. archive keeps provenance (new columns copied, no compat fallback)
    async with pool.acquire() as c:
        await c.execute(
            "update memories set embedding_model=NULL, embedding_dim=NULL where embedding_model='mxbai-embed-large'"
        )
        await c.execute(
            "update memories set embedding_model='nomic-embed-text', embedding_dim=768 where content like 'the cat%'"
        )
        ids = [
            r["id"]
            for r in await c.fetch(
                "select id from memories where content like 'the cat%' or content like 'she loves%'"
            )
        ]
        await store._archive_and_delete_decayed_memories(c, ids)
        arch = await c.fetch(
            "select content, embedding_model, embedding_dim, pg_typeof(embedding)::text t from archived_memories order by content"
        )
        left = await c.fetchval("select count(*) from memories")
    print(
        "6 archived:",
        [
            (r["content"][:10], r["embedding_model"], r["embedding_dim"], r["t"])
            for r in arch
        ],
        "active left:",
        left,
    )
    assert len(arch) == 2 and left == 1
    by = {r["content"][:3]: r for r in arch}
    assert (
        by["the"]["embedding_model"] == "nomic-embed-text"
        and by["the"]["embedding_dim"] == 768
    )
    assert by["she"]["embedding_model"] is None  # legacy NULL stays NULL

    # 7. archive recall and promotion back to active keeps/sets provenance
    store._embedding_scopes_verified.clear()
    store._l1_cache.clear()
    res = await store.search_memories(
        "the cat sat on the mat", wing="personal", limit=5, refresh_on_recall=False
    )
    print(
        "7 recall:",
        [str(r.get("content", r))[:18] for r in res],
        "err=",
        store.last_search_error,
    )
    async with pool.acquire() as c:
        act = await c.fetch(
            "select content, embedding_model, embedding_dim from memories where content like 'the cat%'"
        )
    print(
        "7 active after promote:",
        [(r["content"][:10], r["embedding_model"], r["embedding_dim"]) for r in act],
    )
    assert store.last_search_error is None and act
    await pool.close()
    print("ALL OK")


asyncio.run(main())
