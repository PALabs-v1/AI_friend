# DR-004: Speaking style, traits, AND baseline mood/PAD may all evolve

**Round**: 1 (identity/philosophy)
**Date**: 2026-09-24
**Question**: Which parts of identity should ever be able to change, even slowly?

**Decision**: all three offered — speaking style/tone, the up-to-5 adaptive traits, and baseline mood/PAD. This goes beyond what the code currently allows: `baseline_mood` and the other PAD baselines are CONSTITUTIONAL today (`persona/profile.py`), set once at persona authoring and never touched by `evolve_persona`. This decision authorizes making them adaptive.

**Consistent with DR-002**: an entity with "an actual mind" plausibly has an emotional resting point that shifts over a long relationship/lived history, not a fixed factory-set baseline forever.

**Consequences for workstreams**:
- **W2 (user words → affect)** and **W3 (trust)**: the "decay toward baseline" model in `agent_state.py`'s tick handler now decays toward a target that itself can move. This needs explicit design — likely a slow, bounded drift of the baseline itself (much slower time constant than momentary mood), not baseline updated every turn. Left to be designed concretely in W2, not decided here.
- **Round 6 (learning/personality evolution, upcoming)**: since evolution is currently a no-op in production (nothing calls `LearningReviewQueue.approve`), this decision doesn't yet cause any drift — Round 6 decides *how* (auto-approve, human-in-the-loop, rate limits) before anything in this list is wired to actually move.
- **BrainBench** (`06-benchmark-plan.md`): the "personality" suite's drift measurement needs a defined acceptable-drift envelope for baseline PAD specifically, once Round 6 sets the mechanism — flagged for that workstream's test design.

**Not decided here**: the actual drift rate, rate limits, or approval mechanism — that's Round 6.
