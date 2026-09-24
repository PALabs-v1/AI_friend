# DR-015: Trust feeds relationship sentiment; they're distinct but causally connected

**Round**: 4 (trust/relationship)
**Date**: 2026-09-24
**Question**: How should trust relate to relationship sentiment (Round 3's slowest affect layer)?

**Decision**: trust (the three DR-012 dimensions, updated per DR-013/DR-014) is the evidence-based input. Relationship sentiment is the slower-moving emotional summary that trust, plus accumulated affect history, rolls up into over time. Distinct concepts, causally connected — not independent tracks, not the same variable under two names.

**Consequence for W2/W3 jointly**: this is the concrete answer DR-010 deferred. Relationship sentiment's update rule is now scoped as some slow rolling function of (trust dimensions, momentary/mood-layer affect history) — not a fourth independently-updated state. Implementation-wise, likely similar in spirit to DR-011's baseline-mood rolling average, but fed by trust+affect jointly rather than mood alone. This needs concrete design during W3 (own workstream, since trust is its primary subject), with W2 supplying the affect-history input.
