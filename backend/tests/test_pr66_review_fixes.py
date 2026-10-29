"""
Regression tests for the eleven pre-existing bugs surfaced by the PR #66 review.

All of these predated the F1 decomposition: the refactor did not introduce them,
it made them visible by lifting the logic out of two thousand-line functions.
Each test below fails against the code as it stood before this branch.
"""

import asyncio
import sqlite3
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from app.cognitive.action import (
    _SAFE_FALLBACK_LINE,
    ActionService,
    _ChatStreamState,
)
from app.cognitive.decision import ActionPlan
from app.state.memory_store import MemoryStore


def _store():
    store = MemoryStore(MagicMock(), MagicMock())
    store.qdrant_store.client = None
    return store


def _service(llm=None, memory=None):
    return ActionService(llm_service=llm or MagicMock(), memory_store=memory)


def _plan(action_type="RESPOND_CHAT", **payload):
    base = {
        "message": "hi",
        "identity_prompt": "You are Aniket.",
        "emotion_state": "neutral",
    }
    base.update(payload)
    return ActionPlan(action_type=action_type, payload=base, goal="ENGAGE")


class _ScriptedLLM:
    """Yields a fixed chunk list per call; later calls reuse the last script."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls = 0

    def generate_stream(
        self, prompt=None, system=None, model=None, options_override=None, role=None
    ):
        script = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1

        async def _gen():
            for chunk in script:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        return _gen()


async def _drain(agen):
    return [item async for item in agen]


# --------------------------------------------------------------------------
# action.py
# --------------------------------------------------------------------------


def test_split_thought_does_not_log_reasoning_content(caplog):
    """Finding #1: the reasoning block quotes the user and surfaced memories."""
    private_content = "the user's own disclosure about their medical history"
    with caplog.at_level("DEBUG"):
        tail = ActionService._split_thought(
            f"<thought>{private_content}</thought>Hello."
        )

    assert tail == "Hello."
    assert private_content not in caplog.text
    # The length is fine to record; the content is not.
    assert str(len(private_content)) in caplog.text


@pytest.mark.parametrize("bad", ["not a list", 42, {"a": 1}, None])
def test_build_tom_context_survives_malformed_known_concepts(bad):
    """Finding #0: this runs before the stream's try block, so a raise here
    aborted the turn with no terminal event ever reaching the client."""
    out = ActionService._build_tom_context({"known_concepts": bad, "implied_goals": []})
    assert "Theory of Mind" in out


def test_build_tom_context_coerces_non_string_members():
    out = ActionService._build_tom_context(
        {"known_concepts": [1, None, "tea"], "implied_goals": [7, "vent"]}
    )
    assert "tea" in out and "vent" in out
    assert "7" in out


def test_build_tom_context_keeps_last_ten_concepts():
    out = ActionService._build_tom_context(
        {"known_concepts": [f"c{i}" for i in range(15)], "implied_goals": []}
    )
    assert "c14" in out
    assert "c4" not in out.split("Known Concepts")[1]


def test_visible_segments_strips_thought_split_across_chunks():
    """The CoT machine both paths now share."""
    svc = _service()
    state = _ChatStreamState()
    seen = []
    for chunk in ["<thought>plan", " harder", "</thought>", "Real reply."]:
        seen.extend(svc._visible_segments(chunk, state))
    assert "".join(seen) == "Real reply."
    assert "plan" not in "".join(seen)


@pytest.mark.parametrize(
    "chunks,expected",
    [
        # The tag arriving whole was the only case the old parser handled.
        (["<thought>SECRET</thought>Hi."], "Hi."),
        # Models stream token by token, so these are the common cases, not edge
        # cases: the old parser spoke the entire reasoning block for all of them.
        (["<tho", "ught>SECRET</thought>Hi."], "Hi."),
        (["<", "thought", ">", "SECRET", "</thought>", "Hi."], "Hi."),
        (["<thought>SECRET</thou", "ght>Tail"], "Tail"),
        # Visible text before a block used to be dropped entirely.
        (["Hello <thought>SECRET</thought> Bye"], "Hello  Bye"),
        # Only the first block was ever recognised.
        (["A<thought>S1</thought>B<thought>S2</thought>C"], "ABC"),
        # An unclosed block must not be spoken.
        (["<thought>SECRET never closes"], ""),
    ],
)
def test_visible_segments_never_leaks_reasoning(chunks, expected):
    svc = _service()
    state = _ChatStreamState()
    out = []
    for chunk in chunks:
        out.extend(svc._visible_segments(chunk, state))
    out.extend(svc._visible_trailing("", state))
    spoken = "".join(out)

    assert spoken == expected
    assert "SECRET" not in spoken
    assert "S1" not in spoken and "S2" not in spoken


@pytest.mark.parametrize(
    "text",
    ["3 < 4 and 5<6", "Wait <pause=300ms> ok", "no tags here at all"],
)
def test_visible_segments_does_not_swallow_non_thought_markup(text):
    """The partial-tag hold must release anything that never becomes a tag."""
    svc = _service()
    state = _ChatStreamState()
    out = svc._visible_segments(text, state)
    out.extend(svc._visible_trailing("", state))
    assert "".join(out) == text


def test_self_correction_yields_fallback_when_retry_produces_nothing():
    """An empty retry stream left the user with 'Wait, let me rephrase that...'
    and then silence, same as the exception path."""
    llm = _ScriptedLLM(["As an AI I cannot"], [])
    svc = _service(llm=llm)
    out = asyncio.run(_drain(svc.execute(_plan())))

    contents = [c["data"] for c in out if c["type"] == "content"]
    assert _SAFE_FALLBACK_LINE in contents
    assert out[-1]["type"] == "done"


def test_visible_segments_passes_through_plain_text():
    svc = _service()
    state = _ChatStreamState()
    seen = []
    for chunk in ["Hello", " there"]:
        seen.extend(svc._visible_segments(chunk, state))
    assert "".join(seen) == "Hello there"


def test_self_correction_strips_thought_blocks():
    """Finding #2: the retry emitted raw <thought> content to the user."""
    llm = _ScriptedLLM(
        ["As an AI I cannot"],
        ["<thought>I must avoid that phrase</thought>Of course, happy to help."],
    )
    svc = _service(llm=llm)
    out = asyncio.run(_drain(svc.execute(_plan())))
    spoken = "".join(c["data"] for c in out if c["type"] == "content")

    assert "Of course, happy to help." in spoken
    assert "<thought>" not in spoken
    assert "I must avoid that phrase" not in spoken


def test_self_correction_uses_a_fresh_sanitizer():
    """Finding #3: the aborted primary stream left a partial control tag
    buffered, which corrupted the retry's first chunk."""
    llm = _ScriptedLLM(
        ["As an AI I cannot <emo"],
        ["All better now."],
    )
    svc = _service(llm=llm)
    out = asyncio.run(_drain(svc.execute(_plan())))
    spoken = "".join(c["data"] for c in out if c["type"] == "content")

    assert "All better now." in spoken
    assert "<emo" not in spoken


def test_self_correction_failure_yields_fallback_not_silence():
    """Finding #4: users heard 'Wait, let me rephrase that...' then nothing."""
    llm = _ScriptedLLM(["As an AI I cannot"], [RuntimeError("retry stream died")])
    svc = _service(llm=llm)
    out = asyncio.run(_drain(svc.execute(_plan())))

    contents = [c["data"] for c in out if c["type"] == "content"]
    assert _SAFE_FALLBACK_LINE in contents
    assert out[-1]["type"] == "done"


def test_store_memory_reports_failure_when_persistence_fails():
    """Finding #5: add_memory returning False still claimed success."""
    memory = MagicMock()
    memory.add_memory = AsyncMock(return_value=False)
    svc = _service(memory=memory)
    out = asyncio.run(
        _drain(svc.execute(_plan("STORE_MEMORY", content="remember this")))
    )

    assert any(c["type"] == "error" for c in out)
    assert not any("committed that to memory" in str(c["data"]) for c in out)
    assert out[-1]["type"] == "done"


def test_store_memory_reports_failure_when_no_store_attached():
    svc = _service(memory=None)
    out = asyncio.run(
        _drain(svc.execute(_plan("STORE_MEMORY", content="remember this")))
    )

    assert any(c["type"] == "error" for c in out)
    assert not any("committed that to memory" in str(c["data"]) for c in out)


def test_store_memory_confirms_on_success():
    memory = MagicMock()
    memory.add_memory = AsyncMock(return_value=True)
    svc = _service(memory=memory)
    out = asyncio.run(
        _drain(svc.execute(_plan("STORE_MEMORY", content="remember this")))
    )

    assert any("committed that to memory" in str(c["data"]) for c in out)
    assert not any(c["type"] == "error" for c in out)


# --------------------------------------------------------------------------
# memory_store.py
# --------------------------------------------------------------------------


def test_as_aware_utc_parses_sqlite_timestamp_strings():
    """Finding #6: SQLite hands back TEXT, and the archive path did datetime
    arithmetic on it."""
    got = MemoryStore._as_aware_utc("2026-07-18 12:30:00")
    assert got == datetime(2026, 7, 18, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-07-18T12:30:00", datetime(2026, 7, 18, 12, 30, tzinfo=UTC)),
        ("2026-07-18T12:30:00Z", datetime(2026, 7, 18, 12, 30, tzinfo=UTC)),
        ("2026-07-18T12:30:00+00:00", datetime(2026, 7, 18, 12, 30, tzinfo=UTC)),
    ],
)
def test_as_aware_utc_handles_iso_variants(raw, expected):
    assert MemoryStore._as_aware_utc(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "not a timestamp"])
def test_as_aware_utc_degrades_to_none_on_unparseable(bad):
    """Callers apply their own fallback; raising mid-retrieval discarded
    otherwise valid active results."""
    assert MemoryStore._as_aware_utc(bad) is None


def test_as_aware_utc_preserves_existing_behaviour():
    naive = datetime(2026, 7, 18, 12, 30)
    aware = datetime(2026, 7, 18, 12, 30, tzinfo=UTC)
    assert MemoryStore._as_aware_utc(None) is None
    assert MemoryStore._as_aware_utc(naive) == aware
    assert MemoryStore._as_aware_utc(aware) == aware


def test_missing_column_error_detection():
    """Finding #7: only an un-migrated schema justifies dropping columns."""
    assert MemoryStore._is_missing_column_error(
        sqlite3.OperationalError("table memories has no column named virtue")
    )

    pg_err = Exception('column "virtue" does not exist')
    pg_err.sqlstate = "42703"
    assert MemoryStore._is_missing_column_error(pg_err)


@pytest.mark.parametrize(
    "exc",
    [
        sqlite3.IntegrityError("UNIQUE constraint failed: memories.id"),
        sqlite3.OperationalError("database is locked"),
        ConnectionError("server closed the connection unexpectedly"),
    ],
)
def test_non_schema_errors_are_not_treated_as_missing_columns(exc):
    """A constraint violation or transient outage must propagate, not silently
    re-insert the row with its developmental metadata stripped."""
    assert not MemoryStore._is_missing_column_error(exc)


def _insert_kwargs():
    return {
        "memory_id": "m1",
        "content": "c",
        "raw_val": "c",
        "wing": "personal",
        "room": None,
        "vector_str": "[]",
        "importance": 0.5,
        "emotion": 0.0,
        "valence": 0.0,
        "certainty": 1.0,
        "source": "user",
        "metadata_json": "{}",
        "lifespan_stage": "",
        "crisis": "",
        "virtue": "",
        "relations": "",
        "relation_circles": "",
        "modality": "",
        "current_time": None,
    }


def test_insert_row_falls_back_only_on_a_missing_column():
    """The predicate is wired into the insert, not merely defined."""
    store = _store()
    conn = MagicMock()
    calls = []

    async def _execute(sql, *args):
        calls.append(sql)
        if len(calls) == 1:
            raise sqlite3.OperationalError("table memories has no column named virtue")

    conn.execute = _execute
    asyncio.run(store._insert_memory_row(conn, **_insert_kwargs()))

    assert len(calls) == 2, "a schema error should retry without the Eriksonian columns"
    assert "virtue" in calls[0] and "virtue" not in calls[1]


@pytest.mark.parametrize(
    "exc",
    [
        sqlite3.IntegrityError("UNIQUE constraint failed: memories.id"),
        sqlite3.OperationalError("database is locked"),
        ConnectionError("server closed the connection unexpectedly"),
    ],
)
def test_insert_row_propagates_non_schema_errors(exc):
    """Finding #7: retrying on any failure silently re-inserted the row with
    its developmental metadata stripped."""
    store = _store()
    conn = MagicMock()
    calls = []

    async def _execute(sql, *args):
        calls.append(sql)
        raise exc

    conn.execute = _execute

    with pytest.raises(type(exc)):
        asyncio.run(store._insert_memory_row(conn, **_insert_kwargs()))

    assert len(calls) == 1, "must not retry a non-schema failure"


def test_archive_activation_survives_an_unparseable_stored_timestamp():
    """_as_aware_utc returning None must not then be subtracted from. Otherwise
    one malformed archive row discards the entire search result set."""
    store = _store()
    row = {
        "last_recalled_at": "not a timestamp",
        "recall_count": 2,
        "valence": 0.1,
        "emotional_weight": 0.2,
        "importance_score": 0.6,
    }
    score, _spread, _dist_emo, recall_count, _now = store._archive_row_activation(
        row,
        0.5,
        current_valence=0.0,
        current_arousal=0.5,
        current_cortisol=0.2,
        current_time=None,
    )
    assert isinstance(score, float)
    assert recall_count == 2


def test_promotion_survives_a_qdrant_failure_after_the_sql_move():
    """The SQL move is authoritative and has already committed. Treating a
    vector-store failure as a failed promotion would strand the memory: absent
    from the results and absent from the archive."""
    store = _store()
    # is_sqlite is a read-only structural check (the A5 fix), so satisfy it
    # honestly with a real sqlite3 connection rather than patching the property.
    store.pool.connection.conn = sqlite3.connect(":memory:")

    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=conn)
    acquired.__aexit__ = AsyncMock(return_value=False)
    store.pool.acquire = MagicMock(return_value=acquired)

    store.qdrant_store = MagicMock()
    store.qdrant_store.client = object()
    store.qdrant_store.add_vector_memory = MagicMock(
        side_effect=RuntimeError("qdrant unreachable")
    )

    # Must not raise: the caller uses an exception here to mean "not promoted".
    asyncio.run(
        store._write_promoted_memory(
            "m1", "content", {}, [0.1], 1, datetime.now(UTC), {}, sql_meta={}
        )
    )
    assert conn.execute.await_count == 2  # insert + archive delete


def test_promotion_propagates_a_sql_failure():
    """A genuine SQL failure must still reach the caller so the row is skipped."""
    store = _store()
    # is_sqlite is a read-only structural check (the A5 fix), so satisfy it
    # honestly with a real sqlite3 connection rather than patching the property.
    store.pool.connection.conn = sqlite3.connect(":memory:")

    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("disk full"))
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=conn)
    acquired.__aexit__ = AsyncMock(return_value=False)
    store.pool.acquire = MagicMock(return_value=acquired)
    store.qdrant_store = MagicMock()
    store.qdrant_store.client = None

    with pytest.raises(RuntimeError):
        asyncio.run(
            store._write_promoted_memory(
                "m1", "c", {}, [0.1], 1, datetime.now(UTC), {}, sql_meta={}
            )
        )


def test_promotion_payload_does_not_let_custom_metadata_overwrite_wing():
    """Finding #8: a stored key named 'wing' silently replaced the real one."""
    row = {"wing": "personal", "room": "kitchen", "created_at": None}
    payload = MemoryStore._build_promotion_payload(
        row, {"wing": "ATTACKER", "room": "ATTACKER", "note": "keep me"}
    )

    assert payload["wing"] == "personal"
    assert payload["room"] == "kitchen"
    assert orjson.loads(payload["custom_metadata"]) == {
        "wing": "ATTACKER",
        "room": "ATTACKER",
        "note": "keep me",
    }


def test_promotion_payload_round_trips_through_the_qdrant_read_path():
    """The read path does orjson.loads(meta['custom_metadata'])."""
    payload = MemoryStore._build_promotion_payload({}, {"topic": "tea"})
    assert orjson.loads(payload["custom_metadata"]) == {"topic": "tea"}


def test_promotion_payload_accepts_string_created_at():
    payload = MemoryStore._build_promotion_payload(
        {"created_at": "2026-07-18 12:30:00"}, {}
    )
    assert payload["created_at"].startswith("2026-07-18T12:30:00")


def test_search_cache_key_separates_self_reflection_modes():
    """Finding #10: pronoun cues resolve in opposite directions, so the two
    modes returning each other's memories for the cache TTL was a real
    perspective bug, not just a stale-read."""
    store = _store()
    store.get_embedding = AsyncMock(return_value=None)
    probed = []

    class _RecordingCache(dict):
        """The lookup runs on every call, however early the pipeline exits."""

        def __contains__(self, key):
            probed.append(key)
            return False

    store._l1_cache = _RecordingCache()

    async def _run():
        for flag in (False, True):
            await store.search_memories("what do I like", is_self_reflection=flag)

    asyncio.run(_run())

    assert len(probed) == 2
    assert probed[0] != probed[1], "self-reflection modes must not share a cache entry"
    # The flag is the only component that *means* to differ. Compared
    # positionally by value equality -- what dict-key collision actually
    # depends on, since lookup uses __eq__/__hash__, never `is`. P3-9's
    # quantization (memory_store.py: _quantize) allocates a fresh float each
    # call even for an unchanged value, so identity comparison here would
    # flag the untouched valence/arousal/cortisol positions as "differing"
    # for a reason that has nothing to do with self-reflection mode.
    differing = [i for i, (a, b) in enumerate(zip(probed[0], probed[1])) if a != b]
    assert len(differing) == 1
    assert (probed[0][differing[0]], probed[1][differing[0]]) == (False, True)
