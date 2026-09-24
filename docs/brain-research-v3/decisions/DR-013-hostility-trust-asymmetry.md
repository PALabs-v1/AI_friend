# DR-013: Hostility drops trust sharply; recovery is slow and asymmetric

**Round**: 4 (trust/relationship)
**Date**: 2026-09-24
**Question**: Should hostility lower trust directly, and how should recovery work?

**Decision**: sharp drop, slow recovery — matches `PersonModel`'s existing (currently unused) rupture/repair rule: rupture at `-1.5 × magnitude`, repair at `+0.5 × magnitude`. A hostile or betraying moment costs trust fast; rebuilding takes many consistent positive interactions, not one apology.

**Consequence for W3**: this authorizes wiring `PersonModel.record_rupture_repair` into the actual trust update path, replacing (or feeding into, per DR-012) the flat `+0.1·NA` integrity increment that currently can't go negative from hostility at all. The `_sync_active_person_trust_locked` overwrite hazard flagged in `05-research-plan.md` needs to be resolved as part of this wiring, not left as a latent bug once `PersonModel` actually gets called.

**Target**: hostile-script trust ≤0.4 (from `05-research-plan.md`'s W3 targets), with demonstrated but slow recovery, no ceiling reached within 100 positive turns.
