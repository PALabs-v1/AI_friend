# Benchmark Plan — Lifesim + BrainBench

Two deliverables: a reusable synthetic longitudinal human-robot life generator (`backend/evals/lifesim/`), and a whole-brain benchmark harness that drives real components against it (`backend/evals/brainbench/`). Existing infrastructure (`backend/evals/cognitive/`, `backend/tools/measure`) is extended, not replaced — `mx.summarize`, the cluster bootstrap, and `PrecomputedEmbedder` are reused directly.

## Why ground truth comes first

The master prompt is explicit: the textual conversation is an *observation* of a synthetic world, not the authoritative truth. Every persona carries an event-sourced ground-truth timeline (slot histories with validity windows, certainty, and supersession chains) that observation text is rendered *from*. This is what makes objective scoring possible — "does the retrieved memory match the current truth at time T" is a projection query against the timeline, not a judgment call.

## Lifesim architecture

`world.py` (persona state: identity, relationships with deliberately similar names, work, locations, possessions, habits, preferences, goals, commitments) → `timeline.py` (ground-truth slot history, `current_truth(t)`/`historical_truth(t)` projections) → `events.py` (stochastic life processes: preference drift, temporary states, relationship events, emotional events, commitments, interference families, realistic interaction gaps) → `observe.py` (renders events into utterances with hidden annotations: event id, oracle intent/appraisal, expected memory ids — used only by scoring, never leaked to the architecture under test) → `probes.py` (questions with answer targets: current-vs-historical, stale-fact traps, multi-hop, interference, should-be-forgotten trivia, unanswerable/abstention, relationship-defining recall, commitment-due checks) → `personas.py` (a parameter space: verbosity, forgetfulness, contradiction rate, emotionality, network size, lifestyle complexity, preference volatility, interaction frequency, conversational style — a fixed panel of ~12 archetypes plus random draws, never optimized for one persona).

Paraphrase/dialogue banks are generated once via local `claude -p`, frozen to disk with a sha256 manifest; the generator refuses to run if a bank's hash doesn't match, so nobody accidentally re-generates (and re-randomizes) the eval's own fixtures.

**Horizons**: 100/500/1000 turns; a simulated week/month/6 months; 1/3/5/10 years (further if computationally practical). A turn is never assumed to equal a fixed real-time interval — gaps follow realistic patterns (daily rhythm, weekday/weekend, vacations, bursty weeks).

**Seed partitions, enforced in code, not convention**: dev 1000-1099, tune 2000-2199, validation 3000-3099, held-out 9000-9099. A held-out run requires an explicit `--final-run` flag and appends to `results/HELDOUT_LOG.md`, so "we only ran it once" is checkable, not just claimed.

**Anti-gaming gates** (run as tests, not manual review): no positional regularity in correct answers, realistic lexical-overlap bounds between probe and target, vocabulary rotation across paraphrases, distractors present per probe, variable temporal gaps. Codex runs an independent adversarial leakage review before any workstream is allowed to tune against the eval.

## BrainBench

Drives the **real** brain components (not a simulation of them) against lifesim output. Requires one seam that doesn't exist yet: an injectable clock (`app/cognitive/clock.py`, defaulting to wall time so production is unaffected) — every `time.time()`/`datetime.now()` call in `memory_store.py`, `agent_state.py`, `person_model.py`, the subconscious tick, proactive eligibility, and ACT-R decay needs to go through it, or ten simulated years can't be exercised honestly in real time.

**Two modes, never merged in results**:
- `architecture_only` — deterministic/oracle intent classification, oracle or deterministic appraisal inputs, no LLM-generated text. Scores against retrieved memory ids and state, not against text quality.
- `llm_augmented` — the real Ollama models running on home-gpu (production `llama3.2:3b`/`qwen2.5:3b`, plus `qwen3:4b`/`gemma3:4b` as comparisons). Stored and reported separately, always labeled.

**Suites** (mapping directly to the master prompt's capability list): memory (encoding/retrieval/current-vs-historical/stale-win/forgetting/consolidation/correction/interference/multi-hop/abstention), attention (distractor filtering, salience/novelty), affect (response/persistence/decay/recovery/saturation/stability, r(user valence, mood)), trust/relationship (growth/loss/recovery/hostility/reliability-vs-warmth), personality (drift vs. the immutable/constitutional tiers), executive/background/proactive (goal resurfacing, useful-vs-irrelevant initiation, starvation resistance), barge-in/voice (lifecycle correctness under fuzz — feeds from W4/W5), metacognition (uncertainty, missing info, contradiction surfacing), resources (CPU/RAM/VRAM/DB growth, P50/P95/P99 latency).

**Statistics**: clustered by persona seed, 95% CIs, paired deltas against the V2 lifesim baseline, win/loss/tie counts, Cliff's delta / Cohen's d, Holm correction across ablation families. A 1-2% improvement is never reported as a finding without a significance and practical-value case attached.

**Observability**: `last_search_trace` extended with temporal/graph/affect score terms; equivalent structured traces for affect updates, trust updates, and proactive/arbitration decisions. Traces carry ids and numbers only, never memory text.

**Ablation switchboard**: every proposed mechanism sits behind a config flag, so `baseline`, `+temporal`, `+graph`, `+affect-input`, `+consolidation`, `all` are one-command runs, and each workstream's ablation table falls out of the same harness.

**Regression gates**: a small fixed dev-seed slice runs in under 60 seconds and fails if any capability drops below its recorded V2 baseline band — this is what stands between "improved temporal correctness" and "silently broke episodic recall to do it."

## What this produces first

The **V2 lifesim baseline**: architecture_only results across the full persona panel and horizon set, plus one llm_augmented reference run, before any Brain V3 mechanism is implemented. Every subsequent workstream's ablation table is a delta against this baseline, not against whatever the previous workstream happened to leave behind.
