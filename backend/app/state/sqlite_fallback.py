import asyncio
import logging
import os
import re
import sqlite3
import threading
from datetime import date, datetime

logger = logging.getLogger("sqlite_fallback")


def _convert_timestamp(raw: bytes):
    """TIMESTAMP column converter (registered below, used via PARSE_DECLTYPES).

    The stdlib default converter cannot parse a UTC offset unless the value
    happens to carry exactly six fractional digits (its truncation then cuts
    the offset off by accident): "2026-01-01 00:00:00+00:00" raises
    ValueError inside `fetchall()`. One such row -- any aware `current_time`
    that lands on a whole second -- made every query touching the column
    raise, so memory search on the SQLite fallback returned nothing for
    every question. `fromisoformat` parses offsets, "Z" and any fractional
    precision; an unparseable value is returned as text so it degrades one
    field instead of the whole result set.
    """
    text = raw.decode("utf-8", "replace")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Unparseable TIMESTAMP value left as text: %r", text[:40])
        return text


def _adapt_datetime(value: datetime) -> str:
    # Same text the (deprecated in 3.12) default adapter wrote, made explicit.
    return value.isoformat(" ")


sqlite3.register_converter("timestamp", _convert_timestamp)
sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_adapter(date, lambda value: value.isoformat())


class SQLiteConnection:
    def __init__(self, db_path=":memory:"):
        # Ensure parent directory exists if referencing a file
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        # audit/ROADMAP.md P2-6 (M2-P3): every public method below now runs
        # its body via asyncio.to_thread, so calls arrive on whichever worker
        # thread happens to run them rather than always the caller's thread.
        # check_same_thread=False lifts sqlite3's same-thread restriction on
        # this connection object; it does not make concurrent access safe by
        # itself, which is what self._lock is for. Mirrors the fix already
        # applied to working_memory_store.py's own L2 SQLite fallback.
        self.conn = sqlite3.connect(
            db_path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_schema()

    def _create_schema(self):
        cursor = self.conn.cursor()

        # Sessions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                trust_benevolence REAL DEFAULT 0.5,
                trust_competence REAL DEFAULT 0.5,
                trust_integrity REAL DEFAULT 0.5,
                metadata TEXT DEFAULT '{}'
            )
        """)

        # Schema migration for existing databases
        cursor.execute("PRAGMA table_info(sessions)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        for col in ["trust_benevolence", "trust_competence", "trust_integrity"]:
            if col not in existing_cols:
                cursor.execute(
                    f"ALTER TABLE sessions ADD COLUMN {col} REAL DEFAULT 0.5"
                )

        # Messages Table (Consolidated column handles ACT-R processing)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                consolidated INTEGER DEFAULT 0,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)

        # Agent Configs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_configs (
                id INTEGER PRIMARY KEY,
                personality TEXT,
                background_history TEXT,
                evolved_learnings TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Memories Table (pgvector fallback storage in SQLite)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT,
                raw_content TEXT,
                wing TEXT DEFAULT 'personal',
                room TEXT,
                embedding TEXT,
                importance_score REAL DEFAULT 0.5,
                emotional_weight REAL DEFAULT 0.0,
                valence REAL DEFAULT 0.0,
                certainty REAL DEFAULT 1.0,
                source TEXT DEFAULT 'user',
                recall_count INTEGER DEFAULT 0,
                last_recalled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                speaker TEXT,
                record_type TEXT NOT NULL DEFAULT 'episode',
                valid_from TEXT,
                valid_until TEXT,
                contradicts_id TEXT,
                lifespan_stage TEXT,
                crisis TEXT,
                virtue TEXT,
                relations TEXT,
                relation_circles TEXT,
                modality TEXT
            )
        """)

        # Brain V2: the retrieval index checks a wing's count/max(rowid) on
        # every search; without this that is a full table scan.
        cursor.execute("CREATE INDEX IF NOT EXISTS memories_wing_idx ON memories(wing)")

        # Archived Memories Table (pgvector fallback storage in SQLite)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS archived_memories (
                id TEXT PRIMARY KEY,
                content TEXT,
                raw_content TEXT,
                wing TEXT DEFAULT 'personal',
                room TEXT,
                embedding TEXT,
                importance_score REAL DEFAULT 0.5,
                emotional_weight REAL DEFAULT 0.0,
                valence REAL DEFAULT 0.0,
                certainty REAL DEFAULT 1.0,
                source TEXT DEFAULT 'user',
                recall_count INTEGER DEFAULT 0,
                last_recalled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                speaker TEXT,
                record_type TEXT NOT NULL DEFAULT 'episode',
                valid_from TEXT,
                valid_until TEXT,
                contradicts_id TEXT,
                lifespan_stage TEXT,
                crisis TEXT,
                virtue TEXT,
                relations TEXT,
                relation_circles TEXT,
                modality TEXT
            )
        """)

        # Mental lexicon: learned vocabulary (pgvector fallback storage in SQLite).
        # embedding is nullable TEXT, reserved for a future semantic-neighbor pass.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vocabulary (
                term TEXT PRIMARY KEY,
                surface_forms TEXT DEFAULT '[]',
                embedding TEXT,
                times_seen INTEGER DEFAULT 1,
                source TEXT DEFAULT 'acquired',
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Learned word associations (distributional co-occurrence). Canonical
        # ordering term_a < term_b; weight reinforced on each co-occurrence.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lexical_associations (
                term_a TEXT NOT NULL,
                term_b TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                last_reinforced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (term_a, term_b)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS lexical_assoc_a_idx ON lexical_associations(term_a)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS lexical_assoc_b_idx ON lexical_associations(term_b)"
        )

        # Screen-sourced salient visual episodes (P3-1). Privacy-sensitive --
        # a screen can show anything open on the machine -- so these are
        # pruned on a hard TTL rather than the ACT-R fade `memories` rows
        # get; see MemoryStore.add_visual_screen_trace's docstring.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS visual_screen_traces (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                valence REAL DEFAULT 0.0,
                arousal REAL DEFAULT 0.5,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS visual_screen_traces_created_at_idx "
            "ON visual_screen_traces(created_at)"
        )

        # Specifics the agent tried to assert about its own life but could not
        # ground in the biography, the surfaced memories or the user's message.
        # Keyed on the term so repeated hits accumulate.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS self_knowledge_gaps (
                term TEXT PRIMARY KEY,
                times_hit INTEGER NOT NULL DEFAULT 1,
                example_prompt TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                asked_at TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS self_gap_hits_idx ON self_knowledge_gaps(times_hit DESC)"
        )

        # Migration for existing memories table to add developmental stage columns
        cursor.execute("PRAGMA table_info(memories)")
        existing_mem_cols = {row[1] for row in cursor.fetchall()}
        for col in [
            "speaker",
            "record_type",
            "valid_from",
            "valid_until",
            "contradicts_id",
            "lifespan_stage",
            "crisis",
            "virtue",
            "relations",
            "relation_circles",
            "modality",
        ]:
            if col not in existing_mem_cols:
                cursor.execute(f"ALTER TABLE memories ADD COLUMN {col} TEXT")
        cursor.execute(
            "UPDATE memories SET record_type = 'episode' WHERE record_type IS NULL"
        )

        # Migration for existing archived_memories table to add developmental stage columns
        cursor.execute("PRAGMA table_info(archived_memories)")
        existing_arch_cols = {row[1] for row in cursor.fetchall()}
        for col in [
            "speaker",
            "record_type",
            "valid_from",
            "valid_until",
            "contradicts_id",
            "lifespan_stage",
            "crisis",
            "virtue",
            "relations",
            "relation_circles",
            "modality",
        ]:
            if col not in existing_arch_cols:
                cursor.execute(f"ALTER TABLE archived_memories ADD COLUMN {col} TEXT")
        cursor.execute(
            "UPDATE archived_memories SET record_type = 'episode' "
            "WHERE record_type IS NULL"
        )

        # Migration for memories.id column type: ensure it's TEXT for UUID compatibility
        cursor.execute("PRAGMA table_info(memories)")
        id_col_info = [row for row in cursor.fetchall() if row[1] == "id"]
        if id_col_info and id_col_info[0][2].upper() != "TEXT":
            logger.info(
                f"Migrating memories.id from {id_col_info[0][2]} to TEXT for UUID compatibility"
            )
            # Create temp table with correct schema
            cursor.execute("""
                CREATE TABLE memories_new (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    raw_content TEXT,
                    wing TEXT DEFAULT 'personal',
                    room TEXT,
                    embedding TEXT,
                    importance_score REAL DEFAULT 0.5,
                    emotional_weight REAL DEFAULT 0.0,
                    valence REAL DEFAULT 0.0,
                    certainty REAL DEFAULT 1.0,
                    source TEXT DEFAULT 'user',
                    recall_count INTEGER DEFAULT 0,
                    last_recalled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT DEFAULT '{}',
                    speaker TEXT,
                    record_type TEXT NOT NULL DEFAULT 'episode',
                    valid_from TEXT,
                    valid_until TEXT,
                    contradicts_id TEXT,
                    lifespan_stage TEXT,
                    crisis TEXT,
                    virtue TEXT,
                    relations TEXT,
                    relation_circles TEXT,
                    modality TEXT
                )
            """)
            # Copy data, converting id to TEXT
            # Detect which columns exist in the source memories table
            cursor.execute("PRAGMA table_info(memories)")
            source_cols = {row[1] for row in cursor.fetchall()}

            # Define expected columns with their default values for missing columns
            column_defaults = {
                "content": "''",
                "raw_content": "''",
                "wing": "'personal'",
                "room": "NULL",
                "embedding": "NULL",
                "importance_score": "0.5",
                "emotional_weight": "0.0",
                "valence": "0.0",
                "certainty": "1.0",
                "source": "'user'",
                "recall_count": "0",
                "last_recalled_at": "CURRENT_TIMESTAMP",
                "created_at": "CURRENT_TIMESTAMP",
                "metadata": "'{}'",
                "speaker": "NULL",
                "record_type": "'episode'",
                "valid_from": "NULL",
                "valid_until": "NULL",
                "contradicts_id": "NULL",
                "lifespan_stage": "NULL",
                "crisis": "NULL",
                "virtue": "NULL",
                "relations": "NULL",
                "relation_circles": "NULL",
                "modality": "NULL",
            }

            # Build SELECT projection: use actual column if exists, otherwise use default
            select_parts = ["CAST(id AS TEXT)"]
            for col, default in column_defaults.items():
                if col in source_cols:
                    select_parts.append(col)
                else:
                    select_parts.append(f"{default} AS {col}")

            select_clause = ", ".join(select_parts)
            cursor.execute(
                """
                INSERT INTO memories_new
                SELECT """
                f"{select_clause}"  # nosec B608 - select_clause is built from the hardcoded column_defaults dict above
                """
                FROM memories
            """
            )
            # Drop old table and rename new one
            cursor.execute("DROP TABLE memories")
            cursor.execute("ALTER TABLE memories_new RENAME TO memories")

        self.conn.commit()

    def _translate_query(self, query: str):
        # 1. Translate PostgreSQL $1, $2 placeholders to SQLite ?
        translated = re.sub(r"\$\d+", "?", query)

        # 2. Replace Postgres NOW() with SQLite's CURRENT_TIMESTAMP
        translated = re.sub(
            r"\bNOW\(\)", "CURRENT_TIMESTAMP", translated, flags=re.IGNORECASE
        )

        # 3. Replace clock_timestamp() with CURRENT_TIMESTAMP
        translated = re.sub(
            r"\bclock_timestamp\(\)",
            "CURRENT_TIMESTAMP",
            translated,
            flags=re.IGNORECASE,
        )

        # 4. Handle UUID / VARCHAR types (SQLite handles these as TEXT naturally)
        translated = re.sub(r"\bUUID\b", "TEXT", translated, flags=re.IGNORECASE)
        translated = re.sub(
            r"\bVARCHAR\(\d+\)\b", "TEXT", translated, flags=re.IGNORECASE
        )
        translated = re.sub(r"\bBOOLEAN\b", "INTEGER", translated, flags=re.IGNORECASE)
        translated = re.sub(r"\bFALSE\b", "0", translated, flags=re.IGNORECASE)
        translated = re.sub(r"\bTRUE\b", "1", translated, flags=re.IGNORECASE)
        translated = re.sub(
            r"\bTIMESTAMP WITH TIME ZONE\b",
            "TIMESTAMP",
            translated,
            flags=re.IGNORECASE,
        )
        translated = re.sub(
            r"\bhalfvec\(\d+\)\b", "TEXT", translated, flags=re.IGNORECASE
        )
        translated = re.sub(
            r"\bvector\(\d+\)\b", "TEXT", translated, flags=re.IGNORECASE
        )
        translated = re.sub(r"\bJSONB\b", "TEXT", translated, flags=re.IGNORECASE)
        translated = re.sub(
            r"\bALTER TABLE messages ADD COLUMN IF NOT EXISTS consolidated.*",
            "SELECT 1",
            translated,
            flags=re.IGNORECASE,
        )
        translated = re.sub(
            r"\bALTER TABLE self_knowledge_gaps ADD COLUMN IF NOT EXISTS asked_at.*",
            "SELECT 1",
            translated,
            flags=re.IGNORECASE,
        )

        # 5. Translate PostgreSQL ON CONFLICT DO UPDATE to SQLite INSERT OR REPLACE INTO or ON CONFLICT(id) DO UPDATE
        if "ON CONFLICT" in translated.upper() and "DO UPDATE" in translated.upper():
            # If SQLite supports ON CONFLICT (id) DO UPDATE (3.24.0+), we can try to translate it or simplify
            # For simplicity, convert simple ON CONFLICT DO UPDATE patterns:
            # e.g., ON CONFLICT (id) DO UPDATE SET personality = EXCLUDED.personality -> DO UPDATE SET personality = excluded.personality
            translated = re.sub(
                r"\bEXCLUDED\b", "excluded", translated, flags=re.IGNORECASE
            )

        return translated

    # Every public method below is a thin `asyncio.to_thread` wrapper
    # around a `_sync_*` body holding `self._lock`. Called directly (as this
    # class was before P2-6), each call blocked the event loop for the full
    # duration of the query -- and because every agent runs a single asyncio
    # loop, that stalled the NATS client and every concurrent cognitive turn
    # for as long as the fallback stayed active, not just the caller. See
    # working_memory_store.py's own `_sqlite_lock` comment for why the lock
    # (not just check_same_thread=False) is what makes sharing one
    # connection across to_thread's worker threads safe.

    async def execute(self, query, *args):
        await asyncio.to_thread(self._sync_execute, query, args)

    def _sync_execute(self, query, args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]

        # Strip trailing comments or empty statements that SQLite driver rejects
        translated = "\n".join(
            line
            for line in translated.splitlines()
            if not line.strip().startswith("--")
        )

        with self._lock:
            cursor = self.conn.cursor()
            try:
                # Handle multiple SQL statements separated by semicolons
                if ";" in translated and len(translated.strip().split(";")) > 2:
                    cursor.executescript(translated)
                else:
                    cursor.execute(translated, cleaned_args)
                self.conn.commit()
            except sqlite3.OperationalError as e:
                # Guard against pg-specific extension checks like "create extension" or indexing
                if "vector" in str(e) or "hnsw" in str(e) or "pgcrypto" in str(e):
                    logger.debug(
                        f"SQLite Schema Guard: Ignored PG-specific index/extension statement: {e}"
                    )
                else:
                    logger.error(
                        f"SQLite execution failed for query: {query}\nTranslated: {translated}\nError: {e}"
                    )
                    raise

    async def fetch(self, query, *args):
        return await asyncio.to_thread(self._sync_fetch, query, args)

    def _sync_fetch(self, query, args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(translated, cleaned_args)
            rows = cursor.fetchall()
            # Commits a statement that both writes and returns rows (e.g. `UPDATE
            # ... RETURNING`), which arrives through the fetch path rather than
            # execute(). Unconditional rather than prefix-sniffed on the first
            # keyword: `fetchval` used exactly that check and, having none for
            # RETURNING statements, never committed one at all. A commit after a
            # plain SELECT is a no-op -- sqlite3 opens a transaction for DML only
            # -- so there's no correctness reason to guess whether a query wrote.
            self.conn.commit()
            return [dict(row) for row in rows]

    async def fetchrow(self, query, *args):
        return await asyncio.to_thread(self._sync_fetchrow, query, args)

    def _sync_fetchrow(self, query, args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(translated, cleaned_args)
            row = cursor.fetchone()
            self.conn.commit()
            return dict(row) if row else None

    async def fetchval(self, query, *args):
        return await asyncio.to_thread(self._sync_fetchval, query, args)

    def _sync_fetchval(self, query, args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(translated, cleaned_args)
            row = cursor.fetchone()
            self.conn.commit()
            return row[0] if row else None


class SQLitePoolAcquisition:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class SQLitePool:
    def __init__(self, db_path="app.db"):
        self.connection = SQLiteConnection(db_path)

    def acquire(self):
        return SQLitePoolAcquisition(self.connection)

    async def close(self):
        pass
