# 05 — Brain V2: what changed, how to run it, how to roll it back

Brain V2 is evolutionary. The agent topology, contracts and databases are
unchanged. Six mechanisms changed, each with an experiment or a failing test
behind it.

## Changes

| Area | V1 | V2 | Evidence | Config / rollback |
|---|---|---|---|---|
| Memory candidates | 20 most recent (SQLite) / top-20 ACT-R (PG) | 60 most similar on every backend; SQLite via in-process vector index | ADR-001, E3/E6 | `MEMORY_RANKING_POLICY=actr_v1` |
| Memory ranking | raw ACT-R sum + substring ×5 | z(cos) + bounded whole-word BM25 + small ACT-R prior | ADR-001 | same |
| Retrieval trace | log line | `MemoryStore.last_search_trace` (ids + per-term scores, no text) | — | — |
| Reappraisal | `w1, w2` adapted and persisted every turn | prediction error still fires hormones; weights fixed at defaults | ADR-002 | `REAPPRAISAL_WEIGHT_LEARNING_ENABLED=true` |
| Barge-in | stop addressed to the new utterance; truncation read the new turn's (reset) state and rewrote whichever assistant row was newest | stop addressed to the reply that is playing; that reply is cut from a snapshot, only its own history row is rewritten, once; the new turn is not cancelled | ADR-003 | — |
| Proactive prompt | raw memory text | injection-gated, delimited | S-1 | — |
| Graph relations in retrieval | never loaded (neo4j `Record` ≠ `dict`) | loaded (V1 policy) | M-4 | — |
| SQLite timestamps | default converter, whole-query failure on some offsets | ISO-8601 converter, per-field degradation | M-9 | — |

## New configuration (`backend/app/config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `MEMORY_RANKING_POLICY` | `hybrid` | `hybrid` or `actr_v1` |
| `MEMORY_CANDIDATE_POOL` | 60 | memories ranked per query |
| `MEMORY_SQLITE_SCAN_LIMIT` | 5000 | most-recent rows held in the SQLite vector index |
| `REAPPRAISAL_WEIGHT_LEARNING_ENABLED` | `false` | adapt appraisal weights from prediction error |

No schema migration. No contract change. Existing stored memories work as is.

## Latency budgets (measured, this environment, 4 vCPU)

| Operation | Budget | Measured |
|---|---|---|
| Memory retrieval, SQLite, 5k memories | p95 ≤ 25 ms | p95 6.6 ms (8.2 ms with interleaved writes) |
| Retrieval index cold build, 5k memories | off critical path | ~1 s at agent start |
| Ranking itself (`hybrid_rank`, 60 candidates) | ≤ 2 ms | included above |
| Affect update per turn | unchanged | — |

Postgres/Qdrant retrieval latency was not measurable here (no pgvector or
Qdrant). The Postgres query now orders by vector distance, which is the shape
pgvector's HNSW index serves.

## Operating it

* Restart the **brain** and **surfacing** agents to pick up the new retrieval
  policy (they own `MemoryStore` instances). The subconscious agent only
  writes memories; restarting it is harmless but not required.
* Inspect a retrieval: `memory_store.last_search_trace` in a debugger or a
  DEBUG log line `Memory retrieval trace: {...}`.
* Benchmarks: `python -m evals.cognitive {memory,affect,latency}` (no
  services, no model); GPU follow-ups in `backend/experiments/gpu/`.

## Failure behaviour (checked)

| Failure | Behaviour |
|---|---|
| Embedding service down | `search_memories` returns `[]`, `last_search_error="embedding service returned no vector"` (tested) |
| Qdrant down or empty | falls through to the SQL path (unchanged) |
| One corrupt stored embedding (SQLite) | row skipped by the index; no rebuild storm (tested) |
| Unparseable stored timestamp (SQLite) | that field becomes text; the query succeeds (tested) |
| Another process writes memories | picked up on the next query via `(count, max rowid, id at max rowid)` (tested) |
| Rows deleted by decay, or rowids reused after deleting the newest rows | full index rebuild on the next query (tested) |
| Concurrent searches while a write lands | `ensure` serialised per wing; appends bounded to the observed rowid range; no duplicates (tested) |
| Embedding model changed (new dimension) | index rebuilt for the query's dimension; if no stored row matches, `last_search_error` says so and the empty result is not cached; rows of another dimension are counted in the trace (`skipped_dimension`) and logged once per rebuild (tested). Nothing downstream reads `last_search_error` yet: surfacing runs in its own process, and carrying an outage marker over `memory.surfaced` is a contract change (open, P7-FIX-06) |
| Agent starts on an empty store (warm-up builds an empty index) | the first appended rows set the index dimension (tested; was: recall empty until restart) |
| Archived memory promoted and re-embedded | the wing's index is invalidated in that process. Residual gap: a re-embedding promotion in *another* process that reuses the top rowid is not seen until the next rebuild |
| Index rebuild (~1.4 s at 5k rows) | parsing runs in a worker thread: event-loop stall 1,235 ms → 91 ms |
| Postgres HNSW | `SET hnsw.ef_search` raised to the pool size (default 40 would cap it). The wing/room filter is applied after the HNSW scan, so a filtered query can still return fewer than the pool; harmless today (every memory is in wing `personal`, no caller passes `room`). pgvector ≥ 0.8 `hnsw.iterative_scan` would close it; not verifiable here |
| Archived row wins but promotion fails | skipped; next candidate takes the slot |
