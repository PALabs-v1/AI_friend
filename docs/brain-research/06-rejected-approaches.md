# 06 — Rejected approaches (and why), so nobody rediscovers them

| Idea | Tested how | Result | Verdict |
|---|---|---|---|
| Retune V1's two constants (similarity weight, cue boost) | E5, 20-cell grid | best worst case 0.45 vs hybrid 0.69; best setting differs by embedding anisotropy (0.64 vs 0.96 on one regime) | rejected: constants are not portable across embedding models |
| Generative Agents scoring (Park et al., 2023) | E3 | 0.98 on clean `unique`, 0.32 on hard `summary` | rejected: relies on per-memory importance production does not write |
| Reciprocal Rank Fusion (Cormack et al., 2009) | E3 | worst case 0.22 | rejected: a weak signal's rank counts as much as a strong one's |
| Near-duplicate supersession by absolute cosine threshold | E4b, 6 thresholds | < 0.8 costs up to −0.50 hit@3 on the hard profile; ≥ 0.8 does nothing | rejected: supersession needs explicit validity (07 §1) |
| Query-independent emotional-salience prior | E-R rumination test | +0.10 on emotional questions in a calm store; −0.06 and no gain in a distressed store | rejected: it becomes rumination exactly when it matters |
| Mood-congruent retrieval (V1 emotion-distance terms) | E2 ablation | ±0.01 | rejected: decorative at any scale that doesn't also cause rumination |
| Stress-narrowed candidate pool / MRL truncation (V1) | E1/E3 `mood_negative` | no benefit isolated; V1 scores 0.00–0.20 under stress, hybrid 0.61 with a fixed pool | not carried into the hybrid; zeroing dimensions without renormalising also distorts cosine by a per-document factor |
| Store-wide BM25 IDF | E6 comparison | identical to pool IDF on 1,128 of 1,131 rankings | rejected as unnecessary state |
| VADER as the appraisal valence estimator | affect sim + spot checks | hostile trust 0.68 vs oracle 0.34; "Not bad, pretty good day" → −0.29 | rejected as production estimator |
| Keeping reappraisal weight learning with a tighter clamp | analysis + sim | still drifts (numbing) and still learns from the agent's own state | rejected; learning off (ADR-002) |
| Unscoped `audio.stop` for barge-in | design analysis | would re-open the stale-stop bug turn-scoping fixed | rejected (ADR-003) |
| Rewriting retrieval in Rust | latency measurement | Python hybrid + numpy index is 4 ms p50 at 5k | unnecessary; no bottleneck |
