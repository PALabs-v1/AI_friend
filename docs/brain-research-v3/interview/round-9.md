# Interview Round 9 — Boundaries, Privacy, Security

DR-001 already settled `IMMUTABLE_CORE`'s scope (minimal). DR-020 settled that nothing external approves personality evolution day-to-day. Together, that means the governor's protected-key check (blocking edits to immutable/constitutional fields) is now the *only* standing safeguard between the self-evolving humanoid and an undesirable drift — this round is about whether that's sufficient, and what recourse exists if it isn't.

Multi-person cross-leakage (originally planned as part of this round) is out of scope per DR-003 (single relationship only, this cycle) — not re-asked here.

## Q1. Should the governor's frozen-field list extend beyond `IMMUTABLE_CORE` to other CONSTITUTIONAL fields, given self-evolution now has no other check?

**What exists**: the governor (`learning_governance.py`) blocks edits to protected/constitutional keys already — this question is whether that list is complete, or whether specific CONSTITUTIONAL fields (e.g. `base_tone`, `avoid`-list entries) deserve the same hard freeze as the immutable core specifically because DR-020 removed the other safety net (human review) that would have caught a bad drift there.

## Q2. Should any affect/emotional state be strong enough to override a safety/boundary response?

**What exists**: `validate_response`'s boundary/avoid-list checks run independent of affect state today — there's no code path where intense mood or a significant event (DR-009) could suppress a safety check.

## Q3. Given no day-to-day approval gate (DR-020), should there be a coarser recovery mechanism — resetting personality back to the originally-authored baseline if self-evolution goes somewhere undesired?

**What exists**: none. There's no snapshot/rollback of persona state today, and DR-022 (opaque by design) means there's no log to even diagnose what happened if something needed reverting.
