# ADR-W1: temporal truth maintenance

**Status:** implemented behind `MEMORY_TEMPORAL_TRUTH_ENABLED` (default `false`).
- Critic rounds: two Codex critic rounds; round 2's five defects are fixed by the integrator.
- Held-out: obsolete-win (0.1148, one run) misses its frozen target of 0.10.
- Latency: the search path meets its ceiling.
- Still outstanding: the home-GPU BrainBench `llm_augmented` run. Enabling the flag is Aniket's decision.

## Context and decision

M-5 is a write-time truth problem. Semantic similarity and repetition cannot establish whether two statements describe an elaboration, a genuine change, a correction, or an unresolved conflict. W1 retains the typed `BeliefRecord` and `TemporalMemoryStore` rather than replacing them with string-only annotations: records already carry subject, predicate, object, confidence, provenance, valid-time bounds, and supersession/correction links. `classify_contradiction` is retained as the deterministic first pass but now compares valid-time order rather than assuming a later recorded timestamp means a real-world update.

`ReflectionService` extracts a structured fact, matches its normalized subject/predicate slot, and records an explicit `valid_from` (or a year grounded in the extractor's reason) separately from receipt time. When no effective date is extracted, receipt time is the conservative fallback; receipt time alone is not semantic evidence that an earlier fact ended. Value normalization (legal suffixes and acronym forms) runs before date precedence. Cosine similarity only nominates a neighbor; explicit correction, opposite polarity, or a clear change cue can decide deterministically. Words such as “now”, “still”, and “these days” alone cannot close a fact. Ambiguity reaches the local classifier over at most three neighbors, whose constrained prompt explicitly treats hedging and same-value restatements conservatively. No hosted inference is used. This honors DR-006's language-first ordering and DR-035's prohibition on intent detection.

The store is append-only. An update closes the prior active interval and creates a current assertion; history remains queryable. A correction marks the corrected claim `INVALIDATED`, which is omitted from both current and historical recall but retained for audit. Conflicts become disputed and reduce certainty; disputed rows claim a zero-duration validity window, so they do not overlap any truth interval. Later evidence can resolve a disputed slot into a fresh current projection. Temporal assertions are mirrored into existing MemoryStore metadata with `belief_id`, status, certainty, and validity bounds. Writes synchronize prior linked rows; search filters retrieved candidates from this metadata before returning them, without a second temporal-store lookup. Historical cues and explicit year windows keep superseded linked values reachable. First-person retrieval without a configured owner fails closed for linked facts; explicitly named subjects scope to matching linked candidates. The production database path comes from `runtime_state_db("temporal_memory.db")`; it never silently defaults to `:memory:`. No production 768-dimensional schema changed.

The feature flag defaults off until the held-out and full Lifesim memory acceptance gates are complete. The BrainBench switchboard has both `-temporal` and `+temporal` arms. The `llm_augmented` path is the intended production evaluation mode.

## Alternatives

| Alternative | Decision | Reason |
|---|---|---|
| Change the ranker or add a recency penalty | Rejected | The frozen M-5 evidence says ranker-only changes do not repair stale truth and can demote still-true facts. |
| Infer all relations with an LLM | Rejected | Unnecessary for clear language, nondeterministic, and higher cost; LLM is reserved for ambiguous top-three cases. |
| Use only cosine similarity | Rejected | Similarity finds neighbors but cannot distinguish update from correction or preserve valid-time semantics. |
| Reuse typed temporal records and mirror projection state into retrieved-memory metadata | Accepted | Preserves provenance and validity without touching the 768-d schema; candidate-local filtering avoids a second query on each turn. |
| Enable by default immediately | Rejected for now | Frozen held-out and full `llm_augmented` BrainBench evidence is not yet available. |

## Model-free results

Tune runs use seeds 1–20. The selected E8 configuration's `hard/summary` results are shown below; intervals are 95% bootstrap CIs from the benchmark. These tune results are development evidence only. Held-out seeds 101–130 were run once after implementation freeze and are appended below without retuning.

| Measure | V2 hybrid | E8 temporal detector | Frozen target | Status |
|---|---:|---:|---:|---|
| obsolete-win | 0.6556 [0.5778, 0.7389] | 0.0833 [0.0222, 0.1556] | <= 0.10 | Mean meets; CI upper bound does not |
| updated-fact hit@3 | 0.3111 [0.2500, 0.3722] | 0.5000 [0.4278, 0.5778] | >= 0.48 | Mean meets; CI overlaps target |
| paired hit@3 delta | n/a | +0.0301 [0.0191, 0.0421] | no regression | Positive on this stratum |
| paired obsolete-win delta | n/a | -0.0911 [-0.1081, -0.0748] | <= 0.10 absolute | Strong improvement; absolute CI still crosses target |
| false-closure rate | n/a | 0 / 2,853 in synthetic E8 fixture; production write-path 0 / 160 adversarial cases | <= 0.02 | Production tune slice meets; 20 hedged cases were disputed, not superseded |

The single held-out run (seeds 101–130, 30 scenarios, hard/summary) produced:

| Measure | V2 hybrid | E8 temporal detector | Frozen target | Status |
|---|---:|---:|---:|---|
| obsolete-win | 0.6741 [0.6185, 0.7259] | 0.1148 [0.0593, 0.1704] | <= 0.10 | Misses on mean and interval |
| updated-fact hit@3 | 0.3333 [0.2593, 0.4111] | 0.5037 [0.4370, 0.5741] | >= 0.48 | Mean meets; CI lower bound does not |
| paired all-case hit@3 delta | n/a | +0.0258 [0.0185, 0.0330] | no regression | Positive |
| paired obsolete-win delta | n/a | -0.0885 [-0.0974, -0.0788] | <= 0.10 absolute | Strong improvement, but E8 absolute rate misses |
| false-closure rate | n/a | 0 / 4,311 still-true facts (0.000) | <= 0.02 | Meets on held-out fixture |

Latency on the arm64 SQLite fallback, 5,000 linked rows and 40 cold-cache queries: flag-off P95 1.48 ms, flag-on P95 1.52 ms, +2.58%. This meets the +10% ceiling. The flag-on path filters candidate metadata by `belief_id` and mirrored state; it performs no temporal projection query.

The revision tune run covers seeds 1–20 and is preserved at `/tmp/w1a-memory-tune-r2.json`; the earlier held-out run at `/tmp/w1a-memory-heldout.json` was not repeated. The following hard/summary aggregate reports every non-temporal E1–E6 comparison against the V2 hybrid row from the same tune run. E1, E2 and E5 were designed around V1 controls, so their V2 comparison is the same-input E3 V2 arm, not a native baseline in those experiment definitions. E7 is the perfect validity-oracle temporal bound, not the shipped detector. No metric formula changed.

| Experiment | Comparator arm | hit@3 (95% CI) | obsolete-win (95% CI) |
|---|---|---:|---:|
| V2 reference | `hybrid:1.5:0.2:none@vec60` | 0.7091 [0.6867, 0.7309] | 0.6556 [0.5778, 0.7389] |
| E1 baseline paths | `v1@vec20` | 0.4571 [0.4356, 0.4767] | 0.6889 [0.5944, 0.7778] |
| E2 V1 ablation | `cosine@all` | 0.5818 [0.5502, 0.6146] | 0.6556 [0.5889, 0.7278] |
| E3 alternatives | `cosine@vec60` | 0.5818 [0.5502, 0.6146] | 0.6556 [0.5889, 0.7278] |
| E4a hybrid weights | `hybrid:2.0:0.0:none@vec60` | 0.7197 [0.6977, 0.7411] | 0.7389 [0.6778, 0.8057] |
| E5 V1 retune sweep | `v1_retuned:12:2@vec60` | 0.6313 [0.6071, 0.6562] | 0.7222 [0.6333, 0.8000] |
| E6 hybrid ablation | `hybrid:1.5:0.0:none/pool@vec60` | 0.7109 [0.6894, 0.7319] | 0.7333 [0.6722, 0.8056] |

The E8 tune metrics remain 0.0833 [0.0222, 0.1556] obsolete-win and 0.5000 [0.4278, 0.5778] updated-fact hit@3 for hard/summary. The prior synthetic fixture's false-closure count is not evidence about production classification; the production write-path measurement is the adversarial result above. The production false-closure artifact is `/tmp/w1a-false-closure-r2.json`.

## Critic revision findings and fixes

| Finding | Fix and regression evidence |
|---|---|
| Temporal cue alone changed a same-slot value | `now`/`still`/`these days` now only nominate a candidate; the E8 detector requires value difference plus clear change language or polarity evidence. The regression uses `Acme Corporation` → `Acme`, cosine 0.95, context “Ari still works there now.” and expects abstention. |
| Dated paraphrase superseded a true value | Common legal suffixes and acronym forms are compared before date precedence. `Acme Corporation` → `Acme` remains `ELABORATION` and the existing row stays `ACTIVE`. |
| Direct conflict rows overlapped | Direct and classified disputes now set both disputed records to zero-duration intervals. The Hypothesis invariant includes disputed rows, and the direct `[1,10)` / `[5,∞)` case asserts neither disputed row claims an interval. |
| Search exceeded the latency budget | The critic measured 1.74 ms off / 2.01 ms on (+15.5%) with a second per-query temporal projection lookup. The revised implementation mirrors `belief_id`, status, certainty and validity at write time, filters the existing candidate pool in MemoryStore, and removes the foreground projection lookup. The revised 5,000-row / 40-query arm64 run measured 1.48 ms off / 1.52 ms on (+2.58%). |
| E8 fixture understated production false closure | Production `record_assertion` with the deterministic detector and offline LLM stub was measured on 20 tune seeds × 8 adversarial categories. Superseded false closures were 0/160; the 20 hedged assertions were disputed, and this is reported separately. The prior 0/2,853 remains labeled synthetic only. |
| Production DB path default | `CognitiveService` now obtains the configured default with `runtime_state_db("temporal_memory.db")`, rather than constructing a relative `data/` path. |

## Detector arms

| Detector | Scope | False closure | Evidence / status |
|---|---|---:|---|
| E8 deterministic cosine + polarity/negation | Tune seeds 1–20, synthetic fixture | 0 / 2,853 (0.000) | True-closure recall 318 / 540 (0.589) |
| E8 deterministic cosine + polarity/negation | Held-out seeds 101–130 | 0 / 4,311 (0.000) | True-closure recall 470 / 810 (0.580) |
| Production deterministic E8 only | Tune adversarial cases, 7 same-subject categories | 0 / 140 superseded (0.000) | Direct detector output; no LLM call |
| Top-three LLM classifier only | Same 140 still-true cases, offline conservative stub | 0 / 140 superseded (0.000) | Stub result is not a real-model rate |
| Production `record_assertion` + deterministic E8 + offline LLM stub | Tune seeds 1–20; 160 paraphrase/abbreviation/still/now/these-days/hedging/second-person/dated-restatement cases | 0 / 160 superseded (0.000); 20 / 160 disputed | Ambiguous hedging abstains from closure; no real-model claim |
| Top-three local LLM classifier | Ambiguous cases in `llm_augmented` only | Not measured with a real model | Stub-tested offline; home-GPU evaluation belongs to reviewer |

## BrainBench offline check

The supplementary preloaded-fact fixture (20 cases; no LLM fact encoding or consolidation) produced:

| Arm | Current accuracy | Historical update hit | Correction history leak |
|---|---:|---:|---:|
| `-temporal` | 0.00 | 0.00 | 0.00 |
| `+temporal` | 1.00 | 1.00 | 0.00 |

This is a wiring fixture, not the Lifesim memory suite. The r2 runner also executed `--mode architecture_only --suites attention --arm +temporal --seeds 1000 --horizons 1m --pairing cross` offline. The requested `architecture_only` memory-suite run cannot be made valid without changing frozen DR-037: the runner rejects `--mode architecture_only --suites memory` with `legal suites: attention, trust, proactive, bargein, resources`, and memory is defined for `llm_augmented`. No claim is made about stale-trap significance, Holm comparisons, other memory categories, or real-model behavior until the reviewer runs the full suite on home-GPU.

## Neuroscience fidelity dispositions

| Finding | Disposition |
|---|---|
| NF-1 base-level activation | Deferred. No ACT-R equation change; W7/W12 must provide a published-form-pinned unit test and a non-regressing benchmark arm before adoption. |
| NF-2 retrieval strengthening | Deferred. W1 does not conflate candidate exposure with successful retrieval; reference-history storage and a measured arm remain separate work. |
| NF-4 archive/pruning | Preserve append-only temporal rows. DR-007 archive/retention policy is not changed here; no ACT-R deletion rule is introduced. |
| NF-6 temporal contradiction wiring | Implemented: typed facts now pass through classification, append-only validity transitions, and retrieval projection. |
| NF-7 retrieval-conditioned attenuation | No formula change. Existing attenuation remains outside this work; no neuroscience correction is claimed. |
| NF-8 mood-congruent retrieval | Removed the inaccurate active-path mood-congruent description; affect is not added to the hybrid ranker. |
| NF-9 lexicon association | No Hebbian formula change. Co-occurrence remains a heuristic; no published-form test or benchmark justifies a change. |
| NF-10 graph-edge association | No graph/PPR ranker change. The active hybrid path does not consume those helpers and no registered gain supports wiring them here. |

## Cold critic, round 2, and the integrator's fixes

Round 1 of this tournament ran blind on both variants. Its verdict was
NEITHER, and this variant (W1-A) was revised. Round 2 was a fresh Codex
session with this ADR withheld. It re-judged the revision cold, against the
frozen rubric, on tune seeds 1-20 only, and returned **FAIL** with five
reproduced defects.

This is the last critic round (two per unit). The integrator (Claude)
fixed all five on the brain-v3 branch. Each fix has a test in
`tests/test_temporal_truth_critic_r2.py` that fails on the code the critic
reviewed: 18 of its 25 tests fail there, and the other 7 are guards that
pass on both versions. Red was checked by running that module with the
pre-fix `temporal_detector.py`, `memory_store.py`, `temporal_store.py`,
`learning.py` and `pipeline.py` swapped back in.

| # | Sev | Defect | Fix |
|---|---|---|---|
| 1 | HIGH | Any change word anywhere in the extractor evidence decided UPDATE. "Ari moved house last month but still works at Acme" closed Acme. Hedged ("maybe switched") and negated-change ("did not move") evidence did too. | `temporal_detector.py`: the deterministic arm abstains on hedges and continuity (`still`, `stayed`, a negated change verb). The top-three classifier then decides, and conflicting evidence is held as DISPUTED, not closed. A change word counts only in the clause that names the new value, or in a one-clause extractor reason. Guards confirm that real updates ("Ari switched to Globex", "switched jobs recently") still decide UPDATE. |
| 2 | MED | "What did I drink?" hid every superseded fact: the memory-store filter's history cues lacked past-tense questions. The two stores parsed temporal intent separately. | One parser, `app/state/temporal_intent.py`, used by both stores. Past-tense questions reach history unless the query also asks about the present ("now", "currently", "these days"). The costs are asymmetric: a miss hides history, which DR-005 forbids, while a false hit only shows past values next to the current one. |
| 3 | HIGH | Memories with no belief link passed as current. That covers everything written before the flag, and every episode that mentions a fact. The revision had also left `_temporal_belief_memories` with no call site, so typed history was never retrieved. | Stage 3 now runs `_reconcile_temporal_memories`. For a present-tense query, an unlinked row naming a superseded value of the resolved slot is dropped. The typed current beliefs are added for present queries, and the typed history for historical ones. An ambiguous owner changes nothing. |
| 4 | MED | Retrieval trusted a `temporal_belief` link from caller metadata: a writer could hide any memory by forging `SUPERSEDED`. A malformed `valid_until` raised `ValueError` in search. | `add_memory` drops a caller-supplied `temporal_belief`. The link is set only through the keyword-only `temporal_link` that `index_temporal_belief` passes. Link timestamps are parsed as finite floats, and a malformed window excludes nothing. |
| 5 | MED | Each fact write loaded the slot's whole history (unbounded `fetchall`) and re-indexed every row: 100 old rows meant 101 index calls. | Slot queries take `statuses` and `limit`. `record_assertion` loads only the current rows (at most 32) and the three newest. A `belief_index_marks` table records what is already mirrored, so each row is indexed once. A backlog of unlinked rows drains 8 per write. |

The reconciliation adds work to the foreground turn. It sits outside
`search_memories`, so the search comparison cannot see it. The new
`measure_temporal_reconcile` (`python -m evals.cognitive latency
--with-temporal`, key `temporal_reconcile`) reports it on its own.

**Re-measured after the fixes (tune seeds 1-20; held-out not rerun):**

| Measure | Result | Target |
|---|---|---|
| E8 `hard/summary` obsolete-win | 0.0833 [0.0222, 0.1556] (V2 0.6556) | <= 0.10: pass |
| E8 `hard/verbatim` obsolete-win | 0.2722 [0.1889, 0.3611] (V2 0.6500) | <= 0.10: **fail** |
| E8 `hard/unique` obsolete-win | 0.2500 [0.1667, 0.3444] (V2 0.6167) | <= 0.10: **fail** |
| E8 `hard/summary` hit@3 | 0.7392 [0.7168, 0.7613] | V2 0.7091 |
| Production write path, false closure | 0 / 160 still-true (20 held DISPUTED) | <= 0.02 |
| Search P95, flag off vs on, 5,000 linked rows | -0.94% | within +10% |
| Stage 3 reconciliation, 5,000 beliefs | p50 1.1 ms, p95 1.9 ms per turn (flag on only) | DR-024: 300-500 ms per turn |

E8 numbers are unchanged, as expected: E8 runs the lab's synthetic
detector (`evals/cognitive/lab.py`), not the production one. The production
detector is measured by the adversarial write-path suite.

**Where the rubric's obsolete-win row is read, and what it says.** The
critic pooled obsolete-win across all profiles and regimes (0.3247) and
failed the row. The frozen rubric's V2 reference, "0.61-0.67 (hybrid)", is
exactly the spread of V2's three `hard` cells: verbatim 0.6500, unique
0.6167, summary 0.6556. It is not the pooled value; V2 is 0.77-0.96 in the
`nomic_like` and `minilm_like` cells.

The row therefore covers all three `hard` cells, not only `hard/summary`,
which is the one cell this ADR's model-free tables above report. On tune,
E8 meets the <= 0.10 target in `hard/summary` (0.0833) and **misses it in
`hard/verbatim` (0.2722) and `hard/unique` (0.2500)**. The held-out
`hard/summary` value is 0.1148, also a miss. The obsolete-win row is
**not met**.

The `minilm_like` cells show the same weakness more strongly. Relative to
V2, E8 cuts obsolete-win by:

| Cells | Cut |
|---|---|
| `nomic_like` | 100% (to 0.0) |
| `hard` | 58-87% |
| `minilm_like` | only 10-22% (0.61-0.86 remain) |

The synthetic detector's gating depends on how well the embedding
separates a changed value from a restatement.

**Taken from the losing variant (W1-B):** the runtime-state default for
the temporal database (`runtime_state_db`, never `:memory:`), and
candidate-local filtering of linked rows inside MemoryStore. Both are now
in this implementation.

## Limits and follow-up

The deterministic detector is language- and embedding-dependent; novel paraphrases can abstain or be misclassified. The local LLM ambiguity route has only an offline stub test. Slot identity is normalized text, so coreference and entity resolution are not solved. With no owner mapping, first-person linked facts deliberately fail closed; deployment should configure `TEMPORAL_MEMORY_SUBJECT`. Unlinked memories are checked against the projection at retrieval by value mention, so a row that paraphrases a superseded value without naming it can still surface for a present-tense query. Year parsing handles explicit before/after/in/during plus a four-digit year, not arbitrary dates. The single held-out cognitive run is complete and misses obsolete-win; the full BrainBench `llm_augmented` run, Holm significance, and real-model false-closure rate remain outstanding. The flag stays off.

**Spec limitation:** `architecture_only` and the full `memory` suite are mutually disallowed by the frozen runner/DR-037 contract. The dev fixture above is the strongest valid offline switchboard comparison available without changing that decision.
