# 03 — Memory retrieval: experiments and decision

**Outcome:** production retrieval returned the right memory in its top 3 for
**2–4%** of questions on the SQLite path. The Brain V2 hybrid ranker returns it
for **69–100%** (held-out seeds, hardest profile to easiest), at **3.9 ms p50**
instead of 56.9 ms on 5,000 memories. Shipped as the default (ADR-001); V1
remains selectable (`MEMORY_RANKING_POLICY=actr_v1`).

## Question

Given a multi-week history, does retrieval put the memory a question is about
into the 3 memories production hands the model (`limit=3`, both callers)?
Secondary: does it avoid obsolete facts, keyword traps and irrelevant filler?

## Method

**Scenarios** (`backend/evals/cognitive/scenarios.py`). One seeded user history
per seed: 60 days of small talk (3–7 lines/day), 18–26 facts across 10 topics
(some emotional, some repeated, four that later change), a keyword *trap* per
fact (small talk containing a word from the paraphrase question), and probes
asked at day 60.5 against the whole store. Every fact has two questions:
a **paraphrase** sharing no content word with the fact (pinned by
`test_paraphrase_query_shares_no_content_word_with_its_fact`) and a **keyword**
question. Every paraphrase question is also asked in a negative, stressed
agent state (`mood_negative`). Three regimes model production's write paths:

* `verbatim` — small talk repeats word for word (duplicate reinforcement inflates `recall_count`);
* `unique` — every memory text is distinct;
* `summary` — distinct narrative-framed texts at flat importance 0.6, which is what consolidation writes. **Closest to production.**

**Embeddings** (`embedder.py`). No model server exists in this environment, so
vectors are seeded compositions of common/topic/fact/noise components with
controlled cosine structure. Profiles: `nomic_like` (fact 0.80 / topic 0.60 /
unrelated 0.40), `minilm_like` (0.70 / 0.35 / 0.05), and `hard` (0.72±0.05 /
0.62±0.07 / 0.39±0.10), where fact and topic similarities overlap. **The clean
profiles saturate; the `hard` profile discriminates, and conclusions are drawn
from it and from worst cases.** The GPU package reruns everything with the
real `nomic-embed-text` (`backend/experiments/gpu/`).

**Production path vs lab.** `harness.py` drives the real `MemoryStore`
(add_memory, search_memories, Rust ACT-R kernel, SQLite). `lab.py` re-expresses
the ranker as candidate generator × scoring policy so terms can be ablated.
Parity is pinned by tests: the lab's V1 equals production V1 on every probe
(0 / 279 mismatches), and the lab's hybrid equals production hybrid on 557 / 558
(one float-level tie at rank 3).

**Statistics.** 20 tuning seeds (1–20) for sweeps; every decision re-validated
on 30 **held-out** seeds (101–130). 95% intervals are a cluster bootstrap over
scenario seeds (probes within a scenario are correlated, and the stressed-state
probes repeat the paraphrase questions); arm comparisons are paired over
identical probes. 1,707 probes per cell (569 paraphrase questions, the same
569 asked again in a distressed state, 569 keyword questions), 30 scenarios
per held-out cell. The "all" column averages over all 1,707.

## Hypotheses

| ID | Hypothesis | Result |
|---|---|---|
| H1 | The +5.0 substring cue boost dominates ranking | **Confirmed.** Removing it is the only V1 ablation that moves hit@3 (0.38 → 0.22); traps win on it. |
| H2 | Similarity is under-weighted against recency/frequency | **Confirmed.** Plain cosine beats V1 scoring in every cell (mean 0.88 vs 0.38). |
| H3 | Emotional memories are penalised under neutral mood | Present in the formula; **zero measured effect** at V1 scale (±0.01). |
| H4 | The candidate pool, not the scorer, is the dominant SQLite failure | **Confirmed.** Hybrid scoring on V1's SQLite pool: 0.03; on a similarity pool: 0.69. |
| H5 | Repetition makes obsolete facts win | **Confirmed**, and *not* fixed by any ranker (obsolete-wins 0.64–0.67); needs write-time validity (E7). |

## E1 — Baseline V1 on each candidate path (tuning seeds, hit@3)

| arm | nomic/verbatim | nomic/unique | nomic/summary | minilm/verbatim | minilm/unique | minilm/summary | hard/verbatim | hard/unique | hard/summary | worst | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `v1@sqlite` | 0.030 | 0.042 | 0.024 | 0.031 | 0.043 | 0.025 | 0.030 | 0.042 | 0.023 | 0.023 | 0.032 |
| `v1@pg` | 0.050 | 0.271 | 0.141 | 0.068 | 0.334 | 0.183 | 0.041 | 0.254 | 0.129 | 0.041 | 0.163 |
| `v1@vec20` | 0.362 | 0.646 | 0.497 | 0.369 | 0.620 | 0.500 | 0.357 | 0.585 | 0.457 | 0.357 | 0.488 |
| `v1@all` | 0.348 | 0.427 | 0.362 | 0.349 | 0.443 | 0.372 | 0.347 | 0.423 | 0.358 | 0.347 | 0.381 |

## E2 — V1 ablation, every memory scored (tuning seeds)

| arm | nomic/verbatim | nomic/unique | nomic/summary | minilm/verbatim | minilm/unique | minilm/summary | hard/verbatim | hard/unique | hard/summary | worst | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `v1@all` | 0.348 | 0.427 | 0.362 | 0.349 | 0.443 | 0.372 | 0.347 | 0.423 | 0.358 | 0.347 | 0.381 |
| `v1-recency@all` | 0.361 | 0.425 | 0.367 | 0.379 | 0.455 | 0.378 | 0.353 | 0.414 | 0.362 | 0.353 | 0.388 |
| `v1-frequency@all` | 0.379 | 0.429 | 0.333 | 0.401 | 0.460 | 0.361 | 0.371 | 0.419 | 0.332 | 0.332 | 0.387 |
| `v1-spacing@all` | 0.347 | 0.435 | 0.340 | 0.349 | 0.450 | 0.357 | 0.347 | 0.423 | 0.332 | 0.332 | 0.376 |
| `v1-importance@all` | 0.336 | 0.428 | 0.362 | 0.340 | 0.448 | 0.366 | 0.336 | 0.416 | 0.355 | 0.336 | 0.376 |
| `v1-emo_proximity@all` | 0.347 | 0.426 | 0.362 | 0.348 | 0.444 | 0.372 | 0.345 | 0.425 | 0.359 | 0.345 | 0.381 |
| `v1-emo_distance@all` | 0.342 | 0.423 | 0.362 | 0.346 | 0.445 | 0.373 | 0.342 | 0.423 | 0.357 | 0.342 | 0.379 |
| `v1-cue@all` | 0.082 | 0.288 | 0.272 | 0.101 | 0.336 | 0.308 | 0.075 | 0.272 | 0.249 | 0.075 | 0.220 |
| `v1-goal_buffer@all` | 0.348 | 0.427 | 0.362 | 0.349 | 0.443 | 0.372 | 0.347 | 0.423 | 0.358 | 0.347 | 0.381 |
| `cosine@all` | 1.000 | 1.000 | 0.954 | 1.000 | 1.000 | 0.952 | 0.853 | 0.568 | 0.582 | 0.568 | 0.879 |

Only the cue term matters; every ACT-R and affect term is within ±0.01.
Scoring *more* candidates makes V1 worse than plain cosine.

## E3 — Alternatives vs production (held-out seeds 101–130)

Worst case and mean over the 9 profile × regime cells:

| arm | nomic/verbatim | nomic/unique | nomic/summary | minilm/verbatim | minilm/unique | minilm/summary | hard/verbatim | hard/unique | hard/summary | worst | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `v1@sqlite` | 0.021 | 0.040 | 0.025 | 0.024 | 0.040 | 0.030 | 0.021 | 0.040 | 0.022 | 0.021 | 0.029 |
| `v1@vec60` | 0.342 | 0.583 | 0.427 | 0.351 | 0.587 | 0.450 | 0.343 | 0.577 | 0.407 | 0.342 | 0.452 |
| `cosine@vec60` | 1.000 | 1.000 | 0.962 | 1.000 | 1.000 | 0.962 | 0.820 | 0.552 | 0.587 | 0.552 | 0.876 |
| `gen_agents@vec60` | 0.870 | 0.982 | 0.700 | 0.904 | 0.987 | 0.734 | 0.586 | 0.789 | 0.316 | 0.316 | 0.763 |
| `rrf@vec60` | 0.237 | 0.958 | 0.694 | 0.236 | 0.960 | 0.678 | 0.216 | 0.769 | 0.522 | 0.216 | 0.585 |
| `v1_retuned:12:2@vec60` | 0.655 | 0.985 | 0.942 | 0.965 | 1.000 | 0.960 | 0.446 | 0.738 | 0.616 | 0.446 | 0.812 |
| `hybrid:1.5:0.2:none@vec20` | 1.000 | 1.000 | 0.959 | 1.000 | 1.000 | 0.961 | 0.837 | 0.687 | 0.681 | 0.681 | 0.903 |
| `hybrid:1.5:0.2:none@vec60` | 1.000 | 1.000 | 0.958 | 1.000 | 1.000 | 0.960 | 0.823 | 0.735 | 0.693 | 0.693 | 0.908 |
| `hybrid:1.5:0.2:none@all` | 1.000 | 1.000 | 0.960 | 1.000 | 1.000 | 0.960 | 0.797 | 0.779 | 0.672 | 0.672 | 0.908 |
| `hybrid:1.5:0.2:none@sqlite` | 0.047 | 0.042 | 0.032 | 0.047 | 0.042 | 0.032 | 0.047 | 0.042 | 0.032 | 0.032 | 0.040 |

Per category on the production-like cell (`hard/summary`):

| arm | all | paraphrase | keyword | old | emotional | updated | mood_negative | obsolete wins | trap in top-3 | hit@3 95% CI | paired Δ vs baseline [95% CI] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `v1@sqlite` | 0.02 | 0.01 | 0.05 | 0.00 | 0.03 | 0.00 | 0.00 | 0.00 | 0.31 | [0.015, 0.030] | — |
| `v1@vec60` | 0.41 | 0.18 | 0.86 | 0.33 | 0.53 | 0.04 | 0.20 | 0.64 | 0.20 | [0.390, 0.423] | +0.385 [+0.370, +0.400] |
| `cosine@vec60` | 0.59 | 0.56 | 0.63 | 0.55 | 0.59 | 0.37 | 0.57 | 0.60 | 0.02 | [0.558, 0.613] | +0.565 [+0.539, +0.593] |
| `gen_agents@vec60` | 0.32 | 0.30 | 0.34 | 0.10 | 0.34 | 0.11 | 0.30 | 0.09 | 0.15 | [0.283, 0.350] | +0.294 [+0.263, +0.325] |
| `rrf@vec60` | 0.52 | 0.50 | 0.57 | 0.28 | 0.53 | 0.39 | 0.50 | 0.54 | 0.06 | [0.484, 0.559] | +0.500 [+0.465, +0.536] |
| `v1_retuned:12:2@vec60` | 0.62 | 0.49 | 0.87 | 0.51 | 0.68 | 0.20 | 0.50 | 0.77 | 0.09 | [0.594, 0.636] | +0.593 [+0.572, +0.615] |
| `hybrid:1.5:0.2:none@vec20` | 0.68 | 0.61 | 0.83 | 0.62 | 0.71 | 0.36 | 0.61 | 0.63 | 0.02 | [0.654, 0.708] | +0.659 [+0.635, +0.683] |
| `hybrid:1.5:0.2:none@vec60` | 0.69 | 0.61 | 0.86 | 0.62 | 0.72 | 0.33 | 0.61 | 0.67 | 0.02 | [0.666, 0.718] | +0.671 [+0.649, +0.693] |
| `hybrid:1.5:0.2:none@all` | 0.67 | 0.57 | 0.87 | 0.59 | 0.71 | 0.27 | 0.57 | 0.66 | 0.09 | [0.647, 0.695] | +0.650 [+0.629, +0.671] |
| `hybrid:1.5:0.2:none@sqlite` | 0.03 | 0.02 | 0.05 | 0.00 | 0.04 | 0.00 | 0.01 | 0.00 | 0.35 | [0.022, 0.042] | +0.009 [+0.005, +0.013] |

Alternatives tried: Generative Agents scoring (Park et al., 2023: min-max
recency 0.995^h + importance + relevance) — strong on `unique`, collapses to
0.32 on `summary` where importance is flat; Reciprocal Rank Fusion (Cormack et
al., 2009) over cosine/BM25/activation — worst case 0.22; V1 with its two
scale constants retuned (E5) — worst case 0.45 and swings with embedding
anisotropy. Normalisation, not the constants, is what fixes V1.

## E4 — Sensitivity (tuning seeds)

Hybrid weights `hybrid:w_lex:w_act` — a broad plateau at `w_lex` 1–2,
`w_act` 0.15–0.3; `w_act` ≥ 0.6 hurts. The shipped `1.5 : 0.2` is the plateau
centre, not the best cell.

| arm | nomic/verbatim | nomic/unique | nomic/summary | minilm/verbatim | minilm/unique | minilm/summary | hard/verbatim | hard/unique | hard/summary | worst | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hybrid:0.0:0.0:none@vec60` | 1.000 | 1.000 | 0.954 | 1.000 | 1.000 | 0.952 | 0.859 | 0.600 | 0.637 | 0.600 | 0.889 |
| `hybrid:0.0:0.15:none@vec60` | 1.000 | 1.000 | 0.986 | 1.000 | 1.000 | 0.986 | 0.810 | 0.674 | 0.639 | 0.639 | 0.899 |
| `hybrid:0.0:0.3:none@vec60` | 1.000 | 1.000 | 0.998 | 1.000 | 1.000 | 0.998 | 0.733 | 0.721 | 0.630 | 0.630 | 0.898 |
| `hybrid:0.0:0.6:none@vec60` | 0.993 | 1.000 | 1.000 | 0.999 | 1.000 | 1.000 | 0.523 | 0.739 | 0.576 | 0.523 | 0.870 |
| `hybrid:0.0:1.0:none@vec60` | 0.805 | 0.994 | 0.880 | 0.856 | 0.997 | 0.913 | 0.318 | 0.689 | 0.437 | 0.318 | 0.766 |
| `hybrid:0.5:0.0:none@vec60` | 1.000 | 1.000 | 0.936 | 1.000 | 1.000 | 0.932 | 0.878 | 0.644 | 0.674 | 0.644 | 0.896 |
| `hybrid:0.5:0.15:none@vec60` | 1.000 | 1.000 | 0.957 | 1.000 | 1.000 | 0.956 | 0.842 | 0.714 | 0.680 | 0.680 | 0.905 |
| `hybrid:0.5:0.3:none@vec60` | 1.000 | 1.000 | 0.974 | 1.000 | 1.000 | 0.974 | 0.768 | 0.752 | 0.674 | 0.674 | 0.905 |
| `hybrid:0.5:0.6:none@vec60` | 0.994 | 1.000 | 0.989 | 0.999 | 1.000 | 0.989 | 0.570 | 0.770 | 0.616 | 0.570 | 0.881 |
| `hybrid:0.5:1.0:none@vec60` | 0.828 | 0.996 | 0.901 | 0.871 | 0.998 | 0.928 | 0.365 | 0.713 | 0.465 | 0.365 | 0.785 |
| `hybrid:1.0:0.0:none@vec60` | 1.000 | 1.000 | 0.935 | 1.000 | 1.000 | 0.932 | 0.881 | 0.672 | 0.699 | 0.672 | 0.902 |
| `hybrid:1.0:0.15:none@vec60` | 1.000 | 1.000 | 0.955 | 1.000 | 1.000 | 0.955 | 0.851 | 0.735 | 0.703 | 0.703 | 0.911 |
| `hybrid:1.0:0.3:none@vec60` | 1.000 | 1.000 | 0.962 | 1.000 | 1.000 | 0.962 | 0.788 | 0.768 | 0.697 | 0.697 | 0.909 |
| `hybrid:1.0:0.6:none@vec60` | 0.994 | 1.000 | 0.974 | 0.999 | 1.000 | 0.973 | 0.604 | 0.789 | 0.640 | 0.604 | 0.886 |
| `hybrid:1.0:1.0:none@vec60` | 0.838 | 0.997 | 0.903 | 0.877 | 0.998 | 0.921 | 0.406 | 0.736 | 0.493 | 0.406 | 0.797 |
| `hybrid:2.0:0.0:none@vec60` | 1.000 | 1.000 | 0.935 | 1.000 | 1.000 | 0.932 | 0.880 | 0.697 | 0.720 | 0.697 | 0.907 |
| `hybrid:2.0:0.15:none@vec60` | 1.000 | 1.000 | 0.955 | 1.000 | 1.000 | 0.955 | 0.848 | 0.752 | 0.719 | 0.719 | 0.914 |
| `hybrid:2.0:0.3:none@vec60` | 1.000 | 1.000 | 0.962 | 1.000 | 1.000 | 0.962 | 0.789 | 0.785 | 0.711 | 0.711 | 0.912 |
| `hybrid:2.0:0.6:none@vec60` | 0.994 | 1.000 | 0.962 | 0.999 | 1.000 | 0.962 | 0.630 | 0.804 | 0.667 | 0.630 | 0.891 |
| `hybrid:2.0:1.0:none@vec60` | 0.845 | 0.997 | 0.889 | 0.882 | 0.998 | 0.907 | 0.470 | 0.755 | 0.541 | 0.470 | 0.809 |

Near-duplicate supersession by absolute cosine threshold (`tau`, held-out):

| arm | nomic/verbatim | nomic/unique | nomic/summary | minilm/verbatim | minilm/unique | minilm/summary | hard/verbatim | hard/unique | hard/summary | worst | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hybrid:1.5:0.2:none@vec60` | 1.000 | 1.000 | 0.958 | 1.000 | 1.000 | 0.960 | 0.823 | 0.735 | 0.693 | 0.693 | 0.908 |
| `hybrid:1.5:0.2:0.65@vec60` | 0.995 | 0.939 | 0.928 | 1.000 | 1.000 | 1.000 | 0.574 | 0.143 | 0.192 | 0.143 | 0.752 |
| `hybrid:1.5:0.2:0.7@vec60` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.996 | 0.763 | 0.312 | 0.372 | 0.312 | 0.827 |
| `hybrid:1.5:0.2:0.75@vec60` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.960 | 0.812 | 0.566 | 0.584 | 0.566 | 0.880 |
| `hybrid:1.5:0.2:0.8@vec60` | 1.000 | 1.000 | 0.998 | 1.000 | 1.000 | 0.960 | 0.822 | 0.724 | 0.691 | 0.691 | 0.911 |
| `hybrid:1.5:0.2:0.85@vec60` | 1.000 | 1.000 | 0.958 | 1.000 | 1.000 | 0.960 | 0.823 | 0.735 | 0.693 | 0.693 | 0.908 |

Any `tau` that fires on the hard profile (< 0.8) costs up to 0.50; ≥ 0.8 is a
no-op. Rejected.

## E6 — Does each hybrid component earn its place? (held-out)

| arm | nomic/verbatim | nomic/unique | nomic/summary | minilm/verbatim | minilm/unique | minilm/summary | hard/verbatim | hard/unique | hard/summary | worst | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hybrid:1.5:0.2:none/pool@vec60` | 1.000 | 1.000 | 0.958 | 1.000 | 1.000 | 0.960 | 0.822 | 0.735 | 0.693 | 0.693 | 0.908 |
| `hybrid:0.0:0.2:none/pool@vec60` | 1.000 | 1.000 | 0.992 | 1.000 | 1.000 | 0.995 | 0.772 | 0.663 | 0.628 | 0.628 | 0.894 |
| `hybrid:1.5:0.0:none/pool@vec60` | 1.000 | 1.000 | 0.938 | 1.000 | 1.000 | 0.937 | 0.874 | 0.653 | 0.700 | 0.653 | 0.900 |
| `cosine@vec60` | 1.000 | 1.000 | 0.962 | 1.000 | 1.000 | 0.962 | 0.820 | 0.552 | 0.587 | 0.552 | 0.876 |
| `hybrid:1.5:0.2:none/pool@sqlite` | 0.047 | 0.042 | 0.032 | 0.047 | 0.042 | 0.032 | 0.047 | 0.042 | 0.032 | 0.032 | 0.040 |

On `hard/summary`:

| arm | all | paraphrase | keyword | old | emotional | updated | mood_negative | obsolete wins | trap in top-3 | hit@3 95% CI | paired Δ vs baseline [95% CI] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hybrid:1.5:0.2:none/pool@vec60` | 0.69 | 0.61 | 0.86 | 0.62 | 0.72 | 0.33 | 0.61 | 0.67 | 0.02 | [0.666, 0.718] | — |
| `hybrid:0.0:0.2:none/pool@vec60` | 0.63 | 0.61 | 0.67 | 0.53 | 0.63 | 0.39 | 0.61 | 0.58 | 0.02 | [0.597, 0.657] | -0.065 [-0.078, -0.051] |
| `hybrid:1.5:0.0:none/pool@vec60` | 0.70 | 0.62 | 0.86 | 0.65 | 0.74 | 0.33 | 0.62 | 0.70 | 0.02 | [0.674, 0.725] | +0.006 [-0.009, +0.021] |
| `cosine@vec60` | 0.59 | 0.56 | 0.63 | 0.55 | 0.59 | 0.37 | 0.57 | 0.60 | 0.02 | [0.558, 0.613] | -0.106 [-0.130, -0.083] |
| `hybrid:1.5:0.2:none/pool@sqlite` | 0.03 | 0.02 | 0.05 | 0.00 | 0.04 | 0.00 | 0.01 | 0.00 | 0.35 | [0.022, 0.042] | -0.661 [-0.684, -0.639] |

Lexical (whole-word BM25) is worth +0.05 to +0.07 on the hard profile and
costs ~0.03 on clean embeddings (it lifts an obsolete version sharing the
keyword). Activation is mixed (+0.08 on `hard/unique`, −0.05 on
`hard/verbatim`, neutral on `summary`). The full hybrid has the best worst
case, so both stay; both weights are flagged for recalibration on real
embeddings (GPU H-R2). Candidate-pool IDF equals store-wide IDF on 1,128 of
1,131 rankings, so production computes BM25 over the pool.

## E-R — Rumination stress test: a query-independent emotional prior

Adding `w_emo·|valence|·intensity` looked good on the standard corpus
(emotional questions 0.83 → 0.93). With 60% of small talk tagged distressed
(a rough week), it drops non-emotional retrieval 0.642 → 0.580 and its
emotional-question gain vanishes (0.835 vs 0.841). **Rejected**: emotional
memories are retrieved by relevance like everything else.

## E7 — Value of write-time supersession (upper bound, held-out)

A perfect detector that closes a fact's validity when its revision is written:

| arm | all | paraphrase | keyword | old | emotional | updated | mood_negative | obsolete wins | trap in top-3 | hit@3 95% CI | paired Δ vs baseline [95% CI] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hybrid:1.5:0.2:none/pool@vec60` | 0.69 | 0.61 | 0.86 | 0.62 | 0.72 | 0.33 | 0.61 | 0.67 | 0.02 | [0.666, 0.718] | — |
| `hybrid:1.5:0.2:none/pool@vec60+validity` | 0.72 | 0.63 | 0.91 | 0.66 | 0.72 | 0.54 | 0.63 | 0.00 | 0.03 | [0.697, 0.750] | +0.032 [+0.023, +0.041] |
| `v1@vec60` | 0.41 | 0.18 | 0.86 | 0.33 | 0.53 | 0.04 | 0.20 | 0.64 | 0.20 | [0.390, 0.423] | -0.286 [-0.306, -0.266] |
| `v1@vec60+validity` | 0.44 | 0.18 | 0.95 | 0.36 | 0.53 | 0.22 | 0.20 | 0.00 | 0.22 | [0.422, 0.453] | -0.255 [-0.275, -0.234] |

It removes stale answers entirely (obsolete-wins 0.67 → 0.00) and lifts
updated-fact recall 0.33 → 0.54. This is the measured payoff of wiring
`TemporalMemoryStore`; see `07-future-research.md` §1.

## Latency (real `MemoryStore`, SQLite path, cold L1 cache per query)

| memories | policy | writes | p50 ms | p95 ms | p99 ms |
|---:|---|---|---:|---:|---:|
| 200 | actr_v1 | none | 4.6 | 5.97 | 9.09 |
| 200 | actr_v1 | 1 per 5 queries | 4.54 | 5.4 | 7.28 |
| 200 | hybrid | none | 2.98 | 3.38 | 5.59 |
| 200 | hybrid | 1 per 5 queries | 3.39 | 4.89 | 7.18 |
| 1000 | actr_v1 | none | 12.44 | 14.68 | 19.66 |
| 1000 | actr_v1 | 1 per 5 queries | 12.81 | 18.11 | 25.29 |
| 1000 | hybrid | none | 3.25 | 6.75 | 14.01 |
| 1000 | hybrid | 1 per 5 queries | 4.05 | 8.79 | 17.34 |
| 3000 | actr_v1 | none | 35.0 | 47.48 | 55.07 |
| 3000 | actr_v1 | 1 per 5 queries | 35.07 | 44.12 | 58.91 |
| 3000 | hybrid | none | 3.31 | 7.66 | 20.53 |
| 3000 | hybrid | 1 per 5 queries | 3.48 | 12.0 | 34.34 |
| 5000 | actr_v1 | none | 56.94 | 68.29 | 87.31 |
| 5000 | actr_v1 | 1 per 5 queries | 57.44 | 73.19 | 82.82 |
| 5000 | hybrid | none | 3.89 | 6.62 | 19.66 |
| 5000 | hybrid | 1 per 5 queries | 3.76 | 8.16 | 40.78 |

The first hybrid version re-read every embedding as JSON per query (p50
580 ms at 5,000 memories). `sqlite_vector_index.py` keeps parsed embeddings in
a float32 matrix per wing with O(1) staleness detection; agents warm it at
startup (~1 s at 5,000 memories, off the critical path).

## Reproduce

```bash
cd backend
PYTHONPATH=. python -m evals.cognitive memory --seeds tune \
    --experiments E1_baseline_paths E2_v1_ablation E3_alternatives E4a_hybrid_weights E5_v1_retune_sweep \
    --out /tmp/memory_tune.json
PYTHONPATH=. python -m evals.cognitive memory --seeds heldout \
    --experiments E3_alternatives E4b_supersession_tau E6_hybrid_ablation E7_validity_value \
    --out /tmp/memory_heldout.json
PYTHONPATH=. python -m evals.cognitive latency --queries 60 --out /tmp/latency.json
PYTHONPATH=. python -m evals.cognitive.report memory /tmp/memory_heldout.json E3_alternatives hard/summary
```

Runs in ~6 minutes on 4 CPU cores. Every report records the git SHA, seeds,
profiles and regimes. The tables above were produced at base commit `dac8d0a`
plus this change.

## Limits of this evidence

* Synthetic embeddings: relative conclusions (V1 vs hybrid, which terms
  matter) are robust across three profiles; absolute hit@3 is not a
  prediction of production accuracy. GPU H-R1..H-R3 settle it.
* Probes are single questions with one relevant memory; no multi-hop or
  multi-memory answers. The graph leg (PageRank) is not exercised: it was
  unreachable in production (M-4) and is not part of the hybrid. Its value is
  unmeasured (07 §2).
* No abstention: the hybrid always returns `limit` memories. Whether to
  return fewer when nothing is relevant is open (07 §6).
