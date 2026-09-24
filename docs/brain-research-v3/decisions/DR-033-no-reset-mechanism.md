# DR-033: No personality reset mechanism

**Round**: 9 (boundaries/security)
**Date**: 2026-09-24
**Question**: Should there be a way to reset personality to the originally-authored baseline?

**Decision**: no. Consistent with DR-020's "like a human" framing — a person can't be reset to who they were before, so neither should this. Evolution is one-directional.

**Consequence**: reinforces that the only standing safeguards against undesirable drift are DR-001 (immutable core) and DR-031 (frozen safety-relevant fields) — there is no coarse recovery option as a backstop. This raises the stakes on W11's adversarial testing of the governor's boundary (already flagged in `05-research-plan.md`'s W11 entry) — with no reset available, a governor gap that lets an undesirable change through has no correction mechanism at all.
