# Benchmark of the repository-intelligence system

Written for the maintainers deciding whether to trust the tooling. Reproduce with `python -m tools.repo_intel bench` (also gated by `tools/repo_intel/tests/test_bench.py`). Each scenario builds a throwaway repository with known ground truth, runs the real inventory, index and claim code, and compares the result with a baseline workflow that has no such infrastructure: literal text search, where a name that appears in another file counts as used and a claim counts as true if its words appear in the repo. Numbers are measured, not tuned: two real bugs the first run exposed (calls through an `import ... as` alias were dropped; test paths without a leading directory were not recognised as tests) were fixed in the indexer, and the table below is the run after those fixes.

## Results

| Scenario | Metric | Ground truth | Intel result | Intel | Baseline (text search) |
|---|---|---|---|---|---|
| alias | dependency_recall | use.py calls helper through alias h | call edge found: True | ok | MISS |
| alias | false_absence | claim is false (alias call exists) | refuted | ok | ok |
| dynamic_worker | false_absence | claim is false (job runs via importlib) | supported | MISS | MISS |
| dynamic_worker | risk_disclosed | dynamic code present, claim must not be treated as proof | capped at supported, dynamic warning present | ok | MISS |
| cross_language_subscriber | dependency_recall | python publisher and rust subscriber linked by subject | pub 1 sub 1 | ok | MISS |
| obsolete_impl | obsolete_detected | legacy_scorer unreferenced, scorer used | unreferenced lists legacy_scorer: True | ok | ok |
| outdated_doc | contradiction_detected | doc says 600, code says 4500 | refuted | ok | MISS |
| outdated_doc | contradiction_detected | doc cites docs/gone.md which does not exist | [('docs/gone.md', 'unresolved')] | ok | MISS |
| config_switch | dependency_recall | FEATURE_X env var read by s.py:mode | READS_ENV edge: True | ok | ok |
| modified_after_audit | staleness_detected | claim must go stale after f was renamed | {'unchanged': 0, 'rebound': 0, 'stale': 1} | ok | MISS |
| modified_after_audit | staleness_detected | unrelated edit above a quote must re-bind, not stale | {'unchanged': 0, 'rebound': 1, 'stale': 0} | ok | MISS |
| test_does_not_cover | contradiction_detected | risky is imported-module only, never called by a test | refuted | ok | MISS |
| test_does_not_cover | claim_precision | add is called from a test | supported | ok | ok |
| indirect_interface | dependency_recall | SqlStore depends on Store through inheritance | [('Store', 2, 'proven')] | ok | MISS |
| seeded_false_claims | contradiction_detected | truth: refuted | refuted | ok | MISS |
| seeded_false_claims | contradiction_detected | truth: refuted | refuted | ok | MISS |
| seeded_false_claims | claim_precision | truth: supported | supported | ok | ok |
| seeded_false_claims | claim_precision | truth: supported | supported | ok | ok |
| seeded_false_claims | contradiction_detected | truth: refuted | refuted | ok | MISS |
| coverage | inventory_parse_coverage | 5 of 6 files parse; the broken one is recorded as a failure, none vanish | in scope 6, parsed 5, failed 1 | ok | MISS |

| Metric | Cases | Intel correct | Baseline correct |
|---|---:|---:|---:|
| claim_precision | 3 | 3 | 3 |
| contradiction_detected | 6 | 6 | 0 |
| dependency_recall | 4 | 4 | 1 |
| false_absence | 2 | 1 | 1 |
| inventory_parse_coverage | 1 | 1 | 0 |
| obsolete_detected | 1 | 1 | 1 |
| risk_disclosed | 1 | 1 | 0 |
| staleness_detected | 2 | 2 | 0 |

- alias: baseline passes only because the import line mentions the name
- dynamic_worker: static check wrongly supports the claim; 2 dynamic construct(s) flagged in the detail: True


## How to read it

- Counts are small (20 scenario outcomes). They show which failure modes are handled, not a statistical rate. Do not quote a percentage from them.
- **Contradiction detection** (6 of 6 against 0 of 6): outdated number in a document, a citation of a missing file, a test that imports a module without calling the claimed function, and three seeded false claims. Text search cannot judge any of these, which is why its column is empty rather than bad.
- **Dependency recall** (4 of 4 against 1 of 4): an alias call, a Python publisher linked to a Rust subscriber through a shared constant, an environment switch, and inheritance across modules.
- **Staleness** (2 of 2): a renamed function stales its claim; an unrelated edit above a quote re-binds it instead of staling it.
- **Claim precision on true claims** (3 of 3, baseline 3 of 3): text search is as good as the index at confirming true claims whose words are present. The index only wins where the claim is false.
- **False absence** (1 of 2, baseline 1 of 2): the measured weakness. A worker started through `importlib.import_module` plus a computed `getattr` has no static caller, so the claim "nothing calls it" is **wrongly supported**. The mitigation is disclosure, not detection: such claims are capped at `supported` (never `verified`), the detail names the number of dynamic constructs in the repo, and the architecture map lists every one. The baseline fails the same case.

## Not measured

The directive lists scenarios this benchmark does not cover. They are not claimed:

- A feature under unexpected naming. Neither the index nor text search finds it; nothing here is semantic.
- A component whose behaviour changes with configuration, beyond recording which environment variables a file reads.
- Resume correctness across a context loss. The state is persisted and `resume` prints it, but there is no automated test that a fresh session reaches the same conclusions.
- Runtime verification accuracy beyond one allowlisted-command check per claim; no service was started.
- Regression detection across revisions (the index is rebuilt, not diffed).
- Agent hallucination rates. The only independent-reader measurement is below.

## Independent review of real claims

17 of the first 31 claims were reviewed by a fresh Claude sub-agent with no access to how they were produced (reader `claude-subagent-review-1`, same model family as the author, so weaker than cross-family review). It agreed with 13 and disagreed with 4. All four disagreements were genuine overstatements in the author's claims (a count of three where nine exist; "not built" where the work exists on an unmerged branch; "active" where a compose profile gates the service; "skips the check" where a row-level check covers other paths). They are kept in the ledger as `disputed` and replaced by corrected claims #38 to #41. That is 4 of 17 (24%) author errors caught by a second reader on a sample the author believed correct, which is the strongest argument in this document for requiring the independent reader.

## Index coverage on the real repository

All in-scope files parse; failures and stale files are listed by `index`, not hidden. Edge confidence on the real tree: roughly 15,300 proven, 600 lexical, 4,500 inferred, 440 unresolved, 2,970 external. About 22% of the non-external edges (4,561 of 20,959) are `inferred`, which is why every query prints the tier.
