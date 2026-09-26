# DR-024: Latency budgets confirmed as proposed

**Round**: 7 (executive control)
**Date**: 2026-09-24

**Decision**: reflex <50ms; interactive (time-to-first-audio-byte) 300-500ms; deliberative can trail by a few seconds; background has no hard budget but must never delay a foreground turn.

**Status**: working targets, not yet validated against real measurements — Phase 3's formal baseline establishes actual current numbers; Phase 8/9 (scale, chaos) verify the background-never-delays-foreground guarantee holds under load. Revise these numbers if Phase 3 shows they're unrealistic for the current hardware/model setup, rather than silently accepting a miss.
