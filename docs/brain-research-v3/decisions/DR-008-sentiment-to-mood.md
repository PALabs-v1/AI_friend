# DR-008: User sentiment moves mood synchronously, damped and bounded

**Round**: 3 (affect)
**Date**: 2026-09-24
**Question**: Should user words move mood on the spot, or stay a slow background effect?

**Decision**: yes, synchronously, but damped — not raw sentiment 1:1 — with a per-turn cap, so one sentence nudges rather than swings the state. This is ADR-002 Part 2's proposed direction, now authorized to implement (gated on the Phase 4 GPU ToM-valence accuracy experiment's r≥0.8/sign-agreement≥0.9/hostile-trust≤0.5 thresholds still applying as the go/no-go gate for the specific estimator, per that ADR).

**Consequence for W2**: replaces `pipeline.py:865`'s `emotional_bias = state_snapshot.get("mood", 0.0)` with a damped function of an actual user-sentiment estimate (ToM `inferred_valence` if it clears the Phase 4 gate; a deterministic fast estimator otherwise) plus a cap constant to be tuned during implementation and validated against DR-009's single-extreme-event carve-out and DR-010's layer separation.
