# DR-011: Baseline mood drifts as a slow personality set-point

**Round**: 3 (affect)
**Date**: 2026-09-24
**Question**: What should govern baseline mood's drift, now that it's allowed to evolve (DR-004)?

**Decision**: baseline only shifts from sustained, weeks-long patterns across many interactions — never from any single conversation, no matter how intense (DR-009's single-extreme-event carve-out reaches mood or relationship sentiment, explicitly not baseline itself). It's the personality's long-run center of gravity, moving on the order of a real relationship's timescale.

**Consequence for W2/W6**: baseline drift is the slowest-moving quantity in the entire affect/relationship hierarchy — slower than relationship sentiment (DR-010, weeks-months) and far slower than mood (hours-days). Implementation-wise, this is likely a rolling/exponential average of *mood*, not of raw events, with a time constant measured in many weeks. Any Round 6 approval mechanism for personality evolution needs to treat baseline-mood drift consistently with how it treats trait/speaking-style drift, even though the underlying mechanism (a statistical rolling average vs. a discrete learned-trait proposal) is different in kind.
