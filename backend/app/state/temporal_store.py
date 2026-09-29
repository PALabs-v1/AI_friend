"""SQLite repository for append-only experiences and temporal beliefs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .memory_records import (
    BeliefRecord,
    ContradictionDecision,
    DuplicateRecordError,
    ExperienceRecord,
    InvalidIntervalError,
    MemoryStoreError,
    ProcedureRecord,
    RecordNotFoundError,
    classify_contradiction,
)
from .temporal_intent import historical_intent, query_year

_CORRECTION_CUES = (
    "i meant",
    "i mean",
    "actually",
    "correction",
    "sorry,",
    "not ",
    "rather than",
)
_TEMPORAL_CUES = ("now", "currently", "these days", "since", "moved", "switched")
_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


_historical_intent = historical_intent  # one parser for both stores
# Current (ACTIVE/DISPUTED) rows one write considers. Validity windows
# keep ACTIVE to one per slot; unresolved disputes are what can accumulate.
SLOT_CURRENT_LIMIT = 32
_query_year = query_year


def _query_terms(text: str) -> set[str]:
    ignored = {"what", "where", "when", "who", "used", "back", "then", "was", "were"}
    terms = set()
    for raw_word in _WORD_RE.findall(text):
        word = raw_word.casefold().rstrip("'s")
        if word.endswith("s") and len(word) > 4:
            word = word[:-1]
        if len(word) > 2 and word not in ignored:
            terms.add(word)
    return terms


class TemporalMemoryStore:
    """Persist memory truth with serialized SQLite transitions.

    SQLite calls run in worker threads. The instance lock protects its shared
    connection, while ``BEGIN IMMEDIATE`` serializes writers using separate
    store instances backed by the same database file.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=5.0,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            try:
                self._connection.execute("PRAGMA busy_timeout = 5000")
                if self.db_path != ":memory:":
                    self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.executescript(
                    """
                CREATE TABLE IF NOT EXISTS experiences (
                    record_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    participants TEXT NOT NULL,
                    interval_start REAL NOT NULL,
                    interval_end REAL NOT NULL,
                    source_evidence_ids TEXT NOT NULL,
                    appraisal_snapshot TEXT NOT NULL,
                    action_id TEXT,
                    outcome_id TEXT,
                    summary TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS beliefs (
                    record_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from REAL NOT NULL,
                    valid_until REAL,
                    recorded_at REAL NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    superseded_by TEXT,
                    contradicts_id TEXT,
                    provenance TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS beliefs_fts USING fts5(
                    subject, predicate, object,
                    content='beliefs', content_rowid='rowid'
                );

                CREATE INDEX IF NOT EXISTS beliefs_current_idx
                    ON beliefs (subject, status, valid_from, valid_until);
                CREATE INDEX IF NOT EXISTS beliefs_recorded_idx
                    ON beliefs (subject, recorded_at, record_id);
                CREATE INDEX IF NOT EXISTS beliefs_slot_lookup_idx
                    ON beliefs (lower(subject), lower(predicate), valid_from);

                -- Beliefs already mirrored into MemoryStore, so each is
                -- indexed once, not on every write to its slot.
                CREATE TABLE IF NOT EXISTS belief_index_marks (
                    record_id TEXT PRIMARY KEY,
                    indexed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS belief_reinforcements (
                    reinforcement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    existing_record_id TEXT NOT NULL,
                    new_record_id TEXT NOT NULL UNIQUE,
                    confidence REAL NOT NULL,
                    recorded_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS procedures (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    preconditions_json TEXT,
                    postconditions_json TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS experiences_no_delete
                BEFORE DELETE ON experiences BEGIN
                    SELECT RAISE(ABORT, 'experience rows are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS beliefs_no_delete
                BEFORE DELETE ON beliefs BEGIN
                    SELECT RAISE(ABORT, 'belief rows are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS beliefs_fts_insert
                AFTER INSERT ON beliefs BEGIN
                    INSERT INTO beliefs_fts(rowid, subject, predicate, object)
                    VALUES (new.rowid, new.subject, new.predicate, new.object);
                END;
                CREATE TRIGGER IF NOT EXISTS beliefs_fts_update
                AFTER UPDATE OF subject, predicate, object ON beliefs BEGIN
                    INSERT INTO beliefs_fts(beliefs_fts, rowid, subject, predicate, object)
                    VALUES ('delete', old.rowid, old.subject, old.predicate, old.object);
                    INSERT INTO beliefs_fts(rowid, subject, predicate, object)
                    VALUES (new.rowid, new.subject, new.predicate, new.object);
                END;
                CREATE TRIGGER IF NOT EXISTS reinforcements_no_delete
                BEFORE DELETE ON belief_reinforcements BEGIN
                    SELECT RAISE(ABORT, 'reinforcement rows are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS procedures_no_delete
                BEFORE DELETE ON procedures BEGIN
                    SELECT RAISE(ABORT, 'procedure rows are append-only');
                END;
                INSERT INTO beliefs_fts(beliefs_fts) VALUES ('rebuild');
                """
                )
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)

    @staticmethod
    def is_historical_query(query: str) -> bool:
        """Return whether retrieval language requests prior truth."""
        return _historical_intent(query)

    async def store_experience(self, record: ExperienceRecord) -> None:
        """Append an experience once; duplicate identifiers are rejected."""
        await asyncio.to_thread(self._store_experience_sync, record)

    def _store_experience_sync(self, record: ExperienceRecord) -> None:
        with self._lock:
            try:
                self._begin_write()
                self._connection.execute(
                    """
                    INSERT INTO experiences (
                        record_id, session_id, participants, interval_start,
                        interval_end, source_evidence_ids, appraisal_snapshot,
                        action_id, outcome_id, summary, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.record_id,
                        record.session_id,
                        json.dumps(record.participants),
                        record.interval_start,
                        record.interval_end,
                        json.dumps(record.source_evidence_ids),
                        json.dumps(record.appraisal_snapshot),
                        record.action_id,
                        record.outcome_id,
                        record.summary,
                        record.recorded_at,
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                self._raise_sqlite_error(error)
            except BaseException:
                self._connection.rollback()
                raise

    async def get_experience(self, record_id: str) -> ExperienceRecord | None:
        """Return the immutable experience identified by ``record_id``."""
        return await asyncio.to_thread(self._get_experience_sync, record_id)

    def _get_experience_sync(self, record_id: str) -> ExperienceRecord | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT * FROM experiences WHERE record_id = ?", (record_id,)
                ).fetchone()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        if row is None:
            return None
        return ExperienceRecord(
            record_id=str(row["record_id"]),
            session_id=str(row["session_id"]),
            participants=list(json.loads(row["participants"])),
            interval_start=float(row["interval_start"]),
            interval_end=float(row["interval_end"]),
            source_evidence_ids=list(json.loads(row["source_evidence_ids"])),
            appraisal_snapshot={
                str(key): float(value)
                for key, value in json.loads(row["appraisal_snapshot"]).items()
            },
            action_id=row["action_id"],
            outcome_id=row["outcome_id"],
            summary=str(row["summary"]),
            recorded_at=float(row["recorded_at"]),
        )

    async def store_belief(self, record: BeliefRecord) -> None:
        """Store one belief exactly as supplied without overwriting history."""
        await asyncio.to_thread(self._store_belief_sync, record)

    async def record_assertion(
        self,
        record: BeliefRecord,
        *,
        explicit_correction: bool = False,
        classifier_context: str = "",
        classify_deterministic: Callable[
            [list[BeliefRecord], BeliefRecord, str], Awaitable[str | None]
        ]
        | None = None,
        classify_ambiguous: Callable[
            [list[BeliefRecord], BeliefRecord, str], Awaitable[str]
        ]
        | None = None,
    ) -> str:
        """Classify and append an assertion against its current semantic slot.

        The optional classifier is only called for ambiguous, different-value
        claims and receives at most the three newest slot neighbors.
        """
        # Bounded: the current rows decide the relation and the classifier
        # sees the three newest, so a long slot history is never loaded.
        current = await self.query_slot_beliefs(
            record.subject,
            record.predicate,
            statuses=("ACTIVE", "DISPUTED"),
            limit=SLOT_CURRENT_LIMIT,
        )
        neighbors = await self.query_slot_beliefs(
            record.subject, record.predicate, limit=3
        )
        active = [item for item in current if item.status == "ACTIVE"]
        disputed = [item for item in current if item.status == "DISPUTED"]
        if not active and not disputed:
            await self.store_belief(record)
            return "NEW"

        existing = max(
            active or disputed, key=lambda item: (item.valid_from, item.recorded_at)
        )
        relation = classify_contradiction(
            existing, record, explicit_correction=explicit_correction
        )
        # A later effective date can nominate an update, but only the
        # production classifier may confirm that the semantic value changed.
        if relation in {"CONFLICT", "UPDATE"} and (
            classify_deterministic is not None or classify_ambiguous is not None
        ):
            shortlist = neighbors[:3]
            deterministic = (
                await classify_deterministic(shortlist, record, classifier_context)
                if classify_deterministic is not None
                else None
            )
            if deterministic in {"UPDATE", "CORRECTION", "ELABORATION"}:
                relation = deterministic
            elif classify_ambiguous is not None:
                candidate = await classify_ambiguous(
                    shortlist, record, classifier_context
                )
                if candidate in {"UPDATE", "CORRECTION", "CONFLICT", "ELABORATION"}:
                    relation = candidate
        if existing.status == "DISPUTED":
            if relation in {"UPDATE", "CORRECTION"}:
                transition_at = max(record.valid_from, existing.valid_from + 1e-6)
                resolved = record.model_copy(update={"valid_from": transition_at})
                await asyncio.to_thread(
                    self._resolve_disputed_sync, existing.record_id, resolved, relation
                )
            else:
                await self.store_belief(record)
            return relation
        if relation in {"UPDATE", "CORRECTION", "CONFLICT"}:
            transition_at = max(record.valid_from, existing.valid_from + 1e-6)
            record = record.model_copy(update={"valid_from": transition_at})
        decision = ContradictionDecision(
            contradiction_type=relation,
            existing_record_id=existing.record_id,
            new_record_id=record.record_id,
            action_taken=relation.lower(),
            reason="explicit correction" if explicit_correction else "slot relation",
        )
        await self.apply_contradiction(decision, record)
        return relation

    def _resolve_disputed_sync(
        self, existing_id: str, new_record: BeliefRecord, relation: str
    ) -> None:
        """Start a fresh current projection when new evidence resolves a dispute."""
        with self._lock:
            try:
                self._begin_write()
                existing = self._require_belief(existing_id)
                if existing.status != "DISPUTED":
                    raise ValueError("Only disputed beliefs can be resolved here")
                if relation == "UPDATE":
                    self._connection.execute(
                        "UPDATE beliefs SET status = 'SUPERSEDED', valid_until = ?, "
                        "superseded_by = ? WHERE record_id = ?",
                        (new_record.valid_from, new_record.record_id, existing_id),
                    )
                elif relation == "CORRECTION":
                    self._connection.execute(
                        "UPDATE beliefs SET status = 'INVALIDATED', valid_until = ?, "
                        "contradicts_id = ? WHERE record_id = ?",
                        (time.time(), new_record.record_id, existing_id),
                    )
                else:
                    raise ValueError(f"Unsupported dispute resolution: {relation}")
                self._insert_belief(
                    new_record.model_copy(
                        update={
                            "status": "ACTIVE",
                            "contradicts_id": existing_id,
                        }
                    )
                )
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                self._raise_sqlite_error(error)
            except BaseException:
                self._connection.rollback()
                raise

    async def query_slot_beliefs(
        self,
        subject: str,
        predicate: str,
        *,
        limit: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[BeliefRecord]:
        """Return non-invalidated records for one normalized subject/predicate,
        newest first; `limit` and `statuses` bound it on hot paths."""
        rows = await asyncio.to_thread(
            self._query_slot_beliefs_sync,
            subject.casefold(),
            predicate.casefold(),
            limit,
            statuses,
        )
        return rows

    async def unindexed_slot_beliefs(
        self, subject: str, predicate: str, *, limit: int
    ) -> list[BeliefRecord]:
        """Slot beliefs not yet mirrored into MemoryStore, newest first."""
        return await asyncio.to_thread(
            self._unindexed_slot_beliefs_sync,
            subject.casefold(),
            predicate.casefold(),
            limit,
        )

    async def mark_indexed(self, record_ids: list[str]) -> None:
        """Record that these beliefs are mirrored into MemoryStore."""
        if record_ids:
            await asyncio.to_thread(self._mark_indexed_sync, list(record_ids))

    def _unindexed_slot_beliefs_sync(
        self, subject: str, predicate: str, limit: int
    ) -> list[BeliefRecord]:
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT b.* FROM beliefs b LEFT JOIN belief_index_marks m "
                    "ON m.record_id = b.record_id WHERE lower(b.subject) = ? "
                    "AND lower(b.predicate) = ? AND b.status <> 'INVALIDATED' "
                    "AND m.record_id IS NULL "
                    "ORDER BY b.recorded_at DESC, b.record_id DESC LIMIT ?",
                    (subject, predicate, max(0, int(limit))),
                ).fetchall()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        return [self._belief_from_row(row) for row in rows]

    def _mark_indexed_sync(self, record_ids: list[str]) -> None:
        now = time.time()
        with self._lock:
            try:
                self._connection.executemany(
                    "INSERT OR IGNORE INTO belief_index_marks (record_id, indexed_at) "
                    "VALUES (?, ?)",
                    [(record_id, now) for record_id in record_ids],
                )
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                self._raise_sqlite_error(error)

    async def subject_in_query(self, query: str) -> str | None:
        """Resolve one named subject, returning an empty sentinel if ambiguous."""
        return await asyncio.to_thread(self._subject_in_query_sync, query)

    async def has_multiple_subjects(self) -> bool:
        """Return whether an unscoped personal query must fail closed."""
        return await asyncio.to_thread(self._has_multiple_subjects_sync)

    def _has_multiple_subjects_sync(self) -> bool:
        with self._lock:
            rows = self._connection.execute(
                "SELECT lower(subject) FROM beliefs GROUP BY lower(subject) LIMIT 2"
            ).fetchall()
        return len(rows) > 1

    def _subject_in_query_sync(self, query: str) -> str | None:
        words = _WORD_RE.findall(query.casefold())
        windows = [
            " ".join(words[start : start + width])
            for width in range(min(5, len(words)), 0, -1)
            for start in range(len(words) - width + 1)
        ]
        if not windows:
            return None
        placeholders = ", ".join("?" for _ in windows)
        with self._lock:
            rows = self._connection.execute(
                "SELECT subject FROM beliefs WHERE lower(subject) IN ("
                f"{placeholders}) GROUP BY lower(subject)",
                tuple(windows),
            ).fetchall()
        matches = {str(row["subject"]).casefold(): str(row["subject"]) for row in rows}
        if len(matches) > 1:
            return ""
        return next(iter(matches.values()), None)

    def _query_slot_beliefs_sync(
        self,
        subject: str,
        predicate: str,
        limit: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[BeliefRecord]:
        sql = (
            "SELECT * FROM beliefs WHERE lower(subject) = ? "
            "AND lower(predicate) = ? AND status <> 'INVALIDATED'"
        )
        params: list[Any] = [subject, predicate]
        if statuses:
            sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        sql += " ORDER BY recorded_at DESC, record_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock:
            try:
                rows = self._connection.execute(sql, tuple(params)).fetchall()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        return [self._belief_from_row(row) for row in rows]

    async def search_beliefs(
        self,
        query: str,
        *,
        limit: int = 3,
        as_of: datetime | float | None = None,
        include_disputed: bool = False,
        subject: str | None = None,
    ) -> list[BeliefRecord]:
        """Return slot-matched current facts or reachable history for a query."""
        if limit <= 0 or not query.strip():
            return []
        year_query = _query_year(query)
        historical = _historical_intent(query)
        terms = _query_terms(query)
        year_start = (
            datetime(year_query[1], 1, 1, tzinfo=UTC).timestamp()
            if year_query is not None
            else None
        )
        year_end = (
            datetime(year_query[1] + 1, 1, 1, tzinfo=UTC).timestamp()
            if year_query is not None
            else None
        )
        query_time = (
            year_start - 1e-6
            if year_query is not None and year_query[0] == "before"
            else self._as_timestamp(as_of)
            if as_of is not None
            else time.time()
        )
        candidates = await asyncio.to_thread(
            self._search_beliefs_sync,
            sorted(terms),
            query_time,
            historical,
            as_of is not None
            or (year_query is None and not historical)
            or (year_query is not None and year_query[0] == "before"),
            limit,
            include_disputed,
            year_query,
            year_start,
            year_end,
            subject,
        )
        ranked = []
        for item in candidates:
            slot_terms = _query_terms(
                f"{item.subject} {item.predicate.replace('_', ' ')} {item.object}"
            )
            overlap = len(terms & slot_terms)
            subject_match = bool(terms & _query_terms(item.subject))
            predicate_match = bool(terms & _query_terms(item.predicate))
            if overlap == 0:
                continue
            rank = overlap + 2 * subject_match + 2 * predicate_match
            ranked.append((rank, item.valid_from, item))
        ranked.sort(
            key=lambda row: (
                row[0],
                historical and row[2].status == "SUPERSEDED",
                row[1],
            ),
            reverse=True,
        )
        return [row[2] for row in ranked[:limit]]

    def _search_beliefs_sync(
        self,
        terms: list[str],
        query_time: float,
        historical: bool,
        use_validity_window: bool,
        candidate_limit: int,
        include_disputed: bool = False,
        year_query: tuple[str, int] | None = None,
        year_start: float | None = None,
        year_end: float | None = None,
        subject: str | None = None,
    ) -> list[BeliefRecord]:
        if not terms:
            return []
        fts_query = " OR ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms
        )
        if historical:
            status_filter = "status IN ('ACTIVE', 'SUPERSEDED')"
        elif include_disputed:
            status_filter = "status IN ('ACTIVE', 'DISPUTED')"
        else:
            status_filter = "status = 'ACTIVE'"
        time_filter = (
            " AND valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)"
            if use_validity_window
            else ""
        )
        parameters: list[object] = [fts_query]
        subject_filter = ""
        if subject is not None:
            subject_filter = " AND lower(beliefs.subject) = lower(?)"
            parameters.append(subject)
        if use_validity_window and year_query is None:
            parameters.extend((query_time, query_time))
        elif year_query is not None and year_query[0] == "during":
            time_filter = (
                " AND valid_from < ? AND (valid_until IS NULL OR valid_until > ?)"
            )
            parameters.extend((year_end, year_start))
        elif year_query is not None and year_query[0] == "after":
            time_filter = " AND (valid_until IS NULL OR valid_until > ?)"
            parameters.append(year_end)
        elif use_validity_window and year_query is not None:
            parameters.extend((query_time, query_time))
        parameters.append(candidate_limit)
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT beliefs.* FROM beliefs JOIN beliefs_fts "
                    "ON beliefs_fts.rowid = beliefs.rowid "
                    f"WHERE beliefs_fts MATCH ? AND {status_filter}{subject_filter}{time_filter} "
                    "ORDER BY (status = 'SUPERSEDED') DESC, "
                    "bm25(beliefs_fts, 4.0, 2.0, 1.0), valid_from DESC LIMIT ?",
                    parameters,
                ).fetchall()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        return [self._belief_from_row(row) for row in rows]

    def _store_belief_sync(self, record: BeliefRecord) -> None:
        with self._lock:
            try:
                self._begin_write()
                overlaps = self._overlapping_beliefs(record)
                if overlaps:
                    self._connection.executemany(
                        "UPDATE beliefs SET status = 'DISPUTED', confidence = confidence * 0.5, "
                        "valid_until = valid_from "
                        "WHERE record_id = ?",
                        [
                            (row["record_id"],)
                            for row in overlaps
                            if row["status"] == "ACTIVE"
                        ],
                    )
                    record = record.model_copy(
                        update={
                            "status": "DISPUTED",
                            "confidence": record.confidence * 0.5,
                            "valid_until": record.valid_from,
                        }
                    )
                self._insert_belief(record)
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                self._raise_sqlite_error(error)
            except BaseException:
                self._connection.rollback()
                raise

    async def get_belief(self, record_id: str) -> BeliefRecord | None:
        """Return the belief identified by ``record_id``, if it exists."""
        return await asyncio.to_thread(self._get_belief_sync, record_id)

    def _get_belief_sync(self, record_id: str) -> BeliefRecord | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT * FROM beliefs WHERE record_id = ?", (record_id,)
                ).fetchone()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        return self._belief_from_row(row) if row is not None else None

    async def query_current_beliefs(
        self, subject: str | None = None, as_of: datetime | float | None = None
    ) -> list[BeliefRecord]:
        """Return active beliefs now or beliefs valid at a supplied time."""
        query_time = time.time() if as_of is None else self._as_timestamp(as_of)
        return await asyncio.to_thread(
            self._query_current_beliefs_sync, subject, query_time, as_of is not None
        )

    def _query_current_beliefs_sync(
        self, subject: str | None, query_time: float, includes_superseded: bool
    ) -> list[BeliefRecord]:
        if includes_superseded:
            sql = (
                "SELECT * FROM beliefs WHERE status IN ('ACTIVE', 'SUPERSEDED') "
                "AND valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)"
            )
        else:
            sql = (
                "SELECT * FROM beliefs WHERE status = 'ACTIVE' "
                "AND valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)"
            )
        parameters: tuple[object, ...] = (query_time, query_time)
        if subject is not None:
            sql += " AND subject = ?"
            parameters += (subject,)
        sql += " ORDER BY valid_from DESC, confidence DESC, recorded_at DESC, record_id"
        with self._lock:
            try:
                rows = self._connection.execute(sql, parameters).fetchall()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        return [self._belief_from_row(row) for row in rows]

    async def query_historical_beliefs(
        self, subject: str | None = None
    ) -> list[BeliefRecord]:
        """Return every stored belief in recorded-time order."""
        return await asyncio.to_thread(self._query_historical_beliefs_sync, subject)

    def _query_historical_beliefs_sync(self, subject: str | None) -> list[BeliefRecord]:
        sql = "SELECT * FROM beliefs WHERE status <> 'INVALIDATED'"
        parameters: tuple[object, ...] = ()
        if subject is not None:
            sql += " AND subject = ?"
            parameters = (subject,)
        sql += " ORDER BY recorded_at, record_id"
        with self._lock:
            try:
                rows = self._connection.execute(sql, parameters).fetchall()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        return [self._belief_from_row(row) for row in rows]

    async def apply_contradiction(
        self, decision: ContradictionDecision, new_record: BeliefRecord
    ) -> None:
        """Atomically apply one classified truth transition and its audit data."""
        await asyncio.to_thread(self._apply_contradiction_sync, decision, new_record)

    def _apply_contradiction_sync(
        self, decision: ContradictionDecision, new_record: BeliefRecord
    ) -> None:
        if decision.new_record_id != new_record.record_id:
            raise ValueError("Decision new_record_id must match new_record.record_id")
        with self._lock:
            try:
                self._begin_write()
                existing = self._require_belief(decision.existing_record_id)
                if decision.new_record_id == existing.record_id:
                    raise InvalidIntervalError("A belief cannot supersede itself")
                if existing.status != "ACTIVE":
                    raise ValueError(
                        "Contradiction transitions require an active existing belief"
                    )
                if decision.contradiction_type == "UPDATE":
                    if self._would_create_supersession_cycle(
                        existing.record_id, decision.new_record_id
                    ):
                        raise InvalidIntervalError(
                            "Supersession chains must be acyclic"
                        )
                    if new_record.valid_from <= existing.valid_from:
                        raise InvalidIntervalError(
                            "An update cannot start before or at the belief it supersedes"
                        )
                    self._connection.execute(
                        """
                        UPDATE beliefs
                        SET valid_until = ?, status = 'SUPERSEDED', superseded_by = ?
                        WHERE record_id = ?
                        """,
                        (
                            new_record.valid_from,
                            new_record.record_id,
                            existing.record_id,
                        ),
                    )
                    self._insert_belief(
                        new_record.model_copy(
                            update={
                                "status": "ACTIVE",
                                "contradicts_id": existing.record_id,
                            }
                        )
                    )
                elif decision.contradiction_type == "CORRECTION":
                    self._connection.execute(
                        """
                        UPDATE beliefs
                        SET valid_until = ?, status = 'INVALIDATED', contradicts_id = ?
                        WHERE record_id = ?
                        """,
                        (time.time(), new_record.record_id, existing.record_id),
                    )
                    self._insert_belief(
                        new_record.model_copy(
                            update={
                                "status": "ACTIVE",
                                "contradicts_id": existing.record_id,
                            }
                        )
                    )
                elif decision.contradiction_type == "CONFLICT":
                    self._connection.execute(
                        """
                        UPDATE beliefs
                        SET valid_until = ?, status = 'DISPUTED', confidence = confidence * 0.5,
                            contradicts_id = ?
                        WHERE record_id = ?
                        """,
                        (
                            existing.valid_from,
                            new_record.record_id,
                            existing.record_id,
                        ),
                    )
                    self._insert_belief(
                        new_record.model_copy(
                            update={
                                "status": "DISPUTED",
                                "confidence": new_record.confidence * 0.5,
                                "contradicts_id": existing.record_id,
                                "valid_until": new_record.valid_from,
                            }
                        )
                    )
                else:
                    self._connection.execute(
                        """
                        UPDATE beliefs
                        SET confidence = CASE WHEN confidence < ? THEN ? ELSE confidence END
                        WHERE record_id = ?
                        """,
                        (
                            new_record.confidence,
                            new_record.confidence,
                            existing.record_id,
                        ),
                    )
                    self._connection.execute(
                        """
                        INSERT INTO belief_reinforcements (
                            existing_record_id, new_record_id, confidence, recorded_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            existing.record_id,
                            new_record.record_id,
                            new_record.confidence,
                            new_record.recorded_at,
                        ),
                    )
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                self._raise_sqlite_error(error)
            except BaseException:
                self._connection.rollback()
                raise

    async def store_procedure(self, record: ProcedureRecord) -> str:
        """Persist a learned procedure and return its stable identifier."""
        return await asyncio.to_thread(self._store_procedure_sync, record)

    def _store_procedure_sync(self, record: ProcedureRecord) -> str:
        with self._lock:
            try:
                self._begin_write()
                self._connection.execute(
                    """
                    INSERT INTO procedures (
                        id, name, steps_json, preconditions_json,
                        postconditions_json, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.procedure_id,
                        record.name,
                        json.dumps(record.steps),
                        json.dumps(record.preconditions),
                        json.dumps(record.postconditions),
                        str(record.created_at),
                        record.status,
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                self._raise_sqlite_error(error)
            except BaseException:
                self._connection.rollback()
                raise
        return record.procedure_id

    async def get_procedure(self, procedure_id: str) -> ProcedureRecord | None:
        """Return a persisted procedure, if it exists."""
        return await asyncio.to_thread(self._get_procedure_sync, procedure_id)

    def _get_procedure_sync(self, procedure_id: str) -> ProcedureRecord | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT * FROM procedures WHERE id = ?", (procedure_id,)
                ).fetchone()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)
        if row is None:
            return None
        return ProcedureRecord(
            procedure_id=str(row["id"]),
            name=str(row["name"]),
            steps=list(json.loads(row["steps_json"])),
            preconditions=list(json.loads(row["preconditions_json"] or "[]")),
            postconditions=list(json.loads(row["postconditions_json"] or "[]")),
            created_at=float(row["created_at"]),
            status=str(row["status"]),
        )

    async def close(self) -> None:
        """Close the SQLite connection after outstanding work completes."""
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error as error:
                self._raise_sqlite_error(error)

    def _begin_write(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _insert_belief(self, record: BeliefRecord) -> None:
        if (
            record.valid_until is not None and record.valid_until < record.valid_from
        ) or (record.valid_until == record.valid_from and record.status != "DISPUTED"):
            raise InvalidIntervalError("A validity window must have positive duration")
        rows = self._overlapping_projected_beliefs(record)
        for row in rows:
            raise InvalidIntervalError(
                f"Validity windows overlap for slot {record.subject}/{record.predicate}"
            )
        self._connection.execute(
            """
            INSERT INTO beliefs (
                record_id, subject, predicate, object, valid_from, valid_until,
                recorded_at, confidence, status, superseded_by, contradicts_id,
                provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                record.subject,
                record.predicate,
                record.object,
                record.valid_from,
                record.valid_until,
                record.recorded_at,
                record.confidence,
                record.status,
                record.superseded_by,
                record.contradicts_id,
                record.provenance,
            ),
        )

    def _overlapping_projected_beliefs(self, record: BeliefRecord):
        return self._overlapping_beliefs(record, projection_only=True)

    def _overlapping_beliefs(
        self, record: BeliefRecord, *, projection_only: bool = False
    ):
        status_filter = (
            "AND status IN ('ACTIVE', 'SUPERSEDED')"
            if projection_only
            else "AND status <> 'INVALIDATED'"
        )
        rows = self._connection.execute(
            "SELECT record_id, valid_from, valid_until, status FROM beliefs "
            "WHERE lower(subject) = lower(?) AND lower(predicate) = lower(?) "
            f"{status_filter}",
            (record.subject, record.predicate),
        ).fetchall()
        return [
            row
            for row in rows
            if (
                row["valid_until"] is None
                or record.valid_from < float(row["valid_until"])
            )
            and (
                record.valid_until is None
                or float(row["valid_from"]) < record.valid_until
            )
        ]

    def _would_create_supersession_cycle(self, old_id: str, new_id: str) -> bool:
        seen: set[str] = set()
        cursor = new_id
        while cursor not in seen:
            if cursor == old_id:
                return True
            seen.add(cursor)
            row = self._connection.execute(
                "SELECT superseded_by FROM beliefs WHERE record_id = ?", (cursor,)
            ).fetchone()
            if row is None or row["superseded_by"] is None:
                return False
            cursor = str(row["superseded_by"])
        return True

    def _require_belief(self, record_id: str) -> BeliefRecord:
        row = self._connection.execute(
            "SELECT * FROM beliefs WHERE record_id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Belief record {record_id!r} does not exist")
        return self._belief_from_row(row)

    @staticmethod
    def _as_timestamp(value: datetime | float) -> float:
        """Convert API time values to the REAL values used by SQLite."""
        if isinstance(value, datetime):
            return value.timestamp()
        return float(value)

    @staticmethod
    def _raise_sqlite_error(error: sqlite3.Error) -> None:
        """Translate SQLite implementation failures to storage domain errors."""
        if isinstance(error, sqlite3.IntegrityError):
            message = str(error)
            if "UNIQUE constraint failed" in message or "PRIMARY KEY" in message:
                raise DuplicateRecordError(message) from error
        raise MemoryStoreError(str(error)) from error

    @staticmethod
    def _belief_from_row(row: sqlite3.Row) -> BeliefRecord:
        return BeliefRecord.model_validate(dict(row))
