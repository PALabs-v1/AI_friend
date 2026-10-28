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
