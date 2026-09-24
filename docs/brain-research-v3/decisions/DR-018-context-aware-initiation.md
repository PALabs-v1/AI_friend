# DR-018: Proactive initiation should be context-aware

**Round**: 5 (autonomy/proactive)
**Date**: 2026-09-24
**Question**: Should proactive initiation factor in context (time of day, activity patterns) beyond idle-time?

**Decision**: yes. Time-of-day and recent activity patterns should factor in — don't initiate at statistically bad moments even if the idle-time gate is otherwise satisfied.

**Consequence for W9**: needs a "bad moment" signal beyond what exists today. In the near term (before any real usage history exists to learn "bad moments" from), this is likely a simple time-of-day heuristic (e.g. avoid very early morning / late night by default) rather than a learned model — learning what counts as a bad moment for a specific person is itself a longer-horizon signal that lifesim's realistic interaction-gap patterns (`06-benchmark-plan.md`) can help validate once BrainBench exists. Scoped as a heuristic-first, learn-later item for W9, not something requiring new ML infrastructure immediately.
