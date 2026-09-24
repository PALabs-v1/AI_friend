# DR-027: Word-level truncation for "what the user heard"

**Round**: 8 (voice/turn-taking)
**Date**: 2026-09-24

**Decision**: keep today's word-level granularity for history truncation on interruption.

**Consequence**: no change needed to `_truncate_interrupted_reply`'s existing granularity — DR-028 extends *where* this applies, not *how* it truncates.
