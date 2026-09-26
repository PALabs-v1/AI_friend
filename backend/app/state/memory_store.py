"""
Memory Store — ACT-R Based Retrieval (psychological_layer.md §6).

Retrieval scoring adapted from Anderson & Lebiere (1998):
    Aᵢ = Bᵢ + Σⱼ Wⱼ·Sⱼᵢ + ε

With extensions for emotional alignment (Bower, 1981):
    Score = Aᵢ + w_emotion · EmotionalAlignment

Base-level activation (simplified):
    Bᵢ ≈ ln(recall_count) - d · ln(hours_since_last_recall + 1)
"""

import asyncio
import functools
import json
import logging
import math
import re
import sqlite3
import time
import uuid
from collections import Counter, OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import orjson

from .. import clock
from ..config import Config
from ..utils.background_tasks import spawn_background
from .memory_ranking import hybrid_rank

logger = logging.getLogger(__name__)


def _trace_enabled() -> bool:
    # Runtime import avoids app.state -> app.cognitive.__init__ -> core -> app.state.
    from ..cognitive.trace import enabled

    return enabled()


def _emit_trace(kind: str, **fields: Any) -> None:
    from ..cognitive.trace import emit as trace_emit

    trace_emit(kind, **fields)


@functools.lru_cache(maxsize=4096)
def _cached_ln(x: float) -> float:
    """Natural log, memoized on the value rounded to 3 decimal places.

    Was a bare module-level dict that was never evicted (A6). Rounding bounds
    the key space in practice, but "in practice" is doing real work there: the
    keys are memory ages, so a long-lived process with a wide spread of
    timestamps keeps adding entries for the life of the process, and nothing
    ever removes one.

    `lru_cache` gives the same hit rate for this access pattern with an actual
    ceiling. The rounding happens in the caller so the cache key is the rounded
    value rather than the raw float -- memoizing on the raw float would make
    almost every lookup a miss and the cache pure overhead.
    """
    return math.log(x)


def _ln(x: float) -> float:
    return _cached_ln(round(x, 3))


def _quantize(value: float, step: float) -> float:
    """Round `value` to the nearest multiple of `step`. For cache keys where
    near-identical floats should collide, not for anything that gets scored."""
    return round(value / step) * step


def _clip_relevance(similarity) -> float:
    """Cosine similarity clipped to [0, 1]: the query-comparable relevance
    published as `SurfacedMemory.score` (ADR-001)."""
    try:
        return max(0.0, min(1.0, float(similarity or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def pool_is_sqlite(pool) -> bool:
    """Whether `pool` is backed by a stdlib sqlite3 connection under the hood
    (SQLitePool, or a test double shaped like it), rather than real
    asyncpg/Postgres. Extracted from `MemoryStore.is_sqlite` (see its
    docstring for the A5 history) so other dual-backend callers -- e.g.
    `MentalLexicon` -- can check without needing a MemoryStore instance.
    """
    conn = getattr(pool, "connection", None)
    return isinstance(getattr(conn, "conn", None), sqlite3.Connection)


def _get_stem(word: str) -> str:
    w = word.lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    if w.endswith("ed") and len(w) > 4:
        if w.endswith("ated"):
            w = w[:-3] + "e"  # calibrated -> calibrate
        else:
            w = w[:-2]
    if w.endswith("ing") and len(w) > 5:
        if w.endswith("ating"):
            w = w[:-5] + "e"  # activating -> activate
        else:
            w = w[:-3]
    return w


# Query-cue synonym expansion is no longer a hardcoded thesaurus. It now reads
# the humanoid's *learned* vocabulary (MentalLexicon, see lexicon_store.py):
# words that co-occur in lived conversation become associated, and recall-time
# expansion draws on what the system has actually learned. See _get_stem below
# for the (generic, morphological) stemming that still normalizes cues.

# Retrieval scoring constants. These were previously inline "magic numbers"
# (one reverse-engineered to make a benchmark metric land on exactly 0.6).
# Named and documented here so the scoring is honest and tunable.
# Additive score bump per literal query cue found in a memory (see
# _apply_direct_cue_boost). Deliberately large relative to the ACT-R
# base/spread-activation terms it's added to -- those typically run roughly
# -3..+3 for a given candidate (see _base_activation / _effective_similarity
# below) -- so that a single literal keyword match can outrank a merely
# similar ACT-R candidate. Unlike PPR_DAMPING just below, which has a
# textbook justification, the 5.0 magnitude itself is a design choice, not
# derived from measurement against real recall data. Flagging it as such
# rather than presenting it as tuned.
DIRECT_CUE_BOOST = 5.0
PPR_DAMPING = 0.85  # canonical PageRank teleport/damping factor

# ACT-R retrieval-scoring weights. These were duplicated inline across three
# scoring paths (Qdrant/SQLite/PG); naming them here keeps the paths in sync and
# makes the affective tuning explicit. base_activation adds an importance term
# and an emotional-proximity bonus to the classic ln(freq) - d·ln(recency) core;
# the similarity gain rewards congruent valence×arousal and suppresses recall
# under stress (arousal×cortisol); the final score subtracts an emotional-
# distance penalty.
ACTR_IMPORTANCE_WEIGHT = 1.5  # weight on importance_score in base activation
ACTR_EMO_PROXIMITY_WEIGHT = 0.15  # bonus for small emotional distance
ACTR_VALENCE_GAIN = 0.1  # similarity gain from congruent valence×arousal
ACTR_STRESS_SUPPRESSION = 0.2  # similarity suppression under arousal×cortisol
ACTR_EMO_DISTANCE_PENALTY = 0.5  # score penalty per unit emotional distance
# Bucket 9 (voice remediation Phase 3): weight on ln(avg_inter_recall_hours + 1)
# -- see `_base_activation`/`_spacing_hours`. Scaled so a memory spaced across
# roughly a month of recalls (ln(~720)≈6.6) lands in the same ballpark as the
# importance term's own maximum (1.5×1.0), rather than dwarfing or vanishing
# beside the other terms already tuned to the "-3..+3" range noted above.
ACTR_SPACING_WEIGHT = 0.15

# P3-9: L1 cache key quantization. The cache key used to carry raw
# current_valence/arousal/cortisol floats -- affect drifts continuously
# (StateService blends it in small increments every tick), so two calls
# milliseconds apart almost never share an exact float and the cache could
# essentially never hit during a live conversation. The key only needs
# "close enough to be the same context," not scoring precision, so it
# rounds to a coarse bucket; the scoring math elsewhere still uses the raw
# floats. L1_CACHE_TIME_BUCKET_S similarly rounds an explicitly-passed
# current_time (production call sites never pass one -- it defaults to
# None -- but a caller that did would otherwise get a guaranteed-unique key
# on every call, since isoformat() carries microsecond precision).
L1_CACHE_AFFECT_BUCKET = 0.05
L1_CACHE_TIME_BUCKET_S = 5.0

# Generic English stop words plus a few domain-generic conversational terms,
# stripped from a query before it is used for lexical cue matching. Hoisted to
# module scope: this is a constant, and rebuilding the literal on every
# search_memories call was pure waste. Membership is unchanged (the original
# literal contained duplicates, which a set collapses either way).
SEARCH_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "but",
        "yet",
        "for",
        "nor",
        "with",
        "this",
        "that",
        "these",
        "those",
        "you",
        "your",
        "yours",
        "him",
        "her",
        "them",
        "his",
        "hers",
        "their",
        "theirs",
        "was",
        "were",
        "been",
        "have",
        "has",
        "had",
        "did",
        "does",
        "what",
        "where",
        "when",
        "who",
        "why",
        "how",
        "can",
        "could",
        "would",
        "should",
        "shall",
        "will",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "any",
        "are",
        "arent",
        "as",
        "at",
        "be",
        "because",
        "before",
        "being",
        "below",
        "between",
        "both",
        "by",
        "cant",
        "cannot",
        "didnt",
        "dont",
        "down",
        "during",
        "each",
        "few",
        "from",
        "further",
        "hadnt",
        "hasnt",
        "havent",
        "having",
        "he",
        "hed",
        "hell",
        "hes",
        "here",
        "heres",
        "herself",
        "himself",
        "i",
        "id",
        "ill",
        "im",
        "ive",
        "if",
        "in",
        "into",
        "isnt",
        "it",
        "its",
        "itself",
        "lets",
        "me",
        "more",
        "most",
        "mustnt",
        "my",
        "myself",
        "no",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shant",
        "she",
        "shed",
        "shell",
        "shes",
        "shouldnt",
        "so",
        "some",
        "such",
        "than",
        "thats",
        "themselves",
        "then",
        "there",
        "theres",
        "they",
        "theyd",
        "theyll",
        "theyre",
        "theyve",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "wasnt",
        "we",
        "wed",
        "well",
        "weve",
        "werent",
        "whats",
        "whens",
        "wheres",
        "which",
        "while",
        "whos",
        "whom",
        "whys",
        "wont",
        "wouldnt",
        "youd",
        "youll",
        "youre",
        "youve",
        "yourself",
        "yourselves",
        "describe",
        "compare",
        "influence",
        "influenced",
        "friend",
        "companion",
        "robot",
        "human",
        "development",
        "developer",
        "developers",
        "project",
        "workspace",
        "shared",
        "recall",
        "recalled",
        "experience",
        "experiences",
        "related",
    }
)

# Pronoun sets used for speaker/listener cue resolution.
FIRST_PERSON_PRONOUNS = frozenset({"i", "me", "my", "myself", "we", "our", "us"})
SECOND_PERSON_PRONOUNS = frozenset({"you", "your", "yours", "yourself", "yourselves"})

# Postgres retrieval fast path. Two variants of the same query: the current
# schema also carries the Eriksonian lifespan columns on `memories`, while a
# not-yet-migrated database has only the base columns. Both take the identical
# 12-argument tuple; see _fetch_surface_actr_rows.
_SURFACE_ACTR_SELECT_HEAD = """
    SELECT
        m.id AS id,
        s.content,
        s.raw_content,
        s.wing,
        s.room,
        s.importance_score,
        s.emotional_weight,
        s.valence,
        s.recall_count,
        s.last_recalled_at,
        s.created_at,
        s.metadata,
        s.similarity,
        s.score"""

_SURFACE_ACTR_FROM_JOIN = """
    FROM surface_actr_memories($1::vector(768), $2::text, $3::text, $4::double precision, $5::double precision, $6::double precision, $7::double precision, $8::double precision, $9::double precision, $10::double precision, $11::integer, $12::timestamptz) s
    LEFT JOIN memories m ON m.content = s.content AND m.wing = s.wing
"""

_SURFACE_ACTR_SQL_ERIKSONIAN = (
    _SURFACE_ACTR_SELECT_HEAD
    + """,
        m.speaker,
        m.record_type,
        m.valid_from,
        m.valid_until,
        m.contradicts_id,
        m.lifespan_stage,
        m.crisis,
        m.virtue,
        m.relations,
        m.relation_circles,
        m.modality"""
    + _SURFACE_ACTR_FROM_JOIN
)

_SURFACE_ACTR_SQL_LEGACY = _SURFACE_ACTR_SELECT_HEAD + _SURFACE_ACTR_FROM_JOIN

# Archive -> active promotion. Same columns in both dialects; only the
# placeholder style and the EXCLUDED casing differ. Each is written as one
# plain literal (not built via `+` concatenation) since both are fully static
# SQL text with no interpolated values at all, and bandit's B608 check fires
# on string concatenation/formatting, not on a plain literal.
_PROMOTE_INSERT_SQLITE = """INSERT INTO memories (
        id, content, raw_content, wing, room, embedding, importance_score, emotional_weight,
        valence, certainty, source, recall_count, last_recalled_at, created_at,
        metadata, speaker, record_type, valid_from, valid_until, contradicts_id,
        lifespan_stage, crisis, virtue, relations, relation_circles, modality
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        recall_count = excluded.recall_count,
        last_recalled_at = excluded.last_recalled_at,
        importance_score = excluded.importance_score
"""

_PROMOTE_INSERT_PG = """INSERT INTO memories (
        id, content, raw_content, wing, room, embedding, importance_score, emotional_weight,
        valence, certainty, source, recall_count, last_recalled_at, created_at,
        metadata, speaker, record_type, valid_from, valid_until, contradicts_id,
        lifespan_stage, crisis, virtue, relations, relation_circles, modality
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26)
    ON CONFLICT(id) DO UPDATE SET
        recall_count = EXCLUDED.recall_count,
        last_recalled_at = EXCLUDED.last_recalled_at,
        importance_score = EXCLUDED.importance_score
"""


class GoalBuffer:
    def __init__(self, capacity=5):
        self.concepts = []  # List of tuples: (concept_word, turn_added)
        self.capacity = capacity
        self.current_turn = 0

    def update_buffer(self, query_text, dynamic_stop_words):
        self.current_turn += 1

        # Extract clean keywords
        new_words = re.findall(r"\b\w{3,}\b", query_text.lower())
        filtered_words = [w for w in new_words if w not in dynamic_stop_words]

        # Add to buffer if not already present
        for w in filtered_words:
            # Update turn to keep it fresh
            self.concepts = [c for c in self.concepts if c[0] != w]
            self.concepts.append((w, self.current_turn))

        # Retain concepts active for a 3-turn window
        self.concepts = [c for c in self.concepts if self.current_turn - c[1] <= 3]

        # Cap to capacity
        if len(self.concepts) > self.capacity:
            self.concepts = self.concepts[-self.capacity :]

    def flush(self):
        self.concepts = []


class MemoryStore:
    def __init__(self, pool, graph_db, ollama_base_url=None):
        self.pool = pool
        self.graph_db = graph_db
        self.ollama_base_url = (
            ollama_base_url or getattr(Config, "OLLAMA_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.embedding_model = "nomic-embed-text"
        self._http_client = httpx.AsyncClient(timeout=30.0)
        # P4-8: strong-reference holder for the background refresh task below.
        self._background_tasks: set[asyncio.Task] = set()

        # ACT-R Parameters (§6.2)
        self.decay_rate = Config.ACTR_DECAY_RATE  # d
        self.spread_weight = Config.ACTR_SPREAD_WEIGHT  # Wⱼ
        self.emotion_weight = Config.ACTR_EMOTION_WEIGHT  # w_emotion

        # 3-State activation thresholds (Eriksonian Cognitive Alignment)
        self.recall_threshold = -1.5  # theta_recall
        self.subconscious_threshold = -2.5  # theta_sub
        self.pruning_threshold = -3.5  # theta_prune

        # L1 Memory Activation Cache. Bounded LRU: keys are full query
        # signatures (query text + affect + limits), so the working set is
        # unbounded across a long session and an unevicted dict would leak.
        # OrderedDict + a max size caps residency; oldest entries fall out first.
        self._l1_cache = OrderedDict()  # key -> (timestamp, results)
        self._l1_cache_ttl = 15.0  # seconds
        self._l1_cache_max = 256  # entries; evict least-recently-used past this

        self._db_stop_words = set()
        self._last_stop_words_update = 0.0

        # P3-6: search_memories's outer except returns [] on any failure, the
        # same shape a genuine "nothing relevant" result has -- callers can't
        # tell a broken retrieval from an honest miss. The empty return stays
        # (callers depend on it), but the last failure is recorded here so
        # anything that cares (health checks, tests, future callers) can
        # check it without changing the hot-path return contract. Cleared at
        # the start of every search_memories call; a later successful call
        # is the only thing that clears a stale failure.
        self.last_search_error: str | None = None
        self.last_search_error_at: float | None = None
        # Diagnostics of the most recent hybrid retrieval (ids and score
        # terms only, never memory text); see `_search_memories_hybrid`.
        self.last_search_trace: dict | None = None
        from .sqlite_vector_index import SQLiteVectorIndex

        self._sqlite_vector_index = SQLiteVectorIndex(Config.MEMORY_SQLITE_SCAN_LIMIT)

        from .lexicon_store import MentalLexicon
        from .semantic_recall_store import SemanticRecallStore

        self.qdrant_store = SemanticRecallStore()
        # Learned vocabulary: replaces the old static SYNONYM_MAP. Boots with a
        # generic innate seed, then acquires words + associations from experience.
        self.lexicon = MentalLexicon(self.pool)
        self.goal_buffer = GoalBuffer(capacity=5)
        self._last_query_vector = None
        import sys

        if "pytest" in sys.modules:
            self.qdrant_store.client = None

    @property
    def is_sqlite(self) -> bool:
        """Whether the backing pool is actually a stdlib sqlite3 connection under
        the hood (SQLitePool, or a test double shaped like it), rather than real
        asyncpg/Postgres.

        A5: previously sniffed type(self.pool).__name__ against a hardcoded set
        of class names ("MockPGPool", excluding "MagicMock"/"AsyncMock"/"Mock").
        Any pool class not on that exact list - a rename, a subclass, a new test
        double - silently misclassified and routed to the wrong SQL dialect.
        Checking what pool.connection.conn actually *is* (a real sqlite3.Connection,
        the one thing both the production SQLitePool and its test doubles genuinely
        share) is a structural fact instead of a name-matching guess.
        """
        return pool_is_sqlite(self.pool)

    def _in_predicate(
        self, column: str, values: Sequence[Any], param_index: int = 1
    ) -> tuple[str, list[Any]]:
        """Build a `column IN (...)` predicate and its arguments for this backend.

        The two dialects express set membership differently and neither is
        wrong: SQLite wants one placeholder per value, Postgres takes the whole
        list as a single array parameter via `= ANY($n)`. Spelling that out at
        each call site produced the same eight-line if/else repeatedly, where
        the only real content was a column name.

        Returns the clause and the argument list to splat, so callers keep each
        backend's idiom rather than being forced onto a lowest common
        denominator -- flattening Postgres to N placeholders would work but
        would throw away the array form the query planner handles better.

        Deliberately *not* applied to every dual-backend branch in this file.
        Most of them differ for real reasons -- boolean literals (`0`/`1` vs
        `FALSE`/`TRUE`), date arithmetic (`datetime('now', '-24 hours')` vs
        `NOW() - INTERVAL`), `datetime()` normalisation that SQLite needs
        because it stores timestamps as text, and `executemany` which the
        SQLite fallback does not provide. Those are genuine differences between
        the backends, not duplication, and collapsing them would invent a
        sameness that is not there.
        """
        if self.is_sqlite:
            return f"{column} IN ({','.join('?' * len(values))})", list(values)
        return f"{column} = ANY(${param_index})", [list(values)]

    @staticmethod
    def _is_missing_column_error(exc: BaseException) -> bool:
        """True only when a write failed because a column does not exist.

        Distinguishes an un-migrated schema (worth retrying without the
        Eriksonian columns) from constraint violations, serialization
        conflicts, and transient outages (which must propagate). Postgres
        reports SQLSTATE 42703; SQLite only says so in the message text.
        """
        sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if sqlstate == "42703":  # undefined_column
            return True
        if type(exc).__name__ == "UndefinedColumnError":
            return True
        message = str(exc).lower()
        if isinstance(exc, sqlite3.OperationalError):
            return "no column named" in message or "has no column" in message
        return False

    @staticmethod
    def _as_aware_utc(dt):
        """Coerce a datetime to timezone-aware UTC so recency arithmetic never
        mixes naive and aware operands (which raises TypeError).

        Timestamps reach these code paths from two sources: naive values
        (SQLite CURRENT_TIMESTAMP, strptime of stored strings) and aware values
        (Postgres timestamptz, datetime.now(timezone.utc), a caller-supplied
        current_time). Naive inputs are assumed to already be UTC -- which is how
        the stored timestamps are written -- and aware inputs are converted.
        None passes through so callers can apply their own fallback.

        SQLite hands back TEXT for timestamp columns, so archived rows can carry
        an ISO string where the active path carries a datetime. Those are parsed
        here rather than at each call site; anything unparseable degrades to None
        so callers fall back instead of raising mid-retrieval.
        """
        if dt is None:
            return None
        if isinstance(dt, str):
            raw = dt.strip()
            if not raw:
                return None
            # SQLite CURRENT_TIMESTAMP writes "YYYY-MM-DD HH:MM:SS"; fromisoformat
            # handles that plus the "T"-separated and offset-bearing variants.
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                logger.debug(
                    "Unparseable stored timestamp %r; treating as missing.", dt
                )
                return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    def _l1_cache_put(self, cache_key, value):
        """Insert into the L1 cache with LRU eviction.

        The newest entry is moved to the end; once the cache exceeds
        ``_l1_cache_max`` the least-recently-used entry (front) is dropped.
        """
        self._l1_cache[cache_key] = value
        self._l1_cache.move_to_end(cache_key)
        while len(self._l1_cache) > self._l1_cache_max:
            self._l1_cache.popitem(last=False)

    def _invalidate_l1_cache(self):
        """Drop every cached result set.

        Cache keys are query signatures, not memory ids, so a write to any
        single memory can change the correct result of an unknown set of
        queries. A full clear is the only coherent invalidation; it is called
        only on mutations rare relative to reads (writes, pruning), never on the
        per-recall reinforcement path.
        """
        self._l1_cache.clear()

    async def _find_existing_memory(self, conn, content, wing):
        """Return the id of a memory with identical content+wing, else None.

        Backs reinforce-on-repeat dedup. The ``isinstance(rows, list)`` guard is
        load-bearing: on the PG unit-test path ``conn`` is an AsyncMock whose
        ``fetch`` returns a truthy MagicMock, which would otherwise read as a
        hit and make every add masquerade as a duplicate.

        Dedup is an optimization, not a correctness requirement: if the lookup
        errors, return None so the caller falls through to a normal insert
        rather than dropping the write.
        """
        try:
            if self.is_sqlite:
                rows = await conn.fetch(
                    "SELECT id FROM memories WHERE content = ? AND wing = ? LIMIT 1",
                    content,
                    wing,
                )
            else:
                rows = await conn.fetch(
                    "SELECT id FROM memories WHERE content = $1 AND wing = $2 LIMIT 1",
                    content,
                    wing,
                )
        except Exception as e:
            logger.debug("Dedup lookup failed, proceeding to insert: %s", e)
            return None
        if isinstance(rows, list) and rows:
            return rows[0]["id"]
        return None

    async def _reinforce_memory(self, conn, memory_id, importance, current_time):
        """Strengthen an existing memory when the same statement recurs.

        Repetition consolidates a trace, it never weakens one: bump
        ``recall_count`` (ACT-R frequency), refresh recency, and raise
        ``importance_score`` to the max of old/new so a later low-importance
        restatement cannot demote an already-salient memory.
        """
        if self.is_sqlite:
            # CAST the bound importance to REAL: SQLite would otherwise compare a
            # numeric column against a text-affinity param and MAX() would return
            # the text operand, silently demoting a salient memory.
            if current_time is not None:
                await conn.execute(
                    "UPDATE memories SET recall_count = recall_count + 1, "
                    "last_recalled_at = ?, "
                    "importance_score = MAX(importance_score, CAST(? AS REAL)) WHERE id = ?",
                    current_time,
                    importance,
                    memory_id,
                )
            else:
                await conn.execute(
                    "UPDATE memories SET recall_count = recall_count + 1, "
                    "last_recalled_at = CURRENT_TIMESTAMP, "
                    "importance_score = MAX(importance_score, CAST(? AS REAL)) WHERE id = ?",
                    importance,
                    memory_id,
                )
        else:
            if current_time is not None:
                await conn.execute(
                    "UPDATE memories SET recall_count = recall_count + 1, "
                    "last_recalled_at = $1, "
                    "importance_score = GREATEST(importance_score, $2) WHERE id = $3",
                    current_time,
                    importance,
                    memory_id,
                )
            else:
                await conn.execute(
                    "UPDATE memories SET recall_count = recall_count + 1, "
                    "last_recalled_at = NOW(), "
                    "importance_score = GREATEST(importance_score, $1) WHERE id = $2",
                    importance,
                    memory_id,
                )

    def _base_activation(
        self,
        recall_count,
        hours_since,
        importance_score,
        dist_emo,
        spacing_hours=None,
    ):
        """ACT-R base-level activation: ln(freq) - d·ln(recency) plus importance,
        emotional-proximity, and (Bucket 9, voice remediation Phase 3) spacing
        terms. Shared by every retrieval-scoring path so the formula stays
        identical across the Qdrant, SQLite and PG branches.

        `spacing_hours` (see `_spacing_hours`) rewards a memory whose repeat
        recalls were spread out over time (spaced practice) over one recalled
        the same total number of times in a burst (massed practice), even
        when frequency (`recall_count`) and recency (`hours_since`) are
        identical between the two -- the literature's central spacing-effect
        finding, which the plain `ln(freq) - d·ln(recency)` formula above has
        no way to express on its own. `None` means there is no second data
        point to measure spacing from (fewer than 2 recalls, or no creation
        timestamp), so the term is skipped rather than guessed at.
        """
        spacing_bonus = (
            ACTR_SPACING_WEIGHT * _ln(spacing_hours + 1.0)
            if spacing_hours is not None
            else 0.0
        )
        return (
            _ln(recall_count)
            - self.decay_rate * _ln(hours_since + 1.0)
            + ACTR_IMPORTANCE_WEIGHT * importance_score
            + ACTR_EMO_PROXIMITY_WEIGHT * (1.0 - dist_emo)
            + spacing_bonus
        )

    @staticmethod
    def _spacing_hours(recall_count, created_at, last_recall_dt):
        """Approximate average gap between recalls, in hours.

        The schema stores only `recall_count` and `last_recalled_at`, not a
        timestamp per individual recall, so the true ACT-R spacing formula
        (summing `(now - t_j)^-d` over every past presentation `t_j`) is not
        computable from what is actually persisted. This instead spreads the
        span from creation to the most recent recall evenly across
        `recall_count` recalls -- an approximation, not the literal sum, but
        one that still separates a memory recalled a handful of times across
        weeks (spaced) from one recalled the same number of times within an
        hour (massed), which is the effect this bucket exists to capture.

        Returns `None` (deliberately, not 0.0 -- see `_base_activation`) when
        there are fewer than two recalls to measure a gap between, either
        timestamp is missing, or the span is non-positive (a stale or
        fallback-to-"now" creation timestamp racing the recall clock).
        """
        if recall_count < 2 or created_at is None or last_recall_dt is None:
            return None
        span_hours = (last_recall_dt - created_at).total_seconds() / 3600.0
        if span_hours <= 0.0:
            return None
        return span_hours / recall_count

    def _effective_similarity(
        self,
        similarity,
        memory_valence,
        emotion_weight,
        current_arousal,
        current_cortisol,
    ):
        """Neuromodulatory gain on cosine similarity: boosted by congruent
        valence×arousal, suppressed under stress (arousal×cortisol).
        """
        return similarity * (
            1.0
            + ACTR_VALENCE_GAIN * memory_valence * emotion_weight
            - ACTR_STRESS_SUPPRESSION * current_arousal * current_cortisol
        )

    @staticmethod
    def _personalized_pagerank(entity_names, adj, seeds, damping, iterations):
        """HippoRAG-inspired Personalized PageRank over the entity graph.

        The numeric power-method loop is delegated to the ``cognitive_rust``
        extension (the same crate that already owns the ACT-R scoring hot loop),
        with an exact pure-Python fallback if the extension is unavailable. Both
        paths preserve the legacy semantics precisely:

          * ``degrees`` holds each node's ORIGINAL neighbor count. A neighbor
            whose name is absent from ``entity_names`` has no resolvable index
            and is dropped from the push, but the mass is still divided by the
            full degree -- so that share leaks out of the graph, unchanged.
          * A degree-0 node is dangling and redistributes uniformly across the
            seeds rather than the whole graph.

        ``seeds`` is a set of node indices; returns the rank vector (list) of
        length ``len(entity_names)``.
        """
        n = len(entity_names)
        if not seeds or n == 0:
            return [0.0] * n

        node_to_idx = {name: idx for idx, name in enumerate(entity_names)}
        seed_list = sorted(seeds)

        # Resolve name-based adjacency to index lists once; keep the original
        # degree so the divisor (and the leaked mass) matches the legacy loop.
        adjacency_idx = []
        degrees = []
        for name in entity_names:
            neighbors = adj.get(name, ())
            degrees.append(len(neighbors))
            adjacency_idx.append(
                [node_to_idx[nb] for nb in neighbors if nb in node_to_idx]
            )

        try:
            import cognitive_rust

            return list(
                cognitive_rust.personalized_pagerank(
                    adjacency_idx, degrees, seed_list, damping, iterations
                )
            )
        except Exception:
            # Pure-Python fallback with identical arithmetic and ordering.
            p_0 = [0.0] * n
            seed_share = 1.0 / len(seed_list)
            for s_idx in seed_list:
                p_0[s_idx] = seed_share
            p = list(p_0)
            for _ in range(iterations):
                p_next = [0.0] * n
                for i in range(n):
                    degree = degrees[i]
                    if degree:
                        val = p[i] / degree
                        for n_idx in adjacency_idx[i]:
                            p_next[n_idx] += val
                    else:
                        dangling_share = p[i] / len(seed_list)
                        for s_idx in seed_list:
                            p_next[s_idx] += dangling_share
                for i in range(n):
                    p_next[i] = damping * p_next[i] + (1.0 - damping) * p_0[i]
                p = p_next
            return p

    # Columns always written; the Eriksonian columns below may be absent on an
    # un-migrated schema, so a failed full insert falls back to just these.
    _MEMORY_BASE_COLUMNS = (
        "id",
        "content",
        "raw_content",
        "wing",
        "room",
        "embedding",
        "importance_score",
        "emotional_weight",
        "valence",
        "certainty",
        "source",
        "metadata",
    )
    _MEMORY_ERIKSONIAN_COLUMNS = (
        "lifespan_stage",
        "crisis",
        "virtue",
        "relations",
        "relation_circles",
        "modality",
    )
    _MEMORY_PROVENANCE_COLUMNS = (
        "speaker",
        "record_type",
        "valid_from",
        "valid_until",
        "contradicts_id",
    )

    async def _insert_memory_row(
        self,
        conn,
        *,
        memory_id,
        content,
        raw_val,
        wing,
        room,
        vector_str,
        importance,
        emotion,
        valence,
        certainty,
        source,
        metadata_json,
        lifespan_stage,
        crisis,
        virtue,
        relations,
        relation_circles,
        modality,
        current_time,
        speaker=None,
        record_type="episode",
        valid_from=None,
        valid_until=None,
        contradicts_id=None,
    ):
        """Insert a memory row from a single column/placeholder builder.

        Collapses what were eight near-identical INSERTs spanning three binary
        axes -- SQLite vs PostgreSQL placeholders, timed vs untimed
        (created_at/last_recalled_at), and the full Eriksonian column set vs a
        legacy fallback for un-migrated schemas. recall_count is always the
        literal 1; an untimed insert lets last_recalled_at default via
        CURRENT_TIMESTAMP.
        """
        base_vals = [
            memory_id,
            content,
            raw_val,
            wing,
            room,
            vector_str,
            importance,
            emotion,
            valence,
            certainty,
            source,
            metadata_json,
        ]
        provenance_vals = [
            speaker,
            record_type,
            valid_from,
            valid_until,
            contradicts_id,
        ]
        erik_vals = [
            lifespan_stage,
            crisis,
            virtue,
            relations,
            relation_circles,
            modality,
        ]

        async def _insert(include_eriksonian: bool, include_provenance: bool):
            cols = list(self._MEMORY_BASE_COLUMNS)
            vals = list(base_vals)
            if include_provenance:
                cols += list(self._MEMORY_PROVENANCE_COLUMNS)
                vals += provenance_vals
            if include_eriksonian:
                cols += list(self._MEMORY_ERIKSONIAN_COLUMNS)
                vals += erik_vals

            if self.is_sqlite:
                placeholders = ["?"] * len(vals)
            else:
                placeholders = [f"${i}" for i in range(1, len(vals) + 1)]

            # recall_count is a literal, never a bound parameter.
            cols.append("recall_count")
            placeholders.append("1")

            params = list(vals)
            if current_time is not None:
                cols += ["last_recalled_at", "created_at"]
                if self.is_sqlite:
                    placeholders += ["?", "?"]
                else:
                    placeholders += [f"${len(vals) + 1}", f"${len(vals) + 2}"]
                params += [current_time, current_time]
            else:
                cols.append("last_recalled_at")
                placeholders.append("CURRENT_TIMESTAMP")

            sql = (
                f"INSERT INTO memories ({', '.join(cols)}) "  # nosec B608 - cols comes from _MEMORY_BASE_COLUMNS/_MEMORY_ERIKSONIAN_COLUMNS class constants
                f"VALUES ({', '.join(placeholders)})"
            )
            await conn.execute(sql, *params)

        variants = [(True, True)]
        compatibility_index = 0
        while variants:
            include_eriksonian, include_provenance = variants.pop(0)
            try:
                await _insert(include_eriksonian, include_provenance)
                if compatibility_index:
                    logger.warning(
                        "Memory insert used compatibility schema variant %d",
                        compatibility_index,
                    )
                return
            except Exception as e:
                # Only an un-migrated schema justifies dropping columns. Do not
                # turn a constraint violation, serialization conflict, or
                # transient outage into a metadata-stripping retry.
                if not self._is_missing_column_error(e):
                    raise
                if not variants:
                    message = str(e).lower()
                    provenance_missing = any(
                        column in message for column in self._MEMORY_PROVENANCE_COLUMNS
                    )
                    eriksonian_missing = any(
                        column in message for column in self._MEMORY_ERIKSONIAN_COLUMNS
                    )
                    if provenance_missing and not eriksonian_missing:
                        variants = [(True, False), (False, False)]
                    elif eriksonian_missing and not provenance_missing:
                        variants = [(False, True), (False, False)]
                    else:
                        # PostgreSQL's SQLSTATE may omit the column name.
                        variants = [(False, True), (True, False), (False, False)]
                compatibility_index += 1

    async def get_embedding(self, text: str):
        """Generates vector embedding for text using local Ollama."""
        attempts = [
            ("/api/embed", {"model": self.embedding_model, "input": text}),
            ("/api/embeddings", {"model": self.embedding_model, "prompt": text}),
        ]

        last_error = None
        try:
            client = self._http_client
            for endpoint, payload in attempts:
                response = await client.post(
                    f"{self.ollama_base_url}{endpoint}",
                    json=payload,
                )
                if response.status_code == 404:
                    continue

                response.raise_for_status()
                result = response.json()

                embedding = result.get("embedding")
                if embedding:
                    return embedding

                embeddings = result.get("embeddings")
                if isinstance(embeddings, list) and embeddings:
                    return embeddings[0]

                last_error = f"No embedding payload returned by {endpoint}"

            if last_error is None:
                last_error = "All embedding endpoints returned 404"
            raise RuntimeError(last_error)
        except Exception as e:
            logger.error(f"Ollama embedding failed: {e}")
            if getattr(Config, "MOCK_LLM_TEXT", False):
                import numpy as np

                vec = np.random.randn(768)
                norm = np.linalg.norm(vec)
                if norm < 1e-6:
                    vec = np.zeros(768)
                    vec[0] = 1.0
                    return vec.tolist()
                return (vec / norm).tolist()
            return None

    def _mock_embedding_vector(self):
        """A unit-norm random 768-d vector, matching get_embedding's MOCK_LLM_TEXT path."""
        import numpy as np

        vec = np.random.randn(768)
        norm = np.linalg.norm(vec)
        if norm < 1e-6:
            vec = np.zeros(768)
            vec[0] = 1.0
            return vec.tolist()
        return (vec / norm).tolist()

    async def get_embeddings(self, texts: list[str]) -> list[list[float] | None]:
        """Batched form of get_embedding (P4-12, M5-P3 MEASURED: 2.4x cheaper
        per item at batch 32 vs sequential batch 1 -- see Config.EMBEDDING_BATCH_SIZE).

        Order-preserving and length-preserving: the returned list has exactly
        len(texts) entries, aligned 1:1 with the input. A per-item failure
        yields None in that slot rather than shortening the list -- a
        silently shortened list would misalign every downstream row with the
        wrong vector, which is worse than no embedding at all.

        Falls back to sequential get_embedding() when the batch endpoint
        404s, preserving the existing two-endpoint fallback shape rather than
        introducing a second one (the legacy /api/embeddings endpoint is
        single-input only and cannot serve a batch request).
        """
        if not texts:
            return []

        batch_size = max(1, int(getattr(Config, "EMBEDDING_BATCH_SIZE", 32)))
        results: list[list[float] | None] = []

        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            chunk_results = await self._embed_batch_chunk(chunk)
            results.extend(chunk_results)

        return results

    async def _embed_batch_chunk(self, chunk: list[str]) -> list[list[float] | None]:
        """Embed one chunk (<= EMBEDDING_BATCH_SIZE items) via /api/embed,
        falling back to sequential get_embedding() calls on a 404."""
        try:
            client = self._http_client
            response = await client.post(
                f"{self.ollama_base_url}/api/embed",
                json={"model": self.embedding_model, "input": chunk},
            )

            if response.status_code == 404:
                return [await self.get_embedding(text) for text in chunk]

            response.raise_for_status()
            result = response.json()

            embeddings = result.get("embeddings")
            if not isinstance(embeddings, list):
                raise TypeError("No embeddings payload returned by /api/embed")

            if len(embeddings) != len(chunk):
                logger.error(
                    "Ollama batch embed returned %d vectors for %d inputs; "
                    "falling back to sequential calls for this chunk.",
                    len(embeddings),
                    len(chunk),
                )
                return [await self.get_embedding(text) for text in chunk]

            return [
                (vec if isinstance(vec, list) and vec else None) for vec in embeddings
            ]
        except Exception as e:
            logger.error(f"Ollama batch embedding failed: {e}")
            if getattr(Config, "MOCK_LLM_TEXT", False):
                return [self._mock_embedding_vector() for _ in chunk]
            return [None] * len(chunk)

    async def _prelink_memory_entities(self, content: str) -> list[str]:
        """Entities from the graph whose name literally appears in `content`,
        for the memory's `metadata["entities"]` and Qdrant payload."""
        present_entities: list[str] = []
        if not self.graph_db:
            return present_entities
        try:
            entity_records = await self.graph_db.execute_query(
                "MATCH (e:Entity) "
                "WHERE e.name IS NOT NULL "
                "AND toLower($content) CONTAINS toLower(e.name) "
                "RETURN e.name AS name",
                {"content": content},
                use_cache=True,
            )
            entity_names = [r["name"] for r in entity_records]
            content_lower = content.lower()
            for name in entity_names:
                name_lower = name.lower()
                pattern = rf"\b{re.escape(name_lower)}\b"
                if re.search(pattern, content_lower):
                    present_entities.append(name)
        except Exception as ge:
            logger.debug(
                f"Failed to fetch entities for pre-linking in add_memory: {ge}"
            )
        return present_entities

    async def _upsert_qdrant_memory(
        self,
        *,
        memory_id: str,
        vector,
        content: str,
        wing,
        room,
        importance,
        emotion,
        valence,
        certainty,
        source,
        speaker,
        record_type,
        valid_from,
        valid_until,
        contradicts_id,
        lifespan_stage,
        crisis,
        virtue,
        relations,
        relation_circles,
        modality,
        present_entities: list[str],
        metadata: dict,
        current_time,
    ) -> None:
        if not self.qdrant_store.client:
            return
        qdrant_ts = (
            str(current_time.timestamp())
            if current_time is not None
            else str(clock.time())
        )
        metadata_qdrant = {
            "wing": wing,
            "room": room or "",
            "importance_score": importance,
            "emotional_weight": emotion,
            "valence": valence,
            "certainty": certainty,
            "source": source,
            "speaker": speaker,
            "record_type": record_type,
            "valid_from": (
                valid_from.isoformat()
                if isinstance(valid_from, datetime)
                else valid_from
            ),
            "valid_until": (
                valid_until.isoformat()
                if isinstance(valid_until, datetime)
                else valid_until
            ),
            "contradicts_id": contradicts_id,
            "recall_count": 1,
            "last_recalled_at": qdrant_ts,
            "created_at": qdrant_ts,
            "lifespan_stage": lifespan_stage or "",
            "crisis": crisis or "",
            "virtue": virtue or "",
            "relations": relations or "",
            "relation_circles": relation_circles or "",
            "modality": modality or "",
            "entities": present_entities,
        }
        if metadata:
            metadata_qdrant["custom_metadata"] = orjson.dumps(metadata).decode()

        # P3-13a: the three other add_vector_memory call sites in this file
        # all wrap the Qdrant client's synchronous network I/O in
        # asyncio.to_thread; this one didn't, blocking the event loop for
        # the duration of every memory write.
        await asyncio.to_thread(
            self.qdrant_store.add_vector_memory,
            memory_id=memory_id,
            vector=vector,
            content=content,
            metadata=metadata_qdrant,
        )

    @staticmethod
    def _content_polarity(content: str) -> int:
        """Return a small deterministic polarity signal for contradiction checks."""
        positive = re.search(
            r"\b(?:like|likes|liked|love|loves|loved|enjoy|enjoys|enjoyed|prefer|prefers|preferred|want|wants|wanted)\b",
            content,
            re.IGNORECASE,
        )
        negative = re.search(
            r"\b(?:hate|hates|hated|dislike|dislikes|disliked|avoid|avoids|avoided|never|can't|cannot|no longer)\b",
            content,
            re.IGNORECASE,
        )
        if positive and not negative:
            return 1
        if negative and not positive:
            return -1
        return 0

    def _check_row_contradiction(
        self,
        row: dict[str, Any],
        subject_key: str,
        current_polarity: int,
        current_valence: float | None,
    ) -> dict[str, Any] | None:
        """Check if a memory row matches the entity and represents a contradiction."""
        raw_metadata = row.get("metadata") or {}
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = orjson.loads(raw_metadata)
            except Exception:
                raw_metadata = {}
        entities = raw_metadata.get("entities", [])
        if not isinstance(entities, list):
            entities = []
        same_entity = any(str(entity).casefold() == subject_key for entity in entities)
        if not same_entity:
            same_entity = subject_key in str(row.get("content", "")).casefold()
        if not same_entity:
            return None

        prior_polarity = self._content_polarity(str(row.get("content", "")))
        try:
            raw_valence = row.get("valence")
            prior_valence = float(raw_valence) if raw_valence is not None else None
        except (TypeError, ValueError):
            prior_valence = None
        polarity_conflict = (
            current_polarity != 0
            and prior_polarity != 0
            and current_polarity != prior_polarity
        )
        valence_conflict = (
            current_valence is not None
            and prior_valence is not None
            and abs(current_valence) >= 0.2
            and abs(prior_valence) >= 0.2
            and (current_valence > 0) != (prior_valence > 0)
        )
        if polarity_conflict or valence_conflict:
            result = dict(row)
            result["metadata"] = raw_metadata
            return result
        return None

    async def find_contradiction(
        self, content: str, subject: str, valence: float | None = None
    ) -> dict[str, Any] | None:
        """Find the newest same-subject memory with opposing evidence.

        The method only identifies and returns a prior record. The caller owns
        the write and records its id in ``contradicts_id``; the old record is
        never overwritten. Exact entity metadata is preferred, with a bounded
        content fallback for legacy rows that predate entity linking.
        """
        if not content or not subject:
            return None

        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    rows = await conn.fetch(
                        "SELECT * FROM memories WHERE content <> ? "
                        "AND (lower(content) LIKE lower(?) OR lower(metadata) LIKE lower(?)) "
                        "ORDER BY created_at DESC LIMIT ?",
                        content,
                        f"%{subject}%",
                        f"%{subject}%",
                        100,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM memories WHERE content <> $1 "
                        "AND (content ILIKE $2 OR metadata::text ILIKE $2) "
                        "ORDER BY created_at DESC LIMIT 100",
                        content,
                        f"%{subject}%",
                    )
        except Exception as exc:
            logger.debug("Contradiction lookup failed: %s", exc)
            return None

        subject_key = str(subject).casefold()
        current_polarity = self._content_polarity(content)
        try:
            current_valence = float(valence) if valence is not None else None
        except (TypeError, ValueError):
            current_valence = None

        for row in rows:
            match = self._check_row_contradiction(
                row, subject_key, current_polarity, current_valence
            )
            if match is not None:
                return match
        return None

    async def add_memory(
        self,
        content,
        raw_content=None,
        wing="personal",
        room=None,
        importance=0.5,
        emotion=0.0,
        valence=0.0,
        certainty=1.0,
        source="user",
        metadata=None,
        lifespan_stage=None,
        crisis=None,
        virtue=None,
        relations=None,
        relation_circles=None,
        modality=None,
        current_time=None,
        embedding=None,
        speaker=None,
        record_type="episode",
        valid_from=None,
        valid_until=None,
        contradicts_id=None,
        raw_event=None,
    ):
        """Adds a new memory with ACT-R metadata and hierarchical scope.

        embedding: optional pre-computed vector (P4-12, roadmap leftovers
        Item 1). When provided, skips the internal get_embedding() call --
        lets a caller batch embeddings across many memories (via
        get_embeddings()) and hand each one in, rather than this method
        embedding one-at-a-time in a loop. Default None preserves every
        existing caller's behavior byte-for-byte.
        """
        try:
            import uuid

            # Generate a single UUID for both stores to ensure correlation
            memory_id = str(uuid.uuid4())
            raw_val = raw_content or content

            # Reinforce-on-repeat: an identical statement in the same wing should
            # strengthen the existing trace, not mint a duplicate. Human memory
            # consolidates repetition; duplicating it would inflate retrieval
            # with near-identical rows and distort ACT-R frequency counts. Check
            # before the expensive embedding + graph pre-linking work, and skip
            # both when it is a repeat.
            async with self.pool.acquire() as conn:
                existing_id = await self._find_existing_memory(conn, content, wing)
                if existing_id is not None:
                    await self._reinforce_memory(
                        conn, existing_id, importance, current_time
                    )
                    self._invalidate_l1_cache()
                    # Repetition strengthens word associations too (guarded).
                    await self.lexicon.learn_from_text(content)
                    logger.info(
                        f"🧠 Memory Reinforced [{wing}:{room or 'global'}]: {content[:50]}..."
                    )
                    return True

            # Provenance defaults to the actual event speaker when a caller has
            # one. Explicit `speaker` remains authoritative for consolidated or
            # imported records that do not have a raw event envelope.
            if speaker is None and isinstance(raw_event, dict):
                speaker = raw_event.get("user_id")

            # Pre-link entities from graph to metadata
            present_entities = await self._prelink_memory_entities(content)

            if metadata is None:
                metadata = {}
            metadata["entities"] = present_entities

            if contradicts_id is None:
                for entity in present_entities:
                    contradiction = await self.find_contradiction(
                        content, entity, valence=valence
                    )
                    if contradiction:
                        contradicts_id = contradiction.get("id")
                        break

            vector = (
                embedding
                if embedding is not None
                else await self.get_embedding(content)
            )
            if not vector:
                return False

            vector_str = str(vector)
            async with self.pool.acquire() as conn:
                await self._insert_memory_row(
                    conn,
                    memory_id=memory_id,
                    content=content,
                    raw_val=raw_val,
                    wing=wing,
                    room=room,
                    vector_str=vector_str,
                    importance=importance,
                    emotion=emotion,
                    valence=valence,
                    certainty=certainty,
                    source=source,
                    speaker=speaker,
                    record_type=record_type,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    contradicts_id=contradicts_id,
                    metadata_json=orjson.dumps(metadata or {}).decode(),
                    lifespan_stage=lifespan_stage,
                    crisis=crisis,
                    virtue=virtue,
                    relations=relations,
                    relation_circles=relation_circles,
                    modality=modality,
                    current_time=current_time,
                )
            # Upsert into Qdrant if online using the same memory_id
            await self._upsert_qdrant_memory(
                memory_id=memory_id,
                vector=vector,
                content=content,
                wing=wing,
                room=room,
                importance=importance,
                emotion=emotion,
                valence=valence,
                certainty=certainty,
                source=source,
                speaker=speaker,
                record_type=record_type,
                valid_from=valid_from,
                valid_until=valid_until,
                contradicts_id=contradicts_id,
                lifespan_stage=lifespan_stage,
                crisis=crisis,
                virtue=virtue,
                relations=relations,
                relation_circles=relation_circles,
                modality=modality,
                present_entities=present_entities,
                metadata=metadata,
                current_time=current_time,
            )

            logger.info(
                f"🧠 Memory Stored [{wing}:{room or 'global'}]: {content[:50]}..."
            )
            # A new memory can satisfy queries whose cached result sets predate
            # it, so drop the L1 cache to avoid serving stale recalls.
            self._invalidate_l1_cache()
            # Acquire vocabulary + co-occurrence associations from the stored
            # content (guarded internally so it can never fail the write).
            await self.lexicon.learn_from_text(content)
            return True
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            return False

    async def _update_dynamic_stop_words(self):
        """Fetches high-frequency words from active memories to use as stop words."""
        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    # SQLite fallback: since SQLite doesn't have regexp_split_to_table,
                    # we can just fetch content and count in Python
                    rows = await conn.fetch("SELECT content FROM memories LIMIT 500")
                    words = []
                    for r in rows:
                        words.extend(re.findall(r"\b\w{3,}\b", r["content"].lower()))
                    counter = Counter(words)
                    # Any word appearing in more than 15% of records
                    cutoff = max(5, int(len(rows) * 0.15))
                    self._db_stop_words = {
                        w for w, count in counter.items() if count > cutoff
                    }
                else:
                    # Postgres pgvector: fetch words appearing in > 10% of memories
                    total = await conn.fetchval("SELECT count(*) FROM memories")
                    if total and total > 50:
                        cutoff = max(20, int(total * 0.10))
                        rows = await conn.fetch(
                            """
                            SELECT word, count(*) as cnt
                            FROM (
                                SELECT regexp_replace(regexp_split_to_table(lower(content), '\\s+'), '[^\\w]', '', 'g') AS word
                                FROM memories
                            ) AS words
                            WHERE length(word) >= 3
                            GROUP BY word
                            HAVING count(*) > $1
                            ORDER BY cnt DESC
                            LIMIT 50
                            """,
                            cutoff,
                        )
                        self._db_stop_words = {r["word"] for r in rows}
                    else:
                        self._db_stop_words = set()
        except Exception as e:
            logger.debug("Failed to load dynamic stop words: %s", e)
            self._db_stop_words = set()

    # ------------------------------------------------------------------
    # search_memories stages (F1)
    #
    # search_memories was a ~1600-line god-function fusing L1 caching, Qdrant
    # retrieval, two SQL dialects, cue extraction, graph building, pronoun
    # resolution, PageRank spreading activation, archive promotion and result
    # formatting into one body. The stages below are that same pipeline, split
    # at its natural seams so each piece can be read and tested on its own.
    # Behavior is intentionally unchanged - the scoring math, ordering and
    # error-swallowing semantics are preserved exactly.
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_mrl_gating(
        current_arousal: float,
        current_cortisol: float,
        limit,
        full_pool: bool,
    ) -> tuple[int, int]:
        """Dynamic Matryoshka (MRL) dimension gating.

        Higher stress/arousal restricts the search to a smaller Matryoshka
        prefix and a smaller candidate pool, bounding retrieval latency when
        the agent is under load.

        `full_pool` picks between the two unstressed tiers: the full pool a
        conversation turn gets (`limit*6`, floor 120) and the cheaper one a
        latency-sensitive caller gets (`limit*3`, floor 20). It is named for
        what it selects. `search_memories` has historically passed
        `refresh_on_recall` here because its two latency-sensitive callers --
        the `action.py` fallback and `surfacing_agent` -- also happen not to
        want recall counters bumped, but those are separate properties and a
        caller that needs one without the other says so explicitly.
        """
        stress_index = max(current_arousal, current_cortisol)
        if stress_index > 0.8:
            return 256, max(10, limit * 2 if limit is not None else 10)
        if stress_index > 0.6:
            return 512, max(30, limit * 3 if limit is not None else 30)
        if full_pool:
            return 768, max(120, limit * 6 if limit is not None else 120)
        return 768, max(20, limit * 3 if limit is not None else 20)

    def _detect_topic_shift(self, query_vector: list) -> None:
        """Flush the goal buffer when the query diverges sharply from the last one."""
        if self._last_query_vector is not None:
            try:
                dot = sum(a * b for a, b in zip(query_vector, self._last_query_vector))
                norm1 = math.sqrt(sum(a * a for a in query_vector))
                norm2 = math.sqrt(sum(b * b for b in self._last_query_vector))
                sim = dot / (norm1 * norm2) if norm1 > 1e-9 and norm2 > 1e-9 else 1.0
                if sim < 0.15:
                    logger.info(
                        f"🔄 Topic Shift Detected (similarity {sim:.3f} < 0.15). Flushing Goal Buffer."
                    )
                    self.goal_buffer.flush()
            except Exception as ts_err:
                logger.debug("Topic-shift calculation failed: %s", ts_err)
        self._last_query_vector = query_vector

    async def _gather_candidate_sources(
        self, mrl_query_vector, candidate_limit, query_text, user_id
    ):
        """Fetch vector candidates and the query-scoped graph context.

        The graph query starts from entity names mentioned by the current
        query, then expands one hop to include the nodes needed for PPR. It
        never truncates an unordered corpus-wide result set, so a relevant
        entity cannot disappear merely because the graph grew past a fixed
        application constant.
        """

        async def safe_qdrant_search():
            try:
                if self.qdrant_store.client:
                    return await asyncio.to_thread(
                        self.qdrant_store.search_vector_memories,
                        query_vector=mrl_query_vector,
                        limit=candidate_limit,
                    )
            except Exception as qe:
                logger.error(f"Qdrant retrieval failed: {qe}")
            return []

        async def _dummy_graph():
            return [], []

        async def fetch_graph_context():
            if not self.graph_db:
                return await _dummy_graph()

            query_words = re.findall(r"\b\w+\b", query_text.lower())
            query_terms = sorted(
                {
                    token
                    for token in query_words
                    if len(token) >= 3
                    if token not in SEARCH_STOP_WORDS
                }
            )
            has_pronoun = bool(
                set(query_words) & (FIRST_PERSON_PRONOUNS | SECOND_PERSON_PRONOUNS)
            )
            identity_names = [
                identity
                for identity in (getattr(Config, "AI_NAME", None), user_id)
                if isinstance(identity, str) and identity.strip()
            ]
            entity_records = await self.graph_db.execute_query(
                "MATCH (seed:Entity) "
                "WHERE seed.name IS NOT NULL AND (any(term IN $query_terms "
                "WHERE toLower(seed.name) CONTAINS term "
                "OR term CONTAINS toLower(seed.name)) "
                "OR toLower($query_text) CONTAINS toLower(seed.name) "
                "OR ($has_pronoun AND any(identity IN $identity_names "
                "WHERE toLower(seed.name) = toLower(identity)))) "
                "OPTIONAL MATCH (seed)-[]-(neighbor:Entity) "
                "WITH collect(seed) + collect(neighbor) AS nodes "
                "UNWIND [node IN nodes WHERE node IS NOT NULL AND node.name IS NOT NULL] AS e "
                "WITH DISTINCT e "
                "RETURN e.name AS name, e.description AS description",
                {
                    "query_terms": query_terms,
                    "query_text": query_text,
                    "has_pronoun": has_pronoun,
                    "identity_names": identity_names,
                },
                use_cache=True,
            )
            # `Mapping`, not `dict`: `GraphDB.execute_query` returns raw
            # neo4j `Record`s, which are `tuple` + `Mapping` subclasses and
            # never `dict`. Filtering on `dict` discarded every real row, so
            # this relation query never ran and PPR only ever saw
            # co-occurrence edges (tests passed because they mock dicts).
            entity_names = [
                row.get("name")
                for row in entity_records
                if isinstance(row, Mapping) and row.get("name")
            ]
            if not entity_names:
                return entity_records, []

            relation_records = await self.graph_db.execute_query(
                "MATCH (s:Entity)-[r]-(t:Entity) "
                "WHERE s.name IN $entity_names OR t.name IN $entity_names "
                "RETURN s.name AS source, t.name AS target",
                {"entity_names": entity_names},
                use_cache=True,
            )
            return entity_records, relation_records

        candidates, graph_context = await asyncio.gather(
            safe_qdrant_search(), fetch_graph_context()
        )
        entity_records, relation_records = graph_context

        # Defensive type checks
        if not isinstance(entity_records, list):
            entity_records = []
        if not isinstance(relation_records, list):
            relation_records = []
        return candidates, entity_records, relation_records

    @staticmethod
    def _db_or_meta(db_meta, meta: dict, key: str, default):
        """The SQL row wins over the Qdrant payload where present -- Qdrant
        payloads can lag it (recall_count/last_recalled_at move on every
        recall)."""
        if db_meta and db_meta.get(key) is not None:
            return db_meta.get(key)
        return meta.get(key, default)

    @staticmethod
    def _parse_qdrant_created_at(created_val, current_time):
        """Two write paths feed this field two different shapes: `_upsert_
        qdrant_memory` writes a numeric epoch string (`str(timestamp())`),
        but `_build_promotion_payload` (a promoted archived row) writes
        `created_at.isoformat()` -- an ISO-8601 string. Without the second
        branch below, `float(created_val)` raises on every promoted memory,
        silently falling through to `current_time`/`now()` and losing its
        real creation timestamp -- which corrupts `_spacing_hours` for
        exactly the memories old enough to have been promoted at all."""
        if created_val:
            try:
                return datetime.fromtimestamp(float(created_val), UTC)
            except (TypeError, ValueError):
                pass
            parsed = MemoryStore._as_aware_utc(created_val)
            if parsed is not None:
                return parsed
        return current_time if current_time is not None else clock.now(UTC)

    def _score_one_qdrant_candidate(
        self,
        cand,
        db_metadata: dict,
        *,
        wing,
        room,
        excluded,
        threshold,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
        now_ts,
    ) -> dict | None:
        """ACT-R activation + neuromodulatory gating for one Qdrant hit.
        Returns None for anything filtered out (wrong wing/room, excluded
        content, or below the loose pre-threshold)."""
        meta = cand["metadata"]
        c_wing = meta.get("wing")
        c_room = meta.get("room")
        if wing is not None and c_wing != wing:
            return None
        if room is not None and c_room != room:
            return None

        c_content = cand["content"]
        if c_content in excluded:
            return None

        memory_id = cand["id"]
        db_meta = db_metadata.get(str(memory_id))

        memory_valence = self._db_or_meta(db_meta, meta, "valence", 0.0)
        emotion_weight_row = self._db_or_meta(db_meta, meta, "emotional_weight", 0.0)
        importance_score = self._db_or_meta(db_meta, meta, "importance_score", 0.5)
        recall_count = max(1, self._db_or_meta(db_meta, meta, "recall_count", 1))

        last_recall_time = self._coerce_last_recall_ts(db_meta, meta, now_ts)
        hours_since = max(0.001, (now_ts - last_recall_time) / 3600.0)
        last_recall_dt = datetime.fromtimestamp(last_recall_time, UTC)
        created = self._parse_qdrant_created_at(meta.get("created_at"), current_time)

        # 2D/3D Emotional Distance matching the research simulator
        dist_emo = math.sqrt(
            (memory_valence - current_valence) ** 2
            + (emotion_weight_row - current_arousal) ** 2
        )

        spacing_hours = self._spacing_hours(recall_count, created, last_recall_dt)
        base_activation = self._base_activation(
            recall_count, hours_since, importance_score, dist_emo, spacing_hours
        )

        similarity = cand["score"]
        effective_similarity = self._effective_similarity(
            similarity,
            memory_valence,
            emotion_weight_row,
            current_arousal,
            current_cortisol,
        )

        spread_activation = self.spread_weight * effective_similarity
        score = (
            base_activation + spread_activation - ACTR_EMO_DISTANCE_PENALTY * dist_emo
        )

        if score <= (threshold - 2.5) and importance_score < 0.7:
            return None

        custom_metadata = {}
        if "custom_metadata" in meta:
            try:
                custom_metadata = orjson.loads(meta["custom_metadata"])
            except Exception:
                pass  # nosec B110 - malformed/non-JSON custom_metadata degrades to {} regardless of cause

        return {
            "id": memory_id,
            "content": c_content,
            "raw_content": c_content,
            "wing": c_wing or "personal",
            "room": c_room,
            "score": score,
            "valence": memory_valence,
            "created_at": created,
            "recall_count": recall_count,
            "metadata": custom_metadata,
            "speaker": meta.get("speaker"),
            "record_type": meta.get("record_type") or "episode",
            "valid_from": meta.get("valid_from"),
            "valid_until": meta.get("valid_until"),
            "contradicts_id": meta.get("contradicts_id"),
            "lifespan_stage": meta.get("lifespan_stage"),
            "crisis": meta.get("crisis"),
            "virtue": meta.get("virtue"),
            "relations": meta.get("relations"),
            "relation_circles": meta.get("relation_circles"),
            "modality": meta.get("modality"),
            "similarity": similarity,
            "last_recalled_at": last_recall_dt,
            "importance_score": importance_score,
        }

    async def _score_qdrant_candidates(
        self,
        candidates,
        *,
        wing,
        room,
        excluded,
        threshold,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
        now_ts,
    ) -> list:
        """Score Qdrant vector hits with ACT-R activation + neuromodulatory gating."""
        raw_candidates = []
        try:
            db_metadata = await self._fetch_candidate_db_metadata(candidates)

            for cand in candidates:
                scored = self._score_one_qdrant_candidate(
                    cand,
                    db_metadata,
                    wing=wing,
                    room=room,
                    excluded=excluded,
                    threshold=threshold,
                    current_valence=current_valence,
                    current_arousal=current_arousal,
                    current_cortisol=current_cortisol,
                    current_time=current_time,
                    now_ts=now_ts,
                )
                if scored is not None:
                    raw_candidates.append(scored)
        except Exception as qe:
            logger.error(f"Qdrant retrieval failed, falling back to database: {qe}")
        return raw_candidates

    async def _fetch_candidate_db_metadata(self, candidates) -> dict:
        """Load the authoritative SQL metadata for a set of Qdrant candidates.

        Qdrant payloads can lag the SQL row (recall_count/last_recalled_at move
        on every recall), so the DB copy wins where present.
        """
        db_metadata = {}
        try:
            cand_ids = [c["id"] for c in candidates if c.get("id")]
            if cand_ids:
                async with self.pool.acquire() as conn:
                    where, args = self._in_predicate("id", cand_ids)
                    rows = await conn.fetch(
                        "SELECT id, importance_score, emotional_weight, valence, "
                        f"recall_count, last_recalled_at FROM memories WHERE {where}",  # nosec B608 - where is a generated ?/$n predicate from _in_predicate; args are bound
                        *args,
                    )
                    for r in rows:
                        db_metadata[str(r["id"])] = r
        except Exception as db_err:
            logger.warning(
                f"Failed to fetch updated memory metadata from SQL DB for Qdrant candidates: {db_err}"
            )
        return db_metadata

    @staticmethod
    def _coerce_last_recall_ts(db_meta, meta, now_ts) -> float:
        """Normalize last_recalled_at (datetime | epoch | ISO string) to a float epoch."""
        try:
            if db_meta and db_meta.get("last_recalled_at"):
                last_recall_time = db_meta.get("last_recalled_at")
                if isinstance(last_recall_time, (int, float)):
                    return float(last_recall_time)
                if hasattr(last_recall_time, "timestamp"):
                    return last_recall_time.timestamp()
                dt = datetime.fromisoformat(str(last_recall_time).replace(" ", "T"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.timestamp()
            return float(meta.get("last_recalled_at", now_ts))
        except (ValueError, TypeError):
            return now_ts

    async def _fetch_sqlite_candidates(
        self,
        conn,
        *,
        query_vector,
        wing,
        room,
        excluded,
        threshold,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
        candidate_limit,
    ) -> tuple[list, float]:
        """SQLite fallback: fetch rows and score them via the Rust ACT-R kernel.

        Returns the candidates plus the `now_ts` it computed, because the
        original inlined body rebound the enclosing now_ts here and that value
        is what later stamps the L1 cache entry.

        audit/ROADMAP.md P2-6 (M2-P3): this used to `SELECT *` with no
        `LIMIT` -- a full-table scan on every cache miss, unlike its Postgres
        sibling (`_fetch_postgres_candidates`), which already receives and
        applies `candidate_limit`. `embedding` stays in the projection
        despite M2-P3's "excludes embedding where unused" suggestion --
        SQLite has no pgvector, so `cognitive_rust.score_memories_actr_sqlite`
        computes cosine similarity from this column in Rust; dropping it
        would silently zero out every candidate's similarity, not just save
        bytes. `ORDER BY last_recalled_at DESC` biases a hard cap toward
        recently-relevant memories rather than an arbitrary rowid-order
        slice; SQLite sorts NULL as smallest, so never-recalled rows land
        last under DESC, which is the right side of the cut to lose first.
        """
        if room is not None:
            rows = await conn.fetch(
                "SELECT * FROM memories WHERE wing = ? AND room = ? "
                "ORDER BY last_recalled_at DESC LIMIT ?",
                wing,
                room,
                candidate_limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM memories WHERE wing = ? "
                "ORDER BY last_recalled_at DESC LIMIT ?",
                wing,
                candidate_limit,
            )

        # Manual cosine similarity and ACT-R scoring (delegated to Rust PyO3).
        # Imported lazily: the compiled extension is optional in some envs and
        # a module-level import would break importing MemoryStore entirely.
        import cognitive_rust

        now = current_time if current_time is not None else clock.now(UTC)
        now_ts = now.timestamp()

        # Preprocess timestamps for Rust. `_created_ts` (Bucket 9, voice
        # remediation Phase 3) feeds the Rust kernel's spacing-effect term --
        # `_normalize_recall_ts` is generic despite its name (any stored
        # timestamp shape to a float epoch), so it is reused here rather than
        # duplicated for a second field.
        for row in rows:
            row["_last_recall_ts"] = self._normalize_recall_ts(
                row.get("last_recalled_at"), now_ts
            )
            row["_created_ts"] = self._normalize_recall_ts(
                row.get("created_at"), now_ts
            )

        scored_indices = cognitive_rust.score_memories_actr_sqlite(
            query_vector,
            rows,
            excluded,
            current_valence,
            current_arousal,
            current_cortisol,
            self.decay_rate,
            self.spread_weight,
            threshold,
            now_ts,
        )

        raw_candidates = []
        for idx, score, similarity in scored_indices:
            row = rows[idx]
            last_recall = row.get("last_recalled_at")
            if last_recall is None:
                last_recall = now
            elif isinstance(last_recall, str):
                try:
                    last_recall = datetime.fromisoformat(last_recall)
                except Exception:
                    last_recall = now
            if last_recall.tzinfo is None:
                last_recall = last_recall.replace(tzinfo=UTC)

            created = row.get("created_at")
            if isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except Exception:
                    created = now
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=UTC)

            raw_meta = row.get("metadata")
            if isinstance(raw_meta, str):
                try:
                    raw_meta = orjson.loads(raw_meta)
                except Exception:
                    raw_meta = {}

            raw_candidates.append(
                {
                    "id": row.get("id"),
                    "content": row["content"],
                    "raw_content": row.get("raw_content") or row["content"],
                    "wing": row.get("wing", "personal"),
                    "room": row.get("room"),
                    "score": score,
                    "valence": row.get("valence") or 0.0,
                    "created_at": created,
                    "recall_count": max(1, row.get("recall_count") or 1),
                    "metadata": raw_meta or {},
                    "speaker": row.get("speaker"),
                    "record_type": row.get("record_type") or "episode",
                    "valid_from": row.get("valid_from"),
                    "valid_until": row.get("valid_until"),
                    "contradicts_id": row.get("contradicts_id"),
                    "lifespan_stage": row.get("lifespan_stage"),
                    "crisis": row.get("crisis"),
                    "virtue": row.get("virtue"),
                    "relations": row.get("relations"),
                    "relation_circles": row.get("relation_circles"),
                    "modality": row.get("modality"),
                    "similarity": similarity,
                    "last_recalled_at": last_recall,
                    "importance_score": row.get("importance_score"),
                }
            )
        return raw_candidates, now_ts

    @staticmethod
    def _normalize_recall_ts(last_recall, now_ts: float) -> float:
        """Coerce a row's last_recalled_at into a float epoch for the Rust kernel."""
        if last_recall is None:
            return now_ts
        if isinstance(last_recall, datetime):
            return last_recall.timestamp()
        if isinstance(last_recall, (int, float)):
            return float(last_recall)
        if isinstance(last_recall, str):
            try:
                return float(last_recall)
            except ValueError:
                try:
                    dt = datetime.fromisoformat(last_recall.replace(" ", "T"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt.timestamp()
                except Exception:
                    return now_ts
        return now_ts

    def _build_candidate_from_row(
        self,
        row: dict[str, Any],
        now: datetime,
        current_valence: float,
        current_arousal: float,
        current_cortisol: float,
        threshold: float,
    ) -> dict[str, Any] | None:
        """Compute ACT-R score and return candidate dictionary if above threshold."""
        similarity = row.get("similarity") or 0.0
        recall_count = max(1, row.get("recall_count") or 1)

        last_recall = row.get("last_recalled_at")
        last_recall = self._as_aware_utc(last_recall) or now
        hours_since = max(0.001, (now - last_recall).total_seconds() / 3600.0)

        # _as_aware_utc, not a bare `.tzinfo` check: the SQLite converter
        # returns text for an unparseable stored value, and a string here
        # used to raise AttributeError and fail the whole search.
        created = self._as_aware_utc(row.get("created_at"))

        memory_valence = row.get("valence") or 0.0
        emotion_weight_row = row.get("emotional_weight") or 0.0

        dist_emo = math.sqrt(
            (memory_valence - current_valence) ** 2
            + (emotion_weight_row - current_arousal) ** 2
        )

        spacing_hours = self._spacing_hours(recall_count, created, last_recall)
        base_activation = self._base_activation(
            recall_count,
            hours_since,
            row.get("importance_score") or 0.5,
            dist_emo,
            spacing_hours,
        )

        effective_similarity = self._effective_similarity(
            similarity,
            memory_valence,
            emotion_weight_row,
            current_arousal,
            current_cortisol,
        )

        spread_activation = self.spread_weight * effective_similarity
        score = (
            base_activation + spread_activation - ACTR_EMO_DISTANCE_PENALTY * dist_emo
        )

        if score <= (threshold - 2.5) and (row.get("importance_score") or 0.5) < 0.7:
            return None

        raw_meta = row.get("metadata")
        if isinstance(raw_meta, str):
            try:
                raw_meta = orjson.loads(raw_meta)
            except Exception:
                raw_meta = {}

        return {
            "id": row.get("id"),
            "content": row["content"],
            "raw_content": row.get("raw_content") or row["content"],
            "wing": row.get("wing", "personal"),
            "room": row.get("room"),
            "score": score,
            "valence": row.get("valence") or 0.0,
            "created_at": created,
            "recall_count": recall_count,
            "metadata": raw_meta or {},
            "speaker": row.get("speaker"),
            "record_type": row.get("record_type") or "episode",
            "valid_from": row.get("valid_from"),
            "valid_until": row.get("valid_until"),
            "contradicts_id": row.get("contradicts_id"),
            "lifespan_stage": row.get("lifespan_stage"),
            "crisis": row.get("crisis"),
            "virtue": row.get("virtue"),
            "relations": row.get("relations"),
            "relation_circles": row.get("relation_circles"),
            "modality": row.get("modality"),
            "similarity": similarity,
            "last_recalled_at": last_recall,
            "importance_score": row.get("importance_score"),
        }

    async def _fetch_postgres_candidates(
        self,
        conn,
        *,
        vector_str,
        wing,
        room,
        excluded,
        threshold,
        candidate_limit,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
    ) -> list:
        """PostgreSQL fast path via the surface_actr_memories() vector procedure."""
        rows = await self._fetch_surface_actr_rows(
            conn,
            vector_str=vector_str,
            wing=wing,
            room=room,
            threshold=threshold,
            candidate_limit=candidate_limit,
            current_valence=current_valence,
            current_arousal=current_arousal,
            current_cortisol=current_cortisol,
            current_time=current_time,
        )

        raw_candidates = []
        now = (
            self._as_aware_utc(current_time)
            if current_time is not None
            else clock.now(UTC)
        )
        for row in rows:
            if row["content"] in excluded:
                continue
            cand = self._build_candidate_from_row(
                row,
                now,
                current_valence,
                current_arousal,
                current_cortisol,
                threshold,
            )
            if cand is not None:
                raw_candidates.append(cand)
        return raw_candidates

    async def _fetch_surface_actr_rows(
        self,
        conn,
        *,
        vector_str,
        wing,
        room,
        threshold,
        candidate_limit,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
    ):
        """Call surface_actr_memories(), falling back to the pre-Eriksonian schema.

        The two queries take identical arguments and differ only in whether the
        Eriksonian lifespan columns are selected from the JOINed memories row,
        so the argument tuple is built once.
        """
        args = (
            vector_str,
            wing,
            room,
            self.decay_rate,
            self.spread_weight,
            self.emotion_weight,
            current_valence,
            current_arousal,
            current_cortisol,
            threshold - 2.5,
            candidate_limit,
            current_time,
        )
        try:
            return await conn.fetch(_SURFACE_ACTR_SQL_ERIKSONIAN, *args)
        except Exception as pg_err:
            logger.warning(
                f"Eriksonian JOIN pg query failed, falling back to legacy schema: {pg_err}"
            )
            return await conn.fetch(_SURFACE_ACTR_SQL_LEGACY, *args)

    def _resolve_dynamic_stop_words(self, user_id) -> set:
        """Static stop words plus DB-learned ones and the agent/user proper nouns.

        The agent's own name and the speaker's name are suppressed as cues
        because they appear in nearly every memory and would otherwise boost
        everything uniformly.
        """
        dynamic_stop_words = set(SEARCH_STOP_WORDS)
        if getattr(self, "_db_stop_words", None):
            dynamic_stop_words.update(self._db_stop_words)
        ai_name_cfg = getattr(Config, "AI_NAME", None)
        if ai_name_cfg:
            for w in re.findall(r"\b\w{3,}\b", ai_name_cfg.lower()):
                dynamic_stop_words.add(w)
        if user_id:
            for w in re.findall(r"\b\w{3,}\b", user_id.lower()):
                dynamic_stop_words.add(w)
        return dynamic_stop_words

    @staticmethod
    def _compile_entity_pattern(entity_names) -> re.Pattern | None:
        """One compiled alternation over every known entity name.

        audit/ROADMAP.md P2-3 (M2-P1): three call sites each used to build
        and search a fresh `\bname\b` pattern per (candidate, entity) pair
        -- an uncompiled regex re-created O(candidates x entities) times per
        `search_memories` call. A single compiled alternation, searched once
        per candidate via `finditer`, finds the same set of whole-word
        matches in one pass. Longest names first so that if two entity names
        were ever identical after lowercasing (not expected, but not
        enforced anywhere either), the longer alternative is preferred --
        `\b` boundaries alone already prevent a *shorter* name matching
        inside a longer one that merely contains it (e.g. "Sam" cannot match
        inside "Samantha": there is no word boundary between them).
        """
        if not entity_names:
            return None
        escaped = sorted(
            (re.escape(n.lower()) for n in entity_names), key=len, reverse=True
        )
        return re.compile(r"\b(?:" + "|".join(escaped) + r")\b")

    @staticmethod
    def _compute_candidate_entities(raw_candidates, entity_names) -> dict:
        """Base candidate-index -> mentioned-entity-names mapping.

        Computed once and shared by `_build_entity_graph` (co-occurrence
        edges), `_collect_ppr_seeds` (seed fallback) and the first-person
        pronoun layer `_apply_ppr_spreading_activation` adds once
        `agent_node_name` is known (not yet resolved at this point -- see
        the call site). A candidate's own `metadata["entities"]` wins when
        present (`add_memory` precomputes it); only candidates without that
        fall back to scanning content with the compiled pattern below.
        """
        pattern = MemoryStore._compile_entity_pattern(entity_names)
        lower_to_name = {n.lower(): n for n in entity_names}

        cand_entities: dict[int, set] = {}
        for idx, cand in enumerate(raw_candidates):
            payload_meta = cand.get("metadata") or {}
            meta_entities = payload_meta.get("entities")
            if isinstance(meta_entities, list) and meta_entities:
                cand_entities[idx] = set(meta_entities)
                continue
            if pattern is None:
                cand_entities[idx] = set()
                continue
            content_lower = cand["content"].lower()
            cand_entities[idx] = {
                lower_to_name[m.group(0)]
                for m in pattern.finditer(content_lower)
                if m.group(0) in lower_to_name
            }
        return cand_entities

    @staticmethod
    def _build_entity_graph(entity_records, relation_records, raw_candidates):
        """Build the entity list, co-occurrence adjacency, and base
        candidate->entities mapping used by PPR.

        Edges come from Neo4j relations plus entity co-occurrence within each
        candidate memory.
        """
        entity_names = [r["name"] for r in entity_records]
        adj = {}

        for r in relation_records:
            src = r["source"]
            tgt = r["target"]
            adj.setdefault(src, set()).add(tgt)
            adj.setdefault(tgt, set()).add(src)

        cand_entities = MemoryStore._compute_candidate_entities(
            raw_candidates, entity_names
        )

        # Add co-occurrence connections from candidate memories
        for ents in cand_entities.values():
            ents = list(ents)
            for i in range(len(ents)):
                for j in range(i + 1, len(ents)):
                    e1 = ents[i]
                    e2 = ents[j]
                    adj.setdefault(e1, set()).add(e2)
                    adj.setdefault(e2, set()).add(e1)

        return entity_names, adj, cand_entities

    @staticmethod
    def _resolve_agent_node_name(entity_records, entity_names) -> str:
        for r in entity_records:
            desc = r.get("description") or ""
            if "central cognitive system" in desc.lower():
                return r["name"]
        ai_name = getattr(Config, "AI_NAME", "AI Friend")
        for name in entity_names:
            if name.lower() == ai_name.lower():
                return name
        return ai_name

    @staticmethod
    def _resolve_user_node_name(
        entity_records, entity_names, adj, user_id, agent_node_name: str
    ) -> str:
        if user_id:
            for name in entity_names:
                if name.lower() == user_id.lower():
                    return name

        for r in entity_records:
            desc = r.get("description") or ""
            if (
                "user" in desc.lower()
                or "companion" in desc.lower()
                or "friend" in desc.lower()
            ) and r["name"] != agent_node_name:
                return r["name"]

        if entity_names:
            ai_names = {"ai friend", "my friend", agent_node_name.lower()}
            if getattr(Config, "AI_NAME", None):
                ai_names.add(Config.AI_NAME.lower())
            candidates_names = [
                name for name in entity_names if name.lower() not in ai_names
            ]
            if candidates_names:
                candidates_names.sort(
                    key=lambda name: len(adj.get(name, set())), reverse=True
                )
                return candidates_names[0]

        return user_id or "user"

    @classmethod
    def _resolve_identity_nodes(cls, entity_records, entity_names, adj, user_id):
        """Discover which graph nodes represent the agent and the user.

        Falls back through description text, configured AI_NAME, and finally
        the most-connected non-agent entity, so pronoun resolution still works
        on a graph that was never explicitly annotated.
        """
        # 1. Discover Agent Node Name dynamically
        agent_node_name = cls._resolve_agent_node_name(entity_records, entity_names)

        # 2. Discover User Node Name dynamically
        user_node_name = cls._resolve_user_node_name(
            entity_records, entity_names, adj, user_id, agent_node_name
        )

        return agent_node_name, user_node_name

    @staticmethod
    def _resolve_pronoun_cues(
        query_text, agent_node_name, user_node_name, user_id, is_self_reflection
    ) -> set:
        """Context-aware speaker/listener pronoun resolution.

        Who "I" and "you" refer to flips depending on whether the agent is
        reflecting on itself or the user is speaking.
        """
        query_words_all = re.findall(r"\b\w+\b", query_text.lower())
        resolved_cues = set()

        if is_self_reflection:
            # Agent speaking: "I" -> Agent, "you" -> User
            speaker, listener = agent_node_name, user_node_name
        else:
            # User speaking: "I" -> User, "you" -> Agent
            speaker, listener = user_node_name, agent_node_name

        if any(p in query_words_all for p in FIRST_PERSON_PRONOUNS) and speaker:
            resolved_cues.add(speaker.lower())
        if any(p in query_words_all for p in SECOND_PERSON_PRONOUNS) and listener:
            resolved_cues.add(listener.lower())

        # Add user/agent names if explicitly mentioned in query
        user_aliases = {"user"}
        if user_id:
            user_aliases.add(user_id.lower())
        if user_node_name:
            user_aliases.add(user_node_name.lower())

        agent_aliases = {"ai friend", "my friend"}
        if getattr(Config, "AI_NAME", None):
            agent_aliases.add(Config.AI_NAME.lower())
        if agent_node_name:
            agent_aliases.add(agent_node_name.lower())

        for word in query_words_all:
            if word in user_aliases and user_node_name:
                resolved_cues.add(user_node_name.lower())
            if word in agent_aliases and agent_node_name:
                resolved_cues.add(agent_node_name.lower())

        return resolved_cues

    @staticmethod
    def _apply_direct_cue_boost(raw_candidates, matched_cues) -> set:
        """Add DIRECT_CUE_BOOST per literal query cue found in a memory.

        Returns the indices that were boosted; PPR treats these as seeds when
        the query itself names no known entity.
        """
        direct_boosted_indices: set[int] = set()
        if not matched_cues:
            return direct_boosted_indices
        for idx, cand in enumerate(raw_candidates):
            content_lower = cand["content"].lower()
            match_count = sum(1 for mc in matched_cues if mc in content_lower)
            if match_count > 0:
                cand["score"] += DIRECT_CUE_BOOST * match_count
                direct_boosted_indices.add(idx)
        return direct_boosted_indices

    def _apply_ppr_spreading_activation(
        self,
        raw_candidates,
        entity_names,
        adj,
        matched_cues,
        direct_boosted_indices,
        agent_node_name,
        cand_entities,
    ) -> None:
        """HippoRAG-inspired Personalized PageRank spreading activation.

        Seeds are the query's entity cues (or, failing that, the entities of
        directly-cued memories), and each candidate gains a degree-scaled boost
        for the seeded entities it mentions.

        `cand_entities` is the *base* mapping from `_build_entity_graph`,
        computed before `agent_node_name` was known (see the call site in
        `search_memories`). The first-person-pronoun addition -- "I"/"me"/
        etc. count as a mention of the agent itself -- is layered on here,
        once agent_node_name is available, rather than recomputed from
        scratch the way the pre-P2-3 `_map_candidate_entities` did.

        **The layer must go on after `_collect_ppr_seeds`, not before.**
        Pre-P2-3 the pronoun attribution lived only in
        `_map_candidate_entities`, which ran *after* seeds were picked, so
        seeding never saw it -- a directly-cued memory mentioning "I" but no
        named entity produced no seeds, hence an empty PPR vector and no
        boost for anyone. Applying the layer first would silently make the
        agent node seed that case, which is a ranking change, not the
        behavior-preserving hoist this refactor is meant to be.
        """
        if not entity_names:
            return
        try:
            seeds = self._collect_ppr_seeds(
                entity_names, matched_cues, direct_boosted_indices, cand_entities
            )

            if agent_node_name:
                pronoun_pattern = re.compile(
                    r"\b(?:"
                    + "|".join(re.escape(p) for p in FIRST_PERSON_PRONOUNS)
                    + r")\b"
                )
                for idx, cand in enumerate(raw_candidates):
                    if pronoun_pattern.search(cand["content"].lower()):
                        cand_entities.setdefault(idx, set()).add(agent_node_name)

            # Compute Personalized PageRank Vector (3-iteration power method,
            # delegated to the Rust hot loop with a Python fallback).
            # PPR_DAMPING is the canonical teleport factor.
            if not seeds:
                ppr = {}
            else:
                p = self._personalized_pagerank(
                    entity_names, adj, seeds, PPR_DAMPING, 3
                )
                ppr = {entity_names[i]: p[i] for i in range(len(entity_names))}

            # Apply spreading activation boost based on PPR probability
            for idx, cand in enumerate(raw_candidates):
                if idx in direct_boosted_indices:
                    continue
                boost_sum = 0.0
                for ent in cand_entities.get(idx, ()):
                    if ent in ppr:
                        deg = len(adj.get(ent, set()))
                        # HippoRAG-inspired degree-scaled activation boost
                        boost = (1.2 * ppr[ent]) / (1.0 + math.log(max(1, deg)))
                        boost_sum += boost
                if boost_sum > 0:
                    cand["score"] += boost_sum

        except Exception as ne_err:
            logger.error(f"PPR spreading activation failed: {ne_err}")

    @staticmethod
    def _collect_ppr_seeds(
        entity_names, matched_cues, direct_boosted_indices, cand_entities
    ) -> set:
        """Pick the PPR seed entities for this query.

        Reads the shared `cand_entities` mapping (see `_build_entity_graph`)
        rather than re-scanning candidate content -- this fallback branch
        used to duplicate the same per-entity regex search a third time.
        """
        seeds = set()
        name_to_idx = {name.lower(): i for i, name in enumerate(entity_names)}

        # 1. Query cues that match entity names
        for cue in matched_cues:
            idx = name_to_idx.get(cue.lower())
            if idx is not None:
                seeds.add(idx)

        # 2. If no direct query seeds, use entities from directly cued memories
        #    (vector-guided associative recall)
        if not seeds:
            for idx in direct_boosted_indices:
                for ent in cand_entities.get(idx, ()):
                    e_idx = name_to_idx.get(ent.lower())
                    if e_idx is not None:
                        seeds.add(e_idx)
        return seeds

    def _apply_goal_buffer_boost(self, raw_candidates) -> None:
        """Prime candidates that mention concepts held in the active goal buffer."""
        active_concepts = [c[0] for c in self.goal_buffer.concepts]
        if not active_concepts:
            return
        for cand in raw_candidates:
            content_lower = cand["content"].lower()
            match_count = sum(1 for c in active_concepts if c in content_lower)
            if match_count > 0:
                w_j = 1.5 / len(active_concepts)
                boost = match_count * w_j * 1.2
                cand["score"] += boost
                logger.debug(
                    f"GoalBuffer Prime: Added +{boost:.3f} spreading activation to memory {cand.get('id') or cand.get('content', 'N/A')}"
                )

    @staticmethod
    def _format_results(raw_candidates, threshold) -> list:
        """Drop sub-threshold candidates and project them to the public shape."""
        results = []
        for cand in raw_candidates:
            if cand["score"] <= threshold:
                continue
            results.append(
                {
                    "id": cand.get("id"),
                    "content": cand["content"],
                    "raw_content": cand["raw_content"],
                    "wing": cand["wing"],
                    "room": cand["room"],
                    "score": cand["score"],
                    "valence": cand["valence"],
                    "created_at": cand["created_at"].isoformat()
                    if cand["created_at"]
                    else None,
                    "recall_count": cand["recall_count"],
                    "metadata": cand["metadata"],
                    # Eriksonian columns
                    "lifespan_stage": cand.get("lifespan_stage"),
                    "crisis": cand.get("crisis"),
                    "virtue": cand.get("virtue"),
                    "relations": cand.get("relations"),
                    "relation_circles": cand.get("relation_circles"),
                    "modality": cand.get("modality"),
                }
            )
            # `score` is not absolute relevance under either policy: hybrid
            # scores are relative to the pool (the top result scores ~2-3
            # even for an irrelevant query) and V1 scores are an unbounded
            # activation sum. `relevance` is the clipped cosine, comparable
            # across queries, and is what the surfacing agent publishes.
            if "similarity" in cand:
                results[-1]["relevance"] = _clip_relevance(cand["similarity"])
            if "score_terms" in cand:  # hybrid: why this memory ranked here
                results[-1]["score_terms"] = cand["score_terms"]
        return results

    # --- L3 sub-conscious archive search and promotion -------------------

    def _expand_archive_cues(
        self, query_words, dynamic_stop_words, matched_cues, resolved_cues, user_id
    ) -> set:
        """Build the lexical cue set used to probe archived (L3) memories.

        Unlike the active-tier cues, the speaker's own name is *kept* here: an
        archived memory is often only findable by who it was about. Cues are
        then widened by stemming and the learned mental lexicon.
        """
        archive_stop_words = set(dynamic_stop_words)
        if user_id:
            for w in re.findall(r"\b\w{3,}\b", user_id.lower()):
                archive_stop_words.discard(w)
        archive_cues = [w for w in query_words if w not in archive_stop_words]

        # Also include any resolved cues (agent name / resolved user name)
        for cue in resolved_cues:
            if cue not in archive_cues:
                archive_cues.append(cue)

        if not archive_cues:
            archive_cues = list(matched_cues)

        # Apply lexical priming/synonym expansion to cues
        expanded_cues = set()
        for cue in archive_cues:
            expanded_cues.add(cue)
            stem = _get_stem(cue)
            expanded_cues.add(stem)
            # Learned lexical priming: pull the cue's strongest acquired
            # associates (empty until the lexicon has learned them).
            expanded_cues.update(self.lexicon.expand(cue))
            expanded_cues.update(self.lexicon.expand(stem))
        return expanded_cues

    async def _fetch_archive_rows(self, expanded_cues_list, wing, vector_str) -> list:
        """Hybrid semantic + lexical lookup against archived_memories."""
        patterns = [f"%{cue}%" for cue in expanded_cues_list]
        # Fetch more candidates than needed so they can be re-ranked by keyword
        # match count and ACT-R score before the promotion cut.
        archive_limit = 250
        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    where_clause = " OR ".join(
                        "lower(content) LIKE ?" for _ in expanded_cues_list
                    )
                    query = (
                        """
                        SELECT * FROM archived_memories
                        WHERE wing = ? AND ("""
                        f"{where_clause}"  # nosec B608 - where_clause is always the literal "lower(content) LIKE ?" repeated per cue count; cue values are bound via *patterns
                        """)
                        ORDER BY importance_score DESC, last_recalled_at DESC
                        LIMIT ?
                        """
                    )
                    return await conn.fetch(query, wing, *patterns, archive_limit)
                # Postgres pgvector: hybrid semantic + lexical synonym search
                # using the HNSW index over halfvec.
                query = """
                    SELECT *, (1 - (embedding <=> $2::halfvec))::double precision AS similarity_arch
                    FROM archived_memories
                    WHERE wing = $1 AND (embedding <=> $2::halfvec < 0.45 OR content ILIKE ANY($3))
                    ORDER BY coalesce((1 - (embedding <=> $2::halfvec)), 0.0) DESC, importance_score DESC, last_recalled_at DESC
                    LIMIT $4
                """
                return await conn.fetch(
                    query, wing, vector_str, patterns, archive_limit
                )
        except Exception as arch_err:
            logger.error(f"Archived memories hybrid lookup failed: {arch_err}")
            return []

    @staticmethod
    def _parse_stored_embedding(emb_val):
        """Parse an archived embedding stored as JSON text, a vector literal, or a list."""
        if not emb_val:
            return None
        if isinstance(emb_val, list):
            return emb_val
        if isinstance(emb_val, str):
            try:
                return json.loads(emb_val)
            except Exception:
                try:
                    return [
                        float(x)
                        for x in re.findall(
                            r"[-+]?\d*\.\d+|\d+e[-+]?\d+|[-+]?\d+", emb_val
                        )
                    ]
                except Exception:
                    return None
        return None

    def _archive_row_activation(
        self,
        row,
        similarity,
        *,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
    ):
        """ACT-R activation for one archived row.

        Returns (score, spread_activation, dist_emo, recall_count) - the extra
        terms are reused when the row is promoted and rescored as active.
        """
        recall_count = max(1, row.get("recall_count") or 1)
        last_recall = row.get("last_recalled_at")
        now = (
            self._as_aware_utc(current_time)
            if current_time is not None
            else clock.now(UTC)
        )
        # `or now`: an unparseable stored timestamp must not raise here and
        # discard otherwise valid archive candidates.
        last_recall = self._as_aware_utc(last_recall) or now

        hours_since = max(0.001, (now - last_recall).total_seconds() / 3600.0)
        memory_valence = row.get("valence") or 0.0
        emotion_weight_row = row.get("emotional_weight") or 0.0

        dist_emo = math.sqrt(
            (memory_valence - current_valence) ** 2
            + (emotion_weight_row - current_arousal) ** 2
        )

        created = self._as_aware_utc(row.get("created_at"))
        spacing_hours = self._spacing_hours(recall_count, created, last_recall)
        base_activation = self._base_activation(
            recall_count,
            hours_since,
            row.get("importance_score") or 0.5,
            dist_emo,
            spacing_hours,
        )
        effective_similarity = self._effective_similarity(
            similarity,
            memory_valence,
            emotion_weight_row,
            current_arousal,
            current_cortisol,
        )
        spread_activation = self.spread_weight * effective_similarity
        score = (
            base_activation + spread_activation - ACTR_EMO_DISTANCE_PENALTY * dist_emo
        )
        return score, spread_activation, dist_emo, recall_count, now

    async def _rank_archive_rows(
        self,
        archive_rows,
        expanded_cues,
        query_vector,
        *,
        limit,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
    ) -> list:
        """Score archived rows and keep the best few for promotion."""
        scored_archive_rows = []
        for row in archive_rows:
            content = row.get("content", "")
            similarity = row.get("similarity_arch")
            if similarity is None:
                similarity = self._archive_similarity_fallback(row, query_vector)

            score, _spread, _dist, _recall, _now = self._archive_row_activation(
                row,
                similarity,
                current_valence=current_valence,
                current_arousal=current_arousal,
                current_cortisol=current_cortisol,
                current_time=current_time,
            )

            # Lexical match count boost so direct query matches sort higher
            # (HippoRAG key-relevance ranking).
            content_lower = content.lower()
            match_count = sum(1 for cue in expanded_cues if cue in content_lower)
            ranking_score = score + DIRECT_CUE_BOOST * match_count

            scored_archive_rows.append((ranking_score, score, similarity, row))

        scored_archive_rows.sort(key=lambda x: x[0], reverse=True)
        # Limit the promotion list to prevent flooding the active tier
        promote_limit = min(5, limit) if limit else 5
        return scored_archive_rows[:promote_limit]

    @staticmethod
    def _archive_similarity_fallback(row, query_vector) -> float:
        """Cosine similarity computed in Python when the DB did not supply one."""
        emb = MemoryStore._parse_stored_embedding(row.get("embedding"))
        if not emb or not query_vector:
            return 0.0
        import numpy as np

        q_arr = np.array(query_vector)
        emb_arr = np.array(emb)
        norm_q = np.linalg.norm(q_arr)
        norm_emb = np.linalg.norm(emb_arr)
        if norm_q > 0 and norm_emb > 0:
            return float(np.dot(q_arr, emb_arr) / (norm_q * norm_emb))
        return 0.0

    async def _write_promoted_memory(
        self, mem_id, content, row, emb, recall_count, now, payload_meta, sql_meta
    ):
        """Move one archived row back into the active tier (SQL + Qdrant).

        `sql_meta` is the row's own metadata envelope and `payload_meta` the
        Qdrant search payload; they are deliberately not the same object. Qdrant
        needs the denormalized scalars flattened for filtering, while the SQL
        `metadata` column is the authoritative record and must round-trip what
        `add_memory` would have written.

        Raises on failure so the caller does not report an unpersisted memory.
        On PostgreSQL the insert and the archive delete run in one transaction;
        the SQLite shim commits per statement, so there the delete is merely
        ordered after the insert. The insert is an idempotent upsert, so a
        partial failure re-converges on retry rather than duplicating.
        """
        insert_values = (
            mem_id,
            content,
            row.get("raw_content"),
            row.get("wing"),
            row.get("room"),
            str(emb),
            row.get("importance_score"),
            row.get("emotional_weight"),
            row.get("valence"),
            row.get("certainty"),
            row.get("source"),
            recall_count + 1,
            now,
            row.get("created_at"),
            json.dumps(sql_meta),
            row.get("speaker"),
            row.get("record_type") or "episode",
            row.get("valid_from"),
            row.get("valid_until"),
            row.get("contradicts_id"),
            row.get("lifespan_stage"),
            row.get("crisis"),
            row.get("virtue"),
            row.get("relations"),
            row.get("relation_circles"),
            row.get("modality"),
        )

        async def _move(conn):
            if self.is_sqlite:
                await conn.execute(_PROMOTE_INSERT_SQLITE, *insert_values)
                await conn.execute("DELETE FROM archived_memories WHERE id = ?", mem_id)
            else:
                await conn.execute(_PROMOTE_INSERT_PG, *insert_values)
                await conn.execute(
                    "DELETE FROM archived_memories WHERE id = $1", mem_id
                )

        async with self.pool.acquire() as conn:
            transaction = getattr(conn, "transaction", None)
            if not self.is_sqlite and callable(transaction):
                async with transaction():
                    await _move(conn)
            else:
                await _move(conn)

        # Upsert Qdrant. The SQL move above is the authoritative promotion and
        # has already committed: the active row exists and the archive row is
        # gone. Letting a vector-store failure propagate here would make the
        # caller skip a memory that is, in fact, promoted -- stranding it out of
        # both the returned results and the archive. Log and carry on; the
        # vector index is a search accelerator, rebuildable from SQL.
        if self.qdrant_store and self.qdrant_store.client:
            try:
                await asyncio.to_thread(
                    self.qdrant_store.add_vector_memory,
                    mem_id,
                    emb,
                    content,
                    payload_meta,
                )
            except Exception as qerr:
                logger.error(
                    f"Promoted memory {mem_id} committed to SQL but its Qdrant "
                    f"upsert failed; vector index is stale for this row: {qerr}"
                )

        logger.info(
            f"📥 [Memory Promotion] Promoted memory '{content[:40]}...' from archive back to active storage."
        )

    @staticmethod
    def _build_promotion_payload(row, raw_meta) -> dict:
        """Qdrant payload for a memory being promoted out of the archive.

        Mirrors the canonical shape `add_memory` writes: the authoritative
        columns stay sourced from the row itself, and the memory's own metadata
        goes under `custom_metadata` where the read path expects it. Splatting
        `raw_meta` at the top level (as this once did) let a stored key named
        `wing` or `room` silently overwrite the real one, and hid the custom
        fields from readers looking for the envelope.
        """
        created_at = MemoryStore._as_aware_utc(row.get("created_at"))
        return {
            "wing": row.get("wing", "personal"),
            "room": row.get("room") or "",
            "importance_score": row.get("importance_score"),
            "emotional_weight": row.get("emotional_weight"),
            "valence": row.get("valence"),
            "certainty": row.get("certainty"),
            "source": row.get("source"),
            "speaker": row.get("speaker"),
            "record_type": row.get("record_type") or "episode",
            "valid_from": (
                row.get("valid_from").isoformat()
                if isinstance(row.get("valid_from"), datetime)
                else row.get("valid_from")
            ),
            "valid_until": (
                row.get("valid_until").isoformat()
                if isinstance(row.get("valid_until"), datetime)
                else row.get("valid_until")
            ),
            "contradicts_id": row.get("contradicts_id"),
            "created_at": created_at.isoformat() if created_at else None,
            "lifespan_stage": row.get("lifespan_stage") or "",
            "crisis": row.get("crisis") or "",
            "virtue": row.get("virtue") or "",
            "relations": row.get("relations") or "",
            "relation_circles": row.get("relation_circles") or "",
            "modality": row.get("modality") or "",
            # Serialized, matching add_memory's writer and the orjson.loads()
            # in the Qdrant read path.
            "custom_metadata": orjson.dumps(raw_meta or {}).decode(),
        }

    async def _promote_one_archived_row(
        self,
        row,
        emb,
        similarity,
        matched_cues,
        *,
        threshold,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
    ) -> dict | None:
        """Score, gate, and (if it qualifies) persist one archived row's
        promotion to the active tier. Returns the active-shaped result dict,
        or None if it doesn't qualify or the write failed."""
        content = row["content"]
        if not emb:
            return None

        (
            score,
            spread_activation,
            dist_emo,
            recall_count,
            now,
        ) = self._archive_row_activation(
            row,
            similarity,
            current_valence=current_valence,
            current_arousal=current_arousal,
            current_cortisol=current_cortisol,
            current_time=current_time,
        )

        # Only promote if it passes the threshold or is a milestone memory
        if not (
            score > (threshold - 2.5) or (row.get("importance_score") or 0.5) >= 0.7
        ):
            return None

        mem_id = str(row.get("id") or uuid.uuid4())
        raw_meta = row.get("metadata")
        if isinstance(raw_meta, str):
            try:
                raw_meta = json.loads(raw_meta)
            except Exception:
                raw_meta = {}
        elif not isinstance(raw_meta, dict):
            raw_meta = {}

        payload_meta = self._build_promotion_payload(row, raw_meta)

        try:
            await self._write_promoted_memory(
                mem_id,
                content,
                row,
                emb,
                recall_count,
                now,
                payload_meta,
                sql_meta=raw_meta,
            )
        except Exception as prom_err:
            # The move did not persist, so this memory is still archived.
            # Returning it anyway surfaced a result the next turn could not
            # find again, and cached it as though it were active.
            logger.error(f"Failed to promote memory {mem_id}: {prom_err}")
            return None

        created = self._as_aware_utc(row.get("created_at"))

        # Rescore as an active memory: recall_count was incremented on
        # promotion and last_recalled_at is now, so recency is ~0.
        spacing_hours_active = self._spacing_hours(recall_count + 1, created, now)
        base_activation_active = self._base_activation(
            recall_count + 1,
            0.001,
            row.get("importance_score") or 0.5,
            dist_emo,
            spacing_hours_active,
        )
        score_active = (
            base_activation_active
            + spread_activation
            - ACTR_EMO_DISTANCE_PENALTY * dist_emo
        )
        # Apply direct cue boost since it was promoted for keyword matches
        match_count = sum(1 for mc in matched_cues if mc in content.lower())
        score_active += DIRECT_CUE_BOOST * match_count

        return {
            "content": content,
            "raw_content": row.get("raw_content") or content,
            "wing": row.get("wing", "personal"),
            "room": row.get("room"),
            "score": score_active,
            "valence": row.get("valence") or 0.0,
            "created_at": created.isoformat() if created else None,
            "recall_count": recall_count + 1,
            "metadata": raw_meta or {},
            "lifespan_stage": row.get("lifespan_stage"),
            "crisis": row.get("crisis"),
            "virtue": row.get("virtue"),
            "relations": row.get("relations"),
            "relation_circles": row.get("relation_circles"),
            "modality": row.get("modality"),
            "relevance": _clip_relevance(similarity),
        }

    async def _promote_archived_rows(
        self,
        scored_archive_rows,
        matched_cues,
        *,
        threshold,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
    ) -> list:
        """Promote qualifying archived rows to the active tier and return them."""
        # P4-12 (roadmap leftovers Item 1): archived rows missing a stored
        # embedding used to fetch it one HTTP round trip at a time, on this
        # search-path loop. Pre-scan for which rows actually need a fetch,
        # then batch them in one get_embeddings() call -- order-preserving,
        # so row index i's embedding is embeddings_by_index[i].
        parsed_embeddings = [
            self._parse_stored_embedding(row.get("embedding"))
            for _rs, _s, _sim, row in scored_archive_rows
        ]
        missing_indices = [i for i, emb in enumerate(parsed_embeddings) if not emb]
        if missing_indices:
            fetched = await self.get_embeddings(
                [scored_archive_rows[i][3]["content"] for i in missing_indices]
            )
            for idx, vec in zip(missing_indices, fetched, strict=True):
                parsed_embeddings[idx] = vec

        promoted_results = []
        for row_index, (_ranking_score, _score, similarity, row) in enumerate(
            scored_archive_rows
        ):
            promoted = await self._promote_one_archived_row(
                row,
                parsed_embeddings[row_index],
                similarity,
                matched_cues,
                threshold=threshold,
                current_valence=current_valence,
                current_arousal=current_arousal,
                current_cortisol=current_cortisol,
                current_time=current_time,
            )
            if promoted is not None:
                promoted_results.append(promoted)
                if row_index in missing_indices:
                    # Re-embedded on promotion: if SQLite reused the rowid of
                    # the same id, the index key cannot tell the row changed.
                    self._sqlite_vector_index.invalidate(row.get("wing") or "personal")
        return promoted_results

    async def _recall_from_archive(
        self,
        *,
        query_words,
        dynamic_stop_words,
        matched_cues,
        resolved_cues,
        user_id,
        wing,
        vector_str,
        query_vector,
        existing_results,
        excluded,
        limit,
        threshold,
        current_valence,
        current_arousal,
        current_cortisol,
        current_time,
    ) -> list:
        """L3 sub-conscious search: surface archived memories and promote the best."""
        expanded_cues = self._expand_archive_cues(
            query_words, dynamic_stop_words, matched_cues, resolved_cues, user_id
        )
        archive_rows = await self._fetch_archive_rows(
            list(expanded_cues), wing, vector_str
        )
        if not archive_rows:
            return []

        active_contents = {res["content"] for res in existing_results}
        archive_rows = [
            r
            for r in archive_rows
            if r.get("content")
            and r["content"] not in active_contents
            and r["content"] not in excluded
        ]
        if not archive_rows:
            return []

        scored_archive_rows = await self._rank_archive_rows(
            archive_rows,
            expanded_cues,
            query_vector,
            limit=limit,
            current_valence=current_valence,
            current_arousal=current_arousal,
            current_cortisol=current_cortisol,
            current_time=current_time,
        )
        return await self._promote_archived_rows(
            scored_archive_rows,
            matched_cues,
            threshold=threshold,
            current_valence=current_valence,
            current_arousal=current_arousal,
            current_cortisol=current_cortisol,
            current_time=current_time,
        )

    @staticmethod
    def _build_search_cache_key(
        query_text,
        wing,
        room,
        threshold,
        limit,
        current_valence: float,
        current_arousal: float,
        current_cortisol: float,
        exclude_contents,
        user_id,
        is_self_reflection: bool,
        current_time,
    ) -> tuple:
        """L1 cache key. Affect/time are quantized (see L1_CACHE_AFFECT_BUCKET
        / L1_CACHE_TIME_BUCKET_S) -- "close enough to be the same context,"
        not the precise floats scoring uses."""
        return (
            query_text,
            wing,
            room,
            threshold,
            limit,
            _quantize(current_valence, L1_CACHE_AFFECT_BUCKET),
            _quantize(current_arousal, L1_CACHE_AFFECT_BUCKET),
            _quantize(current_cortisol, L1_CACHE_AFFECT_BUCKET),
            tuple(sorted(exclude_contents or [])),
            user_id,
            # Pronoun cues resolve in opposite directions depending on this
            # flag ("I"/"my" bind to the agent when self-reflecting, to the
            # user otherwise), so the two modes must not share a cache entry.
            is_self_reflection,
            (
                int(current_time.timestamp() // L1_CACHE_TIME_BUCKET_S)
                if current_time is not None
                else None
            ),
        )

    def _resolve_search_entity_context(
        self, entity_records, relation_records, raw_candidates, user_id
    ):
        """Build the entity graph and identify the agent/user nodes in it,
        tolerating a malformed graph (search still works without cues)."""
        entity_names: list = []
        adj: dict = {}
        cand_entities: dict = {}
        agent_node_name = None
        user_node_name = None
        try:
            entity_names, adj, cand_entities = self._build_entity_graph(
                entity_records, relation_records, raw_candidates
            )
            agent_node_name, user_node_name = self._resolve_identity_nodes(
                entity_records, entity_names, adj, user_id
            )
        except Exception as e:
            logger.debug("Failed to process entities: %s", e)
        return entity_names, adj, cand_entities, agent_node_name, user_node_name

    def _finalize_search_results(
        self,
        results: list,
        *,
        query_text: str,
        limit,
        refresh_on_recall: bool,
        current_valence: float,
        current_time,
        cache_key: tuple,
        now_ts: float,
    ) -> list:
        """Sort/limit, spawn the background recall-strengthening refresh, and
        populate the L1 cache -- the shared tail of every non-early-return
        path through `search_memories`."""
        if results:
            # Sort and limit results to maintain full compatibility with offline tests
            results.sort(key=lambda x: x["score"], reverse=True)
            if limit:
                results = results[:limit]

            logger.info(
                f"\U0001f9e0 ACT-R Recall: {len(results)} memories for: '{query_text[:30]}...'"
            )

        if results and refresh_on_recall:

            def _done_callback(t):
                try:
                    t.result()
                except Exception as e:
                    logger.error(f"Background Memory Refresh Failed: {e}")

            # P4-8: spawn_background keeps a strong reference so this task
            # cannot be GC'd mid-refresh; _done_callback is chained after it
            # purely for the existing error-logging behavior.
            task = spawn_background(
                self._background_tasks,
                self._refresh_memories(
                    results,
                    current_valence=current_valence,
                    current_time=current_time,
                ),
            )
            task.add_done_callback(_done_callback)

        # Cache results in L1 memory cache before returning. Not a degraded
        # answer: a cache hit clears `last_search_error`, so a repeat query
        # would report an outage as "nothing relevant".
        if not self.last_search_error:
            self._l1_cache_put(cache_key, (now_ts, results))
        return results

    async def _fetch_raw_candidates(
        self,
        *,
        candidates,
        query_vector,
        mrl_query_vector,
        vector_str: str,
        wing,
        room,
        excluded,
        threshold,
        candidate_limit: int,
        current_valence: float,
        current_arousal: float,
        current_cortisol: float,
        current_time,
        now_ts: float,
        is_sqlite: bool,
    ) -> tuple[list, float]:
        """Qdrant selective-vector path, falling back to a direct SQL scan
        (dialect-branched) when Qdrant is offline or returned nothing."""
        # 1. Qdrant Selective Vector Path
        raw_candidates = []
        if self.qdrant_store.client and candidates:
            raw_candidates = await self._score_qdrant_candidates(
                candidates,
                wing=wing,
                room=room,
                excluded=excluded,
                threshold=threshold,
                current_valence=current_valence,
                current_arousal=current_arousal,
                current_cortisol=current_cortisol,
                current_time=current_time,
                now_ts=now_ts,
            )

        # 2. Database Fallback (Qdrant offline or returned no candidates)
        if not raw_candidates:
            async with self.pool.acquire() as conn:
                if is_sqlite:
                    # The SQLite path recomputes "now"; keep its value, it is
                    # what stamps the L1 cache entry below.
                    raw_candidates, now_ts = await self._fetch_sqlite_candidates(
                        conn,
                        query_vector=query_vector,
                        wing=wing,
                        room=room,
                        excluded=excluded,
                        threshold=threshold,
                        current_valence=current_valence,
                        current_arousal=current_arousal,
                        current_cortisol=current_cortisol,
                        current_time=current_time,
                        candidate_limit=candidate_limit,
                    )
                else:
                    raw_candidates = await self._fetch_postgres_candidates(
                        conn,
                        vector_str=vector_str,
                        wing=wing,
                        room=room,
                        excluded=excluded,
                        threshold=threshold,
                        candidate_limit=candidate_limit,
                        current_valence=current_valence,
                        current_arousal=current_arousal,
                        current_cortisol=current_cortisol,
                        current_time=current_time,
                    )
        return raw_candidates, now_ts

    async def _l1_cache_hit(
        self,
        cache_key: tuple,
        now_ts: float,
        *,
        refresh_on_recall: bool,
        current_valence: float,
        current_time,
    ) -> list | None:
        """A fresh L1 hit, refreshed if requested; None on a miss/stale entry."""
        if cache_key not in self._l1_cache:
            return None
        ts, cached_results = self._l1_cache[cache_key]
        if now_ts - ts >= self._l1_cache_ttl:
            return None
        self._l1_cache.move_to_end(cache_key)
        if cached_results and refresh_on_recall:
            await self._refresh_memories(
                cached_results,
                current_valence=current_valence,
                current_time=current_time,
            )
        return cached_results

    async def _refresh_stop_words_if_stale(self, now_ts: float) -> None:
        """Periodically refresh database-derived stop words (TTL = 5 minutes)."""
        if (
            hasattr(self, "_last_stop_words_update")
            and now_ts - self._last_stop_words_update <= 300.0
        ):
            return
        await self._update_dynamic_stop_words()
        # Reload the learned-vocabulary association cache on the same
        # cadence (first call also creates tables + plants the seed).
        await self.lexicon.refresh()
        self._last_stop_words_update = now_ts

    # ------------------------------------------------------------------
    # Brain V2 hybrid retrieval (ADR-001). Selected by
    # Config.MEMORY_RANKING_POLICY == "hybrid"; the V1 pipeline below is
    # untouched and remains selectable for comparison and rollback.
    # ------------------------------------------------------------------

    _PG_SIMILARITY_SQL = """
        SELECT *, (1 - (embedding <=> $1::vector))::double precision AS similarity
        FROM memories
        WHERE wing = $2 AND ($3::text IS NULL OR room = $3)
        ORDER BY embedding <=> $1::vector
        LIMIT $4
    """

    async def _fetch_similarity_candidates(
        self, query_vector, *, wing, room, excluded, pool_size, current_time, now_ts
    ) -> tuple[list, str]:
        """The `pool_size` memories most similar to the query, as candidate
        dicts, plus which backend produced them.

        V1 chose candidates by recency (SQLite) or by the full ACT-R score
        (Postgres), where similarity is a minor term -- so the relevant
        memory was usually never scored at all. Every backend here selects by
        cosine similarity; the ranker decides everything else.
        """
        neutral = {
            "current_valence": 0.0,
            "current_arousal": 0.5,
            "current_cortisol": 0.0,
        }
        if self.qdrant_store.client:
            # Filter by scope inside Qdrant (V1 post-filtered, so other wings
            # could exhaust the pool) and over-fetch for exclusions.
            scope = {"wing": wing} if room is None else {"wing": wing, "room": room}
            try:
                hits = await asyncio.to_thread(
                    self.qdrant_store.search_vector_memories,
                    query_vector=query_vector,
                    limit=pool_size + len(excluded),
                    filter_dict=scope,
                )
            except Exception as qe:
                logger.error(f"Qdrant retrieval failed: {qe}")
                hits = []
            if hits:
                candidates = await self._score_qdrant_candidates(
                    hits,
                    wing=wing,
                    room=room,
                    excluded=excluded,
                    threshold=float("-inf"),
                    current_time=current_time,
                    now_ts=now_ts,
                    **neutral,
                )
                if candidates:
                    return candidates[:pool_size], "qdrant"

        async with self.pool.acquire() as conn:
            now = (
                self._as_aware_utc(current_time)
                if current_time is not None
                else clock.now(UTC)
            )
            if self.is_sqlite:
                # In-process vector index over the wing's most recent
                # MEMORY_SQLITE_SCAN_LIMIT rows (sqlite_vector_index.py);
                # full rows are then read only for the winners. Over-fetch by
                # the exclusion count so excluded contents cannot shrink the pool.
                hits = await self._sqlite_vector_index.top_k(
                    conn, wing, query_vector, pool_size + len(excluded), room=room
                )
                if not hits:
                    index = await self._sqlite_vector_index.ensure(conn, wing)
                    if index.skipped_dimension and not index.ids:
                        # Rows exist but none share the query's dimension
                        # (an embedding-model change): an outage, not "nothing
                        # relevant", so say so.
                        self.last_search_error = (
                            "no stored embedding matches the query dimension "
                            f"({len(query_vector)})"
                        )
                        self.last_search_error_at = clock.time()
                    return [], "sqlite"
                similarity_by_id = dict(hits)
                where, args = self._in_predicate("id", list(similarity_by_id))
                sql = f"SELECT * FROM memories WHERE {where}"  # nosec B608 - where is "id IN (?,...)" from _in_predicate; values bound via args
                rows = await conn.fetch(sql, *args)
                candidates = []
                for row in rows:
                    row = dict(row)
                    if row.get("content") in excluded:
                        continue
                    row["similarity"] = similarity_by_id.get(str(row.get("id")), 0.0)
                    candidates.append(
                        self._build_candidate_from_row(
                            row, now, threshold=float("-inf"), **neutral
                        )
                    )
                candidates.sort(key=lambda c: c.get("similarity") or 0.0, reverse=True)
                return candidates[:pool_size], "sqlite"

            # Over-fetch by the exclusion count, as the SQLite branch does, so
            # excluded contents cannot shrink the pool. pgvector's HNSW scan
            # returns at most `hnsw.ef_search` rows (default 40) -- fewer after
            # the wing/room filter -- so raise it to the pool. Session-level on
            # purpose: a higher ef_search left on a pooled connection only
            # makes later HNSW scans more thorough, and it needs no transaction.
            fetch_n = pool_size + len(excluded)
            await conn.execute(f"SET hnsw.ef_search = {max(40, int(fetch_n))}")
            rows = await conn.fetch(
                self._PG_SIMILARITY_SQL, str(query_vector), wing, room, fetch_n
            )
            candidates = []
            for row in rows:
                row = dict(row)
                if row.get("content") in excluded:
                    continue
                cand = self._build_candidate_from_row(
                    row,
                    now,
                    threshold=float("-inf"),
                    **neutral,
                )
                if cand is not None:
                    candidates.append(cand)
            return candidates[:pool_size], "postgres"

    async def warm_retrieval_index(self, wing: str = "personal") -> None:
        """Build the SQLite vector index before the first user turn needs it.

        The first hybrid search on a SQLite store parses every stored
        embedding (~1 s at 5,000 memories); afterwards a search costs a few
        milliseconds. Agents call this at startup, off the critical path.
        No-op for Postgres/Qdrant and for the V1 policy. Never raises.
        """
        if Config.MEMORY_RANKING_POLICY != "hybrid" or not self.is_sqlite:
            return
        try:
            started = time.perf_counter()
            async with self.pool.acquire() as conn:
                index = await self._sqlite_vector_index.ensure(conn, wing)
            logger.info(
                "Retrieval index warm: %d memories in %.0f ms",
                len(index.ids),
                (time.perf_counter() - started) * 1000.0,
            )
        except Exception as e:
            logger.warning(f"Retrieval index warm-up failed (will build lazily): {e}")

    async def _fetch_archive_candidates(
        self,
        query_text,
        *,
        stop_words,
        user_id,
        wing,
        query_vector,
        excluded,
        active_contents,
        max_candidates,
    ) -> list:
        """Archived (L3) memories that lexically or semantically match the
        query, shaped as ordinary candidates so they compete on the same
        scale. Only those that win a top-k slot get promoted (see
        `_materialize_hybrid_results`) -- V1 wrote up to five promotions back
        to the active tier even when `limit` then discarded them."""
        query_words = re.findall(r"\b\w{3,}\b", query_text.lower())
        matched_cues = [w for w in query_words if w not in stop_words]
        if not matched_cues:
            return []
        expanded = self._expand_archive_cues(
            query_words, stop_words, matched_cues, set(), user_id
        )
        rows = await self._fetch_archive_rows(list(expanded), wing, str(query_vector))
        candidates = []
        for row in rows or []:
            content = row.get("content")
            if not content or content in excluded or content in active_contents:
                continue
            similarity = row.get("similarity_arch")
            if similarity is None:
                similarity = self._archive_similarity_fallback(row, query_vector)
            candidates.append(
                {
                    "id": row.get("id"),
                    "content": content,
                    "raw_content": row.get("raw_content") or content,
                    "wing": row.get("wing", "personal"),
                    "room": row.get("room"),
                    "valence": row.get("valence") or 0.0,
                    "created_at": self._as_aware_utc(row.get("created_at")),
                    "recall_count": max(1, row.get("recall_count") or 1),
                    "metadata": row.get("metadata") or {},
                    "similarity": float(similarity or 0.0),
                    "last_recalled_at": self._as_aware_utc(row.get("last_recalled_at")),
                    "importance_score": row.get("importance_score"),
                    "_archive_row": row,
                }
            )
        candidates.sort(key=lambda c: c["similarity"], reverse=True)
        return candidates[:max_candidates]

    async def _materialize_hybrid_results(
        self, ranked, *, limit, stop_words, query_text, current_time
    ) -> list:
        """Walk the ranking best-first, promoting archived winners; an archived
        winner whose promotion fails is skipped and the next candidate takes
        its slot, so a failed write never surfaces a memory that is not
        actually active."""
        matched_cues = [
            w
            for w in re.findall(r"\b\w{3,}\b", query_text.lower())
            if w not in stop_words
        ]
        results: list[dict[str, Any]] = []
        for cand in ranked:
            if limit and len(results) >= limit:
                break
            row = cand.pop("_archive_row", None)
            if row is None:
                results.extend(self._format_results([cand], float("-inf")))
                continue
            promoted = await self._promote_archived_rows(
                [(cand["score"], cand["score"], cand["similarity"], row)],
                matched_cues,
                threshold=float("-inf"),
                current_valence=0.0,
                current_arousal=0.5,
                current_cortisol=0.0,
                current_time=current_time,
            )
            for item in promoted:
                item["score"] = cand["score"]
                item["score_terms"] = cand.get("score_terms")
                item["relevance"] = _clip_relevance(cand.get("similarity"))
                results.append(item)
        return results

    async def _search_memories_hybrid(
        self,
        query_text,
        *,
        wing,
        room,
        threshold,
        limit,
        refresh_on_recall,
        exclude_contents,
        current_valence,
        current_arousal,
        current_cortisol,
        user_id,
        is_self_reflection,
        current_time,
    ):
        started = time.perf_counter()
        cache_key = self._build_search_cache_key(
            query_text,
            wing,
            room,
            threshold,
            limit,
            current_valence,
            current_arousal,
            current_cortisol,
            exclude_contents,
            user_id,
            is_self_reflection,
            current_time,
        ) + ("hybrid",)
        now_ts = current_time.timestamp() if current_time is not None else clock.time()
        cache_hit = await self._l1_cache_hit(
            cache_key,
            now_ts,
            refresh_on_recall=refresh_on_recall,
            current_valence=current_valence,
            current_time=current_time,
        )
        if cache_hit is not None:
            if _trace_enabled():
                _emit_trace(
                    "memory.search",
                    policy="hybrid",
                    source="cache",
                    pool=0,
                    archived_candidates=0,
                    skipped_dimension=0,
                    error=False,
                    ms=round((time.perf_counter() - started) * 1000.0, 2),
                    cache_hit=True,
                    results=[
                        {
                            "id": result.get("id"),
                            "score": round(result["score"], 4),
                            "terms": result.get("score_terms"),
                        }
                        for result in cache_hit
                    ],
                )
            return cache_hit

        try:
            await self._refresh_stop_words_if_stale(now_ts)
            query_vector = await self.get_embedding(query_text)
            if not query_vector:
                self.last_search_error = "embedding service returned no vector"
                self.last_search_error_at = clock.time()
                if _trace_enabled():
                    _emit_trace(
                        "memory.search",
                        policy="hybrid",
                        source="embedding",
                        pool=0,
                        archived_candidates=0,
                        skipped_dimension=0,
                        error=True,
                        error_code="empty_embedding",
                        ms=round((time.perf_counter() - started) * 1000.0, 2),
                        cache_hit=False,
                        results=[],
                    )
                return []

            excluded = {content for content in (exclude_contents or []) if content}
            pool_size = Config.MEMORY_CANDIDATE_POOL
            candidates, source = await self._fetch_similarity_candidates(
                query_vector,
                wing=wing,
                room=room,
                excluded=excluded,
                pool_size=pool_size,
                current_time=current_time,
                now_ts=now_ts,
            )
            stop_words = self._resolve_dynamic_stop_words(user_id)
            archived = await self._fetch_archive_candidates(
                query_text,
                stop_words=stop_words,
                user_id=user_id,
                wing=wing,
                query_vector=query_vector,
                excluded=excluded,
                active_contents={c["content"] for c in candidates},
                max_candidates=max(1, pool_size // 4),
            )
            now = (
                self._as_aware_utc(current_time)
                if current_time is not None
                else clock.now(UTC)
            )
            ranked = hybrid_rank(
                candidates + archived,
                query_text,
                stop_words,
                now,
                limit=None,
                decay=self.decay_rate,
            )
            results = await self._materialize_hybrid_results(
                ranked,
                limit=limit,
                stop_words=stop_words,
                query_text=query_text,
                current_time=current_time,
            )
            # Observability (no memory text): which backend answered, how big
            # the pool was, and why each winner won.
            self.last_search_trace = {
                "policy": "hybrid",
                "source": source,
                "pool": len(candidates),
                "archived_candidates": len(archived),
                "skipped_dimension": (
                    self._sqlite_vector_index.skipped_dimension(wing)
                    if source == "sqlite"
                    else 0
                ),
                "error": self.last_search_error,
                "error_code": "retrieval_error" if self.last_search_error else None,
                "ms": round((time.perf_counter() - started) * 1000.0, 2),
                "results": [
                    {
                        "id": r.get("id"),
                        "score": round(r["score"], 4),
                        "terms": r.get("score_terms"),
                    }
                    for r in results
                ],
            }
            if _trace_enabled():
                _emit_trace(
                    "memory.search",
                    policy=self.last_search_trace["policy"],
                    source=self.last_search_trace["source"],
                    pool=self.last_search_trace["pool"],
                    archived_candidates=self.last_search_trace["archived_candidates"],
                    skipped_dimension=self.last_search_trace["skipped_dimension"],
                    error=bool(self.last_search_trace["error"]),
                    **(
                        {"error_code": self.last_search_trace["error_code"]}
                        if self.last_search_trace["error_code"]
                        else {}
                    ),
                    ms=self.last_search_trace["ms"],
                    cache_hit=False,
                    results=self.last_search_trace["results"],
                )
            logger.debug("Memory retrieval trace: %s", self.last_search_trace)
            return self._finalize_search_results(
                results,
                query_text=query_text,
                limit=limit,
                refresh_on_recall=refresh_on_recall,
                current_valence=current_valence,
                current_time=current_time,
                cache_key=cache_key,
                now_ts=now_ts,
            )
        except Exception as e:
            from ..cognitive.trace import TraceSchemaError

            if isinstance(e, TraceSchemaError):
                raise
            logger.exception("Memory search failed")
            self.last_search_error = str(e)
            self.last_search_error_at = clock.time()
            if _trace_enabled():
                _emit_trace(
                    "memory.search",
                    policy="hybrid",
                    source="error",
                    pool=0,
                    archived_candidates=0,
                    skipped_dimension=0,
                    error=True,
                    error_code=type(e).__name__,
                    ms=round((time.perf_counter() - started) * 1000.0, 2),
                    cache_hit=False,
                    results=[],
                )
            return []

    @staticmethod
    def _emit_actr_search_trace(
        source: str,
        *,
        results: list | None = None,
        pool: int = 0,
        archived_candidates: int = 0,
        cache_hit: bool = False,
        error_code: str | None = None,
    ) -> None:
        """One `memory.search` trace event for the ACT-R policy path (ids and
        numbers only, never memory text)."""
        if not _trace_enabled():
            return
        _emit_trace(
            "memory.search",
            policy=Config.MEMORY_RANKING_POLICY,
            source=source,
            pool=pool,
            archived_candidates=archived_candidates,
            skipped_dimension=0,
            error=error_code is not None,
            **({"error_code": error_code} if error_code is not None else {}),
            cache_hit=cache_hit,
            results=[
                {
                    "id": result.get("id"),
                    "score": result.get("score", 0.0),
                    "terms": result.get("score_terms"),
                }
                for result in results or []
            ],
        )

    async def search_memories(
        self,
        query_text,
        wing: str = "personal",
        room: str | None = None,
        threshold=-1.5,
        limit=5,
        refresh_on_recall=True,
        exclude_contents: Iterable[str] | None = None,
        current_valence: float = 0.0,
        current_arousal: float = 0.5,
        current_cortisol: float = 0.0,
        user_id: str | None = None,
        is_self_reflection: bool = False,
        current_time=None,
        *,
        full_candidate_pool: bool | None = None,
    ):
        """
        ACT-R Based Retrieval with Hierarchical & Neuromodulatory Gating:
            Score = B_i + w_spread*Similarity_eff + w_emotion*EmotionalAlignment

        Filters results by 'wing' and optionally 'room' before scoring.

        The pipeline runs as: L1 cache -> embed + MRL gating -> candidate fetch
        (Qdrant, else SQL) -> lexical/graph cue analysis -> spreading-activation
        boosts -> threshold + format -> L3 archive promotion -> sort/limit.
        Each stage is a `_`-prefixed helper defined above.

        Args:
            full_candidate_pool: How wide a candidate pool to gather, when the
                agent is not stressed. Defaults to `refresh_on_recall`, which is
                how this has always been decided -- but the two are different
                properties that happened to coincide, and reading one off the
                other silently narrows the search for anyone who wants the
                counters left alone. Pass it explicitly to say which you mean.
        """
        self.last_search_error = None
        self.last_search_error_at = None

        if getattr(Config, "MEMORY_RANKING_POLICY", "hybrid") == "hybrid":
            return await self._search_memories_hybrid(
                query_text,
                wing=wing,
                room=room,
                threshold=threshold,
                limit=limit,
                refresh_on_recall=refresh_on_recall,
                exclude_contents=exclude_contents,
                current_valence=current_valence,
                current_arousal=current_arousal,
                current_cortisol=current_cortisol,
                user_id=user_id,
                is_self_reflection=is_self_reflection,
                current_time=current_time,
            )

        # L1 Cache lookup to bypass DB and math activation loops for active topics.
        # P3-9: valence/arousal/cortisol and current_time are quantized (see
        # L1_CACHE_AFFECT_BUCKET / L1_CACHE_TIME_BUCKET_S above) -- this key is
        # "close enough to be the same context," not the precise floats scoring
        # uses below.
        cache_key = self._build_search_cache_key(
            query_text,
            wing,
            room,
            threshold,
            limit,
            current_valence,
            current_arousal,
            current_cortisol,
            exclude_contents,
            user_id,
            is_self_reflection,
            current_time,
        )
        now_ts = current_time.timestamp() if current_time is not None else clock.time()
        cache_hit = await self._l1_cache_hit(
            cache_key,
            now_ts,
            refresh_on_recall=refresh_on_recall,
            current_valence=current_valence,
            current_time=current_time,
        )
        if cache_hit is not None:
            self._emit_actr_search_trace("cache", results=cache_hit, cache_hit=True)
            return cache_hit

        try:
            await self._refresh_stop_words_if_stale(now_ts)

            query_vector = await self.get_embedding(query_text)
            if not query_vector:
                self.last_search_error = "embedding service returned no vector"
                self.last_search_error_at = clock.time()
                self._emit_actr_search_trace("embedding", error_code="empty_embedding")
                return []

            mrl_dim, candidate_limit = self._compute_mrl_gating(
                current_arousal,
                current_cortisol,
                limit,
                refresh_on_recall
                if full_candidate_pool is None
                else full_candidate_pool,
            )

            # Slice query_vector to mrl_dim and pad with zeros to 768
            mrl_query_vector = list(query_vector)
            for i in range(mrl_dim, len(mrl_query_vector)):
                mrl_query_vector[i] = 0.0

            self._detect_topic_shift(query_vector)

            vector_str = str(mrl_query_vector)
            excluded = {content for content in (exclude_contents or []) if content}
            is_sqlite = self.is_sqlite

            # Concurrently fetch vector candidates and Neo4j graph data
            (
                candidates,
                entity_records,
                relation_records,
            ) = await self._gather_candidate_sources(
                mrl_query_vector, candidate_limit, query_text, user_id
            )

            raw_candidates, now_ts = await self._fetch_raw_candidates(
                candidates=candidates,
                query_vector=query_vector,
                mrl_query_vector=mrl_query_vector,
                vector_str=vector_str,
                wing=wing,
                room=room,
                excluded=excluded,
                threshold=threshold,
                candidate_limit=candidate_limit,
                current_valence=current_valence,
                current_arousal=current_arousal,
                current_cortisol=current_cortisol,
                current_time=current_time,
                now_ts=now_ts,
                is_sqlite=is_sqlite,
            )

            # 3. Post-process in Python: direct cue boost + spreading activation
            query_words = re.findall(r"\b\w{3,}\b", query_text.lower())
            dynamic_stop_words = self._resolve_dynamic_stop_words(user_id)
            matched_cues = [w for w in query_words if w not in dynamic_stop_words]
            self.goal_buffer.update_buffer(query_text, dynamic_stop_words)

            (
                entity_names,
                adj,
                cand_entities,
                agent_node_name,
                user_node_name,
            ) = self._resolve_search_entity_context(
                entity_records, relation_records, raw_candidates, user_id
            )

            resolved_cues = self._resolve_pronoun_cues(
                query_text,
                agent_node_name,
                user_node_name,
                user_id,
                is_self_reflection,
            )
            for cue in resolved_cues:
                if cue not in matched_cues:
                    matched_cues.append(cue)

            direct_boosted_indices = self._apply_direct_cue_boost(
                raw_candidates, matched_cues
            )
            self._apply_ppr_spreading_activation(
                raw_candidates,
                entity_names,
                adj,
                matched_cues,
                direct_boosted_indices,
                agent_node_name,
                cand_entities,
            )
            self._apply_goal_buffer_boost(raw_candidates)

            # 4. Filter by final threshold, format and return results
            results = self._format_results(raw_candidates, threshold)

            # L3 Sub-conscious Search and Promotion
            if matched_cues:
                promoted_results = await self._recall_from_archive(
                    query_words=query_words,
                    dynamic_stop_words=dynamic_stop_words,
                    matched_cues=matched_cues,
                    resolved_cues=resolved_cues,
                    user_id=user_id,
                    wing=wing,
                    vector_str=vector_str,
                    query_vector=query_vector,
                    existing_results=results,
                    excluded=excluded,
                    limit=limit,
                    threshold=threshold,
                    current_valence=current_valence,
                    current_arousal=current_arousal,
                    current_cortisol=current_cortisol,
                    current_time=current_time,
                )
                if promoted_results:
                    results.extend(promoted_results)

            finalized = self._finalize_search_results(
                results,
                query_text=query_text,
                limit=limit,
                refresh_on_recall=refresh_on_recall,
                current_valence=current_valence,
                current_time=current_time,
                cache_key=cache_key,
                now_ts=now_ts,
            )
            self._emit_actr_search_trace(
                "sqlite" if is_sqlite else "postgres",
                results=finalized,
                pool=len(raw_candidates),
                archived_candidates=len(promoted_results) if matched_cues else 0,
            )
            return finalized

        except Exception as e:
            from ..cognitive.trace import TraceSchemaError

            if isinstance(e, TraceSchemaError):
                raise
            import traceback

            traceback.print_exc()
            logger.error(f"Memory search failed: {e}")
            # P3-6: the empty return below is indistinguishable from a
            # genuine "nothing relevant" result on its own -- record the
            # failure so a caller that cares can tell the difference.
            self.last_search_error = str(e)
            self.last_search_error_at = clock.time()
            self._emit_actr_search_trace("error", error_code=type(e).__name__)
            return []

    async def _refresh_memories(
        self, memories: list[dict], current_valence: float = 0.0, current_time=None
    ):
        """
        Updates last_recalled_at and increments recall_count (ACT-R frequency).
        This strengthens the base-level activation for recently accessed memories.
        Also performs emotional habituation / PTSD extinction decay under neutral/positive context.
        """
        try:
            contents = [
                memory["content"] for memory in memories if memory.get("content")
            ]
            if not contents:
                return
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    placeholders = ",".join("?" for _ in contents)
                    try:
                        if current_time is not None:
                            params = [
                                current_time,
                                current_valence,
                                current_valence,
                            ] + contents
                            time_placeholder = "?"
                        else:
                            params = [current_valence, current_valence] + contents
                            time_placeholder = "CURRENT_TIMESTAMP"

                        await conn.execute(
                            """
                            UPDATE memories
                            SET last_recalled_at = """
                            f"{time_placeholder}"  # nosec B608 - time_placeholder is one of the fixed literals set above, never user input
                            """,
                                 recall_count = recall_count + 1,
                                 emotional_weight = CASE
                                     WHEN valence < -0.4 AND ? >= 0.0 THEN emotional_weight * 0.95
                                     ELSE emotional_weight
                                 END,
                                 importance_score = CASE
                                     WHEN valence < -0.4 AND ? >= 0.0 THEN importance_score * 0.98
                                     ELSE importance_score
                                 END
                            WHERE content IN ("""
                            f"{placeholders}"  # nosec B608 - placeholders is the ?-count marker string generated above, never user input
                            """)
                            """,
                            *params,
                        )
                    except Exception as sq_err:
                        logger.warning(
                            f"Decay refresh failed, falling back to legacy: {sq_err}"
                        )
                        if current_time is not None:
                            await conn.execute(
                                """
                                UPDATE memories
                                SET last_recalled_at = ?,
                                     recall_count = recall_count + 1
                                WHERE content IN ("""
                                f"{placeholders}"  # nosec B608 - placeholders is the ?-count marker string generated above, never user input
                                """)
                                """,
                                current_time,
                                *contents,
                            )
                        else:
                            await conn.execute(
                                """
                                UPDATE memories
                                SET last_recalled_at = CURRENT_TIMESTAMP,
                                     recall_count = recall_count + 1
                                WHERE content IN ("""
                                f"{placeholders}"  # nosec B608 - placeholders is the ?-count marker string generated above, never user input
                                """)
                                """,
                                *contents,
                            )
                else:
                    try:
                        if current_time is not None:
                            await conn.execute(
                                """
                                UPDATE memories
                                SET last_recalled_at = $3,
                                     recall_count = recall_count + 1,
                                     emotional_weight = CASE
                                         WHEN valence < -0.4 AND $2 >= 0.0 THEN emotional_weight * 0.95
                                         ELSE emotional_weight
                                     END,
                                     importance_score = CASE
                                         WHEN valence < -0.4 AND $2 >= 0.0 THEN importance_score * 0.98
                                         ELSE importance_score
                                     END
                                WHERE content = ANY($1)
                                """,
                                contents,
                                current_valence,
                                current_time,
                            )
                        else:
                            await conn.execute(
                                """
                                UPDATE memories
                                SET last_recalled_at = CURRENT_TIMESTAMP,
                                     recall_count = recall_count + 1,
                                     emotional_weight = CASE
                                         WHEN valence < -0.4 AND $2 >= 0.0 THEN emotional_weight * 0.95
                                         ELSE emotional_weight
                                     END,
                                     importance_score = CASE
                                         WHEN valence < -0.4 AND $2 >= 0.0 THEN importance_score * 0.98
                                         ELSE importance_score
                                     END
                                WHERE content = ANY($1)
                                """,
                                contents,
                                current_valence,
                            )
                    except Exception as pg_err:
                        logger.warning(
                            f"Decay refresh failed, falling back to legacy: {pg_err}"
                        )
                        if current_time is not None:
                            await conn.execute(
                                """
                                UPDATE memories
                                SET last_recalled_at = $2,
                                     recall_count = recall_count + 1
                                WHERE content = ANY($1)
                                """,
                                contents,
                                current_time,
                            )
                        else:
                            await conn.execute(
                                """
                                UPDATE memories
                                SET last_recalled_at = CURRENT_TIMESTAMP,
                                     recall_count = recall_count + 1
                                WHERE content = ANY($1)
                                """,
                                contents,
                            )
        except Exception as e:
            logger.error(f"Failed to refresh memories: {e}")

    async def get_recent_unconsolidated_episodes(self, limit: int = 10):
        """Fetches recent user and assistant dialogue entries from messages for consolidation within 24 hours."""
        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    rows = await conn.fetch(
                        "SELECT id, role, content, timestamp FROM messages WHERE consolidated = 0 AND role != 'system' AND timestamp >= datetime('now', '-24 hours') ORDER BY timestamp DESC LIMIT ?",
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT id, role, content, timestamp FROM messages WHERE consolidated = FALSE AND role != 'system' AND timestamp >= NOW() - INTERVAL '24 hours' ORDER BY timestamp DESC LIMIT $1",
                        limit,
                    )
                return rows
        except Exception as e:
            logger.error(f"Failed to fetch recent episodes: {e}")
            return []

    async def get_recent_high_importance_memory_contents(
        self,
        limit: int = 20,
        min_importance: float = 0.5,
        lookback_hours: int = 168,
    ) -> list[str]:
        """Bucket 12 (voice remediation Phase 3), items 2-3: candidates for
        `SubconsciousAgent._run_rest_phase_replay`'s idle/night-gated sweep --
        memories worth periodically re-scoring and re-checking for pruning
        via `apply_actr_decay`, independent of whether they happen to be tied
        to the current tick's just-consolidated dialogue the way
        `_run_consolidation_pass`'s own candidates are. Mirrors
        `get_recent_unconsolidated_episodes`'s dual-backend query shape.
        """
        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    rows = await conn.fetch(
                        "SELECT content FROM memories WHERE importance_score >= ? "
                        "AND COALESCE(last_recalled_at, created_at) >= "
                        "datetime('now', ? || ' hours') "
                        "ORDER BY importance_score DESC LIMIT ?",
                        min_importance,
                        f"-{lookback_hours}",
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT content FROM memories WHERE importance_score >= $1 "
                        "AND COALESCE(last_recalled_at, created_at) >= "
                        "NOW() - ($2 || ' hours')::interval "
                        "ORDER BY importance_score DESC LIMIT $3",
                        min_importance,
                        str(lookback_hours),
                        limit,
                    )
                return [row["content"] for row in rows if row.get("content")]
        except Exception as e:
            logger.error(f"Failed to fetch rest-phase replay candidates: {e}")
            return []

    async def get_recent_high_importance_memories_for_relinking(
        self,
        limit: int = 20,
        min_importance: float = 0.5,
        lookback_hours: int = 168,
    ) -> list[dict]:
        """Bucket 12's re-linking half of rest-phase replay: the same
        candidate pool as `get_recent_high_importance_memory_contents`
        (identical WHERE clause, deliberately -- re-linking is framed as
        part of the same sweep, not a second query with its own knobs), but
        carrying `id` and `metadata` too, since re-linking has to know which
        row to update and what it already believes about its own entities.

        `metadata` here is returned pre-parsed to a dict, following the same
        str-vs-already-decoded normalization `_fetch_sqlite_candidates`/
        `_fetch_postgres_candidates` already use for this same column --
        a JSON column comes back as a string on at least one backend/driver
        path, and a candidate with no metadata at all must not crash the
        sweep, so both cases collapse to `{}`.
        """
        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    rows = await conn.fetch(
                        "SELECT id, content, metadata FROM memories "
                        "WHERE importance_score >= ? "
                        "AND COALESCE(last_recalled_at, created_at) >= "
                        "datetime('now', ? || ' hours') "
                        "ORDER BY importance_score DESC LIMIT ?",
                        min_importance,
                        f"-{lookback_hours}",
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT id, content, metadata FROM memories "
                        "WHERE importance_score >= $1 "
                        "AND COALESCE(last_recalled_at, created_at) >= "
                        "NOW() - ($2 || ' hours')::interval "
                        "ORDER BY importance_score DESC LIMIT $3",
                        min_importance,
                        str(lookback_hours),
                        limit,
                    )
                candidates = []
                for row in rows:
                    if not row.get("content") or not row.get("id"):
                        continue
                    raw_meta = row.get("metadata")
                    if isinstance(raw_meta, str):
                        try:
                            raw_meta = orjson.loads(raw_meta)
                        except Exception:
                            raw_meta = {}
                    candidates.append(
                        {
                            "id": row["id"],
                            "content": row["content"],
                            "metadata": raw_meta or {},
                        }
                    )
                return candidates
        except Exception as e:
            logger.error(f"Failed to fetch re-linking candidates: {e}")
            return []

    async def _update_memory_metadata(self, memory_id: str, metadata: dict) -> bool:
        """Write a full metadata dict back to one row.

        Read-modify-write, not a native JSON merge: `add_memory` already
        writes this column as a plain serialized string on both backends
        (`_insert_memory_row`'s `metadata_json` parameter), so an UPDATE
        binds identically rather than assuming a JSONB-specific merge
        operator that would work on Postgres but not SQLite.
        """
        try:
            metadata_json = orjson.dumps(metadata).decode()
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    await conn.execute(
                        "UPDATE memories SET metadata = ? WHERE id = ?",
                        metadata_json,
                        memory_id,
                    )
                else:
                    await conn.execute(
                        "UPDATE memories SET metadata = $1 WHERE id = $2",
                        metadata_json,
                        memory_id,
                    )
            return True
        except Exception as e:
            logger.error(f"Failed to update metadata for memory {memory_id}: {e}")
            return False

    async def relink_memory_entities(self, candidates: list[dict]) -> int:
        """Bucket 12's re-linking: pick up entity associations a memory
        missed because the entity did not exist in the graph yet when the
        memory was first written.

        `_prelink_memory_entities` only ever runs once, at `add_memory` time
        (`app/state/memory_store.py`), against whichever entities existed in
        Neo4j *then*. `_compute_candidate_entities` (used by `search_memories`'
        graph-boost/PPR scoring) prefers a memory's stored
        `metadata["entities"]` over a live regex scan whenever that list is
        non-empty -- a real performance win, but it means a memory whose
        precomputed list is merely *incomplete* rather than empty never gets
        re-scanned by anything, forever. This closes exactly that gap:
        recompute the live entity match for each candidate and merge in
        anything new, without dropping entities a prior run already found.

        Union, never replacement -- an entity that no longer matches (renamed
        or deleted in the graph) is left in place rather than removed, since
        removing associations was never this bucket's stated goal and Neo4j
        entity deletion is its own separate, deliberate act elsewhere.

        Only writes a row whose entity set actually grew, so a rest-phase
        sweep over mostly-already-linked memories does not spend a write on
        every candidate every time it runs. A newly-linked entity can let a
        memory satisfy a graph-boost match it couldn't before -- the same
        reasoning `add_memory` already gives for invalidating `_l1_cache` on
        every new memory -- so this invalidates once at the end, only if at
        least one row actually changed.
        """
        relinked = 0
        for candidate in candidates:
            content = candidate.get("content")
            memory_id = candidate.get("id")
            if not content or not memory_id:
                continue
            metadata = candidate.get("metadata") or {}
            existing_entities = set(metadata.get("entities") or [])
            live_entities = set(await self._prelink_memory_entities(content))
            if live_entities <= existing_entities:
                continue
            merged = sorted(existing_entities | live_entities)
            updated_metadata = {**metadata, "entities": merged}
            if await self._update_memory_metadata(memory_id, updated_metadata):
                relinked += 1
        if relinked:
            self._invalidate_l1_cache()
        return relinked

    async def mark_episodes_consolidated(self, message_ids: list[str]):
        """Marks specific dialogue messages as consolidated to avoid duplicate processing."""
        if not message_ids:
            return
        try:
            async with self.pool.acquire() as conn:
                where, args = self._in_predicate("id", message_ids)
                # `1` vs `TRUE` stays a dialect literal: a Postgres boolean
                # column will not accept the integer, so this is a real
                # difference rather than noise the helper should absorb.
                truth = "1" if self.is_sqlite else "TRUE"
                await conn.execute(
                    f"UPDATE messages SET consolidated = {truth} WHERE {where}",  # nosec B608 - truth is a fixed literal, where comes from _in_predicate
                    *args,
                )
        except Exception as e:
            logger.error(f"Failed to mark episodes consolidated: {e}")

    @staticmethod
    def _parse_actr_created_at(created_at, current_time):
        """Best-effort parse of a stored `created_at` into a datetime,
        falling back to `current_time`/now for anything unparseable."""
        if not created_at:
            return current_time if current_time is not None else clock.now()

        if not isinstance(created_at, str):
            return created_at

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                return datetime.strptime(created_at.split("+")[0], fmt)
            except ValueError:
                continue
        return current_time if current_time is not None else clock.now()

    @staticmethod
    def _extract_actr_decay_rate(metadata, default_rate: float) -> float:
        """Per-memory decay_rate override from metadata, if present."""
        import json

        meta = {}
        if isinstance(metadata, str):
            try:
                meta = json.loads(metadata)
            except Exception:  # nosec B110 - malformed metadata falls back to {} / default_rate below
                pass
        elif isinstance(metadata, dict):
            meta = metadata

        return float(meta.get("decay_rate", default_rate)) if meta else default_rate

    def _compute_actr_decay(self, rows: list, current_time=None) -> tuple[list, list]:
        """Pure ACT-R activation decision per row: which memory ids should be
        pruned to the archive (`to_delete`) and which get a softened
        importance score in place (`to_update`). No I/O -- isolated so the
        date-parsing/activation math can be reasoned about (and radon-scored)
        apart from the dual-backend persistence that follows it."""
        to_delete = []
        to_update = []

        for row in rows:
            mem_id = row.get("id")
            recall_count = row.get("recall_count")
            metadata = row.get("metadata")
            importance_score = row.get("importance_score") or 0.5

            # Fallbacks
            n_recalls = max(1, recall_count if recall_count is not None else 1)

            dt = self._parse_actr_created_at(row.get("created_at"), current_time)

            # Calculate hours since creation. Coerce both operands to
            # aware-UTC so a naive stored created_at and an aware
            # current_time (or vice versa) never raise on subtraction.
            now = (
                self._as_aware_utc(current_time)
                if current_time is not None
                else clock.now(UTC)
            )
            delta = now - (self._as_aware_utc(dt) or now)
            hours_since = max(0.0, delta.total_seconds() / 3600.0)

            decay_rate = self._extract_actr_decay_rate(metadata, self.decay_rate)

            # Shield recent memories created in the last 24 hours from pruning (deletion)
            is_shielded = hours_since < 24.0

            # Milestones (importance >= 0.7) are protected from active importance decay,
            # but they are allowed to decay and be pruned to the archive when inactive.
            activation = math.log(n_recalls) - decay_rate * math.log(hours_since + 1.0)

            # Dual-threshold active pruning:
            # Distractors (< 0.5) pruned below -3.5, Anecdotes/Milestones (>= 0.5) pruned below -4.5
            threshold = -3.5 if importance_score < 0.5 else -4.5
            if activation < threshold and not is_shielded:
                to_delete.append(mem_id)
            elif importance_score < 0.7:
                # Decay importance score slightly for non-milestones
                new_importance = max(0.01, importance_score * 0.8)
                to_update.append((new_importance, mem_id))

        return to_delete, to_update

    async def _archive_and_delete_decayed_memories(self, conn, to_delete: list) -> None:
        """Copy pruned rows to `archived_memories`, delete them from the
        active table (and Qdrant), for whichever backend `conn` is."""
        if self.is_sqlite:
            placeholders = ",".join("?" for _ in to_delete)
            archive_sql = f"""
                INSERT INTO archived_memories (
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, speaker, record_type, valid_from, valid_until, contradicts_id,
                    lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding
                )
                SELECT
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, speaker, record_type, valid_from, valid_until, contradicts_id,
                    lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding
                FROM memories
                WHERE id IN ({placeholders})
                ON CONFLICT(id) DO UPDATE SET
                    recall_count = excluded.recall_count,
                    last_recalled_at = excluded.last_recalled_at,
                    importance_score = excluded.importance_score
                """  # nosec B608 - placeholders contains only generated '?' markers
            legacy_archive_sql = (
                """
                INSERT INTO archived_memories (
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding
                )
                SELECT
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding
                FROM memories
                WHERE id IN ("""
                + placeholders
                + """)
                ON CONFLICT(id) DO UPDATE SET
                    recall_count = excluded.recall_count,
                    last_recalled_at = excluded.last_recalled_at,
                    importance_score = excluded.importance_score
                """
            )
            try:
                await conn.execute(archive_sql, *to_delete)
            except Exception as exc:
                if not self._is_missing_column_error(exc):
                    raise
                logger.warning("Archive used compatibility schema: %s", exc)
                await conn.execute(legacy_archive_sql, *to_delete)
            # Delete from memories
            await conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})",  # nosec B608 - placeholders is only comma-separated "?" marks, ids bound via *to_delete
                *to_delete,
            )
        else:
            archive_sql = """
                INSERT INTO archived_memories (
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, speaker, record_type, valid_from, valid_until, contradicts_id,
                    lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding
                )
                SELECT
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, speaker, record_type, valid_from, valid_until, contradicts_id,
                    lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding::halfvec
                FROM memories
                WHERE id = ANY($1)
                ON CONFLICT(id) DO UPDATE SET
                    recall_count = EXCLUDED.recall_count,
                    last_recalled_at = EXCLUDED.last_recalled_at,
                    importance_score = EXCLUDED.importance_score
                """
            legacy_archive_sql = """
                INSERT INTO archived_memories (
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding
                )
                SELECT
                    id, content, raw_content, wing, room, importance_score, emotional_weight,
                    valence, certainty, source, recall_count, last_recalled_at, created_at,
                    metadata, lifespan_stage, crisis, virtue, relations, relation_circles, modality, embedding::halfvec
                FROM memories
                WHERE id = ANY($1)
                ON CONFLICT(id) DO UPDATE SET
                    recall_count = EXCLUDED.recall_count,
                    last_recalled_at = EXCLUDED.last_recalled_at,
                    importance_score = EXCLUDED.importance_score
                """
            try:
                await conn.execute(archive_sql, to_delete)
            except Exception as exc:
                if not self._is_missing_column_error(exc):
                    raise
                logger.warning("Archive used compatibility schema: %s", exc)
                await conn.execute(legacy_archive_sql, to_delete)
            # Delete from memories
            await conn.execute("DELETE FROM memories WHERE id = ANY($1)", to_delete)

        # Delete from Qdrant if active
        if self.qdrant_store and self.qdrant_store.client:
            try:
                from qdrant_client.http import models

                await asyncio.to_thread(
                    self.qdrant_store.client.delete,
                    collection_name=self.qdrant_store.collection_name,
                    points_selector=models.PointIdsList(
                        points=[str(pid) for pid in to_delete]
                    ),
                )
            except Exception as qe:
                logger.error(f"Failed to delete points from Qdrant: {qe}")

        logger.info(
            f"🗑️ Pruned {len(to_delete)} memories with base activation below {self.pruning_threshold} to subconscious archive."
        )

    async def _apply_importance_updates(self, conn, to_update: list) -> None:
        if self.is_sqlite:
            for importance, mem_id in to_update:
                await conn.execute(
                    "UPDATE memories SET importance_score = ? WHERE id = ?",
                    importance,
                    mem_id,
                )
        else:
            # Batch update for PostgreSQL
            await conn.executemany(
                "UPDATE memories SET importance_score = $1 WHERE id = $2",
                to_update,
            )

    async def _cleanup_expired_archived_memories(self, conn, current_time=None) -> None:
        """Permanent cleanup on archived_memories based on biological timelines."""
        now_cleanup = current_time if current_time is not None else clock.now(UTC)
        cutoff_distractors = now_cleanup - timedelta(days=30)
        cutoff_anecdotes = now_cleanup - timedelta(days=180)
        cutoff_milestones = now_cleanup - timedelta(days=720)

        try:
            # COALESCE(last_recalled_at, created_at): a memory that was
            # archived but never recalled has NULL last_recalled_at, and
            # `NULL < cutoff` is NULL (never true) -- such rows would be
            # immortal in the archive. Age them out by creation time
            # instead, matching how the activation SQL already coalesces
            # this column (db/schema.sql).
            if self.is_sqlite:
                # Normalise both operands through datetime(): the SQLite
                # fallback stores timestamps as text, so a raw string
                # comparison of differing ISO formats/precision/offsets
                # is unreliable. datetime() canonicalises to UTC.
                await conn.execute(
                    """
                    DELETE FROM archived_memories
                    WHERE (importance_score < 0.5 AND datetime(COALESCE(last_recalled_at, created_at)) < datetime(?))
                       OR (importance_score >= 0.5 AND importance_score < 0.7 AND datetime(COALESCE(last_recalled_at, created_at)) < datetime(?))
                       OR (importance_score >= 0.7 AND importance_score < 0.9 AND datetime(COALESCE(last_recalled_at, created_at)) < datetime(?));
                    """,
                    cutoff_distractors.isoformat(),
                    cutoff_anecdotes.isoformat(),
                    cutoff_milestones.isoformat(),
                )
            else:
                await conn.execute(
                    """
                    DELETE FROM archived_memories
                    WHERE (importance_score < 0.5 AND COALESCE(last_recalled_at, created_at) < $1)
                       OR (importance_score >= 0.5 AND importance_score < 0.7 AND COALESCE(last_recalled_at, created_at) < $2)
                       OR (importance_score >= 0.7 AND importance_score < 0.9 AND COALESCE(last_recalled_at, created_at) < $3);
                    """,
                    cutoff_distractors,
                    cutoff_anecdotes,
                    cutoff_milestones,
                )
            logger.info(
                "🗑️ Completed permanent cleanup on subconscious archived memories."
            )
        except Exception as clean_err:
            logger.error(f"Failed subconscious archive cleanup: {clean_err}")

    async def apply_actr_decay(self, memory_contents: list[str], current_time=None):
        """Decays the importance score of consolidated raw episodic memories using ACT-R feedback."""
        try:
            if not memory_contents:
                return

            # Deduplicate memory contents to prevent repeated decay updates in SQLite loop
            unique_contents = list(dict.fromkeys(memory_contents))

            async with self.pool.acquire() as conn:
                # 1. Fetch matching memories
                where, args = self._in_predicate("content", unique_contents)
                rows = await conn.fetch(
                    "SELECT id, content, recall_count, created_at, metadata, "
                    f"importance_score FROM memories WHERE {where}",  # nosec B608 - where comes from _in_predicate, values bound via *args
                    *args,
                )

                if not rows:
                    return

                # 2. Decide which rows get pruned vs. softened
                to_delete, to_update = self._compute_actr_decay(rows, current_time)

                # 3. Execute Archiving, Deletions and Updates
                if to_delete:
                    await self._archive_and_delete_decayed_memories(conn, to_delete)

                if to_update:
                    await self._apply_importance_updates(conn, to_update)

                # 4. Permanent Cleanup on archived_memories based on biological timelines
                await self._cleanup_expired_archived_memories(conn, current_time)

            # Pruning and importance decay change what search over the active
            # `memories` table should return, so drop the L1 cache when either
            # actually mutated rows. (Archive cleanup alone touches only
            # archived_memories, which the cached search path never reads.)
            if to_delete or to_update:
                self._invalidate_l1_cache()

            logger.info(
                f"📉 Checked and decayed {len(rows)} memories (pruned: {len(to_delete)})."
            )
        except Exception as e:
            logger.error(f"Failed to apply ACT-R decay pruning: {e}")

    async def add_visual_screen_trace(
        self, description: str, valence: float = 0.0, arousal: float = 0.5
    ) -> None:
        """Stores a screen-sourced salient visual episode (P3-1).

        Screen captures are more privacy-sensitive than camera captures -- a
        screen can show anything open on the machine, not just the user's
        face -- so these go through a dedicated table with a hard TTL
        (`prune_expired_visual_screen_traces`, `Config.VISUAL_SCREEN_TRACE_TTL_H`)
        rather than the graded ACT-R fade `memories` rows get. Camera-sourced
        visual traces go through `add_memory` instead (modality="visual",
        source="vision_camera") and do follow that normal lifecycle -- both
        paths are gated by the same salience check in the caller
        (SubconsciousAgent._on_vision_description).
        """
        trace_id = str(uuid.uuid4())
        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    await conn.execute(
                        "INSERT INTO visual_screen_traces "
                        "(id, description, valence, arousal) VALUES (?, ?, ?, ?)",
                        trace_id,
                        description,
                        valence,
                        arousal,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO visual_screen_traces "
                        "(id, description, valence, arousal) VALUES ($1, $2, $3, $4)",
                        trace_id,
                        description,
                        valence,
                        arousal,
                    )
        except Exception as e:
            logger.error(f"Failed to store visual screen trace: {e}")

    async def prune_expired_visual_screen_traces(
        self, ttl_hours: float | None = None, current_time=None
    ) -> None:
        """Deletes screen-sourced visual traces past their privacy TTL.

        Called from the same periodic maintenance pass that already runs
        ACT-R decay (`SubconsciousAgent._run_consolidation_pass`), not a new
        tick path. Unlike `apply_actr_decay`'s pruning, this has no rowcount
        to report back: the SQLite fallback's `execute()` discards its
        cursor result (`SQLiteConnection.execute`, sqlite_fallback.py), so a
        count would be accurate on Postgres and silently wrong on SQLite --
        this stays honest about that rather than fabricate one.
        """
        ttl = ttl_hours if ttl_hours is not None else Config.VISUAL_SCREEN_TRACE_TTL_H
        now = (
            self._as_aware_utc(current_time)
            if current_time is not None
            else clock.now(UTC)
        )
        cutoff = now - timedelta(hours=ttl)
        try:
            async with self.pool.acquire() as conn:
                if self.is_sqlite:
                    # Normalise via datetime(): stored as text, and a raw
                    # string comparison of differing ISO precision/offsets is
                    # unreliable (same reasoning as apply_actr_decay's archive
                    # cleanup above).
                    await conn.execute(
                        "DELETE FROM visual_screen_traces "
                        "WHERE datetime(created_at) < datetime(?)",
                        cutoff.isoformat(),
                    )
                else:
                    await conn.execute(
                        "DELETE FROM visual_screen_traces WHERE created_at < $1",
                        cutoff,
                    )
        except Exception as e:
            logger.error(f"Failed to prune expired visual screen traces: {e}")

    async def close(self):
        """Cancel refresh work before closing the resources it uses."""
        tasks = [task for task in self._background_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        await self._http_client.aclose()
