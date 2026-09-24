# DR-028: Ordinary barge-in cuts history the same as speculative

**Round**: 8 (voice/turn-taking)
**Date**: 2026-09-24
**Question**: Should a non-speculative barge-in truncate history like the speculative-confirmed path does?

**Decision**: yes, same treatment. The current special-casing (only speculative-then-confirmed cuts history) is a gap, not an intentional distinction.

**Consequence for W5**: this closes one of ADR-003's own "Known and not changed" items directly — `_truncate_interrupted_reply`/`_store_heard_reply` need to run for *any* confirmed interruption, not only the speculative-stop path. This is a concrete, scoped fix within W5, using DR-027's existing word-level granularity, not new design work.
