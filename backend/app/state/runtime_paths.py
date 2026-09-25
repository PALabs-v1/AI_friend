"""Where runtime state lives: the Redis endpoint and the SQLite state files.

One place for both, because they drifted apart (F-016):
- `WorkingMemoryStore` read `Config.REDIS_URL`, but `StateService` hardcoded
  `127.0.0.1:6379`.
- `CognitiveService` put three of its databases under the deployment's data
  directory, while `state_cache.db` (agent state and the learned adaptive
  weights) landed in the process's working directory. In the production
  containers that is the image's writable layer, so every redeploy reset it.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from ..config import Config

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 6379


def redis_endpoint() -> tuple[str, int] | None:
    """(host, port) from `Config.REDIS_URL`, or None when Redis is disabled.

    An empty `REDIS_URL` disables Redis for every client, which then uses its
    SQLite fallback directly instead of spending a connect timeout first.
    BrainBench relies on this for per-cell isolation. A URL without an
    explicit host or port keeps the historical 127.0.0.1:6379 defaults.
    """
    url = (getattr(Config, "REDIS_URL", None) or "").strip()
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"redis://{url}")
    try:
        port = parsed.port or _DEFAULT_PORT
    except ValueError:
        port = _DEFAULT_PORT
    return parsed.hostname or _DEFAULT_HOST, port


def runtime_state_db(
    filename: str,
    *,
    base_path: str | os.PathLike[str] | None = None,
    legacy_filename: str | None = None,
) -> str:
    """Path for a runtime SQLite file under the deployment's data directory.

    The directory is `base_path` if given, else `Config.IDENTITY_BASE_PATH`
    (the same volume `IdentityManager` persists to). With neither set, the
    bare relative filename is returned, which is the historical behaviour
    for local and test use.

    Migration: when the target does not exist yet but the legacy file
    (`legacy_filename`, default `filename`) exists in the working directory,
    it is copied over with SQLite's online backup API. That is consistent
    even for a database in WAL mode, and it leaves the original in place for
    rollback. A deployment moving to this code keeps its learned state
    instead of silently starting over.
    """
    base = base_path or getattr(Config, "IDENTITY_BASE_PATH", None)
    if not base:
        return filename
    target = Path(base) / filename
    # SQLite can create a file but not its directory; a fresh volume may be empty.
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy = Path(legacy_filename or filename)
    if (
        not target.exists()
        and legacy.is_file()
        and legacy.resolve() != target.resolve()
    ):
        # `with sqlite3.connect(...)` only scopes a transaction; it never closes.
        src, dst = sqlite3.connect(legacy), sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        logger.warning(
            "Migrated runtime state %s -> %s (original left in place)",
            legacy.resolve(),
            target,
        )
    return str(target)
