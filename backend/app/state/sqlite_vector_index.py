"""In-process vector index for the SQLite memory fallback (Brain V2, ADR-001).

SQLite has no vector index. Scoring similarity by re-reading every row's
embedding as JSON text on each query cost p50 580 ms at 5,000 memories
(`python -m evals.cognitive latency`). This keeps the parsed, L2-normalised
embeddings of the most recently touched `scan_limit` rows of a wing in one
float32 matrix, so similarity for the whole wing is a single mat-vec product.

Freshness without cross-process coordination: the brain, subconscious and
surfacing processes can share one SQLite file, and a write in one process
never reaches another process's cache. Every lookup therefore checks the
wing's `(count(*), max(rowid))` first -- an indexed aggregate, microseconds --
and only reloads what changed:

* count and max rowid grew by the same amount -> rows were only appended:
  fetch and append just those rows (the common case: consolidation, stored
  memories, promotions);
* anything else (a deletion from decay pruning, a replaced row) -> rebuild.

Metadata that changes in place (recall_count, importance, timestamps) is
never cached; callers fetch the full rows for the handful of winners.
"""

from __future__ import annotations

import json
import logging
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
    key: tuple[int, int] = (0, 0)
    ids: list[str] = field(default_factory=list)
    rowids: list[int] = field(default_factory=list)
    matrix: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 0), dtype=np.float32)
    )


class SQLiteVectorIndex:
    def __init__(self, scan_limit: int):
        self.scan_limit = scan_limit
        self._wings: dict[str, _WingIndex] = {}
        self.rebuilds = 0
        self.appends = 0

    def invalidate(self) -> None:
        self._wings.clear()

    async def _state(self, conn, wing: str) -> tuple[int, int]:
        rows = await conn.fetch(
            "SELECT count(*) AS n, max(rowid) AS top FROM memories WHERE wing = ?", wing
        )
        row = rows[0] if rows else {"n": 0, "top": 0}
        return int(row["n"] or 0), int(row["top"] or 0)

    @staticmethod
    def _stack(rows, dim: int | None) -> tuple[list[str], list[int], list[np.ndarray]]:
        ids, rowids, vecs = [], [], []
        for row in rows:
            vec = parse_embedding(row["embedding"])
            if vec is None or (dim is not None and vec.shape[0] != dim):
                continue
            norm = float(np.linalg.norm(vec))
            if norm <= 0:
                continue
            ids.append(str(row["id"]))
            rowids.append(int(row["rowid"]))
            vecs.append(vec / norm)
        return ids, rowids, vecs

    async def _rebuild(self, conn, wing: str, key) -> _WingIndex:
        rows = await conn.fetch(
            "SELECT rowid, id, embedding FROM memories WHERE wing = ? "
            "ORDER BY last_recalled_at DESC LIMIT ?",
            wing,
            self.scan_limit,
        )
        ids, rowids, vecs = self._stack(rows, None)
        dims = {v.shape[0] for v in vecs}
        if len(dims) > 1:  # mixed-dimension store: keep the majority dimension
            dim = max(dims, key=lambda d: sum(1 for v in vecs if v.shape[0] == d))
            keep = [i for i, v in enumerate(vecs) if v.shape[0] == dim]
            ids = [ids[i] for i in keep]
            rowids = [rowids[i] for i in keep]
            vecs = [vecs[i] for i in keep]
        matrix = np.vstack(vecs) if vecs else np.zeros((0, 0), dtype=np.float32)
        self.rebuilds += 1
        return _WingIndex(key, ids, rowids, matrix)

    async def _append(self, conn, wing: str, index: _WingIndex, key) -> _WingIndex:
        rows = await conn.fetch(
            "SELECT rowid, id, embedding FROM memories WHERE wing = ? AND rowid > ? "
            "ORDER BY rowid",
            wing,
            index.key[1],
        )
        dim = index.matrix.shape[1] if index.matrix.size else None
        ids, rowids, vecs = self._stack(rows, dim)
        if vecs:
            new = np.vstack(vecs)
            index.matrix = np.vstack([index.matrix, new]) if index.matrix.size else new
            index.ids.extend(ids)
            index.rowids.extend(rowids)
        # Honour the recency cap: drop the oldest-inserted rows beyond it.
        overflow = len(index.ids) - self.scan_limit
        if overflow > 0:
            index.ids = index.ids[overflow:]
            index.rowids = index.rowids[overflow:]
            index.matrix = index.matrix[overflow:]
        index.key = key
        self.appends += 1
        return index

    async def top_k(
        self, conn, wing: str, query_vector, k: int
    ) -> list[tuple[str, float]]:
        """[(memory id, cosine similarity)] for the `k` most similar rows."""
        index = await self.ensure(conn, wing)
        if not index.ids:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        if query.shape[0] != index.matrix.shape[1]:
            return []
        norm = float(np.linalg.norm(query))
        if norm <= 0:
            return []
        sims = index.matrix @ (query / norm)
        k = min(k, len(index.ids))
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [(index.ids[i], float(sims[i])) for i in top]

    async def ensure(self, conn, wing: str) -> _WingIndex:
        """The wing's index, brought up to date (rebuild or append) if stale."""
        key = await self._state(conn, wing)
        index = self._wings.get(wing)
        if index is None or index.key != key:
            # Pure append: row count and max rowid moved together, so no row
            # disappeared. (Rows whose embedding failed to parse are simply
            # absent from the matrix; they do not force rebuilds.)
            grew = (
                index is not None and key[0] - index.key[0] == key[1] - index.key[1] > 0
            )
            if grew:
                index = await self._append(conn, wing, index, key)
            else:
                index = await self._rebuild(conn, wing, key)
            self._wings[wing] = index
        return index
