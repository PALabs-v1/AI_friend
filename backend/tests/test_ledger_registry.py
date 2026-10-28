"""Drift guard for the V4 state ledger registry (slice 1, D-020).

The registry in `app/state/ledger.py` names tables, columns, attributes and
code symbols. If a refactor renames one, the intervention operators would act on
nothing or on the wrong thing, so this test fails first. Same idea as
`test_doc_drift.py`: every name the registry uses must still resolve.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Config
from app.state.adaptive_weights_store import AdaptiveWeightsStore
from app.state.agent_state import AgentState, StateService
from app.state.ledger import REGISTRY, bound_rows, get_row, unbound_rows
from app.state.sqlite_fallback import SQLiteConnection
from app.state.temporal_store import TemporalMemoryStore

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def stores(tmp_path):
    """Build every SQLite store the registry names, on empty temp databases."""
    from app.state import proactive_queue
    from app.state.identity_core_store import IdentityCoreStore
    from app.state.working_memory_store import WorkingMemoryStore
    from app.state.workspace_store import SQLiteWorkspaceStore

    paths = {
        "state_cache": tmp_path / "state_cache.db",
        "identity_core": tmp_path / "identity_core.db",
        "temporal": tmp_path / "temporal.db",
        "memories": tmp_path / "memories.db",
        "working_memory": tmp_path / "working_memory.db",
        "workspace": tmp_path / "workspace.db",
    }
    with patch.object(Config, "REDIS_URL", ""):
        StateService(graph_store=None, db_path=str(paths["state_cache"]))
        WorkingMemoryStore(db_path=str(paths["working_memory"]))
    SQLiteWorkspaceStore(db_path=str(paths["workspace"]))
    AdaptiveWeightsStore(db_path=str(paths["state_cache"]))
    proactive_queue.enqueue(str(paths["state_cache"]), "seed")
    IdentityCoreStore(db_path=str(paths["identity_core"]))
    TemporalMemoryStore(db_path=paths["temporal"])
    SQLiteConnection(str(paths["memories"])).conn.close()
    return paths


def test_ids_are_unique_and_non_empty():
    ids = [r.id for r in REGISTRY]
    assert len(ids) == len(set(ids))
    assert all(i and i == i.strip() for i in ids)


def test_every_unbound_row_says_why():
    for row in unbound_rows():
        assert row.unbound_reason.strip(), f"{row.id} is unbound with no reason"


def test_bound_and_unbound_partition_the_registry():
    assert len(bound_rows()) + len(unbound_rows()) == len(REGISTRY)
    assert len(bound_rows()) >= 30, "the registry lost most of its bound rows"


def test_every_sqlite_binding_names_a_real_table_and_column(stores):
    for row in REGISTRY:
        b = row.sqlite
        if b is None:
            continue
        conn = sqlite3.connect(stores[b.store])
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({b.table})")}
        finally:
            conn.close()
        assert cols, f"{row.id}: table {b.table} not found in store {b.store}"
        assert b.column in cols, f"{row.id}: column {b.column} not in {b.table}"
        for key in (*b.key_columns, *b.optional_key_columns):
            assert key in cols, f"{row.id}: key column {key} not in {b.table}"


def test_every_attr_binding_resolves_on_agent_state():
    state = AgentState()
    for row in REGISTRY:
        if row.attr is None:
            continue
        obj = state
        *head, leaf = row.attr.path.split(".")
        for part in head:
            obj = getattr(obj, part)
        assert hasattr(obj, leaf), f"{row.id}: {row.attr.path} not on AgentState"


def test_every_writer_and_reader_symbol_still_exists():
    for row in REGISTRY:
        for ref in (*row.writers, *row.readers):
            path, _, symbol = ref.partition("::")
            assert symbol, f"{row.id}: malformed reference {ref!r}"
            file = REPO_ROOT / path
            assert file.is_file(), f"{row.id}: {path} does not exist"
            assert symbol in file.read_text(encoding="utf-8"), (
                f"{row.id}: symbol {symbol!r} not found in {path}"
            )


def test_get_row_rejects_unknown_ids():
    with pytest.raises(KeyError):
        get_row("not-a-row")
