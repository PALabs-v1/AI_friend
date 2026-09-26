# DR-010: Three distinct affect layers — emotion, mood, relationship sentiment

**Round**: 3 (affect)
**Date**: 2026-09-24
**Question**: Should momentary emotion, mood, and relationship sentiment be distinct, or is one PAD state enough?

**Decision**: three layers, each with its own timescale and decay constant:
- **Momentary emotion** — seconds to minutes, reacts fastest, decays fastest. This is the new synchronous DR-008 update target.
- **Mood** — hours to days, today's PAD state, decays toward the baseline (which is itself now slow-drifting per DR-004/DR-011).
- **Relationship sentiment** — weeks to months, closer to trust/attachment territory (Round 4 territory), the slowest-moving of the three.

**Consequence for W2**: this is a genuine architecture change, not a parameter tweak — `AgentState` needs a new fast-decaying emotion field distinct from `mood`, and the existing mood/baseline/trust hierarchy needs to be the middle and slow tiers respectively. DR-009's "one extreme event" carve-out is what's allowed to reach past the fast layer directly into mood or relationship sentiment; ordinary updates (DR-008) only touch the fast layer, which then feeds mood at its own damped rate.

**Consequence for W3 (trust) and Round 4**: relationship sentiment as a concept now explicitly exists as an affect-layer concern, not purely a trust-formula concern — Round 4 needs to clarify how it relates to (or overlaps with) trust/attachment rather than treating them as unrelated.

**Consequence for BrainBench**: the affect suite needs separate persistence/decay/recovery measurements per layer, not one aggregate "affect" number.
