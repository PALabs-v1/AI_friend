# DR-022: Personality evolution is opaque by design

**Round**: 6 (learning/evolution)
**Date**: 2026-09-24
**Question**: Should evolution be logged/inspectable, or opaque?

**Decision**: opaque by design — no dedicated audit trail of "what changed, when, why" for Aniket to inspect, the same way a person's own personality changes aren't something they consciously track or report to someone else.

**Consequence for W11**: reinforces DR-020's conclusion that `LearningReviewQueue` isn't merely un-approved, it's the wrong abstraction — a queue's entire purpose is to make pending/applied changes inspectable for a reviewer, and this decision removes the need for that. `evolve_persona()`'s existing internal logging (if any, for engineering debuggability) can stay for troubleshooting, but no product-facing "personality change log" gets built.

**Tension noted, not a blocker**: this makes W11 harder to test/observe from the outside during development. BrainBench's personality-drift suite (`06-benchmark-plan.md`) still needs to *measure* drift for evaluation purposes — that's an evaluation-harness concern (reading state directly in a test environment), not a product-facing visibility feature, so it doesn't contradict this decision.
