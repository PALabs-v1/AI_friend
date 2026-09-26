# DR-007: Trivial memories fade toward unretrievable, never deleted; emotional memories resist decay, bounded

**Round**: 2 (memory/forgetting)
**Date**: 2026-09-24
**Questions**: (1) what happens to trivial episodic memories over a long horizon; (2) should emotionally significant memories resist forgetting.

**Decision (trivia)**: retrievability decays hard over time and disuse — practically unreachable in ordinary retrieval — but the record is never destroyed. Matches how human memory actually behaves (reconstructive and disuse-based, not deletion-based).

**Decision (emotional protection)**: yes, but bounded. Emotionally significant memories (arguments, celebrations, loss) decay far more slowly than trivial ones. Explicitly **not** additionally boosted in unrelated retrieval — protection applies to resisting forgetting, not to winning more retrievals. This matches V2's own rejected-approach finding (register E-R, `06-rejected-approaches.md`): a query-independent emotional-salience prior caused rumination and measurably hurt non-emotional recall (0.642 → 0.580) when tested. Aniket's answer explicitly avoids re-opening that rejected approach.

**Consequence for W1/W7**:
- The decay curve needs two regimes: a fast-decaying default (trivia) and a slow-decaying regime gated on emotional salience (already partially available via the existing `importance`/valence-distance terms in the hybrid ranker, but currently a single uniform weight — this needs to become an actual decay-rate multiplier, not just a ranking-time score bump).
- "Never destroyed" means storage growth (Objective 6/Phase 8) is a retrieval-relevance problem, not a deletion problem — this is exactly why W7 (consolidation) exists: if nothing is ever deleted, bounding growth has to come from consolidation/abstraction, not pruning.
- BrainBench's memory suite needs a decay-curve test per class (trivial vs. emotionally significant) with two different expected half-lives, not one uniform "forgetting" metric.
