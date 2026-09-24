# DR-003: Single relationship scope for this cycle

**Round**: 1 (identity/philosophy)
**Date**: 2026-09-24
**Question**: The entire state model is singular today (one trust score, one mood, one relationship). Design multi-person in now, or keep this cycle single-relationship?

**Decision**: single relationship only. `PersonModel`'s per-person machinery stays deferred/unused for this cycle.

**Consequence**: W3 (trust/relationship redesign) fixes the *formula*, not the *scope* — trust stays a single, global value keyed to no one in particular, redesigned per DR-002's note that the relationship isn't necessarily one-directional. Multi-person support, if wanted later, is a distinct future cycle, not something W1-W10 need to design around. The lifesim generator (`06-benchmark-plan.md`) still models a rich family/friend network for the *simulated human*, per the master prompt — that's about the human's world, not about the agent tracking multiple relationships with multiple real users.
