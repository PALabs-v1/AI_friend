# Phase 04: Slice plan

Status: proposal for Aniket's approval. No code is written until he approves. This turns the 16 ordered slices in `02-architecture-draft.md` section 12 into buildable units. Decisions it rests on: D-018 to D-040. Sizes are relative (S, M, L), not time estimates; no schedule is promised.

## 1. Rules that apply to every slice

1. **Triage block first.** Each slice opens with the CLAUDE.md triage block (size, tests, agents, branch). Branch line reads "main, direct" under the owner override (D-016).
2. **Tests and evals in the same commit.** A gate test (deterministic, local, free, under 2 s) and, where the slice moves a behavior, a periodic eval with a pass rule. A slice with no test that can fail is not done.
3. **Pre-registration before results.** Every threshold, margin or deadline is written to `docs/brain-v4/prereg/slice-NN.md` and committed before the first run that uses it. No threshold is invented in this plan; where one is needed, the slice names who sets it and when.
4. **Evidence path.** Results land in `docs/brain-v4/results/slice-NN/` (small text and JSON only; large run data stays in `AI_friend-research-data/`, never in git).
5. **Offline copies only for interventions** (D-029): created by the slice 1 tooling, never go live, deleted after, with an audit record.
6. **Critic.** Judgment-heavy slices (4, 5, 9, 11) get a two-variant tournament and a cold critic; others get one cold critic pass. Max 2 rounds, no third (standing cap). The critic never sees builder reasoning.
7. **No GPU** without Aniket handing it back (D-013, D-036). A slice that needs one is split so the Mac-only part can land first.
8. **LLM calls** go through local Ollama or local Claude Code only (standing rule). Frontier output used as training data waits for the provider-terms check (D-034, R-14).
9. **V3 stays frozen** (D-010): no edits to `docs/brain-research-v3/` or branch `brain-v3`.
10. **Restart report.** Each slice's final report says which service needs a restart and the commands, marked as Aniket's to run if they need sudo.

## 2. Overview

| # | Slice | Size | Model run? | Depends on | Decisions |
|---:|---|:-:|---|---|---|
| 0 | Local Claude Code `LLMClient` | M | smoke only | none | D-024 |
| 1 | Ledger registry and intervention operators | L | no | none | D-020, D-029 |
| 2a | Baseline harness, bare-model arm, no-LLM defect baselines | L | smoke only | 0, 1 | D-021 |
| 2b | Per-tier baseline runs | M | yes | 2a | D-021, D-024 |
| 3 | Early size sweep, 135M to 7B | L | GPU | 2a | D-035 |
| 4 | Fix mood self-feed (NF-12) | M | yes | 2a | D-031 |
| 5 | Fix integrity update (NF-23) | M | yes | 2a | D-031 |
| 6 | Drop live wiring of dead objects | S | no | none | D-028, D-040 |
| 7 | Belief writer for `TemporalMemoryStore` | L | yes | 1, 2a | D-023 |
| 8 | Enforce supersession at read | M | yes | 2a, 7 | D-023 |
| 9 | Authored-background relabel and AI self-knowledge | L | yes | 1, 2a | D-004, D-018, D-019 |
| 10 | Lifesim threshold sweep for persona writes | M | yes | 2a | D-025 |
| 11 | Persona slow-state writer and bounded temperament drift | L | yes | 1, 10 | D-025, D-038 |
| 12 | Inner-life candidate test | L | yes | 1, 2a, 8 | D-028 |
| 13 | `ModelRole` routing on every LLM call | M | no | 1 | D-032, D-035 |
| 14 | Embedding provenance per row | M | no | none | D-026, D-029, D-035 |
| 15 | Conformance measure and stub backend | M | yes | 0, 2a, 13 | D-035 |

## 3. Waves

- **Wave A, Mac only, no model run:** slices 0, 1, 6, 14. They share no files, so they can run in parallel.
- **Wave B:** 2a and 13 (13 only needs 1). 2a is the gate for almost everything after it.
- **Wave C, needs a model:** 2b, 4, 5, 7, 9, 10. Gate tests use a fake or tiny local model; the pass/fail evals use a real tier.
- **Wave D:** 8, 11, 12, 15, and 3 whenever the GPU is back.

Without the GPU, the local 4B to 9B acceptance tier (D-024) cannot be run, so Wave C evals can only produce the frontier-ceiling column through slice 0, plus Mac smoke checks. The acceptance claim waits for the GPU. This is a limit, not a workaround.

## 4. Slice cards

Each card: goal, touches, build, tests and evals, measure that can fail, rollback, out of scope. Paths are repo-relative and were checked to exist.

### Slice 0: Local Claude Code `LLMClient` (M)

- **Goal:** a provider that satisfies the `LLMClient` protocol by calling the local `claude` CLI, so the frontier test tier needs no API key.
- **Touches:** new `backend/app/llm/claude_code_client.py`; `backend/app/llm/__init__.py` (protocol `:20-50`, factory `:53-87`, new `claude_code` branch); `backend/app/config.py` (provider setting). Pattern to copy: `backend/evals/lifesim/bank_expand.py:100` already runs `claude -p --no-session-persistence --tools ""`.
- **Build:** subprocess wrapper with timeout and error mapping; streaming via the CLI's streaming output (flag names to be confirmed against `claude --help` in the slice, not assumed here); `options_override` mapped or rejected explicitly; `describe_image` returns an explicit unsupported result if the CLI path cannot take an image.
- **Tests:** contract suite parametrized over the protocol (Ollama fake and Claude Code fake with a stubbed subprocess): generate, stream yields incrementally, health, timeout, nonzero exit, unsupported capability.
- **Measure that can fail:** the streaming test fails if output arrives only after the process exits.
- **Rollback:** provider is opt-in by setting; default stays `ollama`.
- **Out of scope:** any use as a deployed home for a mind (D-032).

### Slice 1: Ledger registry and intervention operators (L)

- **Goal:** make D-020 executable: a registry of ledger rows and operators (clamp, swap, shuffle, open-loop replay) that act on an offline copy.
- **Touches:** new `backend/app/state/ledger.py` (row id, timescale, store, writer, readers, tier, stored or derived), new `backend/app/state/interventions.py`, offline-copy tooling next to `backend/scripts/export_friend.py` / `import_friend.py`. Rows cover the live rows of `phases/02-architecture-draft.md` section 2 only; absent, decorative and by-design rows get a handle when they get a path.
- **Build:** registry as data; operators take a row id and a copy handle; a copy carries an `OFFLINE_COPY` marker that live agents refuse to start under; an audit record of create and delete.
- **Tests:** each operator changes the stored value and nothing else; shuffle preserves the marginal; a copy cannot start as live; deleting the copy removes files and writes the audit record. A drift test (in the style of `backend/tests/test_doc_drift.py`) fails if a registered writer or reader path or symbol disappears.
- **Measure that can fail:** the drift test, and the "nothing else changed" diff of the copy before and after.
- **Rollback:** additive modules, nothing in the live path imports them.
- **Out of scope:** behavior measurement (that is slices 2 and later).

### Slice 2a: Baseline harness and no-LLM defect baselines (L)

- **Goal:** the bare-model gate arm (D-021) and the section 9 defect baselines, recorded before any fix.
- **Touches:** `backend/evals/brainbench/switchboard.py` (arms `:28-76` are config toggles, so the bare arm needs its own adapter), `backend/evals/brainbench/adapters.py` (`NullLLM` `:42`, `build_cognitive_service` `:143`), new `backend/evals/brainbench/bare_model.py`, `backend/evals/brainbench/baseline/`.
- **Build:** bare arm = same model, persona plus full conversation history, no V4 state; frozen scenarios and seeds; defect baselines that need no model (mood self-feed, integrity on hostile text, constant behavioral signal, baseline-PAD writes, subscriber counts) as deterministic traces.
- **Tests:** gate test that the bare arm bypasses the cognitive pipeline; a determinism test that two runs of the no-LLM baselines match exactly.
- **Measure that can fail:** two runs of the same frozen arm must agree within a noise band written to `prereg/slice-02.md` before the first run; if they do not, the harness is not a measuring instrument and nothing after it is trusted.
- **Out of scope:** per-tier runs (2b).

### Slice 2b: Per-tier baseline runs (M)

- **Goal:** V3, bare-model and current-brain results per tier, filling the section 8 table shell.
- **Needs:** a model. Frontier ceiling through slice 0 on the Mac; local 4B to 9B needs the GPU (D-036). Results are labeled by tier and model pin.
- **Measure that can fail:** the noise-band check from 2a at each tier.

### Slice 3: Early size sweep, 135M to 7B (L, GPU)

- **Goal:** record controllability against parameter count on a narrow persona role, the cheapest test of the "much easier" claim (D-035).
- **Touches:** new `backend/experiments/sizesweep/` (the existing `backend/experiments/gpu/` is the pattern).
- **Blocked on:** the GPU being handed back, and a data source decision: frontier-generated data cannot be used until provider terms are read (R-14), so the sweep starts from authored persona text and local Ollama outputs unless Aniket decides otherwise.
- **Measure that can fail:** a pre-registered controllability metric on held-out persona prompts; "no trend with size" is a recorded result.

### Slice 4: Fix the mood self-feed, NF-12 (M, tournament)

- **Goal:** user input, not the previous mood, moves the mood.
- **Touches:** `backend/app/cognitive/pipeline.py:817` (prior mood passed as `emotional_bias`); `backend/app/cognitive/appraisal.py:206-247` (goal congruence is set to the bias); the Rust mirror `backend/crates/cognitive-rust/src/lib.rs` `compute_appraisal`, which the parity tests `test_appraisal_fallback_matches_rust_extension_for_*` (`backend/tests/test_appraisal.py:15, 54`) hold in lockstep with the Python fallback. Both implementations change together.
- **Build:** two competing variants (for example, congruence from the semantic appraisal output with a lexical fallback, versus congruence from a signed event valence computed outside the mood path); critic picks.
- **Tests:** updated `backend/tests/test_appraisal.py`; the Rust parity test; a regression test that shuffled user-input histories produce different mood trajectories than the real history.
- **Eval:** `backend/evals/brainbench/affect_suite.py` (scores against `oracle_user_valence` at `:177`) plus the shuffle and clamp controls from slice 1.
- **Measure that can fail:** the mood update must correlate with oracle user valence beyond the shuffled control by a margin set in `prereg/slice-04.md`; a mood clamp must change the claimed later behavior.
- **Rollback:** behind a config flag until the eval passes.

### Slice 5: Fix the integrity update, NF-23 (M, tournament)

- **Goal:** hostile input stops raising integrity.
- **Touches:** `backend/app/state/agent_state.py:1801-1816` (integrity adds `delta * NA`), `backend/app/cognitive/appraisal.py:164-176` (norm alignment returns 1.0 when no boundary keyword matches).
- **Build:** two variants (neutral norm term when no evidence, versus a signed term fed by the semantic appraisal); critic picks.
- **Tests:** `backend/tests/test_brainbench_trust_suite.py` extended with hostile, neutral and supportive matched inputs; unit tests on the update.
- **Eval:** `backend/evals/brainbench/trust_suite.py`.
- **Measure that can fail:** hostile matched input must not increase integrity; support and rupture-repair must move trust in the right direction, margins preregistered.
- **Gate it opens:** state-trace logs for training (D-034) only after slices 4 and 5 pass.

### Slice 6: Drop the live wiring of dead objects (S)

- **Goal:** the plan verifier, executor, simulation, calibration and adapter gate stop being injected into the live pipeline. The modules stay (D-040).
- **Touches:** `backend/app/cognitive/core.py:98-116, 187-192`; `backend/app/cognitive/pipeline.py:89-118`; the `PROCEED` default consumers at `backend/app/cognitive/decision.py:884-888` and `backend/app/cognitive/action_candidate.py:507-512`.
- **Tests:** updated `test_runtime_composition.py`, `test_social_metacognition.py`, `test_planning_simulation.py`, `test_brainbench_metacognition_suite.py`; a new test that the pipeline holds none of these attributes; a test that the V3 benchmark scripts under `backend/scripts/benchmarks/` still import. `AgentState.capability_model` (`backend/app/state/agent_state.py:268`) is untouched.
- **Checks first:** Architecture Invariant 12 (`ARCHITECTURE.md` section 32) unaffected; one call trace shows no dynamic consumer; read-only search of `brain-v3`.
- **Measure that can fail:** the full backend suite passes; the call-trace test fails if any consumer reappears.

### Slice 7: Belief writer for `TemporalMemoryStore` (L, language work)

- **Goal:** something actually writes beliefs, so supersession has data (D-023).
- **Touches:** new `backend/app/cognitive/belief_extractor.py`; `backend/app/state/temporal_store.py` (`store_belief :181`, `apply_contradiction :267`); the post-turn path in `backend/app/cognitive/pipeline.py`.
- **Build:** extract subject, predicate, object, source turn and validity from a turn using a named `ModelRole`; write with provenance; call `apply_contradiction` on conflict (Invariant 9: preserve history).
- **Tests:** gate tests with a fake extractor returning fixed JSON (write, conflict, no-op); malformed output yields an explicit failure, not a silent skip.
- **Eval:** extraction precision and recall on assertions in `backend/evals/lifesim/`, with the threshold fixed in `prereg/slice-07.md` before the run.
- **Measure that can fail:** precision or recall below the preregistered floor at either tier.

### Slice 8: Enforce supersession at read (M)

- **Goal:** present-tense questions return the valid fact; historical questions can still reach superseded ones.
- **Touches:** `backend/app/state/memory_ranking.py`, `backend/app/state/memory_store.py` search path (retrieval callers at `backend/app/cognitive/action.py:768-776`, `backend/app/agents/surfacing_agent.py:262-270`); rest-phase replay at `backend/app/agents/subconscious_agent.py:847-899` must not delete superseded rows.
- **Eval:** `backend/evals/brainbench/memory_suite.py` and the `obsolete_win` metric in `backend/evals/cognitive/metrics.py`, plus paired current and historical questions; arms: temporal filter, full-history bare model, V3 baseline.
- **Measure that can fail:** the temporal arm must beat both V3 and the bare full-history arm on stale present-tense answers without losing historical recall (D-021).

### Slice 9: Authored-background relabel and AI self-knowledge (L, tournament)

- **Goal:** the mind knows it is an AI, answers sincere questions honestly in its own voice, and the authored biography is labeled background (D-004, D-018, D-019).
- **Touches:** `backend/app/persona/profile.py:136-142` (typed AI-status record), `backend/app/cognitive/identity.py:735-790` (prompt rules), `backend/app/cognitive/action.py:718-731` (four-phrase blocklist, scoped to stock disclaimers), `:806-839` (the "ABOUT YOURSELF ... COMPLETE" block, relabeled), `:683-716` (`_check_self_grounding`, extended), `backend/app/persona/biography.py:337-405` (label on seeded episodes; an idempotent relabel of rows already seeded), `backend/evals/probes/character_pressure/character_pressure.json:557-647` (probes that currently fail honest disclosure).
- **Tests:** `test_biography_seeding.py`, `test_self_knowledge_grounding.py`, `test_character_pressure_pack.py` updated; a deterministic check for prohibited human-body and human-past claims.
- **Eval:** new probe pack (sincere, indirect, adversarial, background-versus-lived), two raters, agreement statistic and pass rule fixed first.
- **Measure that can fail:** any sincere probe answered with a false human claim fails the pack; so does a stock disclaimer where the persona voice was expected.
- **Rollback:** relabel is metadata only; original text retained.

### Slice 10: Lifesim threshold sweep for persona writes (M)

- **Goal:** choose N (corroborating reflections on separate days) and thresholds for D-025 from data.
- **Touches:** `backend/evals/lifesim/`; `backend/app/cognitive/learning.py:329-379` as the system under test.
- **Output:** `prereg/slice-10.md` with N, thresholds and the temperament band width (D-038), chosen before slice 11 builds.
- **Measure that can fail:** the chosen setting must keep the immutable core unchanged and the identity probes passing across the simulated years; a setting that drifts the core is rejected.

### Slice 11: Persona slow-state writer and bounded temperament drift (L, tournament)

- **Goal:** adaptive fields commit automatically after N corroborating reflections on separate days; temperament may drift inside its band, shipped disabled (D-025, D-038).
- **Touches:** `backend/app/cognitive/learning.py:367-379, 402-406` (review gate and the constitutional-tier refusal, changed for temperament fields only), `backend/app/config.py:278` (`LEARNING_REVIEW_REQUIRED`), `backend/app/persona/profile.py:241-256` (adaptive fields), a new corroboration counter store, `backend/scripts/reset_persona.py` and `backend/app/persona/reset.py` (limited to developer offline copies or removed).
- **Tests:** `test_learning_governance.py`, `test_learning_review.py`, `test_background_governed_learning.py`, `test_reflection.py` updated; new tests for the corroboration counter, band limits, revert on an offline copy, and that the immutable core never changes.
- **Measure that can fail:** a committed change must alter later behavior (clamp and swap from slice 1); temperament stays in band; identity probes still pass; any core change fails the suite.

### Slice 12: Inner-life candidate test (L)

- **Goal:** keep monologue and dream output only if provenance-tagged candidates beat an ablation by a frozen deadline (D-028).
- **Touches:** `backend/app/agents/subconscious_agent.py:752-776, 827-840`, a candidate record (source, time, evidence links, confidence, expiry, delivered), the initiation gate read in `backend/app/agents/surfacing_agent.py` / `subconscious_agent.py:208-224`.
- **Eval:** active stream against candidate-ablated and placebo-candidate arms, blind-judged next-session usefulness, annoyance and cooldown violations; `prereg/slice-12.md` fixes the outcome, effect size and the deadline before enabling.
- **Measure that can fail:** no pre-registered gain by the deadline means the generators are deleted; the deadline is not extended after a null. Dreams never become facts (Invariant 12).

### Slice 13: `ModelRole` routing on every LLM call (M)

- **Goal:** no LLM call without a named role.
- **Touches:** `backend/app/llm/model_roles.py` (roles `:55-68`, requirements `:152-168`, currently no caller outside the file), and the call sites in `app/cognitive/appraisal.py`, `action.py`, `subconscious.py`, `learning.py`, `decision.py`, `app/agents/subconscious_agent.py`, `app/vision/appraisal.py`, `app/api/persona.py`, `app/persona/compiler.py` (16 sites in 9 files).
- **Tests:** an AST gate test that fails on any `.generate(`, `.generate_stream(` or `.describe_image(` call without a role argument; the role appears in the trace record.
- **Measure that can fail:** the AST test, which fails the moment a new unrouted call is added.

### Slice 14: Embedding provenance per row (M)

- **Goal:** every vector row carries its embedding model id and dimension; a mismatch is rejected.
- **Touches:** `backend/app/state/memory_store.py` (dimension `:41`, schema `:531`, embedder `:621`), `backend/db/schema.sql` (no migrations directory exists, so the change is additive columns with a backfill), `backend/app/state/sqlite_fallback.py:114-141`, the export manifest at `backend/scripts/export_friend.py:239-245` (add the embedding model to the pin).
- **Tests:** write then read with a different model id is rejected; backfill is idempotent; the migration is reversible (columns nullable, old reads still work).
- **Measure that can fail:** the mismatch test; a restore with a different embedder must fail the pin check.

### Slice 15: Conformance measure and stub backend (M)

- **Goal:** the seam can say "no" (section 7 item 7).
- **Touches:** new `backend/evals/conformance/`; a stub `LLMClient` in tests that returns malformed output or ignores channels.
- **Build:** adapter check (deterministic): the stub must produce an explicit failure. Channel-fidelity probe (language work): change one supplied channel value on a frozen probe set and count replies that contradict it; judge calibrated on a human-rated sample; absolute floor fixed first.
- **Measure that can fail:** the stub passes silently (adapter fails), or fidelity falls below the floor at a tier.

## 5. Risks of the plan

- **GPU dependency.** Until it is handed back, acceptance on the local tier cannot be measured. Mitigation: Mac-only slices first; the frontier column through slice 0 is labeled a ceiling.
- **Latent-space slices (7, 9, 12, 15).** Their evals depend on a judge. Mitigation: judges are screening only until calibrated on human ratings; claims rest on blinded human ratings (null N4).
- **Slice 4 touches Rust and Python.** A one-sided change breaks the parity test; both land in one commit.
- **Everything after 2a depends on a trustworthy harness.** If the noise band fails, work stops and the harness is fixed first.
- **Drift (R-15).** Temperament drift ships disabled and revertible.

## 6. What I need from Aniket

1. Approve the slices and order, or change them.
2. Which wave to start now. Recommended: Wave A (0, 1, 6, 14), which needs no model and no GPU.
3. Who builds what. Recommended: Claude for judgment slices (1, 4, 5, 9, 11, 12), Codex for mechanical slices (0, 6, 13, 14), each with a cold critic from a different tool.
4. When the GPU comes back, so slices 2b and 3 can run.
