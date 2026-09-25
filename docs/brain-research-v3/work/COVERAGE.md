# Brain V3 coverage matrix

Every open item from every source this cycle has produced, mapped to exactly
one owner. `backend/tests/test_brain_v3_coverage.py` fails if an open
register item, an open finding or any decision record is missing from this
table, or if a row names an ID that does not exist. That keeps "nothing left
out" a checked property, not a promise.

Owners:
- `W1`..`W11`: Phase 7 workstreams (`05-research-plan.md`, specs in this directory)
- `SW`: the pre-existing problem sweep (`SW.md`)
- `P6`: Phase 6 (BrainBench) remainder
- `P8` scale, `P9` chaos, `P10` integration and simplification
- `CONSTRAINT`: a decision every workstream must honour; the Phase 10 review
  checks it holds
- `CLOSED`: resolved, with the evidence named in the row

Waves (2026-09-25):
1. Wave A: W4, W9, the W10 attack corpus, P8.
2. Then W5, which needs W4's lifecycle and W9's importance score. Then W10's
   `subconscious_agent.py` items, after W9.
3. Wave B: W1, W3, W2 and SW in parallel. W8 follows W1 (same search path,
   and it reads W1's contradiction records).
4. Wave C:
   - W6 and W7 after W1 and P8.
   - W11 after W2, and only once DR-038 (the evolution-pace decision in
     `W11.md`) is recorded.
5. P9 runs alongside Waves B and C, and P10 comes last.

Every workstream has a spec in this directory: `W1.md`..`W11.md`, `SW.md`,
`P8.md`.

## Register (`docs/brain-research/01-problems.md`)

| ID | Owner | Note |
|---|---|---|
| M-5 | W1 | stale facts win retrieval; target obsolete-win <= 0.10 |
| M-8 | W8 | surfacing outages never reach the brain (F-015 measured 7/7 invisible) |
| M-10 | W8 | `surfaced_memories` never cleared (F-015) |
| M-12 | CLOSED | H-R3 answered no in Phase 4a (`09-gpu-experiment-results.md`): prefixing hurts the `summary` regime; production stays unprefixed |
| M-13 | W8 | `learning.py` ignores `add_memory`'s return |
| M-14 | W8 | Qdrant wing filter at query time; verify against R-10's claimed fix on live Qdrant (`08-live-infra-validation.md` deferred it to W1/W8) |
| A-1 | W2 | user words never move valence |
| A-3 | W3 | trust rises under hostility (F-010) |
| A-4 | W2 | arousal written back into energy |
| A-5 | W2 | tick decay uses message interval, not elapsed time |
| A-6 | W2 | acute distress tests the agent's state; DR-025 trigger |
| A-7 | SW | `learning_gain`, `evaluate_directive`, calibration: wire or delete |
| V-2 | W4 | self-correction stop cancels its own retry; DR-029 flush semantics |
| V-3 | W5 | serial `chat.input` makes preemption unreachable |
| V-4 | W9 | proactive cooldown reset; measured as a flood (F-011) |
| S-2 | W10 | sanitisation gated on `MEMORY_TRUTH_ENABLED` |
| B-2 | SW | `conftest.py` `os._exit` hides summaries |

## Findings (`findings.md`)

| ID | Owner | Note |
|---|---|---|
| F-001 | SW | vendored onnxruntime dylib signature (`build.rs` fix at source) |
| F-002 | W4 | completion signal has no producer |
| F-003 | SW | Qdrant healthcheck can never fail (also C0-2) |
| F-004 | SW | Postgres port disagreement (also C0-4) |
| F-005 | W2 | `llama3.2:3b` classification parse rate; weighs W2's estimator choice (model choice is pluggable, not a product defect) |
| F-006 | SW | JSON extractor cannot read thinking-mode output |
| F-007 | W7 | throttled or concurrent reflection drops episodes |
| F-009 | W2 | System2 semantic-drift appraisal echoes its prompt template |
| F-010 | W3 | trust is a turn counter; saturates at turn 6/13 |
| F-011 | W9 | V-4 flood, 60 outreaches per idle hour |
| F-012 | W11 | evolution frozen, or the whole adaptive self replaced in a week |
| F-013 | W5 | confirmed barge-in leaves no terminal outcome and unheard text in history (W4 supplies the lifecycle it needs) |
| F-014.1 | W7 | `workspace_transitions` never pruned |
| F-014.2 | W8 | surfacing throttles read `time.time()`, not `app.clock` |
| F-014.3 | W8 | `last_search_error` is not a durable outage signal |
| F-014.5 | W11 | `LearningGovernor._proposals` never pruned |
| F-015 | W8 | M-10 measured on a real model |

F-008 and F-014 item 4 are fixed (`167db41b`, `e2b73f89`).

## Decisions (`decisions/`)

| ID | Owner | Note |
|---|---|---|
| DR-001 | W11 | immutable core scope; the governor boundary |
| DR-002 | CONSTRAINT | autonomous humanoid mind, not an assistant |
| DR-003 | CONSTRAINT | single relationship this cycle |
| DR-004 | W11 | style, traits and baseline PAD may drift |
| DR-005 | W1 | supersession: history kept, new value wins present tense |
| DR-006 | W1 | correction vs change |
| DR-007 | W7 | trivia decays to unretrievable, never deleted |
| DR-008 | W2 | sentiment moves mood, damped and capped |
| DR-009 | W2 | single extreme event can leave a mark |
| DR-010 | W2 | three affect layers |
| DR-011 | W11 | baseline set-point drift, weeks-long only |
| DR-012 | W3 | three separate trust dimensions |
| DR-013 | W3 | sharp drop, slow recovery |
| DR-014 | W3 | competence from outcomes, benevolence from tenor |
| DR-015 | W3 | trust feeds relationship sentiment |
| DR-016 | W9 | importance-weighted initiation |
| DR-017 | W9 | diminishing-returns resurfacing; wires `GoalRecord` |
| DR-018 | W9 | time-of-day / activity gating |
| DR-019 | W9 | self-initiated thought category, stricter gate |
| DR-020 | W11 | self-authored evolution, no approval queue |
| DR-021 | W11 | evidence-driven drift, confidence bar only |
| DR-022 | W11 | opaque evolution |
| DR-023 | W11 | keep 0.8 confidence |
| DR-024 | P8 | latency budgets; W9 adds the starvation test, W4 the time-to-first-audio check |
| DR-025 | W2 | regulation can win on a significant event |
| DR-026 | W5 | significant self-thought can interrupt (importance from W9) |
| DR-027 | W5 | word-level heard-text truncation |
| DR-028 | W5 | ordinary barge-in cuts history |
| DR-029 | W4 | self-correction always finishes |
| DR-030 | W5 | important proactive turn gets a grace window (importance from W9) |
| DR-031 | W11 | freeze the avoid-list / refusal fields |
| DR-032 | W10 | affect never overrides safety; extreme-affect regression test once W2 and W9 land |
| DR-033 | W11 | no reset mechanism |
| DR-034 | W7 | emotional memories fade eventually |
| DR-035 | CONSTRAINT | no intent detection for deliberate testing (W1 must not build one) |
| DR-036 | CLOSED | lifesim guardrail; Phase 5 honoured it |
| DR-037 | CLOSED | reflection stays LLM; Phase 6 suites honour it |

## Everything else

| ID | Owner | Source | Note |
|---|---|---|---|
| C0-1 | W10 | `02-audit-comparison.md` | LiveKit `network_mode: host` bypasses the loopback-only port policy, and `mesh-integrity.yml` cannot see it (high) |
| C0-2 | SW | `02-audit-comparison.md` | same as F-003 |
| C0-3 | W10 | `02-audit-comparison.md` | NATS runs with no auth by default |
| C0-4 | SW | `02-audit-comparison.md` | same as F-004 |
| C0-5 | SW | `02-audit-comparison.md` | GPU README omits maturin, does not match CI |
| C0-6 | SW | `02-audit-comparison.md` | `mesh-integrity.yml` env check is warning-only |
| C1-1 | W8 | `02-audit-comparison.md` | `search_memories(threshold=)` ignored under hybrid |
| C1-2 | CLOSED | `02-audit-comparison.md` | lab embedder sees topic labels; Phase 4a real embeddings and lifesim scoring replace it as evidence |
| C1-3 | CLOSED | `02-audit-comparison.md` | held-out harness stubs live stores; Phase 4b validated live stores (`08-live-infra-validation.md`), P8 extends it to scale |
| C1-4 | P10 | `02-audit-comparison.md` | widen `mutmut` scope to ranking, pipeline, affect and trust once W1-W3 land |
| C1-5 | W6 | `02-audit-comparison.md` | ARCHITECTURE.md's graph-in-V2 claim is stale for hybrid |
| C1-6 | CLOSED | `02-audit-comparison.md` | "reflex" terminology: `00-current-architecture.md` already uses it correctly |
| G-1 | SW | `04-local-infrastructure.md` | `LLM_FAST_MODEL` disagrees between `.env` and `.env.example` |
| G-2 | SW | `04-local-infrastructure.md` | `.env` lacks per-agent NATS credentials |
| G-3 | SW | `04-local-infrastructure.md` | `QDRANT_HOST` / `QDRANT_PORT` unset in both env files |
| G-4 | W4 | `04-local-infrastructure.md` | GPT-SoVITS weights empty; W4's live mesh run must state which TTS it uses |
| G-5 | SW | `04-local-infrastructure.md` | GPU README host facts stale (driver 535 vs 595.84) |
| X-1 | W6 | `08-live-infra-validation.md` | ADR-004 storage roles, deferred until lifesim existed |
| X-2 | W8 | `08-live-infra-validation.md` | pgvector post-filter shrinking the candidate pool |
| X-3 | P9 | `08-live-infra-validation.md` | concurrent-writer races on one wing |
| X-4 | P6 | Phase 6 gates | attention: repeated turns score more novel than fresh ones (Cliff's delta -0.26 on the gate slice); settle on the full panel |
| SW-1 | SW | `05-research-plan.md` sweep | pipeline members constructed but never called |
| SW-2 | SW | `05-research-plan.md` sweep | orphan NATS subjects |
| SW-3 | W11 | `05-research-plan.md` sweep | `LearningReviewQueue`: in-memory, nothing approves (DR-020 removes it) |
| SW-4 | W1 | `05-research-plan.md` sweep | `TemporalMemoryStore` defaults to `:memory:` |
| SW-5 | W4 | plan, Phase 7 sweep list | Rust contracts crate missing topic constants |
| SW-6 | SW | plan, Phase 7 sweep list | `.agents/CONTEXT.md` numeric drift |
| SW-7 | SW | Phase 6 | `MockDeterministicLLM.generate_stream` returns a coroutine, not an async generator (`tests/integration/harness/mock_cognitive_engines.py`) |
