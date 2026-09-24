# DR-019: Self-initiated thought is its own legitimate category

**Round**: 5 (autonomy/proactive)
**Date**: 2026-09-24
**Question**: Should the agent ever initiate purely because it wants to share a thought of its own, distinct from usefulness to the user?

**Decision**: yes, as its own category, consistent with DR-002. "I've been thinking about X" is a legitimate reason to speak, separate from (and in addition to) useful-to-user proactive behavior. Needs its own — likely stricter — gating so it doesn't dominate over the useful-to-user category.

**Consequence for W9**: `SubconsciousEngine.evaluate_and_think`'s candidate-thought generation needs a category label (useful-to-user vs. self-directed), each with its own eligibility path through DR-016's importance weighting and DR-018's context awareness. Self-directed thoughts likely need a lower base rate / higher importance threshold than useful-to-user ones, to avoid the agent talking about itself constantly — the exact relative weighting is an implementation/tuning decision for W9, validated against BrainBench's proactive-behavior suite (useful vs. irrelevant initiation, repeated annoyance).
