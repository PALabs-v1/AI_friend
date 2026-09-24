# Interview Round 1 — Identity and Product Philosophy

## Q1. What belongs in `IMMUTABLE_CORE`?

**What exists**: `persona/profile.py:132-142` hardcodes exactly two values (Honesty, Privacy) and two boundaries as `IMMUTABLE_CORE`, refreshed from code on every load (`identity.py:477`) and never overridable by any config file or learning process. Everything else — traits, tone, speech patterns, baseline mood — is CONSTITUTIONAL (settable at persona-authoring time, not learned) or ADAPTIVE (can drift via `learn_traits`/`evolve_persona`).

**Why it matters**: this is the one list that no relationship intensity, no trust level, no emotional state can ever touch. Everything downstream (Round 9's boundaries, W3's trust redesign, W9's proactive annoyance model) inherits whatever floor this sets.

**Alternatives**: (a) keep it minimal — two values plus the boundary phrases; (b) add explicit hard-safety behaviors here too (refusal patterns currently living in `validate_response`'s avoid-list, which *is* adaptive-adjacent); (c) something else entirely.

## Q2. Companion or assistant — does it vary?

**What exists**: nothing in the code branches on this. `identity_summary`, `base_tone`, and `speech_patterns` are single CONSTITUTIONAL fields set once. There's no per-context mode switch.

**Why it matters**: this decides how far the ADAPTIVE tier is allowed to travel (Round 6), what "useful proactive initiation" means (Round 5, W9), and how the affect/trust redesigns (W2/W3) should weight warmth versus reliability.

## Q3. Single relationship or multi-person?

**What exists**: the entire state model (`AgentState`, trust, affect, `PersonModel`) is singular — one trust score, one mood, one relationship. `PersonModel` exists as a per-person evidence-based trust mechanism but has zero production callers.

**Why it matters**: this is a scope decision for the whole cycle. Multi-person changes W3 (trust redesign) from "fix the formula" to "fix the formula and decide whether it's global or keyed by person," and touches the lifesim/BrainBench design (whether personas ever have more than one "user" in the household).

## Q4. Which CONSTITUTIONAL fields may ever evolve, and how fast?

**What exists**: `adaptive_traits` (capped at 5, newest kept) and `speaking_style` already can drift, gated behind `LearningReviewQueue` — which nothing approves in production today, so evolution is currently a no-op regardless of config (see `01-v2-delta.md` §Identity, F-002-adjacent finding). Baseline PAD, rate coefficients, and `identity_summary` are CONSTITUTIONAL and don't drift at all today.

**Why it matters**: Round 6 depends on this answer directly — whether the dead approval queue should be wired to auto-approve, human-approve, or stay permanently off is meaningless to decide before knowing which fields are even allowed to move.
