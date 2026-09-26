import asyncio
import json
import logging
import os
import sqlite3
import threading
from typing import Any, cast

import redis

logger = logging.getLogger("working_memory_store")


class WorkingMemoryStore:
    """
    Tier-1 Working Memory Store.
    Uses Redis (127.0.0.1:6379) for distributed state sync and under-10ms latency,
    with an automatic local SQLite fallback (`working_memory.db`) if Redis is offline.
    """

    def __init__(
        self,
        redis_host: str | None = None,
        redis_port: int | None = None,
        db_path: str = "working_memory.db",
        max_turns: int = 8,
    ):
        from .runtime_paths import redis_endpoint

        # Same parser as StateService (F-016); an empty REDIS_URL disables
        # Redis here too. Explicit arguments still win.
        endpoint = (
            (redis_host or "127.0.0.1", redis_port or 6379)
            if redis_host or redis_port
            else redis_endpoint()
        )

        self.max_turns = max_turns
        self.db_path = db_path
        self.redis_client: redis.Redis | None = None
        # L2: one connection reused for the lifetime of the store rather than
        # opening/closing a fresh handle on every fallback call; guarded by a
        # lock since each call arrives on a different asyncio.to_thread
        # worker thread and SQLite doesn't support concurrent writers anyway.
        self._sqlite_conn: sqlite3.Connection | None = None
        self._sqlite_lock = threading.Lock()

        # 1. Attempt Redis Connection
        if endpoint is not None:
            try:
                self.redis_client = redis.Redis(
                    host=endpoint[0],
                    port=endpoint[1],
                    db=0,
                    socket_connect_timeout=1.0,
                    decode_responses=True,
                )
                # Ping to confirm live connection
                self.redis_client.ping()
                logger.info(
                    f"Connected to Redis Working Memory on {endpoint[0]}:{endpoint[1]}"
                )
            except Exception as e:
                self.redis_client = None
                logger.warning(
                    f"Redis Working Memory unavailable: {e}. Falling back to SQLite: {db_path}"
                )

        # 2. Setup SQLite fallback DB structure
        if self.redis_client is None and db_path != ":memory:":
            db_dir = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(db_dir, exist_ok=True)

        self._initialize_sqlite()

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        """Lazily create and cache the fallback connection (L2).

        `check_same_thread=False` is required because callers reach this from
        whichever `asyncio.to_thread` worker thread happens to run a given
        call; `self._sqlite_lock` (held by every call site) is what actually
        makes sharing the connection across threads safe.
        """
        if self._sqlite_conn is None:
            self._sqlite_conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._sqlite_conn.row_factory = sqlite3.Row
        return self._sqlite_conn

    def _initialize_sqlite(self):
        if self.redis_client is not None:
            return

        with self._sqlite_lock, self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS working_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS working_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    # --- Real-Time Turns Playout (Last 5–8 turns) ---
    #
    # Every public method here is a thin `asyncio.to_thread` wrapper around a
    # `_sync_*` body. Both the Redis client and the sqlite3 fallback are
    # blocking I/O; called directly from async code (as
    # scripts/research/estimate_realtime_latency.py does) they stall the
    # event loop for a network round trip or a disk write. Off-loading to a
    # worker thread matches the fix already applied to
    # `StateService.persist_state` (see the ledger's 2026-07-19 entry).

    async def add_turn(
        self, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        await asyncio.to_thread(self._sync_add_turn, role, content, metadata)

    def _sync_add_turn(
        self, role: str, content: str, metadata: dict[str, Any] | None = None
    ):
        """Append a dialogue turn, immediately trimming excess turns to prevent context bloat."""
        meta_json = json.dumps(metadata or {})
        turn_data = {"role": role, "content": content, "metadata": meta_json}

        if self.redis_client:
            try:
                # Add to Redis List
                self.redis_client.rpush("working:turns", json.dumps(turn_data))
                # Trim list to max_turns
                self.redis_client.ltrim("working:turns", -self.max_turns, -1)
                return
            except Exception as e:
                logger.error(f"Redis add_turn failed: {e}. Falling back to SQLite.")
                # Fall through to SQLite

        # SQLite Fallback execution
        try:
            with self._sqlite_lock, self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO working_turns (role, content, metadata) VALUES (?, ?, ?)",
                    (role, content, meta_json),
                )
                conn.commit()

                # Trim SQLite table to keep only the last max_turns
                cursor.execute(
                    """
                    DELETE FROM working_turns
                    WHERE id NOT IN (
                        SELECT id FROM working_turns
                        ORDER BY id DESC LIMIT ?
                    )
                """,
                    (self.max_turns,),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"SQLite add_turn failed: {e}")

    async def get_recent_turns(self, limit: int | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._sync_get_recent_turns, limit)

    def _sync_get_recent_turns(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Retrieve recent conversation turns in chronological order."""
        limit = limit or self.max_turns
        if self.redis_client:
            try:
                # redis-py's sync Redis shares stubs with the async client, so
                # every command is typed Awaitable[T] | T even here; this
                # client is always constructed as sync (never redis.asyncio).
                raw_turns = cast(
                    list, self.redis_client.lrange("working:turns", -limit, -1)
                )
                turns = []
                for rt in raw_turns:
                    t = json.loads(rt)
                    turns.append(
                        {
                            "role": t["role"],
                            "content": t["content"],
                            "metadata": json.loads(t["metadata"]),
                        }
                    )
                return turns
            except Exception as e:
                logger.error(
                    f"Redis get_recent_turns failed: {e}. Falling back to SQLite."
                )

        # SQLite Fallback
        try:
            with self._sqlite_lock, self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT role, content, metadata FROM working_turns ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
                # Reverse to restore chronological order (oldest to newest)
                reversed_rows = list(reversed(rows))
                return [
                    {
                        "role": row["role"],
                        "content": row["content"],
                        "metadata": json.loads(row["metadata"]),
                    }
                    for row in reversed_rows
                ]
        except Exception as e:
            logger.error(f"SQLite get_recent_turns failed: {e}")
            return []

    async def clear_turns(self) -> None:
        await asyncio.to_thread(self._sync_clear_turns)

    def _sync_clear_turns(self):
        """Reset the active working memory turns (e.g. on new session starts)."""
        if self.redis_client:
            try:
                self.redis_client.delete("working:turns")
                return
            except Exception as e:
                logger.error(f"Redis clear_turns failed: {e}. Falling back to SQLite.")

        try:
            with self._sqlite_lock, self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM working_turns")
                conn.commit()
        except Exception as e:
            logger.error(f"SQLite clear_turns failed: {e}")

    # --- Short-Term Affect / Goals / Variables Synchronization ---

    async def set_state_var(self, key: str, value: Any) -> None:
        await asyncio.to_thread(self._sync_set_state_var, key, value)

    def _sync_set_state_var(self, key: str, value: Any):
        """Set a synced working memory variable (e.g. goals, short-term emotional states)."""
        val_str = json.dumps(value)
        if self.redis_client:
            try:
                self.redis_client.hset("working:state", key, val_str)
                return
            except Exception as e:
                logger.error(
                    f"Redis set_state_var failed: {e}. Falling back to SQLite."
                )

        try:
            with self._sqlite_lock, self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO working_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, val_str),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"SQLite set_state_var failed: {e}")

    async def get_state_var(self, key: str, default: Any = None) -> Any:
        return await asyncio.to_thread(self._sync_get_state_var, key, default)

    def _sync_get_state_var(self, key: str, default: Any = None) -> Any:
        """Get a synced working memory variable."""
        if self.redis_client:
            try:
                # See the lrange comment above re: redis-py's shared sync/async stubs.
                val_str = cast(
                    "str | None", self.redis_client.hget("working:state", key)
                )
                if val_str is not None:
                    return json.loads(val_str)
                return default
            except Exception as e:
                logger.error(
                    f"Redis get_state_var failed: {e}. Falling back to SQLite."
                )

        try:
            with self._sqlite_lock, self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM working_state WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row["value"])
        except Exception as e:
            logger.error(f"SQLite get_state_var failed: {e}")
        return default
