# Architecture discrepancy report (generated from the claims ledger)

Each row is a claim recorded with evidence. Status comes from a deterministic check run against the current code, and `verified` requires an independent reader. Classification is the verdict on the earlier document's conclusion: still supported, superseded, contradicted, incomplete, unverifiable. Historical documents are not edited; this report is the audit trail.

Summary: contradicted 13, incomplete 6, still_supported 10, superseded 5, unverifiable 1; status mix {'supported': 10, 'verified': 21, 'superseded': 4}


## Contradicted (13)

| # | Source | Conclusion | Status | Evidence from current code |
|---|---|---|---|---|
| 7 | academic_benchmarks and brain-research documents | Documents cite docs/COLAB_PATHS_CHEATSHEET.md, which does not exist in the repository. | supported | docs/COLAB_PATHS_CHEATSHEET.md does not exist |
| 8 | academic_benchmarks and brain-research documents | Documents cite docs/FUTURE_WORK.md, which does not exist in the repository. | supported | docs/FUTURE_WORK.md does not exist |
| 9 | academic_benchmarks and brain-research documents | Documents cite HARDWARE.md, which does not exist in the repository. | supported | HARDWARE.md does not exist |
| 10 | academic_benchmarks and brain-research documents | Documents cite docs/BRINGING_IT_TO_LIFE.md, which does not exist in the repository. | supported | docs/BRINGING_IT_TO_LIFE.md does not exist |
| 11 | academic_benchmarks and brain-research documents | Documents cite _archive/research, which does not exist in the repository. | supported | _archive/research does not exist |
| 40 | independent review of claim 23 | vision_agent is a defined service in docker-compose.prod.yml behind profiles: [ vision ], so it starts only on request; the ledger statement that vision is commented out is wrong, and so is calling the service always active. This corrects claim 23. | verified | pattern matched 1 time(s) in docker-compose.prod.yml (need 1..1) |
| 51 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-12 describes documented design, not an oversight: runtime_paths.py states that harness code passes an explicit base_path and only the deployment route through IDENTITY_BASE_PATH is refused for offline copies. | verified | 'if from_deployment and (Path(base) / OFFLINE_MARKER).exists():' occurs 1 time(s) in backend/app/state/runtime_paths.py |
| 2 | evidence/TECHNICAL_EVIDENCE_PACKAGE.md | The evidence package gives the phasic cortisol half-life as 600 s while the code constant CORTISOL_PHASIC_HALFLIFE_S is 4500 s. | verified | 'CORTISOL_PHASIC_HALFLIFE_S: float = 4500.0' occurs 1 time(s) in backend/app/config.py |
| 4 | repo-intel audit session notes | TemporalMemoryStore is constructed by CognitiveService (a static call edge exists), so the earlier finding that it has no caller anywhere was imprecise and is corrected here. | verified | 1 matching CALLS edge(s) (need 1..any) |
| 5 | academic_benchmarks/documentation/sota_comparisons.md | sota_comparisons.md reports a measured 87.5% Recall@5 while literature_review.md marks end-to-end measurements as NOT MEASURED: the benchmark documents contradict each other. | verified | 'NOT MEASURED' occurs 7 time(s) in academic_benchmarks/documentation/literature_review.md |
| 20 | docs/brain-research-v3/decisions (DR-033) | The V3 decision DR-033 forbids a persona reset path, but backend/scripts/reset_persona.py still exists. | verified | backend/scripts/reset_persona.py exists |
| 23 | .agents/CONTEXT.md (Architecture Snapshot) | The ledger says vision is commented out in docker-compose.prod.yml, but vision_agent is an active service there. | superseded | replaced by corrected claim #40 |
| 47 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-08 is contradicted: the offline-copy audit file is created in the copy's parent directory (dest_parent), not inside the copy root, so deleting the copy does not destroy the audit trail. | verified | 'audit_path = dest_parent / AUDIT_FILENAME' occurs 1 time(s) in backend/app/state/offline_copy.py |

## Superseded (5)

| # | Source | Conclusion | Status | Evidence from current code |
|---|---|---|---|---|
| 1 | docs/brain-research/adr/ADR-003-barge-in-turn-targeting.md | ADR-003 states that no COMPLETED playback record fires in production, but the transport agent publishes the playback lifecycle subject, so the statement is out of date. | verified | 1 matching PUBLISHES edge(s) (need 1..any) |
| 6 | academic_benchmarks/documentation/algorithms_equations.md | The default memory ranking policy is hybrid, so documents that describe the V1 ACT-R ranker as the default (algorithms_equations section 5) are out of date. | verified | pattern matched 1 time(s) in backend/app/config.py (need 1..1) |
| 17 | docs/brain-v4/research/critic/round-1.md | The V4 critic finding that no local Claude Code LLM client exists is resolved: llm/__init__.py builds ClaudeCodeClient. | verified | 1 matching CALLS edge(s) (need 1..any) |
| 21 | .agents/CONTEXT.md (Architecture Snapshot) | The engineering ledger architecture snapshot says app/stt holds only __init__.py, but the app/stt package no longer exists. | supported | backend/app/stt/__init__.py does not exist |
| 22 | .agents/CONTEXT.md (Architecture Snapshot) | The ledger snapshot says archived Python agents live under _archive/python_agents, which does not exist in the tree. | supported | _archive/python_agents does not exist |

## Incomplete (6)

| # | Source | Conclusion | Status | Evidence from current code |
|---|---|---|---|---|
| 3 | evidence/TECHNICAL_EVIDENCE_PACKAGE.md | The evidence package marks the bi-temporal memory store as PROVEN, but no method of the TemporalMemoryStore instance is called on the production path: core.py constructs it and the pipeline only stores the reference. | verified | exit 1 (want 1) |
| 41 | independent review of claim 28 | The Wave A critic finding F-02 that hybrid search skips the embedding provenance check is overstated: the scope-level assert is called from one site, but the row-level check _check_row_embedding is defined once and called five times across other retrieval paths. This corrects claim 28. | verified | pattern matched 6 time(s) in backend/app/state/memory_store.py (need 6..6) |
| 46 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-06 is partly wrong: schema.sql adds the embedding_model and embedding_dim columns idempotently with add column if not exists and has no backfill UPDATE, but legacy NULL rows are deliberately treated as the default model by the code and covered by a test. | verified | pattern matched 0 time(s) in backend/db/schema.sql (need 0..0) |
| 55 | docs/brain-v4/research/waveA-critic/round-1.md | Slice 6 commit 6f036aec did not touch decision.py although the slice card lists the PROCEED default consumers at decision.py:884-888; the omission is behaviour-neutral because those defaults still hold and a characterization test passes before and after. Wave A critic finding F-07 is true on the omission and wrong that the baseline test is a defect. | supported | exit 0 (want 0) |
| 24 | CLAUDE.md | The engineering ledger has no entry after 2026-09-26, so the Brain V3 freeze and the Brain V4 cycle are not recorded in the file that CLAUDE.md says is right when documents disagree. | verified | pattern matched 0 time(s) in .agents/CONTEXT.md (need 0..0) |
| 54 | independent review of claim 42 | action.py still blocks stock disclaimer phrases, which D-018 says stays; the real gap against D-018 is that no backend test or eval asks a sincere are-you-human question. This corrects claim 42, which blamed the filter. | supported | exit 1 (want 1) |

## Unverifiable (1)

| # | Source | Conclusion | Status | Evidence from current code |
|---|---|---|---|---|
| 49 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-10 is plausible by code shape: generate_stream writes the whole prompt and awaits drain before it starts reading stdout, which can deadlock if the child fills its stdout pipe before consuming stdin; not reproduced at runtime. | verified | 'await process.stdin.drain()' occurs 1 time(s) in backend/app/llm/claude_code_client.py |

## Still supported (10)

| # | Source | Conclusion | Status | Evidence from current code |
|---|---|---|---|---|
| 44 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-03 holds in part: a bare AgentState target has no offline-copy marker, so _attr_write mutates live in-memory state; the code comment says this path is for unit tests and the in-memory harness, so the risk exists only if a live object is passed. | verified | 'return "attr", None, ""  # bare AgentState' occurs 1 time(s) in backend/app/state/interventions.py |
| 45 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-05 holds: the archived-memory hybrid lookup runs _check_row_embedding inside a broad except Exception that logs and returns an empty list, so an embedding mismatch there degrades to no archive results without setting last_search_error. | verified | 'Archived memories hybrid lookup failed: {arch_err}' occurs 1 time(s) in backend/app/state/memory_store.py |
| 48 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-09 holds: the embedding-scope verification cache key is (table, wing, room) and omits embedding_model and embedding_dimension, so reconfiguring them on a live store instance reuses a stale verdict. | verified | 'key = (table, wing, room)' occurs 1 time(s) in backend/app/state/memory_store.py |
| 50 | docs/brain-v4/research/waveA-critic/round-1.md | Wave A finding F-11 holds: the mock embedding paths in memory_store.py hard-code np.random.randn(768) instead of the configured embedding dimension. | verified | pattern matched 2 time(s) in backend/app/state/memory_store.py (need 2..2) |
| 52 | independent review of claim 19 | BackgroundScheduler is absent from main because Brain V3 unit W9 removed it (ADR-W9-proactive), not unit W4. This corrects claim 19, which credited W4. | supported | 0 definition(s) of BackgroundScheduler |
| 18 | docs/brain-v4/PROJECT_STATE.md | Brain V3 work units W1, W2, W3 and W11 are not built: LearningReviewQueue is still imported and used by learning.py. | superseded | replaced by corrected claim #39 |
| 19 | docs/brain-v4/PROJECT_STATE.md | BackgroundScheduler no longer exists on main, consistent with Brain V3 unit W4 having landed. | superseded | replaced by corrected claim #52 |
| 29 | docs/brain-v4/PROJECT_STATE.md | backend/tools/scale exists, consistent with Brain V3 unit W9 and P8 having landed. | verified | backend/tools/scale/README.md exists |
| 39 | independent review of claim 18 | Brain V3 unit W11 is not on main because learning.py still imports and uses LearningReviewQueue; W1 (commit 9fd49a25) exists only on the unmerged brain-v3 branch with its flag off. This corrects claim 18, which said W1, W2 and W3 are not built. | verified | exit 1 (want 1) |
| 42 | docs/brain-v4/research/phase01-compare/05-phase01-comparison.md | The output filter in action.py still lists the phrase as an ai among blocked phrases, so the repo does not yet meet Brain V4 decision D-018 (the mind may say it is an AI without reciting disclaimers). | superseded | replaced by corrected claim #54 |
