# DR-023: Keep the 0.8 confidence threshold

**Round**: 6 (learning/evolution)
**Date**: 2026-09-24
**Question**: Is 0.8 confidence (`learning.py:158`) the right bar for proposing a personality change?

**Decision**: keep it, unless a workstream finds concrete evidence it's miscalibrated.

**Consequence**: this is now the *only* throttle on evolution rate (DR-021) and the only gate before a change applies (DR-020, alongside the separate immutable-core boundary check) — its calibration matters more under this cycle's decisions than it did when it fed into an approval queue that never activated anyway. W11 should include a check (not necessarily a redesign) of whether 0.8 produces a sensible real-world rate of change once evolution actually goes live.
