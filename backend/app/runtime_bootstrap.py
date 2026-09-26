import asyncio
import json
import logging
import os
import time
from pathlib import Path

import asyncpg
import httpx

from .config import Config
from .nats_streams import setup_streams

logger = logging.getLogger("runtime_bootstrap")


def write_sqlite_fallback_sentinel(reason: str) -> None:
    """Write the sentinel file `/health` (main.py, a separate process) reads
    to report degraded SQLite-fallback status.

    Extracted from `_enter_sqlite_fallback` (audit finding, 2026-09-14) so
    `ConversationHistoryStore.initialize()`'s own, separate Postgres-failure
    fallback branch (`state/conversation_store.py`) can report through the
    same `/health` signal instead of setting `used_fallback_storage` with no
    consumer. Deliberately just the sentinel write, not the production
    fail-closed check below -- `ConversationHistoryStore` already has its
    own, differently-gated strict-mode check (`ORGANISM_MODE_STRICT_STORAGE`)
    and must not have a second, conflicting policy imposed on it here.
    """
    health_file = getattr(Config, "SQLITE_FALLBACK_HEALTH_FILE", "")
    if not health_file:
        return
    try:
        with open(health_file, "w") as fh:
            fh.write(json.dumps({"reason": reason, "timestamp": time.time()}))
    except OSError as file_err:
        logger.debug(
            "[Bootstrap] Could not write SQLite fallback sentinel: %s", file_err
        )


def _enter_sqlite_fallback(reason: str) -> None:
    """P1-6: a Postgres connection failure used to log at WARNING and fall
    back to SQLite with no other signal. Q-M2-2 answered SQLite as
    emergency-only, not a supported runtime mode, which makes silence here
    worse than the degradation itself -- M2-P3's synchronous SQLite I/O then
    blocks the whole mesh's event loop in a mode nobody knows it entered.

    Logs at ERROR, writes a sentinel file `/health` (main.py, a separate
    process) can read, and fails closed under ENVIRONMENT=production unless
    explicitly overridden -- silently losing pgvector in production is worse
    than refusing to start. Deliberately does NOT apply to a deployment that
    never configured DATABASE_URL at all (see `_ensure_database_schema`'s
    first branch): that is a chosen SQLite-only configuration, not a
    downgrade, and must not be made to fail closed.
    """
    logger.error(
        "[Bootstrap] %s. Falling back to SQLite -- this is an EMERGENCY-ONLY "
        "degraded mode (pgvector is unavailable), not a supported runtime "
        "configuration.",
        reason,
    )

    if Config.ENVIRONMENT == "production" and not getattr(
        Config, "ALLOW_SQLITE_FALLBACK", False
    ):
        raise RuntimeError(
            f"{reason}. Refusing to silently downgrade to SQLite in production "
            "(ENVIRONMENT=production). Set ALLOW_SQLITE_FALLBACK=true to permit "
            "this degraded mode explicitly."
        )

    write_sqlite_fallback_sentinel(reason)


def _clear_sqlite_fallback() -> None:
    """Remove the sentinel once Postgres answers again.

    Without this the flag is write-only: one transient Postgres outage
    would leave `/health` reporting `degraded: true` for the lifetime of
    the host, long after the condition cleared. A degradation signal that
    never turns off is one operators learn to ignore, which is the same
    silence P1-6 set out to fix.
    """
    health_file = getattr(Config, "SQLITE_FALLBACK_HEALTH_FILE", "")
    if not health_file:
        return
    try:
        Path(health_file).unlink()
    except FileNotFoundError:
        pass
    except OSError as file_err:
        logger.debug(
            "[Bootstrap] Could not clear SQLite fallback sentinel: %s", file_err
        )


async def bootstrap_runtime() -> None:
    """Run idempotent runtime bootstrap so one-command deploy reaches a working mesh."""
    retries = max(1, int(getattr(Config, "RUNTIME_BOOTSTRAP_RETRIES", 12)))

    await _run_with_retry("database schema", _ensure_database_schema, retries=retries)
    await _run_with_retry("nats streams", _ensure_nats_streams, retries=retries)
    await _run_with_retry("ollama models", _ensure_ollama_models, retries=retries)

    logger.info("[Bootstrap] Runtime prerequisites verified.")


async def _run_with_retry(
    label: str, fn, retries: int, base_delay: float = 2.0
) -> None:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            await fn()
            if attempt > 1:
                logger.info(
                    "[Bootstrap] %s recovered on attempt %s/%s.",
                    label,
                    attempt,
                    retries,
                )
            return
        except (
            Exception
        ) as e:  # pragma: no cover - exercised through integration behavior
            last_error = e
            if attempt == retries:
                break
            delay = min(15.0, base_delay * attempt)
            logger.warning(
                "[Bootstrap] %s failed on attempt %s/%s: %s. Retrying in %.1fs",
                label,
                attempt,
                retries,
                e,
                delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"bootstrap step '{label}' failed after {retries} attempts: {last_error}"
    )


async def _ensure_database_schema() -> None:
    dsn = Config.DATABASE_URL
    if not dsn:
        dsn = "sqlite:///app.db"

    if dsn.startswith("sqlite") or dsn == "sqlite:///:memory:":
        logger.info(
            "[Bootstrap] Detected SQLite target database. Bypassing pg schema initialization."
        )
        from .state.sqlite_fallback import SQLiteConnection

        db_file = "app.db"
        if dsn.startswith("sqlite:///"):
            db_file = dsn.replace("sqlite:///", "")
        elif dsn == "sqlite:///:memory:":
            db_file = ":memory:"
        SQLiteConnection(db_file)
        logger.info("[Bootstrap] SQLite database schema and core tables verified.")
        return

    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        _enter_sqlite_fallback(reason=f"PostgreSQL connection failed: {e}")

        from .state.sqlite_fallback import SQLiteConnection

        SQLiteConnection("app.db")
        logger.info(
            "[Bootstrap] SQLite database schema and core tables verified via fallback."
        )
        return

    _clear_sqlite_fallback()

    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    schema_sql = schema_path.read_text(encoding="utf-8")

    try:
        await conn.execute(schema_sql)

        # Conversation and identity tables are required by ConversationHistoryStore.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id UUID PRIMARY KEY,
                started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP WITH TIME ZONE,
                metadata JSONB
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id UUID PRIMARY KEY,
                session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
                role VARCHAR(50) NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                consolidated BOOLEAN DEFAULT FALSE
            );
            """
        )
        # Migration: add consolidated column to existing tables if it does not already exist
        await conn.execute(
            """
            ALTER TABLE messages ADD COLUMN IF NOT EXISTS consolidated BOOLEAN DEFAULT FALSE;
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_configs (
                id INTEGER PRIMARY KEY,
                personality TEXT,
                background_history TEXT,
                evolved_learnings TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    finally:
        await conn.close()

    logger.info("[Bootstrap] Database schema and core tables verified.")


async def _ensure_nats_streams() -> None:
    # F-019: stream administration needs $JS.API.STREAM.* rights, which only
    # the provisioner identity holds (nats-accounts.conf). An agent running
    # this with its own scoped credentials waits out every JetStream API
    # timeout, exhausts its retries and crash-loops, so it leaves streams to
    # the one-shot nats_provisioner service it already depends on.
    provisioner = os.getenv("NATS_PROVISIONER_USER", "nats_provisioner")
    user = os.getenv("NATS_USER")
    if user != provisioner:
        logger.info(
            "[Bootstrap] NATS streams skipped: running as %r, provisioned by %r.",
            user,
            provisioner,
        )
        return
    await setup_streams(Config.NATS_URL)


async def _ensure_ollama_models() -> None:
    required = _normalized_required_models(
        getattr(Config, "OLLAMA_REQUIRED_MODELS", [])
    )
    if not required:
        logger.info(
            "[Bootstrap] No required Ollama models configured; skipping model check."
        )
        return

    base_url = (Config.OLLAMA_URL or "http://127.0.0.1:11434").rstrip("/")

    async with httpx.AsyncClient(timeout=120.0) as client:
        tags_response = await client.get(f"{base_url}/api/tags")
        tags_response.raise_for_status()
        tags_payload = tags_response.json()

        available = [
            entry.get("name", "")
            for entry in tags_payload.get("models", [])
            if entry.get("name")
        ]
        missing = [model for model in required if not _model_exists(model, available)]

        if not missing:
            logger.info(
                "[Bootstrap] Required Ollama models already present: %s", required
            )
            return

        logger.info("[Bootstrap] Missing Ollama models: %s", missing)

        for model_name in missing:
            logger.info("[Bootstrap] Pulling Ollama model: %s", model_name)
            pull_response = await client.post(
                f"{base_url}/api/pull",
                json={"name": model_name, "stream": False},
            )
            pull_response.raise_for_status()
            logger.info("[Bootstrap] Model ready: %s", model_name)


def _normalized_required_models(models: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for model in models:
        clean = str(model).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def _model_exists(required_model: str, available_models: list[str]) -> bool:
    if required_model in available_models:
        return True

    # Compatibility: treat "model" and "model:latest" as equivalent.
    if ":" not in required_model and f"{required_model}:latest" in available_models:
        return True
    return bool(
        required_model.endswith(":latest")
        and required_model.split(":", 1)[0] in available_models
    )
