# Brain V1 → V2 Delta

Baseline: `dac8d0a` (pre-V2). Merge: `001069c` (PR #216, 14 commits). Source: `docs/brain-research/05-brain-v2-architecture.md` cross-checked against the code; three of the numbers below were re-derived directly from source in this session (marked "verified").

## 1. Memory candidate selection

**V1**: SQLite ordered candidates by `last_recalled_at DESC LIMIT 20`. Postgres ordered the top 20 by ACT-R score computed in SQL. Qdrant took the top 20 by cosine.

**Problem**: a relevant memory older than the 20 most-recently-touched rows was structurally unreachable — hit@3 measured 0.00 on facts older than 21 days (register M-2/M-3). The Postgres ordering also defeated the HNSW index (an ORDER BY on a derived score can't use the vector index).

**V2**: every backend now returns the 60 most similar memories by cosine similarity first, *then* ranks. SQLite gained an in-process float32 vector index (`sqlite_vector_index.py`) scanning the most recent 5000 rows (`MEMORY_SQLITE_SCAN_LIMIT`). Postgres does `ORDER BY embedding <=> query LIMIT N` with `SET hnsw.ef_search` tuned to the pool size.

**Why better**: similarity-first candidate selection means age can no longer exclude a relevant memory from being *considered*; ranking (see #2) decides what wins.

**Trade-offs**: SQLite's index costs ~15 MB per process. The pgvector wing/room filter is applied *after* the HNSW scan, so a filtered query can return fewer than the requested pool (unresolved, M-14-adjacent). A re-embedding promotion in another process that reuses the top rowid isn't visible until the index rebuilds.

**Code**: `backend/app/state/sqlite_vector_index.py`, `memory_store.py` (`_fetch_similarity_candidates`), `sqlite_fallback.py`.

**Unresolved**: candidate pool is fixed at 60 regardless of corpus size; no adaptive widening.

---

## 2. Memory ranking

**V1**: `ln(n) − 0.5·ln(h+1) + 1.5·importance + 0.15·(1−d_emo) + spacing_bonus + cos·(1 + 0.1·v·w − 0.2·A·C) − 0.5·d_emo`, plus a flat **+5.0** for every query word matched as a raw substring in the memory text, plus a goal-buffer bonus up to +1.8, plus PageRank spreading activation.

**Problem**: the +5.0 keyword-substring bonus dominates every other term by an order of magnitude; cosine similarity barely moves the final rank. hit@3 measured 0.02-0.04 in the cognitive benchmark.

**V2**: `hybrid_rank(candidate) = z(cos) + 1.5 · BM25/max(BM25) + 0.2 · z(base_level_activation + 1.5·importance)`, where z-scores and the BM25 max are computed *over the candidate pool* (not globally), and BM25 requires whole-word matches, not substrings.

**Why better**: cosine similarity now actually participates in the score at a comparable scale to the lexical term, instead of being swamped by a keyword trap. Production hit@3 on the benchmark's production-like cell: 0.02 → 0.69 (paired delta +0.67, 95% CI [0.65, 0.69], 1146 wins to 1 loss across the tuning seeds). Keyword-trap intrusion into the top 3: 0.31 → 0.02.

**Trade-offs**: a lexical match can still lift an *obsolete* fact that happens to share the query's vocabulary — ADR-001 names this explicitly as the mechanism behind M-5 (stale facts winning retrieval; see workstream W1). No abstention: `hybrid_rank` always returns exactly `limit` results even when nothing in the pool is actually relevant. Everything V1 offered beyond raw cosine+keyword — PageRank spreading activation, pronoun-cue resolution, the goal buffer, topic-shift flush, MRL/stress-narrowed pooling — is dropped from the default path (still reachable via the `actr_v1` policy flag).

**Code**: `backend/app/state/memory_ranking.py` (`hybrid_rank`), config flag `MEMORY_RANKING_POLICY` (default `hybrid`, `config.py:307`).

**Unresolved**: register M-5 (top priority, see `01-v2-delta.md` §7 and workstream W1); no abstention mechanism (workstream W8); graph value under this ranker unquantified (workstream W6).

---

## 3. Retrieval tracing

**V1**: a log line only, no structured trace.

**V2**: `MemoryStore.last_search_trace` — candidate ids, per-term scores (cosine, BM25, ACT-R), and timings, deliberately excluding memory text (so a trace dump can't leak content). `last_search_error` records outage information.

**Why it matters**: this is the seam the benchmark plan's per-retrieval observability (workstream, `06-benchmark-plan.md`) extends, rather than replaces.

**Code**: `memory_store.py:3866` region.

**Unresolved**: nothing downstream *reads* `last_search_error` — an outage during retrieval never reaches the brain's decision layer (register M-8).

---

## 4. Appraisal-weight learning

**V1**: `ReappraisalEngine` adapted the mood-pull weights `w1`/`w2` every turn from prediction error and persisted them.

**Problem**: the closed-loop gain `0.7 + 0.3·(w1 + 0.5·w2)` could exceed 1.0 (measured max 1.105), so mood saturated at +1.0 for 143 of 200 simulated turns, and repeated COMFORT events numbed `w1` toward 0.1 (the opposite of the intended sensitization).

**V2**: `REAPPRAISAL_WEIGHT_LEARNING_ENABLED=False` by default; `hydrate()` ignores any persisted weights, so the engine always starts from fixed defaults. Prediction-error hormone bursts (dopamine/cortisol/adrenaline) still fire — only the *weight adaptation* is disabled, not the underlying appraisal.

**Why better**: turns-saturated drops from 143/200 to 0/200 with the gate off; this is asserted as a regression gate (`test_gate_affect_weight_learning_runaway_is_off_by_default`).

**Trade-offs**: this is a mitigation, not a fix — it disables a feature rather than correcting the loop-gain math. ADR-002 proposes what should replace it (Part 2, see workstream W2), which is unimplemented.

**Code**: `backend/app/cognitive/reappraisal.py`, config flag `REAPPRAISAL_WEIGHT_LEARNING_ENABLED` (`config.py:335`).

**Unresolved**: ADR-002 Part 2 (route G/RI from actual user-expressed valence) is proposed, not shipped — this is register A-1, verified independently this session at `pipeline.py:865` and `appraisal.py:206-227` (see `00-current-architecture.md`).

---

## 5. Barge-in turn targeting

**V1**: a confirmed barge-in stamped its stop/resume with the *new* utterance's turn id.

**Problem**: the reply that was actually playing kept going at reduced volume, because the stop was never addressed to it; resume never restored full volume either (register V-1).

**V2** (ADR-003): the stop is addressed to `_superseded_turn_id` (the turn that *was* playing), passed through as `interrupted_turn_id`. The superseded reply is snapshotted (`_SupersededReply`); only its own history row is rewritten (by row id, never by appending); the new turn is never itself cancelled by its own confirmation.

**Why better**: the actually-playing audio now receives its own stop command, and history reflects only what the user actually heard.

**Trade-offs**: ADR-003's own "Known and not changed" list is long — an ordinary (non-speculative) barge-in still doesn't cut the history row; only one superseded slot exists, so a second interruption before the first resolves loses the first's state; several tight interleavings around redelivery, startle-during-pacing-sleep, and stop-arrives-after-supersession are explicitly unhandled. `scripts/barge_in_mutations.py` kills 33/38 mutations with 5 called equivalent — no property/stateful test exists to explore the interleavings themselves (workstream W5).

**Code**: `backend/app/agents/brain_agent.py` (`_replace_active_generation`, `_begin_turn`, `_on_audio_stop`, `_truncate_interrupted_reply`, `_store_heard_reply`).

**Unresolved**: see workstream W5; the barge-in coverage gaps are cataloged in `docs/brain-research/adr/ADR-003-barge-in-turn-targeting.md`.

---

## 6. Proactive memory injection protection

**V1**: raw surfaced-memory text was inserted directly into the system prompt.

**Problem**: prompt injection via a memory that was itself written from earlier user input (register S-1).

**V2**: `AntiInjectionGate` quarantines surfaced memories with delimiters before they reach the prompt, both for the reactive chat path and for `_render_proactive_memories`.

**Trade-offs**: the chat path only sanitizes when `MEMORY_TRUTH_ENABLED` is on (default True, but a flag nonetheless — register S-2). The subconscious agent's own `thought_prompt` is inserted ungated. Both are workstream W10 items.

**Code**: `backend/app/cognitive/core.py` (`_render_proactive_memories`), `action.py:815`.

---

## 7. Graph relation retrieval

**V1**: filtered candidate rows with `isinstance(row, dict)` — a Neo4j `Record` is not a `dict`, so the relation query silently never ran (register M-4).

**V2**: fixed the type check; relations now load, but only under the `actr_v1` policy (not the default `hybrid` path).

**Unresolved**: whether the graph earns its infrastructure cost under `hybrid` at all is unquantified — workstream W6 (`hybrid+ppr` arm; drop from retrieval if the multi-hop gain is under 0.05, register #69).

---

## 8. Timestamp handling and outage markers

**V1**: SQLite's default datetime converter choked on a single row with a UTC offset, blanking retrieval entirely for that store (M-9). An unreachable branch after a `continue` meant an outage never produced an activation marker (M-7).

**V2**: an explicit ISO-8601 converter degrades on a per-row basis instead of failing the whole store; outages now produce an activation marker (though, per #3 above, nothing downstream consumes it yet).

**Code**: `sqlite_fallback.py`, `cognitive/memory_activation.py`.

---

## Headline measured results (from `docs/brain-research/README.md`, not re-run this session — Phase 3 re-establishes these as the formal local baseline)

- hit@3, production-like cell: 0.02 → 0.69
- Keyword-trap intrusion into top 3: 0.31 → 0.02
- SQLite retrieval latency at 5k memories, p50/p95: 56.9/68.3 ms → 3.9/6.6 ms
- Valence-saturated turns (200-turn affect sim): 143 → 0
- Backend test count at merge: 2540 passed (this session's rerun on `brain-v3`: **2548 passed** — see `04-local-infrastructure.md`)

## What V2 did not touch (carried forward as this cycle's backlog)

Everything in `docs/brain-research/07-future-research.md`'s queue is still open in code, independently confirmed this session:
1. **Temporal truth / stale facts (M-5)** — `TemporalMemoryStore.apply_contradiction` exists, is never called; `valid_until`/`contradicts_id` are stored, never read by retrieval.
2. **User words → affect (A-1)** — verified at `pipeline.py:865`, `appraisal.py:206-227`.
3. **Trust under hostility (A-3)** — verified at `agent_state.py:1342-1355`, `appraisal.py:164-175`.
4. **Voice completion signal (V-2, ADR-003 "Known")** — verified at `crates/voice-agent/src/main.rs:807-821`, `transport_agent.py:333-337`.
5. **Barge-in remaining interleavings** — cataloged in ADR-003, none property-tested.
6. Graph value under the default ranker unquantified.
7. GPU experiments packaged but never executed (`backend/experiments/gpu/`).

These map directly onto workstreams W1-W10 in `05-research-plan.md`, each gated on the corresponding interview round in `03-open-questions.md`.
