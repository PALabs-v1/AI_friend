# DR-038: Personality change needs corroboration, not just confidence

**Round**: post-interview (Phase 7 planning), refines DR-021
**Date**: 2026-09-25
**Question**: DR-021 made the 0.8 confidence bar the only throttle on persona
evolution. F-012 then measured that path on a real model (`llama3.2:3b`, one
simulated week):
- 58% of reflections were "confident"
- 32 of them were applied
- the entire adaptive trait set was replaced (distance 1.0, 91 traits added
  or dropped)
- the relationship was relabelled 23 times

How should W11 handle this?

**Options presented**:
- Corroboration, where a change applies only once several independent
  reflections over distinct episodes point the same way (recommended)
- Keep DR-021 as is
- An explicit rate cap

**Decision** (Aniket, verbatim choice): "Corroboration (Recommended)".

A proposed change to the adaptive self applies only once the same direction
is supported by several independent reflections drawn from distinct
episodes. There is still no fixed cap on pace: if strong evidence clusters,
the self changes fast. One reflection, however confident, can no longer flip
it.

**Relation to earlier decisions**: DR-021's intent stands (evidence-driven,
no schedule, "like a human"). DR-038 changes what counts as evidence: a
tally of corroborating reflections, not one model's confidence number.
DR-023's 0.8 bar still applies to each contributing reflection. DR-020 (no
external approval), DR-022 (opaque) and DR-033 (no reset) are unchanged.

**Consequence for W11**: build the corroboration mechanism, specifically:
- what "the same direction" means for traits, style and relationship labels
- how many corroborations, from how many distinct episodes
- how old evidence ages out

Put the constants in `Config` with the reasoning. The personality suite
reports corroboration state, and W11's acceptance measures trait distance
and relationship relabels per week against F-012's numbers.
