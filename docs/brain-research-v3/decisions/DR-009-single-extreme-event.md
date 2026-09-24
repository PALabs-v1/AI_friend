# DR-009: One extreme message can still leave a lasting mark

**Round**: 3 (affect)
**Date**: 2026-09-24
**Question**: Can a single message create a lasting effect, or must lasting change require a pattern?

**Decision**: pattern is the default rule (most single messages only touch momentary emotion), but an exceptionally intense single event — severe hostility, a major positive moment — can still create a disproportionate lasting effect, the way one real trauma or one real joy can for a person.

**Consequence for W2 and W3**: needs an explicit intensity threshold, separate from the ordinary damped per-turn update in DR-008, that can push directly into the mood layer (or, per DR-010, even the relationship-sentiment layer) when crossed — not just a bigger version of the normal update, but a distinct "significant event" path. This is functionally similar to how W1 already needs a "corrections vs. genuine change" bimodal classifier (DR-006) — same shape of problem: most inputs go through the smooth/default path, rare inputs get routed to a different mechanism entirely. Threshold calibration is an implementation detail for W2, validated against BrainBench's affect suite (persistence/decay/recovery/saturation).
