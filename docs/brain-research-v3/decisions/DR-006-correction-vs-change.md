# DR-006: Corrections and genuine changes are handled differently

**Round**: 2 (memory/forgetting)
**Date**: 2026-09-24
**Question**: Should a same-turn correction be handled differently from a real change of mind over time?

**Decision**: yes, distinguish them. A correction ("no, I meant Thursday not Tuesday") replaces the record as if the old value was never true — no meaningful history preserved, since it wasn't a real historical state. A genuine evolution ("I used to like cappuccino, now I don't") preserves the old value as real history, per DR-005.

**Classifier design** (for W1, not fully specified here): distinguish first via recency + explicit correction language ("I meant", "actually", "not X", within the same or an adjacent turn); escalate to the LLM classifier only when the cheap signal is ambiguous. This matches the master prompt's own no-LLM-mode requirement — the common case (an immediate, explicitly-worded correction) should be resolvable deterministically, and only genuinely ambiguous cases cost an LLM call.

**Consequence for W1's detector arms**: E8 (cosine + polarity/negation) needs a same-turn/adjacent-turn recency check added specifically for the correction case, distinct from the general contradiction-detection logic that already runs in `find_contradiction`. The fast-LLM-on-top-3-neighbors arm becomes the ambiguous-case escalation path, not the default path for every contradiction.
