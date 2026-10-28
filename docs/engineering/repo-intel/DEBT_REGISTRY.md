# Technical debt and defect registry (generated from the claims ledger)

Recorded and prioritised, not fixed. `status` is what the checks say about the finding against the current code: supported means the defect or gap is confirmed present, refuted means the finding did not hold.

Findings by category: correctness 3, reliability 5, performance 0, security 0, maintainability 3, integration 4, testing 2, architecture 4


## Correctness

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 15 | medium | human_realism_eval.py hard-codes intent_accuracy = 85.70 as a fallback, so a report can show a measured-looking number that was never measured. | verified | 'intent_accuracy = 85.70  # fallback' occurs 1 time(s) in scripts/research/human_realism_eval.py |
| 28 | medium | The memory hybrid-search path checks embedding provenance only through _assert_scope_embedding_provenance in the Postgres candidate fetch, so other retrieval paths skip the check. | disputed | pattern matched 2 time(s) in backend/app/state/memory_store.py (need 2..3) |
| 41 | low | The Wave A critic finding F-02 that hybrid search skips the embedding provenance check is overstated: the scope-level assert is called from one site, but the row-level check _check_row_embedding is defined once and called five times across other retrieval paths. This corrects claim 28. | supported | pattern matched 6 time(s) in backend/app/state/memory_store.py (need 6..6) |

## Reliability

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 13 | medium | Three Python agents in docker-compose.prod.yml are marked healthy by a bare TCP probe of NATS (nc -z nats_mesh 4222), which says nothing about whether the agent itself works. | disputed | pattern matched 9 time(s) in docker-compose.prod.yml (need 3..any) |
| 14 | medium | The Qdrant healthcheck in docker-compose.infra.yml ends in // exit 0, so it can never report unhealthy. | verified | './qdrant --version // exit 0' occurs 1 time(s) in docker-compose.infra.yml |
| 16 | medium | claude_code_client.py has no CancelledError handling and no finally block, so a cancelled request can leave the child process running. | supported | pattern matched 0 time(s) in backend/app/llm/claude_code_client.py (need 0..0) |
| 38 | medium | Nine health checks in docker-compose.prod.yml are the bare TCP probe nc -z nats_mesh 4222, which proves NATS is reachable and says nothing about whether the agent itself is working. This corrects claim 13, which said three. | supported | pattern matched 9 time(s) in docker-compose.prod.yml (need 9..9) |
| 25 | low | backend/app/cognitive/expression.py imports cognitive_rust unconditionally at module level, against the repo's own pyo3 skill rule that the accelerator must degrade to pure Python. | supported | pattern matched 1 time(s) in backend/app/cognitive/expression.py (need 1..1) |

## Maintainability

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 24 | medium | The engineering ledger has no entry after 2026-09-26, so the Brain V3 freeze and the Brain V4 cycle are not recorded in the file that CLAUDE.md says is right when documents disagree. | verified | pattern matched 0 time(s) in .agents/CONTEXT.md (need 0..0) |
| 12 | low | The .gitignore pattern .env* on line 172 comes after the !.env.example exception on line 18, so it re-ignores .env.example; the file is only safe because it is already tracked. | supported | exit 0 (want 0) |
| 27 | low | The sovereign-mesh-architect skill directory declares the frontmatter name multi-agent-architect, so tools that look a skill up by directory name will not find it. | supported | 'name: multi-agent-architect' occurs 1 time(s) in skills/sovereign-mesh-architect/SKILL.md |

## Integration

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 3 | high | The evidence package marks the bi-temporal memory store as PROVEN, but no method of the TemporalMemoryStore instance is called on the production path: core.py constructs it and the pipeline only stores the reference. | verified | exit 1 (want 1) |
| 32 | medium | brain_agent subscribes to voice.segmentation_feedback but nothing in backend/app or backend/crates publishes it, so the handler _on_voice_feedback can never receive a message. | verified | voice.segmentation_feedback: 0 publisher(s), 1 subscriber(s) |
| 34 | low | state.subconscious is published by subconscious_agent and no code subscribes to it. | verified | state.subconscious: 1 publisher(s), 0 subscriber(s) |
| 35 | low | telemetry.reflection is published by CognitiveService and no code subscribes to it. | supported | telemetry.reflection: 1 publisher(s), 0 subscriber(s) |

## Testing

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 43 | high | Two tests in backend/tests/test_memory_insert.py fail on main at e09ca800 because the Postgres insert now has two more placeholders (embedding model id and dimension, V4 slice 14) than the test expects; they failed before any repo-intel change. | supported | exit 1 (want 1) |
| 36 | low | backend/app/agents/system_agent.py is not imported by any test (importing is not coverage, so this is a lower bound on the testing gap). | supported | 0 matching TEST_VALIDATES edge(s) (need 0..0) |

## Architecture

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 18 | medium | Brain V3 work units W1, W2, W3 and W11 are not built: LearningReviewQueue is still imported and used by learning.py. | disputed | 'LearningReviewQueue' occurs 2 time(s) in backend/app/cognitive/learning.py |
| 20 | medium | The V3 decision DR-033 forbids a persona reset path, but backend/scripts/reset_persona.py still exists. | verified | backend/scripts/reset_persona.py exists |
| 39 | medium | Brain V3 unit W11 is not on main because learning.py still imports and uses LearningReviewQueue; W1 (commit 9fd49a25) exists only on the unmerged brain-v3 branch with its flag off. This corrects claim 18, which said W1, W2 and W3 are not built. | supported | exit 1 (want 1) |
| 42 | medium | The output filter in action.py still lists the phrase as an ai among blocked phrases, so the repo does not yet meet Brain V4 decision D-018 (the mind may say it is an AI without reciting disclaimers). | supported | '"as an ai",' occurs 1 time(s) in backend/app/cognitive/action.py |
