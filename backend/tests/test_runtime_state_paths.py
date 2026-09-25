"""F-016: runtime state goes to the deployment's data directory, and every
Redis client reads `Config.REDIS_URL` (empty disables it).

Before the fix, `StateService` hardcoded 127.0.0.1:6379, and `state_cache.db`
(agent state plus the learned reappraisal weights and goal utilities) was a
relative path. In the production containers that is the image's writable
layer, so every redeploy reset it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import Config
from app.state import agent_state as agent_state_module
from app.state import working_memory_store as working_memory_module
from app.state.agent_state import StateService
from app.state.runtime_paths import redis_endpoint, runtime_state_db
from app.state.working_memory_store import WorkingMemoryStore


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("redis://127.0.0.1:6379", ("127.0.0.1", 6379)),
        ("redis://brain_cache:6380/0", ("brain_cache", 6380)),
        ("redis://brain_cache", ("brain_cache", 6379)),
        ("brain_cache:6381", ("brain_cache", 6381)),
        ("", None),
        ("   ", None),
    ],
)
def test_redis_endpoint_parses_config(url, expected):
    with patch.object(Config, "REDIS_URL", url):
        assert redis_endpoint() == expected


def test_no_base_keeps_the_historical_relative_path(monkeypatch):
    monkeypatch.setattr(Config, "IDENTITY_BASE_PATH", None)
    assert runtime_state_db("state_cache.db") == "state_cache.db"


def test_base_path_puts_the_file_under_it_and_creates_the_directory(tmp_path):
    base = tmp_path / "fresh" / "volume"
    path = runtime_state_db("state_cache.db", base_path=base)
    assert path == str(base / "state_cache.db")
    assert base.is_dir()


def _legacy_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _read(path: str | Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


def test_legacy_state_is_migrated_once_and_left_in_place(tmp_path, monkeypatch):
    workdir, base = tmp_path / "work", tmp_path / "data"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    _legacy_db(workdir / "state_cache.db", "learned")

    path = runtime_state_db("state_cache.db", base_path=base)

    assert _read(path) == "learned"
    assert _read(workdir / "state_cache.db") == "learned"  # rollback copy kept
    # A second start never overwrites state written since the migration.
    conn = sqlite3.connect(path)
    conn.execute("UPDATE t SET v = 'newer'")
    conn.commit()
    conn.close()
    runtime_state_db("state_cache.db", base_path=base)
    assert _read(path) == "newer"


def test_legacy_filename_migrates_into_a_differently_named_target(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    _legacy_db(workdir / "state_cache.db", "subconscious")
    path = runtime_state_db(
        "state_cache_subconscious.db",
        base_path=tmp_path / "data",
        legacy_filename="state_cache.db",
    )
    assert _read(path) == "subconscious"


def _record_redis(monkeypatch) -> list[dict]:
    """Record Redis constructions instead of raising: both constructors wrap
    the call in `except Exception`, so a raising guard is swallowed and the
    test would pass on the old code too."""
    calls: list[dict] = []

    def factory(*args, **kwargs):
        calls.append(kwargs)
        raise ConnectionError("no redis in this test")

    monkeypatch.setattr(agent_state_module.redis, "Redis", factory)
    monkeypatch.setattr(working_memory_module.redis, "Redis", factory)
    return calls


def test_empty_redis_url_disables_both_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "REDIS_URL", "")
    calls = _record_redis(monkeypatch)
    state = StateService(db_path=str(tmp_path / "s.db"))
    memory = WorkingMemoryStore(db_path=str(tmp_path / "w.db"))
    assert calls == []  # not even attempted: no connect timeout spent
    assert state.redis_client is None
    assert memory.redis_client is None


def test_state_service_uses_the_configured_redis_endpoint(tmp_path, monkeypatch):
    # The regression: it used to connect to 127.0.0.1:6379 whatever the config.
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(Config, "REDIS_URL", "redis://brain_cache:6380")
    monkeypatch.setattr(agent_state_module.redis, "Redis", factory)
    state = StateService(db_path=str(tmp_path / "s.db"))
    assert factory.call_args.kwargs["host"] == "brain_cache"
    assert factory.call_args.kwargs["port"] == 6380
    assert state.redis_client is client


def test_state_service_default_path_is_under_the_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "REDIS_URL", "")
    monkeypatch.setattr(Config, "IDENTITY_BASE_PATH", str(tmp_path))
    brain = StateService(writer_id="brain_agent")
    subconscious = StateService(writer_id="subconscious_agent")
    assert brain.db_path == str(tmp_path / "state_cache.db")
    # Both processes share the production volume; one file each, or they
    # overwrite each other's row.
    assert subconscious.db_path == str(tmp_path / "state_cache_subconscious.db")


def test_cognitive_service_keeps_every_state_file_under_its_runtime_dir(
    tmp_path, monkeypatch
):
    from evals.brainbench.adapters import build_cognitive_service

    workdir, run_dir = tmp_path / "work", tmp_path / "run"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    calls = _record_redis(monkeypatch)
    service = build_cognitive_service("architecture_only", run_dir)
    assert calls == []  # BrainBench cells never touch a shared Redis
    try:
        cognitive = service.cognitive
        expected = str(run_dir / "state_cache.db")
        assert cognitive.state.db_path == expected
        assert cognitive.reappraisal._store.db_path == expected
        assert cognitive.decision._weights_store.db_path == expected
    finally:
        service.cognitive.close()
    # Nothing leaked into the working directory: the image layer in production.
    assert list(workdir.iterdir()) == []
