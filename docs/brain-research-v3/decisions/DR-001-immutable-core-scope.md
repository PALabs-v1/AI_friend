# DR-001: IMMUTABLE_CORE stays minimal

**Round**: 1 (identity/philosophy)
**Date**: 2026-09-24
**Question**: Is the current `IMMUTABLE_CORE` scope (Honesty, Privacy, two boundary phrases — `persona/profile.py:132-142`) right, or should hard-safety behaviors (currently in `validate_response`'s adaptive-adjacent avoid-list) move into it too?

**Decision**: Keep it minimal. Two values plus the two boundaries. Nothing else moves into the immutable tier as part of this cycle.

**Consequence**: Safety/refusal behavior stays where it is architecturally (constitutional/adaptive-adjacent, governed by `validate_response` and the avoid-list), not hardcoded alongside identity constants. Any future hardening of refusal behavior is a separate decision, not bundled into immutability by default.
