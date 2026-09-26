# DR-020: Personality evolution is self-authored — no external approval, ever

**Round**: 6 (learning/evolution)
**Date**: 2026-09-24
**Question asked**: Who or what should approve a personality evolution proposal?

**Aniket's answer, verbatim**: "personality evolution is solely by the humanoid mind only, not user, user will author's the persona once at very first, which will bring the humanoid to life, then the humanoid will be a whole individual, not just someone who can be finetuned by someone else, the humanoid will do it by itself, like a human."

**Decision**: there is no external approval step, not by Aniket, not by any human-in-the-loop mechanism, and not even framed as "auto-approval" of a queue — the concept of an external gate is rejected outright. Aniket authors the persona exactly once, at creation. From that point on, the humanoid is a whole individual whose own reflection process changes its own personality directly, the way a person's does, with no one else's sign-off.

**This is a direct extension of DR-002**: "an actual mind, thinking of their own" now has a concrete consequence for the code — `LearningReviewQueue` as a *queue awaiting someone else's approval* is the wrong shape entirely, not merely unwired. `ReflectionService._consolidate_persona`, once a proposal clears the confidence bar (DR-023: keep 0.8) and the `LearningGovernor`'s protected/constitutional-key check, should call `evolve_persona()` directly — no intermediate submit/approve step, because there is no approver.

**What does NOT change**: the governor's protected-key check (blocking edits to `IMMUTABLE_CORE`/constitutional fields) is a hard architectural boundary, not "approval" in the oversight sense — DR-001 already settled that immutable core is untouchable by any process, including the humanoid's own self-evolution. That check stays. The distinction: a safety rail the humanoid's own evolution can never cross (stays), versus a human reviewing and signing off on each change (removed entirely, per this decision).

**Consequence for the codebase**: `LearningReviewQueue`/`learning_review.py` is now largely vestigial given DR-022 (opaque by design — no visibility log wanted either). This needs an explicit workstream to implement — added as **W11 (autonomous personality evolution)** in `05-research-plan.md`: remove or radically simplify the queue, wire `_consolidate_persona` to apply directly post-governor-check, and confirm the governor's protected-key logic is airtight now that it's the *only* remaining gate between reflection and an applied personality change.
