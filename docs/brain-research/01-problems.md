# 01 — Problem register

Every entry was verified by reading the code and, where noted, by a failing
test or a measurement. IDs are cited by regression tests
(`backend/tests/test_brain_v2_regressions.py`, `test_memory_ranking.py`,
`test_cognitive_bench.py`). Status: **FIXED** (in this change, with a test
that fails on the old code), **DECIDED** (architecture changed by ADR),
**PROPOSED** (design + experiment written, not shipped), **OPEN**.

## Memory

| ID | Sev | Problem | Evidence | Status |
|---|---|---|---|---|
| M-1 | BLOCKER | Retrieval ranks by keyword substring and history, not relevance: `+5.0` per substring cue vs a cosine term spanning ~0.4; a fact mentioned 3× beats the fact asked about | hit@3 of V1 = **0.02–0.04** on the SQLite path, 0.41–0.58 even with a vector pool (E1, E3) | DECIDED — ADR-001 |
| M-2 | BLOCKER | SQLite candidates are `ORDER BY last_recalled_at DESC LIMIT 20`: any memory older than the 20 newest is never scored. Both prod callers pass `refresh_on_recall=False`, which selects this small pool | `test_v1_policy_still_available_and_still_has_the_recency_cap`; "old" category hit@3 = 0.00 | FIXED (hybrid pool by similarity) |
| M-3 | HIGH | Postgres candidates are the top-20 by the full ACT-R score in SQL, where similarity is a minor term — same starvation, and ordering by an expression defeats the HNSW index | E1 `v1@pg` 0.13–0.33 | FIXED (`ORDER BY embedding <=> q`) |
| M-4 | HIGH | `_gather_candidate_sources` filtered graph rows with `isinstance(row, dict)`; neo4j `Record` is `tuple`+`Mapping`, so the relation query never ran and PageRank only ever saw co-occurrence edges | `test_relation_query_runs_for_real_neo4j_records` (fails before) | FIXED |
| M-5 | HIGH | Obsolete facts outrank their updates (repetition rewards the old preference); `valid_until`/`contradicts_id` are stored but ignored by search; `TemporalMemoryStore` is unwired | obsolete-wins 0.64 (V1) and 0.67 (hybrid) on hard/summary — **no ranker fixes it**; a perfect write-time detector takes it to 0.00 (E7) | OPEN — top research priority (07 §1) |
| M-6 | MEDIUM | Mood terms are query-independent: emotional memories are penalised under neutral mood; the "congruence" gain ignores current valence | zero measured effect at V1 scale (E2); a query-independent emotional prior causes rumination (E-R) | DECIDED — removed in hybrid |
| M-7 | MEDIUM | `memories_to_activations` dropped content-free outage markers (code sat unreachable after `continue`), hiding retrieval outages from `retrieval_degraded` | `test_content_free_outage_marker_becomes_an_outage_activation` | FIXED |
| M-8 | MEDIUM | Surfacing-agent retrieval failures never reach the brain: `SurfacedMemory` has no outage field and `_on_memory_surfaced` drops content-free items; `pipeline._setup_memory_activations` never passes `last_search_error` | reading | OPEN — needs a `memory.surfaced` contract field |
| M-9 | HIGH | SQLite fallback used sqlite3's default TIMESTAMP converter, which raises on a UTC offset without exactly six fractional digits; one such row made every memory query fail (silent empty retrieval) | `test_sqlite_fallback_reads_back_aware_whole_second_timestamps` | FIXED |
| M-10 | MEDIUM | `surfaced_memories` is never cleared between turns, so after the first surfacing event the per-turn synchronous retrieval never runs again and turns reuse memories surfaced for earlier utterances | reading (`core.py:461`, `action.py:1460`) | PROPOSED (07 §3) |
| M-11 | LOW | V1 archive recall wrote up to five promotions to the active tier even when `limit` discarded them | reading | FIXED in hybrid path |
| M-12 | LOW | Production embeds with `nomic-embed-text` without its `search_query:`/`search_document:` task prefixes | reading | PROPOSED — GPU H-R3 |
| M-13 | LOW | `learning.py` ignores `add_memory`'s return; the subconscious marks episodes consolidated even when the summary failed to store (lost on an embedding outage) | reading | OPEN |
| M-14 | LOW | Qdrant search ignores wing at query time (post-filter), so other wings can exhaust the pool | reading | OPEN |

## Affect, trust, learning

| ID | Sev | Problem | Evidence | Status |
|---|---|---|---|---|
| A-1 | BLOCKER | Appraisal's goal congruence is the agent's own mood (`G = mood`, `RI = 0.5·mood`), so the user's words never move valence | affect sim: mood constant across all scripts | PROPOSED — ADR-002 (mechanism validated with oracle input; estimator needs GPU eval) |
| A-2 | HIGH | Reappraisal weight learning is unstable: loop gain `0.7 + 0.3·(w1 + 0.5·w2)` exceeds 1 inside the clamp; from mood 0.6 valence saturates at +1.0 for 143/200 turns; COMFORT turns drive `w1` to 0.1, persisted across restarts | `test_gate_affect_weight_learning_runaway_is_off_by_default` | FIXED — learning off by default (ADR-002) |
| A-3 | HIGH | Trust rises on any message without a boundary word (`+0.1·NA` per turn) — hostile users earn trust; the asymmetric evidence model in `PersonModel` is never called | affect sim: trust 0.83 after 60 hostile turns | PROPOSED (07 §4) |
| A-4 | MEDIUM | Derived arousal (energy + fatigue + adrenaline) is written back into `energy` by the somatic path and System-2 appraisal, permanently absorbing phasic signals | reading (`agent_state.py:1538`, `1240-1244`) | OPEN |
| A-5 | MEDIUM | Tick decay uses the tick message's `interval` field, not elapsed time: if ticks stop, PAD stops decaying while hormones keep decaying | reading | OPEN |
| A-6 | LOW | `_is_acute_distress` tests the *agent's* state but the resulting guideline says "the user appears to be in acute distress" | reading (`decision.py:178`, `action.py:322`) | OPEN |
| A-7 | LOW | `CapabilityLimitationModel.evaluate_directive`, `GlobalControls.learning_gain`, calibration: computed or defined, never used | reading | OPEN (delete or wire) |

## Interaction / voice

| ID | Sev | Problem | Evidence | Status |
|---|---|---|---|---|
| V-1 | HIGH | A confirmed voice "stop" (and a rejected interruption's resume) was addressed to the *new* utterance's turn id; the voice agent and transport only honour signals for the turn they are speaking, so the old reply kept playing at 30% volume and the resume never restored it | `test_stage2_signal_targets_the_interrupted_reply` | FIXED (ADR-003) |
| V-2 | HIGH | Self-correction publishes an unscoped confirmed `audio.stop`; the brain's own handler cancels the generation that is running the retry and the voice agent aborts the rest of the turn, so the retry is never heard | reading (`action.py:1331`, `brain_agent.py:_on_audio_stop`, `main.rs:840`) | PROPOSED (07 §5) — needs flush-without-abort semantics in the Rust voice agent |
| V-3 | MEDIUM | `chat.input` is handled strictly serially (nats-py awaits each callback and the handler awaits the whole turn), so "preempt the in-flight turn" is unreachable from speech | reading | OPEN |
| V-4 | MEDIUM | Proactive cooldown can reset: `mark_proactive_attempt` writes only the subconscious's local state and the next brain broadcast overwrites it | reading (`agent_state.py:983-988`, `1859`) | OPEN |

## Security

| ID | Sev | Problem | Evidence | Status |
|---|---|---|---|---|
| S-1 | HIGH | `generate_proactive_response` inserted raw surfaced-memory text into the system-level proactive prompt — no `AntiInjectionGate`, no `[RETRIEVED-CONTENT]` delimiters | `test_proactive_memory_injection_is_quarantined` | FIXED |
| S-2 | LOW | The chat path sanitises retrieved memory only when `MEMORY_TRUTH_ENABLED` (default on); the flag governs truth semantics, not injection safety | reading | OPEN |

## Benchmark / tooling

| ID | Sev | Problem | Status |
|---|---|---|---|
| B-1 | — | No retrieval-level metric existed: `evals/retrieval.py` scores whether an LLM's answer contains a planted fact (needs Ollama + DB) | FIXED — `evals/cognitive` (recall@k, MRR, obsolete-win, trap intrusion, bootstrap CIs, paired deltas) |
| B-2 | — | `tests/conftest.py` `os._exit`s in `pytest_sessionfinish`, which suppresses pytest's summary and tracebacks locally; run with `CI=1` to see them | documented |

## Adversarial review of the first Brain V2 cut

An independent reviewer (fresh context, deliverable plus a frozen rubric, told
to reject) returned **FAIL** with 13 findings. All were checked and addressed:

| # | Finding | Resolution |
|---|---|---|
| R-1 | Vector index stale after SQLite rowid reuse (delete newest k, insert k) → search returned `[]` with no error | key now includes the id at max rowid, plus a same-top-row check before appending; tests reproduce both reuse patterns |
| R-2 | Index rebuild parsed JSON on the event loop (1.2 s stall at 5k rows) | parsing/stacking moved to a worker thread; stall 91 ms |
| R-3 | A write between the state read and the append duplicated rows; concurrent `ensure` mutated the index | appends bounded to `(old top, new top]`; per-wing `asyncio.Lock`; test |
| R-4 | Over the cap, appends evicted the most recently recalled rows | index held oldest→newest; test |
| R-5 | Embedding-dimension change silently blanked recall forever | rebuild for the query's dimension; `last_search_error` when nothing matches; test |
| R-6 | Unparseable `created_at` failed every hybrid search | `_as_aware_utc` in the candidate builder (found in self-review too); test |
| R-7 | Hybrid `score` is pool-relative but was read downstream as absolute relevance (decision threshold 0.75 always passed) | results carry `relevance` (clipped cosine); surfacing publishes it; tests |
| R-8 | pgvector HNSW caps results at `ef_search` (40) < pool (60) | `SET hnsw.ef_search` per query; test (mocked; not verifiable live here) |
| R-9 | Superseded-turn acceptance let a stale facial-startle stop cancel the new turn | only Stage 2's `confirmed_command` may target the superseded turn, once; tests |
| R-10 | Room filter / exclusions applied after top-k (SQLite), wing post-filter (Qdrant) | room filtered inside the index; Qdrant filtered by wing/room at query time with over-fetch |
| R-11 | Hybrid silently dropped V1 features | each listed with its reason in `memory_ranking.py` and ADR-001 |
| R-12 | `_state` full table scan; `importance or 0.5` | index on `memories(wing)`; zero importance kept (test) |
| R-13 | Bootstrap treated correlated probes as independent | cluster bootstrap over scenario seeds; all intervals in 03 regenerated; test |

## Second adversarial review (of the R-1..R-13 fixes)

A fresh reviewer re-judged the fix commit cold, reran every repro, fuzzed the
index (300 seeds) and checked each doc number against the results JSON. It
confirmed R-1, R-2, R-4 and R-6..R-13 resolved and returned **FAIL** on one new
regression. Every finding below has a test that fails on the code it reviewed.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| R2-1 | HIGH | The startup warm-up on an empty wing stored `dim=None`; every later write took the append path, which never set it, so `top_k` returned `[]` for the life of the process with no error (a fresh install never recalled anything) | the append adopts the dimension of the first rows it loads; unit and end-to-end tests |
| R2-2 | MEDIUM | Accepting Stage 2's stop for the superseded reply cancelled the *new* turn (the "stop" utterance's own flow) and truncated nothing, because that turn had already reset the reply state | superseded reply snapshotted in `_begin_turn`; its playback progress keeps updating the snapshot; the stop truncates the snapshot and leaves the current turn running (ADR-003). The first version of this fix introduced R3-1..R3-3 |
| R2-2b | MEDIUM (pre-existing, found while fixing R2-2) | Truncating a reply whose generation was cancelled overwrote the *previous* turn's stored reply (the cancelled one was never logged); a logged reply's UPDATE could also race its spawned INSERT | first fix (append) was wrong, see R3-2; final (R4-1): the reply's own row, addressed by a brain-generated id, rewritten after its insert lands |
| R2-3 | LOW | A write landing between a rebuild's key read and its fetch was indexed twice after the next append | appends skip ids already present; test |
| R2-4 | LOW | `last_search_error` for a dimension mismatch was cleared by a 15 s L1 cache hit; a partly re-embedded store lost old rows silently | degraded results are not cached; skipped rows traced and logged per rebuild; tests. Downstream consumption stays open (05, P7-FIX-06) |
| R2-5 | LOW | Under `actr_v1` results had no `relevance`, so surfacing published the unbounded ACT-R score (R-7 back on rollback) | `relevance` on every result under both policies; test |
| R2-6 | LOW | Same id reinserted at the same rowid with a new embedding (re-embedding promotion) was invisible to the staleness key | promotion invalidates the wing in-process; cross-process residual documented in 05 |
| R2-7 | LOW (docs) | 03 said "~1,100 probes per cell" (JSON: 1,707); ef_search under a filter can still return fewer than the pool | 03 corrected from the JSON; 05 states the filter limit |

## Third adversarial review (of the R2 fixes)

A third fresh reviewer drove the real `_on_chat_input` flow end to end and
returned **FAIL**: the retrieval fixes held (R2-1, R2-3..R2-7 resolved), but
the first R2-2/R2-2b history code wrote wrong rows. The history design was
replaced rather than patched (ADR-003, "History rules").

| # | Sev | Finding | Resolution |
|---|---|---|---|
| R3-1 | HIGH | A "stop" during a proactive utterance cut the previous *user* reply's text at the proactive turn's offset and wrote it over the proactive turn's row; also a second outcome record for the old intent | `_reply_turn_id` records whose text `last_assistant_response` is; a stop for another turn cuts nothing |
| R3-2 | MEDIUM | The heard part of a reply cancelled mid-generation was appended after the user's "stop" row | nothing is appended; such a reply has no row (accepted loss, ADR-003) |
| R3-3 | HIGH | Every later confirmed stop (a facial startle fires one per startle, even idle) re-appended the full unheard partial and recorded it again | a reply is resolved once (`_reply_resolved`) |
| R3-4 | LOW | `log_message` swallows insert failures, so "insert failed" was never detected and the cut overwrote the previous reply | first fix (content-addressed, `expected=`) was wrong, see R4-1; final: rows addressed by a brain-generated id, so a missing row means no write |
| R3-5 | LOW | After an empty warm-up the first append took the rows' majority dimension over the query's | the query's dimension wins; test |
| R3-6 | LOW (tests) | Some "fails on the old code" tests failed only on a missing helper; the history double had no roles, so it could not see R3-1..R3-3 | barge-in tests moved to `tests/test_barge_in_real_flow.py`, real flow, role-aware history, real-store guard test |

## Fourth adversarial review (of the R3 fixes)

A fourth fresh reviewer confirmed R3-1..R3-3 and R3-5 resolved (fuzz: wrong
rows 3 → 0, rows inserted after "stop" 46 → 0, repeated records 10 → 0) and
returned **FAIL** on two MEDIUM findings.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| R4-1 | MEDIUM | The content-addressed rewrite cut an older reply with identical text when this reply had no row ("Sure thing." → "Sure") | rows addressed by a brain-generated message id; real-store test with identical rows |
| R4-2 | MEDIUM | R3 deleted the tests for three ADR-003 invariants; three mutations survived the whole suite, and `turn_b.cancelled()` could never fail | invariant tests restored in the real-flow module; survival checked by a flag the turn sets after Stage 2; all three mutations now fail the module |
| R4-3 | LOW (pre-existing sibling of R3-1) | A proactive turn's completed playback recorded the previous user reply COMPLETED again | COMPLETED only for the turn owning the text, once |
| R4-4 | LOW (pre-existing) | Replacing a proactive turn recorded CANCELLED for a user reply that had finished | CANCELLED only while that reply is still generating (`_reply_generating`) |
| R4-5 | LOW | The cut awaited the reply's insert under `_turn_state_lock` with no bound; the pool has no command timeout | bounded wait (2 s), reply left uncut after it; test |
| R4-6 | LOW | The history double did not match the real store; SQLite logged every successful rewrite as "not rewritten"; SQLite timestamp ties | double mirrors the id contract; SQLite's unknown rowcount no longer logged as zero; ties irrelevant with id addressing |

Remaining pre-existing barge-in gaps are listed in ADR-003 ("Known and not
changed here").

## Fifth adversarial review (of the R4 fixes)

A fifth fresh reviewer confirmed the R4 behaviour correct (history anomalies
in a 400-seed fuzz: none; the identical-row cut correct on real SQLite and
real PostgreSQL 16) and returned **FAIL** because two new regression tests
could not fail, the defect class R4-2 was filed for.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| R5-1 | MEDIUM | The R4-4 test was vacuous (the fake proactive turn ignored its delay, so nothing was replaced); the R4-1 real-store test compared sorted contents, so it passed whichever identical row was cut; the R4-1 flow test passed on b993dd8 only through a swallowed TypeError; mutations of the `_reply_generating` gate and a wrong-identical-row store survived the suite | fake honours delays and the test asserts the replacement happens; real-store test compares row by row by id; the double implements every past store contract; three new tests separate the paths; 14 of 15 mutations killed, the survivor shown equivalent (ADR-003) |
| R5-2 | LOW | The row id was set in a second critical section after generation ended, so a stop in between cut nothing | text, generation end, id and insert task set in one critical section |
| R5-3 | LOW | CANCELLED was not resolved-once: a stop's cancellation left `_reply_generating` set, so a later replacement recorded the cut reply CANCELLED again | every path that ends generation clears it; test |
| R5-4 | LOW | TRUNCATED recorded for a reply history keeps uncut; only the insert wait is bounded | documented (records describe delivery; the UPDATE was unbounded before) |
| R5-5 | LOW | The newest-row fallback existed only for test doubles, so the old truncation tests exercised a path production never takes | fallback removed; those tests set owner and row id |

