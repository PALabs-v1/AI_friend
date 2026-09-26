# Live infrastructure validation (Phase 4b)

Verifies `MemoryStore`'s cross-store invariants against the real stack (Postgres+pgvector,
Qdrant, Neo4j on home-gpu, `docker-compose.infra.yml` project `aifv3`) — `evals.cognitive`
proves the ranking math is correct against a synthetic embedder and a store built for testing;
nothing before this proved the real write path keeps three real backing stores in sync under
real network calls. Tool: `backend/tools/memory_consistency.py`.

## What it checks and why

1. **Write parity** — N memories through the real `add_memory`: does every one produce exactly
   one Postgres row and one Qdrant point sharing the UUID, with the scalar fields (content,
   wing, importance, valence) agreeing between the two independently-read copies?
2. **No duplicate on repeat** — writing identical content+wing twice should hit
   `MemoryStore`'s documented repeat-detection (`_find_existing_memory` → reinforce) rather than
   create a second row/point in either store.
3. **Entity metadata parity** — `add_memory` pre-links entities from the graph into
   `metadata["entities"]`, then writes that same in-memory list to both Postgres (via
   `_insert_memory_row`) and Qdrant (via `_upsert_qdrant_memory`) through two separate code
   paths. Nothing enforces they stay identical except both reading the same variable — this
   checks that, rather than assuming it.
4. **Archive promotion consistency** — `_write_promoted_memory`'s own docstring flags a known
   risk: SQL commits the promotion first, and if the subsequent Qdrant upsert fails, "the vector
   index is stale for this row" (logged, not raised, so a caller never learns). This exercises
   the actual promotion write path directly (a synthetic row seeded into `archived_memories`,
   then promoted) and checks both stores agree afterward on the live stack, rather than trusting
   the docstring's account of its own behavior.

Every check tags its rows with `wing="_memory_consistency_check"` and cleans up after itself in
all three stores on every exit path (success or failure) — verified separately after each run:
zero residual rows in `memories`/`archived_memories`, zero residual `Entity` nodes.

## Results (2026-09-24, home-gpu, live stack, n=30)

| Check | Result |
|---|---|
| write_parity | **PASS** — 30/30 writes produced a matching row+point pair, all scalar fields agreed |
| no_duplicate_on_repeat | **PASS** — 1 Postgres row (recall_count incremented to 2), 1 Qdrant point after 2 identical writes |
| entity_metadata_parity | **PASS** — `metadata["entities"]` identical between the Postgres row and the Qdrant payload |
| promotion_consistency | **PASS** — active row present, archive row gone, Qdrant point matches, on this run |

Full JSON: `results/home-gpu/memory_consistency.json`.

**Caveat on promotion_consistency**: this run exercises the *happy path* of
`_write_promoted_memory` — it does not inject a Qdrant failure between the SQL commit and the
vector upsert, which is the specific scenario the method's docstring warns about. Passing here
confirms the promotion path is consistent when nothing fails; it does not confirm the documented
failure-mode recovery (there isn't one — the code logs and moves on, by design, treating Qdrant
as a rebuildable accelerator rather than a source of truth). That's a legitimate, transparent
architectural choice already documented at the call site, not a bug this check needed to catch —
recorded here so the caveat travels with the "PASS."

## Bug caught in the tool itself, not the app

The first run of `entity_metadata_parity` failed with `AccessMode: Writing in read access mode
not allowed` — `GraphDB.execute_query()` takes an explicit `write: bool = False` and routes to a
read-only Neo4j session by default; my tool's `CREATE`/`DETACH DELETE` calls didn't pass
`write=True`. Fixed in the tool, re-ran, passed. Worth recording as a small methodology note: a
white-box validation tool can have its own bugs that look exactly like the thing it's checking
for, and the fix here was in the checker, not the checked.

## What this does not cover yet (explicitly deferred)

- **Storage-role value (ADR-004)**: the plan calls for measuring each store's unique cognitive
  value against realistic multi-hop/graph probes. Those probes need `lifesim` (Phase 5, not
  built yet) to generate a realistic family/friends graph with real multi-hop structure — a
  synthetic 30-write smoke test can't produce that. ADR-004 is deferred until Phase 5 exists;
  writing it now from this data would be a guess dressed as a decision.
- **M-14** (Qdrant ignores the `wing` filter at query time) and the **pgvector post-filter
  pool-shrinking** issue from the pre-existing findings ledger are read-path concerns
  (`search_memories`), not write-path consistency — out of this tool's scope by design. They
  belong to W1/W8's retrieval-quality work, not Phase 4b.
- Concurrent-writer races (two turns writing the same wing simultaneously) — this tool runs
  sequentially. A real concurrency stress test belongs in Phase 9 (chaos testing).

## Doc numbering note

This file is `08-` in the sequence, ahead of the plan's originally-envisioned
`08-brain-v3-architecture.md` (reserved for the Phase 10 final integration doc). That file will
need a different number (`09-` or later) when Phase 10 writes it — noted here so that renumbering
decision isn't a surprise later.
