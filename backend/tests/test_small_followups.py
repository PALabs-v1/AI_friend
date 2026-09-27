"""
Five small follow-ups, three of which change behaviour.

- `relationship` had two owners and the authored one was read by nobody.
- Deleting a biography passage left its memory in place forever.
- The self-correction retry never tagged speculative chunks.
- `_publish_speech_chunk` re-derived the affect vector instead of using the
  coordinator that exists to build it.
"""

import json
from types import SimpleNamespace

import pytest

from app.cognitive.identity import IdentityManager
from app.persona.biography import (
    BiographyEntry,
    prune_biography,
    stale_fingerprints,
)

# --------------------------------------------------------------------------
# relationship has one owner
# --------------------------------------------------------------------------

AUTHORED = """
name = "Written"
relationship = "New Acquaintance"
"""


def _persona_file(tmp_path):
    path = tmp_path / "persona.toml"
    path.write_text(AUTHORED, encoding="utf-8")
    return path


def test_an_authored_relationship_actually_takes_effect(tmp_path):
    """It used to do nothing at all, silently.

    `PersonaProfile.relationship` is what `persona.toml` sets; the prompt reads
    `history["relationship"]`. Nothing connected them — grep for readers of the
    profile field and there were none — so someone writing
    `relationship = "New Acquaintance"` got "Friend" and no warning that their
    setting had been discarded.
    """
    agent = IdentityManager(
        base_path=str(tmp_path), persona_file=_persona_file(tmp_path)
    )

    assert agent.first_boot is True
    assert agent.history["relationship"] == "New Acquaintance"
    assert "New Acquaintance" in agent.get_persona_prompt("")


def test_an_unwritten_relationship_never_overwrites_a_stored_one(tmp_path):
    """Seeding applies what someone wrote, not what the schema defaults to.

    `relationship` has a default of "Friend". Seeding it unconditionally on a
    first boot pushed that default over whatever the durable store held, so an
    agent hydrating from a store that said "Trusted Friend" was demoted on
    every start by a value nobody chose. Caught by an existing regression test,
    not by this file — the first version of this feature shipped that bug.
    """
    silent = tmp_path / "persona.toml"
    silent.write_text('name = "Written"\n', encoding="utf-8")

    agent = IdentityManager(base_path=str(tmp_path), persona_file=silent)
    agent.history["relationship"] = "Trusted Friend"
    agent._seed_relationship_from_profile()

    assert agent.history["relationship"] == "Trusted Friend"


def test_a_lived_relationship_is_not_reset_by_the_file(tmp_path):
    """The seed-once rule, applied to the field that most needs it.

    Trust and closeness are exactly what months of conversation build. If an
    edit to the authored file reset the relationship, the friendship would be
    worth nothing.
    """
    (tmp_path / "personality.json").write_text(
        json.dumps({"name": "Lived"}), encoding="utf-8"
    )
    (tmp_path / "history.json").write_text(
        json.dumps({"memories": ["we met in October"], "relationship": "Close Friend"}),
        encoding="utf-8",
    )
    agent = IdentityManager(
        base_path=str(tmp_path), persona_file=_persona_file(tmp_path)
    )

    assert agent.first_boot is False
    assert agent.history["relationship"] == "Close Friend"


# --------------------------------------------------------------------------
# deleting a passage forgets it
# --------------------------------------------------------------------------


def _entry(heading, text):
    return BiographyEntry(heading=heading, text=text)


def test_a_removed_passage_is_reported_stale():
    """Seeding was one-directional: adding created, deleting did nothing."""
    kept = _entry("Her sister", "They text every morning.")
    gone = _entry("Work", "She quit in March.")

    stale = stale_fingerprints([kept], [kept.fingerprint, gone.fingerprint])
    assert stale == [gone.fingerprint]


def test_an_edited_passage_counts_as_removed_and_added():
    """The fingerprint covers heading and text, so an edit is a new passage.

    It has to show up on both sides — stale *and* pending — or an edited
    paragraph would leave the old wording in memory alongside the new one, and
    the agent would recall a version of the story the file no longer tells.
    """
    before = _entry("Work", "She quit in March.")
    after = _entry("Work", "She quit in April.")

    assert stale_fingerprints([after], [before.fingerprint]) == [before.fingerprint]
    assert after.fingerprint != before.fingerprint


def test_nothing_is_stale_when_the_file_is_unchanged():
    """The common case must be a no-op, or every boot rewrites the ledger."""
    entry = _entry("Her sister", "They text every morning.")
    assert stale_fingerprints([entry], [entry.fingerprint]) == []


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def fetch(self, query, *args):
        if "archived_memories" in query:
            return []
        return [{"id": i} for i, mark in self.rows if mark in args]

    async def execute(self, query, *args):
        self.executed.append((query, args))


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_pruning_deletes_the_row_for_a_removed_passage():
    gone = _entry("Work", "She quit in March.")
    conn = FakeConn([("mem-1", gone.fingerprint)])
    store = SimpleNamespace(pool=FakePool(conn), is_sqlite=True, qdrant_store=None)

    removed = await prune_biography([gone.fingerprint], store)

    assert removed == [gone.fingerprint]
    assert any("DELETE FROM memories" in q for q, _ in conn.executed)
    assert any("mem-1" in str(a) for _, a in conn.executed)


@pytest.mark.asyncio
async def test_a_fingerprint_with_no_row_still_leaves_the_ledger():
    """Otherwise the scan is retried on every boot, forever.

    A passage whose memory was already pruned by decay has nothing to delete.
    Keeping its fingerprint would mean re-running the query for a row that
    cannot exist, on every single start.
    """
    conn = FakeConn([])
    store = SimpleNamespace(pool=FakePool(conn), is_sqlite=True, qdrant_store=None)

    assert await prune_biography(["orphan-fingerprint"], store) == [
        "orphan-fingerprint"
    ]


class ExplodingConn(FakeConn):
    """A connection whose scans fail, as a database under load does."""

    async def fetch(self, query, *args):
        raise RuntimeError("connection reset")


@pytest.mark.asyncio
async def test_a_passage_whose_scan_failed_stays_in_the_ledger():
    """A transient database error must not orphan the row permanently.

    The ledger entry is the only record that a fingerprint was ever seeded. If
    a failed scan still counted as removed, the entry would be dropped while
    its memory row survived, and nothing would ever look for that row again —
    the passage would be recalled by a friend who was told to forget it, with
    no remaining trace pointing at why.
    """
    store = SimpleNamespace(
        pool=FakePool(ExplodingConn([])), is_sqlite=True, qdrant_store=None
    )

    assert await prune_biography(["unlucky-fingerprint"], store) == []


@pytest.mark.asyncio
async def test_a_scanned_passage_leaves_the_ledger_even_when_a_sibling_failed():
    """One bad fingerprint must not hold back the others.

    Otherwise a single permanently-unscannable entry would re-run the whole
    prune on every boot forever.
    """

    class HalfBroken(FakeConn):
        async def fetch(self, query, *args):
            if "bad" in args:
                raise RuntimeError("connection reset")
            return []

    store = SimpleNamespace(
        pool=FakePool(HalfBroken([])), is_sqlite=True, qdrant_store=None
    )

    assert await prune_biography(["good", "bad"], store) == ["good"]


def test_a_rejected_persona_file_does_not_count_as_seeded(tmp_path):
    """A file that failed validation applied nothing, so it seeded nothing.

    `seeded_from_file` and `authored_keys` used to be set before the profile
    was validated. When validation then failed, the agent fell back to schema
    defaults while still believing the author had chosen them — so the seed
    marker was stamped and the *default* relationship was applied as if it had
    been written down. That spends the single, one-time seed opportunity on a
    persona whose contents never took effect, and only a reset gets it back.
    """
    bad = tmp_path / "persona.toml"
    # Out of bounds by design: mood decay of zero is a permanent mood lock, and
    # the schema rejects it rather than clamping.
    bad.write_text(
        'relationship = "New Acquaintance"\nmood_decay_rate = 0.0\n', encoding="utf-8"
    )

    agent = IdentityManager(base_path=str(tmp_path), persona_file=bad)

    assert agent.seeded_from_file is False
    assert agent.authored_keys == set()
    assert agent.history["relationship"] == "Friend"


@pytest.mark.asyncio
async def test_an_unreadable_biography_never_prunes_everything(tmp_path):
    """The most expensive possible misreading of an ambiguous situation.

    A biography that fails to parse yields no entries. Treating that as "every
    passage was deleted" would erase the entire seeded history on one bad edit.
    """
    from app.cognitive.core import CognitiveService

    class Store:
        def __init__(self):
            self.added = []

        async def add_memory(self, **kw):
            self.added.append(kw)

    # A store with a real `pool`, so pruning would genuinely run if it were
    # reached. Without one, `prune_biography` bails on the first line and this
    # test passes no matter what the code does — which is exactly how the first
    # version of it passed against a mutant that pruned everything.
    conn = FakeConn([("mem-1", "some-old-fingerprint")])
    store = Store()
    store.pool = FakePool(conn)
    store.is_sqlite = True
    store.qdrant_store = None

    service = CognitiveService(
        llm_service=None,
        memory_store=store,
        graph_db=None,
        base_path=str(tmp_path),
    )
    service.identity.history[CognitiveService.SEEDED_KEY] = ["some-old-fingerprint"]

    missing = tmp_path / "nope.md"
    assert await service.seed_biography_once(missing) == 0
    assert service.identity.history[CognitiveService.SEEDED_KEY] == [
        "some-old-fingerprint"
    ], "an unreadable file must not erase the seeded history"


# --------------------------------------------------------------------------
# one affect vector, one action loop
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamed_chunks_carry_the_same_affect_as_the_coordinator_builds():
    """Two implementations of a wire contract drift, and this one already had.

    `_publish_speech_chunk` re-derived the same eight `state_snap.get(...)`
    defaults that `create_chunk_payload` already applies, so a change to one
    produced streams whose chunks disagreed with their own `done` message.

    Asserted behaviourally rather than by reading the source: a test that greps
    for a call name passes for any refactor that keeps the name, including one
    that reintroduces a second construction beside it.
    """
    from app.agents.brain_agent import BrainAgent
    from app.utils.speech import SpeechCoordinator

    agent = object.__new__(BrainAgent)
    snapshot = {
        "valence": 0.42,
        "arousal": 0.31,
        "dominance": 0.77,
        "trust": 0.66,
        "attachment": 0.55,
        "emotion": "fond",
        "fatigue": 0.12,
    }
    agent.coordinator = SpeechCoordinator(segmenter=None)
    agent._reply_ledger = {}  # W5 per-reply ledger
    agent.cognitive_core = SimpleNamespace(
        state=SimpleNamespace(get_context_snapshot=lambda: snapshot)
    )
    agent.last_user_distance = 1.5

    published = []

    async def capture(subject, payload):
        published.append(payload)

    agent.publish = capture
    await agent._publish_speech_chunk(["hello", "you"], turn_id="t1")

    expected = agent.coordinator.create_chunk_payload(
        words=["hello", "you"],
        state_snap=snapshot,
        turn_id="t1",
        user_distance=1.5,
    )
    assert published[0]["affect"] == expected.affect.model_dump()
    assert published[0]["content"] == "hello you"

    # Pinned against the snapshot as well as against the coordinator. Comparing
    # the two alone is tautological — both read the same mapping, so a mapping
    # that drops a field still makes them agree with each other while the
    # voice agent receives an affect vector the state never had.
    affect = published[0]["affect"]
    assert affect["valence"] == 0.42
    assert affect["arousal"] == 0.31
    assert affect["dominance"] == 0.77
    assert affect["trust"] == 0.66
    assert affect["attachment"] == 0.55
    assert affect["emotion"] == "fond"
    assert affect["fatigue"] == 0.12
    assert affect["user_distance"] == 1.5


@pytest.mark.asyncio
async def test_the_self_correction_retry_still_tags_speculative_chunks():
    """The drift the collapsed loop fixes.

    Stage 9's retry was a second copy of stage 8's loop that never applied the
    `speculative` tag. A speculative turn that got self-corrected emitted chunks
    disagreeing with the plan that produced them, and nothing downstream noticed
    because a missing key just reads as "not speculative".
    """
    from app.cognitive.pipeline import CognitivePipeline

    pipeline = object.__new__(CognitivePipeline)

    async def fake_execute(_plan):
        yield {"type": "content", "data": "hi"}
        yield {"type": "done"}

    pipeline.action = SimpleNamespace(execute=fake_execute)

    async def no_internal(_chunk):
        return False

    pipeline._consume_internal_chunk = no_internal

    result = {"response": "", "done": None}
    chunks = [c async for c in pipeline._stream_action_pass(None, True, result)]

    assert all(c.get("speculative") for c in chunks)
    assert result["response"] == "hi"
    assert result["done"] is not None
