# DR-039: Lifesim gets a separate expressed-valence label

**Round**: post-interview (Phase 7, W2)
**Date**: 2026-09-27
**Question**: ADR-002's pre-registered rule for a user-valence estimator needs
Pearson r >= 0.8 and sign agreement >= 0.9 on two sets: the Phase 4a ToM set
and lifesim's oracle user valence (dev seeds). The W2 bake-off (seed 1000,
`steady_professional` + `volatile_creative`, 1m, 278 turns) scored:

| Estimator | ToM r | Lifesim r |
|---|---|---|
| VADER | 0.905 | 0.499 |
| 3 CPU transformers | 0.945-0.984 | 0.430-0.478 |
| qwen3:8b / llama3.1:8b / gemma3:4b / qwen3:4b (home-gpu) | 0.975-0.994 | 0.389-0.533 |

Every estimator hits the same lifesim ceiling. The oracle is the cause:
lifesim's `user_valence` is the valence of the underlying life event, not
what the utterance expresses. 181 of 278 turns are labelled exactly 0.0,
including "The light looks lovely today." (0.0; cardiff +0.98, VADER +0.59)
and "Hey, good to see you." (0.0). Small talk averages oracle +0.00 against
every estimator's positive reading. No estimator can meet the rule on that
set, and the affect suite's `user_valence_reaches_mood` would penalise a
brain that reads the user correctly.

**Options** (asked 2026-09-27):
- Add a separate `expressed_valence` label (recommended)
- Relabel `user_valence` (changes what every past lifesim run measured)
- Keep the set and report W2 blocked

**Decision (Aniket)**: add a separate `expressed_valence` label. ADR-002's
bar stays at r >= 0.8 and sign agreement >= 0.9.

**Rule, frozen before any rescoring** (the labelling unit implements it; W2
does not label):

1. Every fixed fragment the renderer can emit (each bank template's own
   words, small talk, greetings, questions, verbosity tails, style
   wrappers, the emotion openers in `observe.py`, each event line shape
   with its payload vocabulary) gets a hand label in [-1, 1] for the
   valence its wording expresses to a careful human reader. Neutral wording
   is 0. Labels are written from the words alone: no estimator is run on
   lifesim text while labelling.
2. An utterance's `expressed_valence` is the label of its fragment with the
   largest magnitude (ties: the first in the utterance). A joke wrapper
   ("just kidding") halves it. The result is clamped to [-1, 1].
3. `user_valence` (event valence) is unchanged. Nothing already recorded
   changes meaning.
4. Validity check before use: a second, independent labeller (a fresh Codex
   session that never sees the first labels) labels a random 60-fragment
   sample; the two must agree at Pearson r >= 0.8. If not, the rule stops
   here and comes back to Aniket.
5. W2's bake-off and the affect suite then score against
   `expressed_valence` on dev seeds, ToM set unchanged, rule unchanged.

**Consequence**: a lifesim schema addition (`Annotation.expressed_valence`),
new label data in `evals/lifesim/`, a regenerated bank manifest hash, and a
rerun of W2's bake-off on the new label. Past runs keep their meaning.
