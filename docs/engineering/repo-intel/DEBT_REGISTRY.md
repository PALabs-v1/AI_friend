# Technical debt and defect registry (generated from the claims ledger)

Recorded and prioritised, not fixed. `status` is what the checks say about the finding against the current code: supported means the defect or gap is confirmed present, refuted means the finding did not hold.

Findings by category: correctness 7, reliability 9, performance 0, security 0, maintainability 4, integration 4, testing 5, architecture 5


## Correctness

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 15 | medium | human_realism_eval.py hard-codes intent_accuracy = 85.70 as a fallback, so a report can show a measured-looking number that was never measured. | verified | 'intent_accuracy = 85.70  # fallback' occurs 1 time(s) in scripts/research/human_realism_eval.py |
| 28 | medium | The memory hybrid-search path checks embedding provenance only through _assert_scope_embedding_provenance in the Postgres candidate fetch, so other retrieval paths skip the check. | superseded | replaced by corrected claim #41 |
| 47 | medium | Wave A finding F-08 is contradicted: the offline-copy audit file is created in the copy's parent directory (dest_parent), not inside the copy root, so deleting the copy does not destroy the audit trail. | verified | 'audit_path = dest_parent / AUDIT_FILENAME' occurs 1 time(s) in backend/app/state/offline_copy.py |
| 41 | low | The Wave A critic finding F-02 that hybrid search skips the embedding provenance check is overstated: the scope-level assert is called from one site, but the row-level check _check_row_embedding is defined once and called five times across other retrieval paths. This corrects claim 28. | verified | pattern matched 6 time(s) in backend/app/state/memory_store.py (need 6..6) |
| 44 | low | Wave A finding F-03 holds in part: a bare AgentState target has no offline-copy marker, so _attr_write mutates live in-memory state; the code comment says this path is for unit tests and the in-memory harness, so the risk exists only if a live object is passed. | verified | 'return "attr", None, ""  # bare AgentState' occurs 1 time(s) in backend/app/state/interventions.py |
| 48 | low | Wave A finding F-09 holds: the embedding-scope verification cache key is (table, wing, room) and omits embedding_model and embedding_dimension, so reconfiguring them on a live store instance reuses a stale verdict. | verified | 'key = (table, wing, room)' occurs 1 time(s) in backend/app/state/memory_store.py |
| 50 | low | Wave A finding F-11 holds: the mock embedding paths in memory_store.py hard-code np.random.randn(768) instead of the configured embedding dimension. | verified | pattern matched 2 time(s) in backend/app/state/memory_store.py (need 2..2) |

## Reliability

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 13 | medium | Three Python agents in docker-compose.prod.yml are marked healthy by a bare TCP probe of NATS (nc -z nats_mesh 4222), which says nothing about whether the agent itself works. | superseded | replaced by corrected claim #38 |
| 14 | medium | The Qdrant healthcheck in docker-compose.infra.yml ends in // exit 0, so it can never report unhealthy. | verified | './qdrant --version // exit 0' occurs 1 time(s) in docker-compose.infra.yml |
| 16 | medium | claude_code_client.py has no CancelledError handling and no finally block, so a cancelled request can leave the child process running. | supported | pattern matched 0 time(s) in backend/app/llm/claude_code_client.py (need 0..0) |
| 38 | medium | Nine health checks in docker-compose.prod.yml are the bare TCP probe nc -z nats_mesh 4222, which proves NATS is reachable and says nothing about whether the agent itself is working. This corrects claim 13, which said three. | superseded | replaced by corrected claim #53 |
| 53 | medium | Seven health checks in docker-compose.prod.yml are the bare TCP probe nc -z nats_mesh 4222 (two further matches are comments), which proves NATS is reachable and says nothing about whether the agent works. This corrects claims 13 and 38, which said three and nine. | verified | pattern matched 7 time(s) in docker-compose.prod.yml (need 7..7) |
| 25 | low | backend/app/cognitive/expression.py imports cognitive_rust unconditionally at module level, against the repo's own pyo3 skill rule that the accelerator must degrade to pure Python. | verified | pattern matched 1 time(s) in backend/app/cognitive/expression.py (need 1..1) |
| 45 | low | Wave A finding F-05 holds: the archived-memory hybrid lookup runs _check_row_embedding inside a broad except Exception that logs and returns an empty list, so an embedding mismatch there degrades to no archive results without setting last_search_error. | verified | 'Archived memories hybrid lookup failed: {arch_err}' occurs 1 time(s) in backend/app/state/memory_store.py |
| 49 | low | Wave A finding F-10 is plausible by code shape: generate_stream writes the whole prompt and awaits drain before it starts reading stdout, which can deadlock if the child fills its stdout pipe before consuming stdin; not reproduced at runtime. | verified | 'await process.stdin.drain()' occurs 1 time(s) in backend/app/llm/claude_code_client.py |
| 63 | low | The phasic cortisol burst is not persisted although its half-life is 4500 s, so a process restart within the first hour drops most of a recent stress signal to zero (Codex review finding 3; the comment that said otherwise was corrected). | supported | 'cortisol_halflife_s: float = 4500.0' occurs 1 time(s) in backend/app/state/agent_state.py |

## Maintainability

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 24 | medium | The engineering ledger has no entry after 2026-09-26, so the Brain V3 freeze and the Brain V4 cycle are not recorded in the file that CLAUDE.md says is right when documents disagree. | verified | pattern matched 0 time(s) in .agents/CONTEXT.md (need 0..0) |
| 12 | low | The .gitignore pattern .env* on line 172 comes after the !.env.example exception on line 18, so it re-ignores .env.example; the file is only safe because it is already tracked. | verified | exit 0 (want 0) |
| 27 | low | The sovereign-mesh-architect skill directory declares the frontmatter name multi-agent-architect, so tools that look a skill up by directory name will not find it. | verified | 'name: multi-agent-architect' occurs 1 time(s) in skills/sovereign-mesh-architect/SKILL.md |
| 55 | low | Slice 6 commit 6f036aec did not touch decision.py although the slice card lists the PROCEED default consumers at decision.py:884-888; the omission is behaviour-neutral because those defaults still hold and a characterization test passes before and after. Wave A critic finding F-07 is true on the omission and wrong that the baseline test is a defect. | verified | exit 0 (want 0) |

## Integration

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 3 | high | The evidence package marks the bi-temporal memory store as PROVEN, but no method of the TemporalMemoryStore instance is called on the production path: core.py constructs it and the pipeline only stores the reference. | verified | exit 1 (want 1) |
| 32 | medium | brain_agent subscribes to voice.segmentation_feedback but nothing in backend/app or backend/crates publishes it, so the handler _on_voice_feedback can never receive a message. | verified | voice.segmentation_feedback: 0 publisher(s), 1 subscriber(s) |
| 34 | low | state.subconscious is published by subconscious_agent and no code subscribes to it. | verified | state.subconscious: 1 publisher(s), 0 subscriber(s) |
| 35 | low | telemetry.reflection is published by CognitiveService and no code subscribes to it. | verified | telemetry.reflection: 1 publisher(s), 0 subscriber(s) |

## Testing

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 43 | high | Two tests in backend/tests/test_memory_insert.py fail on main at e09ca800 because the Postgres insert now has two more placeholders (embedding model id and dimension, V4 slice 14) than the test expects; they failed before any repo-intel change. | superseded | replaced by corrected claim #56 |
| 54 | medium | action.py still blocks stock disclaimer phrases, which D-018 says stays; the real gap against D-018 is that no backend test or eval asks a sincere are-you-human question. This corrects claim 42, which blamed the filter. | verified | exit 1 (want 1) |
| 36 | low | backend/app/agents/system_agent.py is not imported by any test (importing is not coverage, so this is a lower bound on the testing gap). | verified | 0 matching TEST_VALIDATES edge(s) (need 0..0) |
| 46 | low | Wave A finding F-06 is partly wrong: schema.sql adds the embedding_model and embedding_dim columns idempotently with add column if not exists and has no backfill UPDATE, but legacy NULL rows are deliberately treated as the default model by the code and covered by a test. | verified | pattern matched 0 time(s) in backend/db/schema.sql (need 0..0) |
| 56 | info | After commit 2569ae0a the Postgres placeholder tests in test_memory_insert.py pass: the insert binds 27 parameters timed and 25 untimed, matching the two embedding identity columns added in V4 slice 14. This replaces claim 43, which described the failure before the fix. | verified | exit 0 (want 0) |

## Architecture

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 18 | medium | Brain V3 work units W1, W2, W3 and W11 are not built: LearningReviewQueue is still imported and used by learning.py. | superseded | replaced by corrected claim #39 |
| 20 | medium | The V3 decision DR-033 forbids a persona reset path, but backend/scripts/reset_persona.py still exists. | verified | backend/scripts/reset_persona.py exists |
| 39 | medium | Brain V3 unit W11 is not on main because learning.py still imports and uses LearningReviewQueue; W1 (commit 9fd49a25) exists only on the unmerged brain-v3 branch with its flag off. This corrects claim 18, which said W1, W2 and W3 are not built. | verified | exit 1 (want 1) |
| 42 | medium | The output filter in action.py still lists the phrase as an ai among blocked phrases, so the repo does not yet meet Brain V4 decision D-018 (the mind may say it is an AI without reciting disclaimers). | superseded | replaced by corrected claim #54 |
| 51 | low | Wave A finding F-12 describes documented design, not an oversight: runtime_paths.py states that harness code passes an explicit base_path and only the deployment route through IDENTITY_BASE_PATH is refused for offline copies. | verified | 'if from_deployment and (Path(base) / OFFLINE_MARKER).exists():' occurs 1 time(s) in backend/app/state/runtime_paths.py |
