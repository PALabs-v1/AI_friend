# DR-005: Superseded facts become history-only, never deleted

**Round**: 2 (memory/forgetting)
**Date**: 2026-09-24
**Question**: When a fact changes, what happens to the old value at retrieval time?

**Decision**: the new value always wins present-tense/current-truth queries. The old value is never deleted and stays fully queryable for explicitly historical questions ("what did I used to drink").

**Consequence for W1**: this is a "supersession by projection," not "supersession by deletion." Confirms wiring `TemporalMemoryStore.apply_contradiction`'s UPDATE path (marks old `SUPERSEDED`, doesn't erase) rather than any destructive alternative. Retrieval needs a temporal-intent signal (is this query asking about now, or about the past?) to route to the current-truth projection vs. the full historical timeline — this becomes a concrete requirement for W1's detector design, not just a nice-to-have. Directly targets the "obsolete-win ≤0.10" goal from `05-research-plan.md` without sacrificing the "updated-fact hit@3 ≥0.48" historical-recall goal.
