# Interview Round 6 — Learning and Personality Evolution

DR-004 already authorized speaking style, traits, and baseline mood/PAD to evolve. This round decides the mechanism — currently there isn't one that actually runs: `LearningReviewQueue.submit` gets called after a reflection suggestion clears a confidence bar and the `LearningGovernor`'s protected-key check, but nothing ever calls `approve`. The queue is also in-memory (its own docstring claims durability that the implementation doesn't provide) — a process restart loses anything sitting in it anyway.

## Q1. Who or what approves an evolution proposal?

**What exists**: nothing does, in production, today. The governor (`learning_governance.py`) already blocks protected/constitutional keys — the queue exists purely as a second gate on top of that for the fields DR-004 allows to move.

**Why it matters**: per DR-002/DR-003, this is a single-relationship system, not a multi-tenant product where one person's bad-faith interactions could corrupt behavior seen by others — that risk profile changes what "safe to auto-approve" means here.

## Q2. How fast should the allowed fields actually drift, given they can now change?

**What exists**: `adaptive_traits` is capped at 5 (newest kept) but has no time-based rate limit — if evolution were turned on today, a burst of similar reflections could add several traits back-to-back. Baseline mood/PAD (DR-011) already got a concept-level answer ("slow personality set-point") but not a number.

## Q3. Should personality evolution be visible/inspectable to you, or opaque?

**What exists**: no visibility mechanism at all today — even if the queue worked, there's no way to see what changed, when, or why.

## Q4. Is the current confidence bar for proposing a change (0.8, `learning.py:158`) the right threshold?

**What exists**: reflection only proposes a persona change above 0.8 confidence. This is a real number already in the code, not a placeholder — worth confirming rather than assuming it's right.
