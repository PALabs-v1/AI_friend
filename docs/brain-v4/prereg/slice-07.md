# Preregistration: slice 7 (belief writer for `TemporalMemoryStore`)

**Status: PROPOSED (2026-10-30). Nothing here is approved.** Written for Aniket. Not yet committed; to be committed before any slice 7 code or run. Per D-047, no model-backed run happens until Aniket approves or changes the numbers below. Deterministic gate tests and the build behind the flag are not blocked. Every numeric margin is marked PROPOSED with its reason.

## Claim under test

Today nothing writes a belief. `TemporalMemoryStore` is built at `backend/app/cognitive/core.py:91` and handed to the pipeline (`pipeline.py:111`), but `store_belief` (`backend/app/state/temporal_store.py:181`) and `apply_contradiction` (`:267`) have no caller outside the store (D-023). After the build, with the flag on, each user turn is read once by a named `ModelRole`, the persistent facts it states are written as `BeliefRecord`s with provenance, and a changed fact supersedes the old one without deleting it (Architecture Invariant 9). Slice 8 (supersession at read) depends on this data existing.

The claim is about the writer only: does it capture what the user said, and does the store keep history when a fact changes. It is not a claim that replies get better (that is slice 8).

## What the code already does (evidence, read today)

- `BeliefRecord` (`memory_records.py:45-57`) has subject, predicate, object, `valid_from`, `valid_until`, `recorded_at`, `confidence`, `status`, `superseded_by`, `contradicts_id`, `provenance` (free string). **There is no source-turn column.** The turn id has to ride in `provenance`, or the table gets a column (a contract change, see Open item 3).
- `classify_contradiction` (`memory_records.py:99-150`) needs the same subject and predicate. It returns UPDATE only if the new record has `confidence >=` the old one and a later `valid_from` or `recorded_at` (`:140`). A hedged new statement (low confidence) over a plain old one returns CONFLICT, and the store marks both DISPUTED. So confidence cannot be a free model float; the build derives it (below).
- UPDATE raises `InvalidIntervalError` if the new `valid_from` is before the old one (`temporal_store.py:288`). A user saying "I used to live in Oslo" after "I live in Bergen" is that case. The writer must catch it as an explicit outcome.
- CORRECTION stamps `valid_until` with wall-clock `time.time()` (`temporal_store.py:311`), not the stream time. A replay of simulated turns therefore gets a `valid_until` in the real present. The build passes stream time or the eval compares order, not stamps. Either way a test pins it.
- The existing LLM fact extractor, `learning.py:195-238`, feeds Neo4j and **swallows failure**: a non-list becomes `[]` (`:225-228`), a parse failure returns `[]` (`:567-577`), and the outer `except` only logs (`:237-238`). Slice 7 must not copy that. It is a different store; slice 7 does not touch it.
- `ModelRole.INTERPRETATION` exists (`model_roles.py:55`), needs structured output and a 4096 context (`:153`), and slice 13 already routes the other fact extractor through it. `validate_execution_result` (`:346`) and `ensure_committable` (`:391`) are the existing gate for a role result.
- The Claude Code client logs and ignores `options_override` keys it does not support (`claude_code_client.py:72-82`). Whether temperature can be lowered on the frontier tier is unverified; the noise band below is measured at whatever the client allows.

## Design under test

**Latent part (a prompt plus an eval):** read one user turn, emit candidate assertions.
**Deterministic part (code plus gate tests):** everything else. Schema check, evidence-span check, subject and predicate normalization, time-expression resolution, confidence mapping, contradiction handling, id, idempotency, failure recording.

### Extraction record (what the model returns)

A JSON object `{"beliefs": [ ... ]}`. An empty array is a valid "nothing to record". Each element:

| Field | Type | Filled by | Rule |
|---|---|---|---|
| `subject` | string | model | `user` for first person, else the name or relation as written ("Kavya", "my sister") |
| `predicate` | string | model | from the frozen list `belief_predicates.json`, else `other` |
| `object` | string | model | the value as written |
| `evidence_span` | string | model | an exact substring of the turn; code rejects the record if it is not |
| `valid_time_kind` | `stated_now` / `past` / `future` / `unspecified` | model | |
| `valid_from_expr` | string or null | model | the time phrase verbatim ("last month", "since 2020"), never an ISO date |
| `polarity` | `affirm` / `correction` | model | `correction` only on an explicit "no, I meant" or "actually" style repair |
| `hedged` | bool | model | "I think", "maybe", "probably" |
| `confidence` | float | model | recorded in provenance for audit, **not used** |

Code adds: `source_turn_id` and `session_id` (from `session_state.turn_id`, used at `pipeline.py:895`), `record_id`, stored `confidence`, resolved `valid_from`.

- **Stored confidence is derived:** 0.9 plain, 0.6 hedged, 0.5 for `other` predicates and for records variant B marks unsure. Reason: `classify_contradiction`'s `>=` test (`memory_records.py:140`) makes a model-chosen float an uncontrolled switch between UPDATE and DISPUTED.
- **Time is code, not model.** A small resolver (stdlib plus `dateutil.relativedelta`, which is installed) handles "today", "yesterday", "last week/month/year", "N units ago", "in <month> <year>", "since <year>". Anything else falls back to the turn time and records `time_resolved=false` in provenance. No date arithmetic in the model reply.
- **Predicate list:** a frozen JSON file with each predicate's cardinality. Single-valued predicates (`lives_in`, `works_at`, `partner_is`, `favorite_food`, `usual_drink`, `favorite_cuisine`, `favorite_music`, `exercise_routine`, and so on) may supersede. Multi-valued predicates (`likes`, `hobby`, `knows`) are append-only and never call `apply_contradiction`. `other` is stored with confidence 0.5 and never supersedes. Naming singular preferences `favorite_*` and `usual_*` matches the lifesim labels ("usual drink", "favorite dish", `probes.py:14-40`) without making real "I like jazz" then "I like classical" look like a contradiction.
- **Subject handling is lexical.** `user`, or `person:<lowercase name>`, or `user.<relation>`. No alias merging ("my sister" = "Kavya") in slice 7: there is no labeled data to measure it (see "What the labels cannot show").
- **Provenance string:** `conversation|turn=<turn_id>|session=<id>|extractor=<variant>@v1|span=<sha256[:8]>|time_resolved=<bool>|model_conf=<f>`. Parsed by one helper. If Aniket prefers a real column, that is a schema change to `temporal_store.py:_initialize_schema` plus a contract entry (Open item 3).
- **`record_id`** = `bel_` + first 24 hex of sha256 of `session|turn|subject|predicate|object`. Re-running the same turn cannot double-write; `DuplicateRecordError` (`temporal_store.py:485`) is caught and counted as `duplicate_skipped`, which is a counted outcome, not a silent skip.

### ModelRole

`ModelRole.INTERPRETATION` for extraction (structured-output role, same role the other fact extractor already uses). Variant B's verification pass uses `ModelRole.EVALUATION` (judges a claim, needs structured output, `model_roles.py:162`). Slice 13's AST gate (`tests/test_model_role_routing.py`) already fails any call without a role, so this is enforced for free.

### Malformed output is an explicit failure

The extractor returns `Extracted(records)` or `Failed(reason, raw_excerpt)`. Reasons are a closed enum: `no_json`, `not_object`, `schema_violation`, `span_not_in_turn`, `unknown_subject_form`, `llm_error`, `timeout`, `role_not_committable` (from `ensure_committable` raising `RoleResultRejected`). A valid `{"beliefs": []}` is **not** a failure; a missing key, a bare list or prose is.

Per failure: (1) WARNING log and a `MEASURE_TRACE` event `component=belief_extractor event=failed reason=...` (same style as `role_trace.py`); (2) one row in a new `belief_extraction_log` table, `CREATE TABLE IF NOT EXISTS` in the temporal DB (additive, nothing existing changes); (3) no belief written for that turn. A record that fails its own check (span not in the turn) drops that record and logs `record_rejected`; the other records in the batch still write. In the eval, failed calls count as zero recall and stay in every denominator.

### Where it runs

`pipeline.py` stage 10 ends at `:929`, then `pipeline_telemetry` (`:932`) and the terminal done chunk (`:935`). The writer is scheduled as a background task there, kept in a set so it is not garbage collected, the way `_system2_task` is (`:104`, `:306`). It is not awaited before the done chunk, so reply latency does not change. Only `USER_MESSAGE` events, all intents (`_check_reflection_needed` at `:695` filters to CHAT and REMEMBER, which would skip `share_fact` turns, so the writer does not reuse that filter). Input is the user's text wrapped as untrusted content, as `learning.py:32` does for its extractor. Output cannot reach any store field other than the ones above.

### Conflict handling (history preserved)

For a single-valued predicate, look up the active belief with the same subject and predicate (`query_current_beliefs(subject)` filtered by predicate; `temporal_store.py:212`).

| Situation | Action |
|---|---|
| none active | `store_belief` |
| same object | `classify_contradiction` returns ELABORATION; `apply_contradiction` adds a reinforcement row, no new belief row |
| different object, `stated_now` or `future`, later valid time | UPDATE: old row gets `valid_until`, status SUPERSEDED, `superseded_by`; new row ACTIVE |
| different object, `polarity=correction` | CORRECTION: old row INVALIDATED (kept), new row ACTIVE |
| different object, same time or lower confidence | CONFLICT: both DISPUTED, confidence halved (the store's own rule, `temporal_store.py:322` and `:332`); nothing deleted |
| `past` statement older than the active belief | not `apply_contradiction` (it would raise at `:288`). Insert as a closed historical row (`valid_until` set, status SUPERSEDED), counted as `historical_insert` |

No code path deletes or overwrites a belief row. The property test (below) enforces that.

### Flag

Config field `BELIEF_WRITER_ENABLED: bool = False`, next to `TRUST_EVIDENCE_MODEL_ENABLED` (`config.py:244`). It is not the switchboard's reserved `MEMORY_TEMPORAL_TRUTH_ENABLED` (W1), which is the read side and belongs to slice 8. BrainBench arm: `+belief-writer`. Flag off: no extra LLM call and the temporal DB file is byte-identical after a turn (tested). Rollback: set the flag false. Rows already written stay (history is never deleted); they carry `extractor=` in provenance so they can be filtered or removed from an offline copy.

## Gold data: what exists and how much (measured today)

The lifesim generator is deterministic and free (`python -m evals.lifesim panel ...`, run from `backend/`). Gold for slice 7 is `Annotation.claims` (`schema.py:Claim`: entity, attribute, value, truthful, about, assertion_id). I generated panels of 12 archetypes and counted with a scratch script (not yet committed; the slice's eval commits the real counter). User turns only; no assistant replies exist in the stream.

Claims split into two kinds. **Plan claims** (attributes `status`, `when`, `kind`, `what`, `with`, `where`) are plans and events, not persistent facts, and are about 80% of all claim records at 1y (5,258 of 6,537). **Slot claims** (city, employer, partner, drink, and so on) are what a belief store holds. Only slot claims are gold for slice 7.

| Panel (12 archetypes each) | Turns | Slot claim records | Unique slot assertions | Slot supersession pairs (both ends stated) | Slot correction turns | Slot claims with `truthful=False` |
|---|---:|---:|---:|---:|---:|---:|
| tune, 6m (seeds 2000-2011) | 14,742 | 628 | 265 | 53 | n/c | 4 |
| dev, 6m (1000-1011) | 14,916 | 676 | 232 | 38 | n/c | 5 |
| validation, 3m (3000-3011) | 7,833 | 494 | 176 | 30 | n/c | 2 |
| validation, 1y (3000-3011) | 30,334 | 1,279 | 444 | 118 | 12 | 12 |

(n/c: not counted.) Hedged slot claims: **0** in every panel. Slot claims about the user themself: 396 of 1,279 at 1y; the rest are about third parties. Only about 3.5% of validation 1y turns (1,076 of 30,334) carry a slot claim, so scoring every turn by model would spend about 28 calls per labeled turn.

### Is this enough to fix a floor?

For recall of persistent facts and for the overall write path: yes, enough to fix floors with honest intervals (444 unique assertions, 1,279 records at 1y). For the pieces the product depends on most it is thin:

| Quantity | n | 95% Wilson interval if the true rate were 0.70 / 0.80 (illustrative) | Verdict |
|---|---:|---|---|
| unique slot assertions | 444 | 0.656 to 0.741 / 0.760 to 0.834 | enough for a floor, before clustering |
| same, if effective n is 296 (design effect 1.5 for 12 personas, an assumption) | 296 | 0.645 to 0.749 / 0.751 to 0.842 | the interval the cluster bootstrap will likely show |
| supersession pairs | 118 | 0.616 to 0.778 / 0.715 to 0.859 | floor possible, wide |
| slot corrections | 12 | 0.391 to 0.862 / 0.552 to 0.953 | **no floor**; report counts only |
| hedged slot claims | 0 | none | **no floor, no calibration of hedged to confidence possible** |

The headline intervals are cluster bootstrap over the 12 personas (claims inside one persona share vocabulary and templates), not Wilson on pooled claims. Wilson is shown for scale only.

## Arms

| Arm | What it is |
|---|---|
| current | main, flag off (writes nothing; the baseline for "no beliefs exist") |
| variant A | single pass: one `INTERPRETATION` call per turn plus the deterministic checks |
| variant B | A plus a verification pass: for each surviving record a second `EVALUATION` call answers supported / not supported / unsure from the turn text alone; "not supported" is dropped and logged, "unsure" is kept at confidence 0.5 and cannot supersede |
| gold-replay control | the oracle claims (not the model) written through the same conflict code, to test the store path with no model in the loop |
| shuffled control | each variant run with turn texts shuffled across turn ids (seeded); recall must collapse, which proves the metric measures the text and not the id pattern |

The winner is chosen by a cold critic plus the selection rule below. The loser is deleted, not kept behind a flag.

## Measures and proposed pass rules

**Splits (enforced by `evals/lifesim/splits.py`).** Prompt work and the predicate list use the tune split (seeds 2000-2199). The tournament is scored on tune panel seeds 2000-2011. The winner's floors are scored once on validation seeds 3000-3011, 1y. Held-out seeds (9000-9099) are not touched by this slice. Gold matching rules (surface name to entity id via `world.json`, attribute to predicate map, value normalization) are written into the eval and frozen before the first model run. The scorer may read `world.json`; the extractor never does.

**Sampling (so the run is affordable).** The extractor sees each turn alone, so turns are independent for extraction scoring. Sample all slot-claim turns plus a seeded random 2 non-claim turns per claim turn, with design weights (non-claim weight = total non-claim over sampled non-claim). Validation 1y: 1,076 + 2,152 = **3,228 calls per arm**. Tune 6m: 519 + 1,038 = **1,557 calls per arm**. Conflict handling is scored separately on full oracle sequences with no model (gold-replay), so sampling does not break it.

**Budget.** Tournament on tune: A 1,557, B at most 3,114 (one extra call per kept record, assumed at most 1 per turn; unmeasured). Winner on validation: 3,228 (A) or at most 6,456 (B). Noise band per D-048: 5 repeats of a fixed 10% subset of the validation sample (323 turns): 1,615 (A) or at most 3,230 (B). Total **about 9,500 to 14,400 calls** depending on which variant wins. Seconds per call through local Claude Code are unmeasured; hours are calls times that:

| Seconds per call | 9,514 calls (A wins) | 14,357 calls (B wins) |
|---:|---:|---:|
| 5 | 13 h | 20 h |
| 10 | 26 h | 40 h |
| 20 | 53 h | 80 h |

Runs are serial, unattended, resumable by turn id. Open item 2.

### Rules

1. **Baseline first.** The shuffled control and a 3-turn smoke on tune are run and recorded before any floor is tested. Noise band per D-048 on the 10% subset.
2. **Failure rate (proposed):** Failed calls / calls at most **0.02**, and the one-sided 95% upper bound at most **0.04**. Reason: a writer that fails 1 turn in 25 loses 4% of facts silently-by-volume even though each is logged. n is large (3,228), so this interval is narrow.
3. **Recall (proposed):** gold-matched slot assertions / gold slot assertions, cluster-bootstrap 95% lower bound at least **0.65**. Reason: a floor is a "can fail" bar, not the target. Templated, declarative user text at the frontier tier should clear it easily; if it does not, extraction is broken. An extractor with true recall near 0.72 would usually clear it (observed 0.72 with a plus or minus 6 point clustered interval gives a lower bound near 0.66); one near 0.60 would usually fail. No data exists to set it higher; the tune split result may raise it before validation (rule 9).
4. **Supported precision (proposed):** the share of extracted in-vocabulary records the turn actually supports, lower bound at least **0.85**. Label agreement alone understates it: the generator labels only modeled assertions, so a correct "I had pasta for lunch" extraction scores as a false positive. So precision is label-matched records plus a human audit of unmatched ones. **Audit:** 100 randomly sampled unmatched extracted records from the winner on validation (seeded), shown with their turn text and nothing else; the rater marks supported / not supported. At 30 s each that is **50 minutes** for Aniket, and a second rater on a 30-record overlap (15 minutes) for agreement. Basis for 0.85: a belief store is read as truth, so a false belief costs more than a missing one; with n=100 audited, an observed 92 of 100 gives a Wilson lower bound near 0.85.
5. **Current value on updated slots (proposed):** over the 118 validation slots that have a supersession pair, the store's active belief equals the gold current value at end of stream, lower bound at least **0.55**. Reason: needs the latest statement extracted, so it tracks recall, and n=118 gives about plus or minus 9 points. Corrections (n=12) are reported, not floored.
6. **History preserved (deterministic, zero tolerance):** for every update where both statements were extracted, the old row still exists with status SUPERSEDED or INVALIDATED and `valid_until` set; the belief row count never decreases across a run; no `record_id` disappears. 100%, no interval, because it is a property of code.
7. **Gold-replay (deterministic, zero tolerance):** feeding the oracle claims through the conflict code reproduces the gold current value for all 118 validation update pairs, all 12 slot corrections, and the same on tune and dev. Any miss is a store or classifier bug and is fixed and reported, not floored. This is where the `>=` confidence trap and the `InvalidIntervalError` path show up if the build mishandles them.
8. **Flag off (deterministic):** temporal DB byte-identical, zero extra LLM calls.
9. **Floors may be raised, never lowered, from the tune result.** After the tune tournament and before the validation run, Aniket may raise rules 3 to 5 using the tune numbers. Lowering a floor after seeing any result voids the run. Validation is run once.
10. **Tournament selection (proposed):** B beats A only if its supported precision is at least **0.05** higher, that gap exceeds the larger of the two noise bands (D-048), and its recall is no more than **0.03** lower. Otherwise A wins on cost and simplicity. Why: B doubles calls; 0.05 is about the interval half-width at n=265 unique assertions, so a smaller gap cannot be told from noise.
11. **Shuffled control:** recall of the winner on shuffled turn texts must be at most 0.10 (proposed; any value near the real arm means the scorer is matching something other than the text).

### Gate tests (deterministic, local, free, under 2 s)

A fake extractor returns fixed JSON. Cases: write; ELABORATION; UPDATE; CORRECTION; CONFLICT; past statement older than the active belief (no raise, one `historical_insert`); empty `beliefs` array (no write, no failure); each `Failed` reason produces a log row, a trace event, no belief; span not in turn drops only that record; hedged-over-plain stays UPDATE because confidence is derived; same turn twice writes once; injection text in the turn cannot add a record without a matching span; flag off leaves the DB bytes unchanged; a property test over random sequences of records asserts rule 6; the CORRECTION wall-clock stamp is pinned. A drift test fails if `belief_predicates.json` and the gold attribute map disagree.

## What the labeled data cannot show

- **Real language.** Turn text is built from frozen template banks (`banks/MANIFEST.json`, 39 families, 402 templates in total, 6 to 20 per family). Recall on templates overstates recall on free speech. No sarcasm, no negated facts, no multi-fact run-on sentences beyond the templates.
- **Context.** The stream has user turns only (`speaker` is always `user`). Pronoun and reference resolution against the previous assistant reply is untested, and that is the pipeline's real input.
- **Hedging.** Zero hedged slot claims. The hedged-to-confidence mapping is untested against any label, and `classify_contradiction`'s behavior on hedged updates is covered by gate tests only.
- **Corrections.** 12 at 1y. No floor is possible.
- **Aliases.** "My sister" versus "Kavya" as one subject. The store would hold two subjects; nothing measures that.
- **Multi-valued facts.** The generator's attributes are single-valued, so the append-only policy for `likes` and `hobby` has no gold. It is covered by gate tests, not by a score.
- **Unlabeled true facts.** Only the 100-record audit sees them.
- **Wrong or lying users.** 12 `truthful=False` slot claims at 1y. The writer records what was said, with provenance; whether to believe it is not measured.
- **Third-party privacy.** About two thirds of slot claims are about other people. Storing them is a product choice, not a measured one (Open item 7).
- **Cost and latency** at the local 4B to 9B tier, and any effect on replies (slice 8).

## Not claimed

No claim about the local 4B to 9B tier (no GPU, D-036). A frontier-tier result is a test-tier result (D-003, D-024). No claim that replies improve. No claim about hedged facts, corrections beyond counts, aliases, or non-English text.

## Open for Aniket

1. **The numbers.** Failure rate at most 0.02 (upper bound 0.04); recall lower bound 0.65; supported precision lower bound 0.85; updated-slot current value lower bound 0.55; shuffled-control recall at most 0.10; B-over-A margins 0.05 and 0.03. Approve, replace, or tell me which to raise.
2. **Budget.** About 9,500 to 14,400 calls. At an unmeasured 5 to 20 s per call that is 13 to 80 hours of unattended Mac time. Approve the sampling (2 non-claim turns per claim turn, 10% noise subset), or choose a smaller panel. I will measure seconds per call on 20 turns first (no result is scored from them).
3. **Where the source turn lives.** In the `provenance` string (no schema change, proposed), or a new column on `beliefs` (contract change, needs a migration note).
4. **Audit raters.** You for 100 records (50 minutes) and one more person for 30 (15 minutes). OK?
5. **Trigger.** All `USER_MESSAGE` turns (proposed), or only CHAT and REMEMBER intents as stage 10 filters today.
6. **Floors can rise from the tune result, never fall (rule 9).** Accept?
7. **Third-party facts.** Store beliefs about people the user mentions (proposed, since the gold and the use case need it), or limit to `user` subjects. This is a privacy choice (R-05 is about briefs, not this).


## Revisions after cross-family review (2026-10-30, Codex `gpt-6-luna`, read-only)

The review verdict was UNSOUND as first written. These amendments take precedence over any earlier text in this file. Report: `/tmp/codex-review2/report2.md`.

1. **UPDATE can classify and then raise.** `classify_contradiction` returns UPDATE on a later `recorded_at` even when `valid_from` is earlier, and `apply_contradiction` then raises `InvalidIntervalError` (`memory_records.py:136-140`, `temporal_store.py:286-290`). The writer checks `new.valid_from >= existing.valid_from` before calling the store and otherwise takes the historical-insert path. Open for Aniket: whether an earlier `valid_from` with a later `recorded_at` should classify as UPDATE at all.
2. **Historical insert is new store behavior, not an existing API.** It would write a closed row (`valid_to` = the active row's `valid_from`, status SUPERSEDED, `superseded_by` = the active row's id, never active), returned only by queries whose time falls inside its interval (`temporal_store.py:224-243`). It needs its own gate test. If Aniket does not want a store change, historical inserts are dropped and counted as `skipped_out_of_order`, which lowers recall and says so.
3. **CORRECTION stamps wall-clock time** (`temporal_store.py:304-312`). The slice does not change that. The eval compares the order of supersession, never the stamp. Passing stream time would be a store code change, outside this slice.
4. **The panel counts are unverified.** They came from an uncommitted scratch script. The first build task is to commit the counter, reproduce 30,334 turns, 1,279 slot-claim records, 444 unique assertions, 118 supersession pairs and 12 corrections, and replace the numbers here. No floor is frozen until the counts match.
5. **Supported precision rule.** If fewer than 100 unmatched records exist, audit all of them. If none exist, precision is reported as "no unmatched records" and is not a pass. The estimate combines label-matched records and the audited sample with a stratified bootstrap interval.
6. **History preservation has two required parts.** (a) Writer-conditional: every update where both statements were extracted keeps both rows. (b) An oracle replay that feeds the gold assertions straight to the store, independent of the extractor, and checks no row is ever deleted. (b) cannot be skipped by an extractor that misses a statement.
7. **Shuffled control.** It runs on the same turns with the same scoring opportunities. It must score at most 0.10, and the real arm must beat it by at least 0.40 (PROPOSED, my judgment).
8. **Call budget.** B's verification ceiling assumes at most one surviving record per sampled turn. After the 20-turn timing run, re-project from the measured records per turn; the 9,514 and 14,357 totals are conditional on that assumption.
9. **Corrections have no floor.** With 12 correction turns they are reported as counts only and cannot establish correction quality.
10. **Added decisions for Aniket.** (a) Item 1 above. (b) Item 2 above. (c) Whether a user claim that may be false or a lie is stored as a belief with `provenance = user_claim` and a confidence cap (proposed), stored verbatim as a plain belief, or not stored.
