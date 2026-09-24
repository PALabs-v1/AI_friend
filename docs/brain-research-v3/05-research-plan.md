# Research Plan — Workstreams W1-W10

Each workstream follows: hypothesis → experiment → ablation → implement → Codex adversarial review → regression check. None starts implementation before its gating interview round is answered (see `03-open-questions.md`). Each leaves an ADR, tests (property-based where a state machine is involved), a BrainBench ablation arm, and a `findings.md` entry if it surfaces a separate bug.

## W1 — Temporal truth maintenance (register M-5, top priority)
**Gated on**: Round 2.
**Hypothesis**: write-time contradiction classification (ELABORATION / UPDATE / CORRECTION / CONFLICT) plus a slot-based current-state projection reduces obsolete-fact-wins without hurting historical recall or latency.
**Baseline**: obsolete-win ≈0.64-0.67 under both V1 and hybrid rankers (no ranker fixes this — it's a write-time problem).
**Plan**: wire `TemporalMemoryStore.apply_contradiction` and `classify_contradiction` (`memory_records.py:118`) into `add_memory`, behind a flag, replacing either if the audit shows a design flaw. Detector arms: E8 (cosine + polarity/negation), a fast-LLM classifier on the top-3 neighbors (llm_augmented only).
**Targets** (from `docs/brain-research/07-future-research.md`): obsolete-win ≤0.10, updated-fact hit@3 ≥0.48, false-closure rate ≤0.02, no episodic/latency regression.
**Variant tournament**: 2-3 competing designs, blind critic comparison.

## W2 — User words → affect (register A-1; ADR-002 Part 2)
**Gated on**: Round 3, plus the GPU experiment's ToM-valence accuracy result (Phase 4).
**Hypothesis**: routing G/RI from an estimate of the user's actual expressed valence (not the agent's own mood) makes mood track the user without over-reacting to a single sentence.
**Options**: the ToM `inferred_valence` already computed for intent classification (needs its stage-ordering fixed — it currently runs after the affect update it should inform); a deterministic fast estimator; social-meaning-aware appraisal.
**Also fixes in scope**: A-4 (derived arousal incorrectly written back into `energy`), A-5 (tick decay uses the tick message's `interval` field instead of elapsed wall time), A-6 (the "acute distress" check inspects the agent's own state while its name implies it's about the user).
**Design constraint from ADR-002**: adopt only if Pearson r ≥0.8 and sign agreement ≥0.9 against oracle valence, and hostile-script trust stays ≤0.5 (this ties directly to W3).

## W3 — Trust/relationship model (register A-3)
**Gated on**: Round 4.
**Hypothesis**: removing the flat `+0.1·NA` per-turn integrity increment and routing trust through `PersonModel`'s existing (unused) evidence-based rule (success/failure/rupture/repair) produces a trust signal that actually falls under hostility and recovers under good behavior.
**Baseline**: trust ≈0.83 after 60 hostile turns regardless of script (measured in the affect sim).
**Targets**: hostile-script trust ≤0.4, demonstrated recovery, no ceiling reached within 100 positive turns, reliability separated from emotional warmth (both move trust, differently).
**Also fixes**: the `_sync_active_person_trust_locked` overwrite hazard, where calling `PersonModel` methods that currently have no caller would silently clobber the appraisal-driven trust values if ever wired up carelessly.
**Variant tournament**: yes — trust decomposition is judgment-heavy.

## W4 — Voice lifecycle contract (registers V-1/V-2 + completion gap)
**Gated on**: Round 8 for semantics; the engineering itself is objective.
**Hypothesis**: a proper `audio.playback.lifecycle` state machine (STARTED → PLAYING → {COMPLETED | INTERRUPTED | FAILED}, keyed by `utterance_id`+`turn_id`, monotonic sequence numbers, idempotent terminal states) closes the "finished playing" gap and gives V-2 (self-correction flush) a real signal to hang off.
**Plan**: voice-agent emits an end-of-stream trailer on `audio.stream`; transport (which owns actual playout timing, not just buffering) emits COMPLETED when the last frame is *played*, not merely received. Add a `flush` flag to `AudioStop`. Contract bump in both `contracts.py` and `crates/contracts/src/lib.rs`, extending `test_rust_contract_fixtures.py`.
**Tests**: Rust `proptest` + Python `hypothesis` on the state machine; a live mesh run (voice-agent + transport + brain over real NATS) observing STARTED → COMPLETED and STARTED → INTERRUPTED end to end.
**Split**: Rust side and Python/transport side implemented separately, each reviewed by the other's author (me + Codex).

## W5 — Barge-in end-to-end
**Gated on**: Round 8.
**Hypothesis**: a `hypothesis` stateful machine driving `BrainAgent` through random interleavings of chat.input / audio.stop / progress / lifecycle events / proactive turns / redelivery finds real bugs beyond the 33/38-mutations-killed status quo, because mutation testing only perturbs existing code paths — it can't discover an interleaving nobody wrote a branch for.
**Invariants to check**: exactly one terminal outcome per reply; history equals what was actually heard; no stale stop applies to the wrong turn; no unbounded waits.
**Also fixes**: ordinary barge-in not cutting history (per Round 8's answer), the single superseded-reply slot, serial `chat.input` handling (V-3) that makes some preemptions structurally unreachable.
**Split**: Codex writes the state machine independently from ADR-003's "Known" list; I write it independently from the same list; diff the two before merging either.

## W6 — Graph value under the default ranker
**Hypothesis**: the Neo4j graph earns its infrastructure cost through multi-hop/causal recall that the hybrid ranker's cosine+BM25+ACT-R terms can't reach on their own.
**Experiment**: a `hybrid+ppr` arm evaluated on lifesim's multi-hop and causal-chain probes against live Neo4j.
**Decision rule**: if the measured gain is under 0.05, the graph stays for its other roles (entity linking, reflection triplets) but is dropped from the retrieval ranking path — recorded in ADR-004 either way, never silently.

## W7 — Consolidation and memory growth
**Hypothesis**: repeated episodic mentions of the same fact should eventually produce a semantic abstraction ("usually drinks black coffee in the morning") while the individual episodes remain separately retrievable, bounding long-horizon storage growth without losing recall quality.
**Measured over**: 10 simulated years of lifesim data — recall quality, obsolete-win rate, and raw growth curves (memories/nodes/edges/vectors) with and without consolidation.
**Adopt only if**: it improves quality or measurably bounds growth, without regressing episodic recall.

## W8 — Metacognition
**Scope**: abstention with a relevance floor calibrated against real embeddings (Phase 4), per-turn memory freshness (register M-10 — `surfaced_memories` is never cleared between turns), outage propagation to the decision layer (register M-8 — `last_search_error` currently reaches nowhere), and register M-13 (learning ignoring `add_memory`'s return value).

## W9 — Attention, proactive behavior, and goals
**Gated on**: Round 5 and Round 7.
**Scope**: fix register V-4 (the proactive cooldown resets on the next brain state broadcast, defeating its own purpose); decide whether to wire up or delete the currently-dead `GoalRecord`/`review_due_goals`/`BackgroundScheduler` trio (nothing ever enqueues background work today); build whatever annoyance model Round 5 asks for; add a starvation test proving background consolidation cannot delay a foreground turn past its latency budget.

## W10 — Security
**Scope**: a stored-injection corpus run against the full retrieval → prompt path; graph poisoning via reflection-written triplets; malformed-metadata fuzzing; cross-session/cross-person leakage (once multi-person is in scope per Round 1/9); register S-2 (sanitization gated behind `MEMORY_TRUTH_ENABLED` instead of unconditional); the subconscious agent's `thought_prompt`, which is inserted into a prompt ungated today; an audit of any unsafe deserialization in the memory/state stores.
**Adversary**: Codex, explicitly instructed to attack rather than review.

---

## Continuous: pre-existing problem sweep (Objective 9)

Logged incrementally in `findings.md` as they're found or fixed, not batched here. Known candidates from the reconstruction pass, each needing a wire-up-or-delete decision:
- Pipeline members constructed but never called: `plan_verifier`, `plan_executor`, `episodic_simulator`, `learning_governor`, `offline_adapter_gate`, `provider_capability_negotiator`, `external_action_dispatcher`, `temporal_memory_store` (`pipeline.py`)
- Orphan NATS subjects with no subscriber or publisher: `audio.pre_generate`, `state.subconscious`, `telemetry.reflection`, `voice.segmentation_feedback`
- `LearningReviewQueue` claims durability in its docstring but is an in-memory dict, and nothing calls `approve`
- `GlobalControls.learning_gain` defined, never read (register A-7)
- `TemporalMemoryStore` defaults to an in-memory (`:memory:`) SQLite connection
- `.env`/`.env.example` drift (fast-model default disagreement, missing `QDRANT_HOST`/`PORT`, missing per-agent NATS creds)
- F-001 (this session): stale vendored `libonnxruntime` code signature breaking macOS local test runs — see `findings.md`
