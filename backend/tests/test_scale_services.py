"""Opt-in smoke checks for the services used by backend scale cells."""

from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backend", ["postgres", "qdrant-pinned", "qdrant-latest", "neo4j"]
)
async def test_scale_service_connection_when_opted_in(backend, tmp_path):
    if os.getenv("SCALE_RUN_INTEGRATION") != "1":
        pytest.skip("set SCALE_RUN_INTEGRATION=1 to probe local scale services")
    from tools.scale.runner import open_store

    database_url = os.getenv("SCALE_DATABASE_URL") if backend == "postgres" else None
    try:
        store, pool, graph_db = await open_store(backend, tmp_path, database_url)
    except Exception as exc:
        pytest.skip(
            f"{backend} service/schema unavailable: {type(exc).__name__}: {exc}"
        )
    try:
        if backend.startswith("qdrant-"):
            assert store.qdrant_store.client is not None
        if backend == "neo4j":
            assert graph_db is not None
    finally:
        await store._http_client.aclose()
        if graph_db:
            await graph_db.close()
        if pool:
            await pool.close()
