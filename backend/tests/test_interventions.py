"""Intervention operators on offline copies (V4 slice 1, D-020, D-029).

Each operator must change the stored value, change nothing else, be readable by
the live reader (a `StateService` hydrating from the copy), and leave the source
untouched. The copy must refuse to go live and leave an audit trail when deleted.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Config
from app.state.agent_state import StateService
from app.state.interventions import (
    InterventionError,
    clamp,
    read_value,
    replay,
    shuffle,
    swap,
)
from app.state.offline_copy import AUDIT_FILENAME, OfflineCopy
from app.state.runtime_paths import OfflineCopyError, runtime_state_db
from app.state.sqlite_fallback import SQLiteConnection

AGENT = "friend"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(path)
    try:
        tables = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        return {
            t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables
        }
    finally:
        conn.close()


async def _seed(tmp_path: Path, name: str = "src", **state) -> dict[str, Path]:
    """A persisted state_cache plus a memories db, as a live friend would leave."""
    root = tmp_path / name
    root.mkdir()
    db = root / "state_cache.db"
    with patch.object(Config, "REDIS_URL", ""):
        svc = StateService(graph_store=None, db_path=str(db))
    cs = svc.current_state
    cs.mood = state.get("mood", 0.3)
    cs.trust_benevolence = state.get("trust_benevolence", 0.6)
    cs.trust_competence = state.get("trust_competence", 0.4)
    cs.user_interaction_hours = state.get("hours", [9, 10, 11, 14, 20, 21])
    await svc.persist_state(AGENT)
    mem = root / "memories.db"
    conn = SQLiteConnection(str(mem)).conn
    conn.execute(
        "INSERT INTO memories (id, content, valid_until) VALUES (?, ?, ?)",
        ("m1", "I live in Pune", None),
    )
    conn.execute(
        "INSERT INTO memories (id, content, valid_until) VALUES (?, ?, ?)",
        ("m2", "I live in Delhi", None),
    )
    conn.commit()
    conn.close()
    return {"state_cache": db, "memories": mem}


async def _hydrated(copy: OfflineCopy) -> StateService:
    svc = copy.open_state_service()
    await svc.hydrate_state(AGENT)
    return svc


@pytest.fixture
async def copy_a(tmp_path):
    sources = await _seed(tmp_path)
    c = OfflineCopy.create(sources, tmp_path / "copies", label="a")
    yield c
    if not c.deleted:
        c.delete(reason="test teardown")


@pytest.fixture
async def copy_b(tmp_path):
    sources = await _seed(
        tmp_path, "src_b", mood=-0.5, trust_benevolence=0.9, hours=[3, 4, 5]
    )
    c = OfflineCopy.create(sources, tmp_path / "copies", label="b")
    yield c
    if not c.deleted:
        c.delete(reason="test teardown")


# --------------------------------------------------------------------------
# clamp
# --------------------------------------------------------------------------


async def test_clamp_changes_the_stored_value_and_the_live_reader_sees_it(copy_a):
    before = await _hydrated(copy_a)
    assert before.current_state.mood == pytest.approx(0.3)

    rec = clamp(copy_a, "mood", -0.8)
    assert (rec.before, rec.after) == (pytest.approx(0.3), pytest.approx(-0.8))

    after = await _hydrated(copy_a)
    assert after.current_state.mood == pytest.approx(-0.8)


async def test_clamp_changes_nothing_else_and_never_touches_the_source(tmp_path):
    sources = await _seed(tmp_path)
    source_hash = {k: _digest(v) for k, v in sources.items()}
    copy = OfflineCopy.create(sources, tmp_path / "copies")
    try:
        state_before = _dump(copy.path("state_cache"))
        clamp(copy, "mood", -0.8)
        state_after = _dump(copy.path("state_cache"))

        rows_b = state_before["agent_state"][0]
        rows_a = state_after["agent_state"][0]
        cols = [
            r[1]
            for r in sqlite3.connect(copy.path("state_cache")).execute(
                "PRAGMA table_info(agent_state)"
            )
        ]
        changed = [c for c, x, y in zip(cols, rows_b, rows_a, strict=True) if x != y]
        assert changed == ["mood"]
        for table in state_before:
            if table != "agent_state":
                assert state_before[table] == state_after[table]
        assert {k: _digest(v) for k, v in sources.items()} == source_hash
    finally:
        copy.delete()


async def test_clamp_in_process_hormone_burst_reaches_the_derived_reader(copy_a):
    svc = copy_a.open_state_service()
    rest = svc.current_state.cortisol
    rec = clamp(svc, "cortisol_burst", 0.9)
    assert rec.after == pytest.approx(0.9)
    assert svc.current_state.cortisol_phasic_peak == pytest.approx(0.9)
    assert svc.current_state.cortisol > rest


async def test_clamp_refuses_a_state_service_outside_an_offline_copy(tmp_path):
    with patch.object(Config, "REDIS_URL", ""):
        live = StateService(graph_store=None, db_path=str(tmp_path / "live.db"))
    with pytest.raises(InterventionError, match="not inside an offline copy"):
        clamp(live, "mood", 0.0)


async def test_keyed_rows_need_where_and_reject_misses(copy_a):
    with pytest.raises(InterventionError, match="needs `where`"):
        clamp(copy_a, "memory_valid_until", "2026-01-01")
    with pytest.raises(InterventionError, match="no row matched"):
        clamp(copy_a, "memory_valid_until", "2026-01-01", where={"id": "nope"})
    rec = clamp(copy_a, "memory_valid_until", "2026-01-01", where={"id": "m1"})
    assert rec.after == "2026-01-01"
    assert read_value(copy_a, "memory_valid_until", where={"id": "m2"}) in (
        None,
        "None",
    )


# --------------------------------------------------------------------------
# swap
# --------------------------------------------------------------------------


async def test_swap_transplants_a_row_from_another_copy(copy_a, copy_b):
    b_value = read_value(copy_b, "trust_benevolence")
    assert b_value == pytest.approx(0.9)
    rec = swap(copy_a, copy_b, "trust_benevolence")
    assert rec.before == pytest.approx(0.6) and rec.after == pytest.approx(0.9)
    assert read_value(copy_b, "trust_benevolence") == pytest.approx(0.9)
    # a different row in the destination is untouched
    assert read_value(copy_a, "trust_competence") == pytest.approx(0.4)


async def test_swap_needs_targets_of_the_same_kind(copy_a):
    svc = copy_a.open_state_service()
    with pytest.raises(InterventionError, match="same kind"):
        swap(copy_a, svc, "mood")


# --------------------------------------------------------------------------
# shuffle
# --------------------------------------------------------------------------


async def test_shuffle_keeps_the_values_changes_the_order_and_is_seeded(copy_a, copy_b):
    original = read_value(copy_a, "interaction_hours")
    rec = shuffle(copy_a, "interaction_hours", seed=7)
    assert sorted(rec.after) == sorted(original)
    assert rec.after != original

    rec_b = shuffle(copy_b, "interaction_hours", seed=7)
    again = shuffle(copy_a, "interaction_hours", seed=7)
    assert rec_b.seed == again.seed == 7
    assert read_value(copy_a, "interaction_hours") == again.after


async def test_shuffle_rejects_scalar_rows(copy_a):
    with pytest.raises(InterventionError, match="not list-valued"):
        shuffle(copy_a, "mood", seed=1)


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------


async def test_replay_writes_each_step_and_calls_back_in_order(copy_a):
    seen = []
    records = replay(
        copy_a,
        "mood",
        [0.1, -0.2, 0.5],
        on_step=lambda i, v: seen.append((i, v, read_value(copy_a, "mood"))),
    )
    assert [r.after for r in records] == [
        pytest.approx(0.1),
        pytest.approx(-0.2),
        pytest.approx(0.5),
    ]
    assert [(i, v) for i, v, _ in seen] == [(0, 0.1), (1, -0.2), (2, 0.5)]
    assert all(read == pytest.approx(v) for _, v, read in seen)
    with pytest.raises(InterventionError, match="non-empty"):
        replay(copy_a, "mood", [])


# --------------------------------------------------------------------------
# log, guard, lifecycle
# --------------------------------------------------------------------------


async def test_every_intervention_is_logged_with_before_and_after(copy_a):
    clamp(copy_a, "mood", 0.0)
    shuffle(copy_a, "interaction_hours", seed=3)
    lines = (copy_a.root / "interventions.jsonl").read_text().splitlines()
    ops = [json.loads(line) for line in lines]
    assert [o["op"] for o in ops] == ["clamp", "shuffle"]
    assert ops[0]["copy_id"] == copy_a.copy_id
    assert ops[0]["before"] == pytest.approx(0.3) and ops[0]["after"] == 0.0


async def test_a_deployment_path_inside_an_offline_copy_is_refused(copy_a):
    with patch.object(Config, "IDENTITY_BASE_PATH", str(copy_a.root)):
        with pytest.raises(OfflineCopyError):
            runtime_state_db("state_cache.db")
    # the harness route, an explicit base_path, stays allowed
    assert runtime_state_db("x.db", base_path=copy_a.root).endswith("x.db")


async def test_delete_removes_the_copy_and_leaves_an_audit_trail(tmp_path):
    sources = await _seed(tmp_path)
    copy = OfflineCopy.create(sources, tmp_path / "copies", label="to-delete")
    root = copy.root
    copy.delete(reason="done")
    assert not root.exists()
    events = [
        json.loads(line)
        for line in (tmp_path / "copies" / AUDIT_FILENAME).read_text().splitlines()
    ]
    assert [e["event"] for e in events] == ["create", "delete"]
    assert events[1]["copy_id"] == copy.copy_id and events[1]["reason"] == "done"
    with pytest.raises(RuntimeError, match="was deleted"):
        copy.path("state_cache")


async def test_create_with_a_missing_source_leaves_nothing_behind(tmp_path):
    with pytest.raises(FileNotFoundError):
        OfflineCopy.create({"state_cache": tmp_path / "absent.db"}, tmp_path / "copies")
    assert not any((tmp_path / "copies").glob("copy-*"))
