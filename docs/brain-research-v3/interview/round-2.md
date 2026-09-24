# Interview Round 2 — Memory and Forgetting

This round gates workstream W1 (temporal truth maintenance), the top-priority item in the backlog: obsolete facts currently win retrieval 64-67% of the time (register M-5), and nothing in the ranker or the write path fixes it — `TemporalMemoryStore.apply_contradiction` exists and is fully implemented but has zero callers.

Framed per DR-002: this isn't "what's useful for the user to have remembered" — it's closer to how an actual mind's memory works, which forgets, misremembers, and holds onto emotionally significant things disproportionately, without ever truly zeroing out the past.

## Q1. When a fact changes, what happens to the old value?

**What exists**: `add_memory` sets `contradicts_id` automatically when it detects an opposing record (via polarity/negation regex or an opposite valence sign) — but the old row is never modified. Both rows sit in the store with equal standing. `valid_from`/`valid_until` columns exist in both the SQL schema and the Qdrant payload but nothing writes or reads `valid_until` meaningfully. `TemporalMemoryStore` (unused) can already do UPDATE→SUPERSEDED, CORRECTION→INVALIDATED, CONFLICT→DISPUTED.

**Why it matters**: this is the actual mechanism W1 builds. "Supersede but keep queryable as history" vs. "the old value should resist being surfaced at all for present-tense questions" are different retrieval-time behaviors, not just different storage.

## Q2. Correction vs. genuine belief change — same handling, or different?

**What exists**: no distinction today. "No, I meant Thursday not Tuesday" (a correction — the old value was never true) and "I used to like cappuccino, now I prefer black coffee" (a real historical fact worth keeping) currently go through the identical `contradicts_id` mechanism with no differentiation.

**Why it matters**: if they're handled identically, a genuine multi-year preference history gets erased with the same finality as a same-conversation typo-fix — or the reverse, a same-turn correction gets preserved as if it were a meaningful piece of the user's history.

## Q3. Trivial episodic memories over a long horizon (years) — what should happen to them?

**What exists**: nothing decays retrievability today beyond the ACT-R base-level term already baked into the hybrid score (a small weight, not a real forgetting curve) — everything stays in the same pool forever, competing equally in every future retrieval.

**Why it matters**: this is the master prompt's own explicit question ("should forgotten memories remain archived?"). A real mind doesn't hard-delete "what did I eat on a random Tuesday three years ago" — it becomes nearly impossible to recall unprompted, but isn't gone if something jogs it.

## Q4. Should emotionally significant memories resist forgetting more than trivial ones?

**What exists**: importance is one term in the hybrid score (weight 1.5, combined with ACT-R base-level activation) but nothing specifically protects *emotional* salience from the same decay/pooling as everything else, and the master prompt separately warns against the opposite failure — over-indexing emotionally salient but irrelevant memories, causing rumination (this was explicitly tested and rejected in V2: register E-R, "mood-congruent retrieval" made non-emotional recall worse).
