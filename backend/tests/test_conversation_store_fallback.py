import json
import logging
from unittest.mock import patch

import pytest

import app.config as config_module
from app.state.conversation_store import ConversationHistoryStore


@pytest.mark.asyncio
async def test_lenient_storage_mode_preserves_sqlite_fallback(caplog, monkeypatch):
    """H5: a PostgreSQL outage used to fail over to local SQLite with only a
    `logger.warning` and no queryable signal - a prior conversation's history
    (in Postgres) goes silently unreachable, and nothing distinguishes that
    from a fresh install. `used_fallback_storage` gives a health check
    something to report, and the log level makes it hard to miss in
    aggregated logs that filter below CRITICAL/ERROR.
    """
    store = ConversationHistoryStore()
    store.dsn = "postgresql://user:pass@nonexistent-host:5432/db"
    monkeypatch.setattr(
        config_module.config_instance, "ORGANISM_MODE_STRICT_STORAGE", False
    )

    with (
        patch(
            "app.state.conversation_store.asyncpg.create_pool",
            side_effect=ConnectionError("could not connect to server"),
        ),
        patch(
            "app.state.sqlite_fallback.SQLitePool",
            return_value=type(
                "FakePool",
                (),
                {
                    "acquire": lambda self: _NullAcquire(),
                },
            )(),
        ),
        caplog.at_level(logging.CRITICAL),
    ):
        await store.initialize()

    assert store.used_fallback_storage is True
    assert any(
        record.levelno >= logging.CRITICAL and "PostgreSQL" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_lenient_fallback_writes_the_health_sentinel(tmp_path, monkeypatch):
    """Audit finding (2026-09-14): `used_fallback_storage` was set on this
    exact path but nothing ever read it - the only wired /health signal came
    from runtime_bootstrap.py's own, separate fallback branch. This asserts
    ConversationHistoryStore's fallback now reports through the same
    sentinel file /health already reads (test_health_sqlite_fallback.py),
    not just a new local flag nothing consumes."""
    sentinel = tmp_path / "sqlite_fallback_active"
    store = ConversationHistoryStore()
    store.dsn = "postgresql://user:pass@nonexistent-host:5432/db"
    monkeypatch.setattr(
        config_module.config_instance, "ORGANISM_MODE_STRICT_STORAGE", False
    )
    monkeypatch.setattr(
        config_module.config_instance, "SQLITE_FALLBACK_HEALTH_FILE", str(sentinel)
    )

    with (
        patch(
            "app.state.conversation_store.asyncpg.create_pool",
            side_effect=ConnectionError("could not connect to server"),
        ),
        patch(
            "app.state.sqlite_fallback.SQLitePool",
            return_value=type(
                "FakePool", (), {"acquire": lambda self: _NullAcquire()}
            )(),
        ),
    ):
        await store.initialize()

    assert sentinel.exists()
    payload = json.loads(sentinel.read_text())
    assert "PostgreSQL connection failed" in payload["reason"]


@pytest.mark.asyncio
async def test_strict_storage_mode_raises_instead_of_using_sqlite(monkeypatch):
    monkeypatch.setattr(
        config_module.config_instance, "ORGANISM_MODE_STRICT_STORAGE", True
    )
    store = ConversationHistoryStore()
    store.dsn = "postgresql://user:pass@nonexistent-host:5432/db"

    with (
        patch(
            "app.state.conversation_store.asyncpg.create_pool",
            side_effect=ConnectionError("database unavailable"),
        ),
        patch("app.state.sqlite_fallback.SQLitePool") as sqlite_pool,
        pytest.raises(RuntimeError, match="Strict storage mode forbids falling back"),
    ):
        await store.initialize()

    # No fallback happened -- strict mode raised instead -- so the flag a
    # health check reads to detect silent SQLite degradation must not claim
    # one occurred (regression guard: it did, before this fix).
    assert store.used_fallback_storage is False
    assert store.pool is None
    sqlite_pool.assert_not_called()


class _NullAcquire:
    async def __aenter__(self):
        raise RuntimeError("not needed for this test")

    async def __aexit__(self, *args):
        return False
