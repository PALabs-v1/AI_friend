# DR-025: Regulation may win over immediate response

**Round**: 7 (executive control)
**Date**: 2026-09-24
**Question**: Should the system ever pause/regulate instead of responding immediately?

**Decision**: yes, consistent with DR-002. Something genuinely surprising or emotionally significant can warrant a brief pause before responding. Needs a real, specific trigger condition — not always-on, not the default.

**Consequence for W2/W9**: register A-6 (urgency pinned ≥0.65 on every turn) needs an actual fix, not just a documented limitation — urgency needs to be able to drop low enough for regulation to win under a defined trigger. The natural trigger candidate is DR-009's "exceptionally intense single event" path (already established as a distinct mechanism from ordinary turn processing) — the same significant-event detector that can push directly into mood/relationship-sentiment (DR-009/DR-010) is a plausible source for "this warrants a beat before responding" too, rather than building a second, separate significance detector. Concrete wiring is scoped to W2 (since it shares the significant-event detection machinery) with a cross-reference from W9 (executive control's arbitration logic).
