import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import asyncpg

from ..config import Config, config_instance
from ..runtime_bootstrap import write_sqlite_fallback_sentinel

logger = logging.getLogger(__name__)

DEFAULT_PERSONALITY = {
    "name": "AI Friend",
    "core_personality": {
        "immutable": {
            "values": ["Honesty", "Privacy", "Curiosity"],
            "base_tone": "Warm, intellectual, and slightly protective",
            "boundaries": [
                "Will never share user data",
                "Will not adopt toxic behavior",
            ],
        },
        "adaptive_traits": [],
    },
    "speaking_style": {"pace": "natural", "verbosity": "balanced"},
    "conversation_rules": {"avoid": []},
}
DEFAULT_HISTORY = {"relationship": "Friend", "memories": []}
DEFAULT_PERSONALITY_JSON = json.dumps(DEFAULT_PERSONALITY)
DEFAULT_HISTORY_JSON = json.dumps(DEFAULT_HISTORY)


class ConversationHistoryStore:
    def __init__(self):
        self.dsn = Config.DATABASE_URL
        self.pool: asyncpg.Pool | None = None
        self.current_session_id: uuid.UUID | None = None
        self.trust_columns_available: bool = True
        # Set when initialize() falls back to local SQLite after a PostgreSQL
        # connection failure (H5). A prior conversation's history, stored in
        # Postgres, becomes silently unreachable from the SQLite session -
        # queryable here so a health check can surface it rather than the
        # user just experiencing what looks like sudden memory loss.
        self.used_fallback_storage: bool = False
        app_dir = Path(__file__).resolve().parents[1]
        self.personality_seed_path = Config.PERSONALITY_SEED_PATH or str(
            app_dir / "personality.json"
        )
        self.history_seed_path = Config.HISTORY_SEED_PATH or str(
            app_dir / "history.json"
        )

    async def initialize(self):
        """Initialize the database connection pool with automatic SQLite fallback."""
        try:
            if (
                not self.dsn
                or self.dsn.startswith("sqlite")
                or self.dsn == "sqlite:///:memory:"
            ):
                from .sqlite_fallback import SQLitePool

                db_file = "app.db"
                if self.dsn and self.dsn.startswith("sqlite:///"):
                    db_file = self.dsn.replace("sqlite:///", "")
                elif self.dsn == "sqlite:///:memory:":
                    db_file = ":memory:"
                self.pool = SQLitePool(db_file)
                logger.info(
                    f"Connected to local SQLite database via fallback: {db_file}"
                )
                await self._ensure_config_exists()
                return

            # Use statement_cache_size=0 for pgbouncer compatibility
            self.pool = await asyncpg.create_pool(dsn=self.dsn, statement_cache_size=0)
            logger.info("Connected to Database via asyncpg.")

            # Run schema migration for sessions table in PostgreSQL
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS trust_benevolence double precision default 0.5"
                    )
                    await conn.execute(
                        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS trust_competence double precision default 0.5"
                    )
                    await conn.execute(
                        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS trust_integrity double precision default 0.5"
                    )
            except Exception as migration_err:
                logger.warning(
                    f"PostgreSQL sessions schema migration skipped/failed: {migration_err}"
                )
                self.trust_columns_available = False

            await self._ensure_config_exists()

        except Exception as e:
            if self.dsn and not self.dsn.startswith("sqlite"):
                strict_storage = getattr(
                    config_instance, "ORGANISM_MODE_STRICT_STORAGE", False
                ) or getattr(Config, "ORGANISM_MODE_STRICT_STORAGE", False)
                if strict_storage:
                    # Checked before touching `used_fallback_storage`/logging
                    # "falling back": no fallback happens on this path, so
                    # nothing here should claim it did. `used_fallback_storage`
                    # exists so a health check can catch silent SQLite
                    # degradation (see its own docstring) -- strict mode's
                    # entire point is refusing to degrade silently, so the
                    # flag must not read "using fallback storage" for a
                    # session that never got one.
                    logger.critical(
                        f"PostgreSQL connection failed: {e}. Strict storage "
                        "mode forbids falling back to SQLite; refusing to "
                        "start rather than silently losing Postgres history."
                    )
                    raise RuntimeError(
                        "Strict storage mode forbids falling back to SQLite after "
                        "a PostgreSQL connection failure"
                    ) from e
                self.used_fallback_storage = True
                logger.critical(
                    f"PostgreSQL connection failed: {e}. Falling back to local "
                    "SQLite database - conversation history stored in "
                    "PostgreSQL is now unreachable from this session."
                )
                # Audit finding (2026-09-14): used_fallback_storage was set
                # but nothing read it -- this store's own fallback never
                # reached /health the way runtime_bootstrap.py's does.
                write_sqlite_fallback_sentinel(f"PostgreSQL connection failed: {e}")
                try:
                    from .sqlite_fallback import SQLitePool

                    self.pool = SQLitePool("app.db")
                    await self._ensure_config_exists()
                    return
                except Exception as ex:
                    logger.error(f"Failed to load SQLite fallback pool: {ex}")
            logger.error(f"Failed to initialize ConversationHistoryStore: {e}")
            raise

    async def _ensure_config_exists(self):
        """Ensure that the agent_configs table has at least one record (id: 1)."""
        if not self.pool:
            return

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT id FROM agent_configs WHERE id = 1")
                if not row:
                    logger.info(
                        "No AgentConfig found. Seeding from local JSON files..."
                    )
                    personality = DEFAULT_PERSONALITY_JSON
                    try:
                        with open(
                            self.personality_seed_path,
                            "r",
                            encoding="utf-8",
                        ) as f:
                            personality = f.read()
                    except Exception as e:
                        logger.warning(
                            f"Could not read local personality.json for seeding: {e}"
                        )

                    history = DEFAULT_HISTORY_JSON
                    try:
                        with open(
                            self.history_seed_path,
                            "r",
                            encoding="utf-8",
                        ) as f:
                            history = f.read()
                    except Exception as e:
                        logger.warning(
                            f"Could not read local history.json for seeding: {e}"
                        )

                    await conn.execute(
                        """
                        INSERT INTO agent_configs (id, personality, background_history, evolved_learnings, updated_at)
                        VALUES (1, $1, $2, '', NOW())
                        """,
                        personality,
                        history,
                    )
                    logger.info("AgentConfig seeded with empty Evolved Learnings.")
        except Exception as e:
            logger.error(f"Failed to ensure AgentConfig exists: {e}")

    async def get_agent_config(self) -> dict[str, str]:
        """Fetch personality, history, and evolved learnings from AgentConfig."""
        if not self.pool:
            return {"personality": "{}", "history": "{}", "evolved_learnings": ""}

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM agent_configs WHERE id = 1")
                if row:
                    # Defensive check in case migration is still propagating
                    data = dict(row)
                    return {
                        "personality": data.get("personality", "{}"),
                        "history": data.get("background_history", "{}"),
                        "evolved_learnings": data.get("evolved_learnings", "") or "",
                    }
        except Exception as e:
            logger.error(f"Failed to fetch AgentConfig: {e}")

        return {"personality": "{}", "history": "{}", "evolved_learnings": ""}

    async def update_agent_config(
        self,
        personality: str,
        history: str,
        evolved_learnings: str = "",
    ):
        """Persist the active runtime identity back to durable config storage."""
        if not self.pool:
            return

        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO agent_configs (
                        id, personality, background_history, evolved_learnings, updated_at
                    )
                    VALUES (1, $1, $2, $3, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        personality = EXCLUDED.personality,
                        background_history = EXCLUDED.background_history,
                        evolved_learnings = EXCLUDED.evolved_learnings,
                        updated_at = NOW()
                    """,
                    personality,
                    history,
                    evolved_learnings or "",
                )
        except Exception as e:
            logger.error(f"Failed to update agent config: {e}")

    async def start_session(
        self,
        trust_benevolence: float = 0.5,
        trust_competence: float = 0.5,
        trust_integrity: float = 0.5,
    ) -> uuid.UUID:
        """Start a new session and return its ID."""
        import math

        def clamp_trust(val: float) -> float:
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return 0.5
            try:
                return max(0.0, min(1.0, float(val)))
            except (ValueError, TypeError):
                return 0.5

        tb = clamp_trust(trust_benevolence)
        tc = clamp_trust(trust_competence)
        ti = clamp_trust(trust_integrity)

        if not self.pool:
            self.current_session_id = uuid.uuid4()
            return self.current_session_id

        self.current_session_id = uuid.uuid4()
        try:
            async with self.pool.acquire() as conn:
                if getattr(self, "trust_columns_available", True):
                    await conn.execute(
                        """
                        INSERT INTO sessions (id, started_at, trust_benevolence, trust_competence, trust_integrity)
                        VALUES ($1, NOW(), $2, $3, $4)
                        """,
                        self.current_session_id,
                        tb,
                        tc,
                        ti,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO sessions (id, started_at)
                        VALUES ($1, NOW())
                        """,
                        self.current_session_id,
                    )
            logger.info(f"Started new session: {self.current_session_id}")
            return self.current_session_id
        except Exception as e:
            logger.error(f"Failed to start session: {e}")
            return self.current_session_id

    async def log_message(self, role: str, content: str):
        """Log a message to the current session."""
        if not self.pool or not self.current_session_id:
            return

        try:
            async with self.pool.acquire() as conn:
                # Self-healing session insert to prevent foreign key violations (e.g. during DB resets)
                await conn.execute(
                    """
                    INSERT INTO sessions (id, started_at)
                    VALUES ($1, NOW())
                    ON CONFLICT (id) DO NOTHING
                    """,
                    self.current_session_id,
                )

                await conn.execute(
                    """
                    INSERT INTO messages (id, session_id, role, content, timestamp)
                    VALUES ($1, $2, $3, $4, NOW())
                    """,
                    uuid.uuid4(),
                    self.current_session_id,
                    role,
                    content,
                )
        except Exception as e:
            logger.error(f"Failed to log message: {e}")

    async def get_last_session_time(self) -> datetime | None:
        """Fetch the ended_at time of the most recent completed session."""
        if not self.pool:
            return None

        try:
            async with self.pool.acquire() as conn:
                if self.current_session_id:
                    row = await conn.fetchrow(
                        """
                        SELECT ended_at
                        FROM sessions
                        WHERE id != $1 AND ended_at IS NOT NULL
                        ORDER BY ended_at DESC
                        LIMIT 1
                        """,
                        self.current_session_id,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT ended_at
                        FROM sessions
                        WHERE ended_at IS NOT NULL
                        ORDER BY ended_at DESC
                        LIMIT 1
                        """
                    )
                return row["ended_at"] if row else None
        except Exception as e:
            logger.error(f"Failed to fetch last session time: {e}")
            return None

    async def get_last_interaction_brief(self) -> str | None:
        """Fetch the very last assistant message content to gauge sentiment."""
        if not self.pool:
            return None
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT content FROM messages WHERE role = 'assistant' ORDER BY timestamp DESC LIMIT 1"
                )
                return row["content"] if row else None
        except Exception as e:
            logger.error(f"Failed to fetch last interaction: {e}")
            return None

    async def update_last_assistant_message(
        self, content: str, *, expected: str | None = None
    ):
        """Update/truncate the content of the very last assistant message in the current session.

        With `expected`, rewrites the newest assistant row that still holds
        exactly `expected` -- that reply's own row -- or nothing. Without it
        a truncation rewrote whichever reply was newest: the previous turn's
        when this one was never stored, a newer one when it had been, or
        either on a timestamp tie.
        """
        if not self.pool or not self.current_session_id:
            return

        match_content = " AND content = $3" if expected is not None else ""
        sql = f"""
                    UPDATE messages
                    SET content = $1
                    WHERE id = (
                        SELECT id FROM messages
                        WHERE session_id = $2 AND role = 'assistant'{match_content}
                        ORDER BY timestamp DESC
                        LIMIT 1
                    )
                    """  # nosec B608 - match_content is one of two literals; values are bound
        args = [content, self.current_session_id]
        if expected is not None:
            args.append(expected)
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(sql, *args)
                # Check rowcount attribute if available (backend-agnostic)
                rowcount = getattr(result, "rowcount", None)
                if rowcount is None:
                    # Fallback: parse string result for PostgreSQL "UPDATE n" format
                    try:
                        rowcount = (
                            int(result.split()[-1])
                            if result and isinstance(result, str)
                            else 0
                        )
                    except (ValueError, IndexError, AttributeError):
                        rowcount = 0

                if rowcount > 0:
                    logger.info(
                        f"Updated last assistant message to {len(content)} characters"
                    )
                elif expected is not None:
                    logger.info(
                        "Interrupted reply not rewritten: no stored assistant "
                        "message holds it (it was never stored)"
                    )
        except Exception as e:
            logger.error(f"Failed to update last assistant message: {e}")

    async def close(self):
        """Close the database connection pool."""
        if self.pool:
            await self.pool.close()
            logger.info("Closed Database connection pool.")
