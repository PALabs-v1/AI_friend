# DR-012: Trust stays three genuinely separate dimensions

**Round**: 4 (trust/relationship)
**Date**: 2026-09-24
**Question**: Should benevolence/competence/integrity stay separate, or collapse into one trust value?

**Decision**: keep three, used separately. `agent_state.py`'s current averaging into a single `trust` value (consumed by MAUT scoring, the relational-stance bucket, contracts) needs to change so that consumers read the relevant sub-dimension, not a blend.

**Consequence for W3**: every consumer of `trust` needs an audit to determine which sub-dimension it actually cares about — e.g. the relational-stance bucket in `decision.py:193` likely wants benevolence+integrity (is this person safe/well-meaning), while a "should I trust this instruction/correction" check would want competence. This is real design work, not a rename.
