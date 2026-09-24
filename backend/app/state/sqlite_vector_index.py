"""In-process vector index for the SQLite memory fallback (Brain V2, ADR-001).

SQLite has no vector index. Scoring similarity by re-reading every row's
embedding as JSON text on each query cost p50 580 ms at 5,000 memories
(`python -m evals.cognitive latency`). This keeps the parsed, L2-normalised
embeddings of the most recently touched `scan_limit` rows of a wing in one
float32 matrix, so similarity for the whole wing is a single mat-vec product.

Freshness without cross-process coordination. The brain, subconscious and
surfacing processes can share one SQLite file, and a write in one process
never reaches another process's cache. Every lookup reads the wing's
`(count(*), max(rowid), id at max(rowid))` and reloads only what changed:

* count and max rowid grew by the same amount *and* the row the cache last
  saw at its top rowid is still the same row -> rows were only appended:
  fetch exactly the rowids in `(old top, new top]`;
* anything else -> rebuild. The id check matters: `memories` has a TEXT
  primary key, so SQLite reuses rowids after the newest rows are deleted, and
  "delete the newest k, insert k" leaves count and max(rowid) unchanged.

Metadata that changes in place (recall_count, importance, timestamps) is
never cached; callers fetch the full rows for the handful of winners.

Ordering and eviction: rows are held oldest-to-newest by recency (last
recall at rebuild time, insertion order for appends), so the `scan_limit` cap
always drops the least recently touched rows first.

Concurrency: `ensure` is serialised per wing with an asyncio lock, and all
JSON parsing / stacking runs in a worker thread so a rebuild (~1 s at 5,000
rows) never stalls the event loop that also carries NATS and audio.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


def parse_embedding(value) -> np.ndarray | None:
    """Stored embedding (JSON/vector-literal text or a list) -> float32 array."""
    if value is None:
        return None
    try:
        if isinstance(value, (list, tuple)):
            arr = np.asarray(value, dtype=np.float32)
        else:
            arr = np.asarray(json.loads(value), dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if arr.ndim != 1 or not arr.size or not np.all(np.isfinite(arr)):
        return None
    return arr


@dataclass
class _WingIndex:
    key: tuple = (0, 0, None)
    dim: int | None = None
    ids: list[str] = field(default_factory=list)
    rowids: list[int] = field(default_factory=list)
    rooms: list[str | None] = field(default_factory=list)
    matrix: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 0), dtype=np.float32)
    )
    skipped_dimension: int = 0  # rows whose embedding dimension differs from `dim`


def _stack(rows, dim: int | None):
    """Parse and normalise rows (runs in a worker thread). Picks the majority
    dimension when `dim` is None. Returns (dim, ids, rowids, rooms, matrix,
    rows skipped for a dimension mismatch)."""
    parsed = []
    for row in rows:
        vec = parse_embedding(row["embedding"])
        if vec is None:
            continue
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            continue
        parsed.append((str(row["id"]), int(row["rowid"]), row.get("room"), vec / norm))
    if dim is None and parsed:
        dim = Counter(p[3].shape[0] for p in parsed).most_common(1)[0][0]
    keep = [p for p in parsed if p[3].shape[0] == dim]
    matrix = (
        np.vstack([p[3] for p in keep]) if keep else np.zeros((0, dim or 0), np.float32)
    )
    return (
        dim,
        [p[0] for p in keep],
        [p[1] for p in keep],
        [p[2] for p in keep],
        matrix,
        len(parsed) - len(keep),
    )


class SQLiteVectorIndex:
    def __init__(self, scan_limit: int):
        self.scan_limit = scan_limit
        self._wings: dict[str, _WingIndex] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.rebuilds = 0
        self.appends = 0

    def invalidate(self, wing: str | None = None) -> None:
        """Drop cached state (one wing, or all). For writes the staleness key
        cannot see: a row deleted and reinserted under the same id at the same
        rowid with a different embedding (archive promotion that re-embeds)."""
        if wing is None:
            self._wings.clear()
        else:
            self._wings.pop(wing, None)

    async def _state(self, conn, wing: str) -> tuple:
        rows = await conn.fetch(
            "SELECT count(*) AS n, max(rowid) AS top FROM memories WHERE wing = ?", wing
        )
        n = int(rows[0]["n"] or 0) if rows else 0
        top = int(rows[0]["top"] or 0) if rows else 0
        top_id = None
        if top:
            ids = await conn.fetch("SELECT id FROM memories WHERE rowid = ?", top)
            top_id = str(ids[0]["id"]) if ids else None
        return n, top, top_id

    async def _rebuild(self, conn, wing: str, key, dim: int | None) -> _WingIndex:
        rows = await conn.fetch(
            "SELECT rowid, id, room, embedding FROM memories WHERE wing = ? "
            "ORDER BY last_recalled_at DESC LIMIT ?",
            wing,
            self.scan_limit,
        )
        rows = list(reversed(rows))  # oldest -> newest: the cap trims the front
        dim, ids, rowids, rooms, matrix, skipped = await asyncio.to_thread(
            _stack, rows, dim
        )
        self.rebuilds += 1
        if skipped:
            # Once per rebuild, so a mixed-dimension store is visible even
            # when enough rows match that queries still return something.
            logger.warning(
                "Vector index for wing %r: %d of %d stored embeddings skipped "
                "(dimension differs from %s); re-embed them to make them retrievable",
                wing,
                skipped,
                len(rows),
                dim,
            )
        return _WingIndex(key, dim, ids, rowids, rooms, matrix, skipped)

    async def _still_same_top(self, conn, index: _WingIndex) -> bool:
        """The row the cache last saw at its top rowid is still that row."""
        old_top, old_top_id = index.key[1], index.key[2]
        if not old_top:
            return True
        rows = await conn.fetch("SELECT id FROM memories WHERE rowid = ?", old_top)
        return bool(rows) and str(rows[0]["id"]) == old_top_id

    async def _append(
        self, conn, wing: str, index: _WingIndex, key, dim: int | None = None
    ) -> _WingIndex:
        rows = await conn.fetch(
            "SELECT rowid, id, room, embedding FROM memories "
            "WHERE wing = ? AND rowid > ? AND rowid <= ? ORDER BY rowid",
            wing,
            index.key[1],
            key[1],
        )
        present = set(index.ids)
        # A write that landed between a rebuild's `_state` and its fetch is
        # already loaded but newer than the cache key; drop the re-fetch.
        rows = [r for r in rows if str(r["id"]) not in present]
        dim, ids, rowids, rooms, new, skipped = await asyncio.to_thread(
            _stack, rows, index.dim if index.dim is not None else dim
        )
        if index.dim is None:
            # Built on an empty wing (the startup warm-up): the query's
            # dimension, else the first rows' majority, decides it. Keeping
            # None made every query miss.
            index.dim = dim
        if ids:
            index.matrix = np.vstack([index.matrix, new]) if index.matrix.size else new
            index.ids.extend(ids)
            index.rowids.extend(rowids)
            index.rooms.extend(rooms)
        index.skipped_dimension += skipped
        overflow = len(index.ids) - self.scan_limit
        if overflow > 0:  # least recently touched rows sit at the front
            index.ids = index.ids[overflow:]
            index.rowids = index.rowids[overflow:]
            index.rooms = index.rooms[overflow:]
            index.matrix = index.matrix[overflow:]
        index.key = key
        self.appends += 1
        return index

    async def ensure(self, conn, wing: str, dim: int | None = None) -> _WingIndex:
        """The wing's index, brought up to date if stale.

        `dim` forces a rebuild preferring rows of that dimension when the
        cached matrix has another one (an embedding-model change), instead of
        silently answering nothing for every query from then on.
        """
        lock = self._locks.setdefault(wing, asyncio.Lock())
        async with lock:
            key = await self._state(conn, wing)
            index = self._wings.get(wing)
            wrong_dim = (
                index is not None and dim is not None and index.dim not in (None, dim)
            )
            if index is not None and index.key == key and not wrong_dim:
                return index
            appended = (
                index is not None
                and not wrong_dim
                and key[0] - index.key[0] == key[1] - index.key[1] > 0
                and await self._still_same_top(conn, index)
            )
            if appended and index is not None:
                index = await self._append(conn, wing, index, key, dim)
            else:
                index = await self._rebuild(conn, wing, key, dim)
            self._wings[wing] = index
            return index

    def skipped_dimension(self, wing: str) -> int:
        """Rows of `wing` left out because their dimension differs."""
        index = self._wings.get(wing)
        return index.skipped_dimension if index else 0

    async def top_k(
        self, conn, wing: str, query_vector, k: int, room: str | None = None
    ) -> list[tuple[str, float]]:
        """[(memory id, cosine similarity)] for the `k` most similar rows,
        restricted to `room` (when given) before ranking."""
        query = np.asarray(query_vector, dtype=np.float32)
        index = await self.ensure(conn, wing, dim=int(query.shape[0]))
        norm = float(np.linalg.norm(query))
        if not index.ids or norm <= 0 or index.dim != query.shape[0]:
            return []
        sims = index.matrix @ (query / norm)
        available = len(index.ids)
        if room is not None:
            mask = np.fromiter(
                (r == room for r in index.rooms), dtype=bool, count=len(index.rooms)
            )
            sims = np.where(mask, sims, -np.inf)
            available = int(mask.sum())
        k = min(k, available)
        if k <= 0:
            return []
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [(index.ids[i], float(sims[i])) for i in top]
