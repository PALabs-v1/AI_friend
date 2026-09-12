---
title: "PostgreSQL Full Text Search"
description: "PostgreSQL native full-text search with tsvector, tsquery, GIN indexes, and ranking functions"
tags: [postgresql, full-text-search, tsvector, tsquery, gin, ranking]
---

# Full-Text Search

## Response focus

- Prioritize ranking correctness, vector maintenance, config mismatches, and typo-tolerance boundaries.
- Skip basic `tsvector` or GIN tutorials unless the user explicitly asks.

## Version History

| Version | Feature | Notes |
|---|---|---|
| All supported versions | `to_tsvector`, `to_tsquery`, `ts_rank`, GIN over `tsvector` | Core FTS building blocks |
| PG 11 | `websearch_to_tsquery()` | Safe default for raw search-box input |
| PG 12 | Generated stored columns | Preferred `tsvector` maintenance pattern |

## Parameter Correctness

| Item | Correct value / meaning | Why it matters |
|---|---|---|
| `ts_rank(..., 2)` | Divide by document length | Fixes long-document bias |
| `ts_rank(..., 32)` | `rank / (rank + 1)` | Scaling only; not length normalization |
| `ts_rank_cd(...)` | Cover-density ranking | Use when proximity matters |
| `websearch_to_tsquery` | Raw user input | Handles punctuation and operators safely |
| `to_tsquery` | Pre-sanitized expert syntax | Fails on arbitrary user input |
| `simple` config | Identifiers, SKUs, codes, emails | Avoids stemming/stop-word loss |
| `english` config | Natural-language prose | Better stemming, but can distort identifiers |

## Feature Interactions

- **FTS + `pg_trgm`**: FTS handles stemming; trigram handles misspellings. Use trigram as fallback, not replacement.
- **FTS + partitioning**: GIN indexes are per-partition; there is no global GIN index across partitions.
- **FTS + generated columns**: Prefer a stored generated column on PG 12+ so queries hit the same expression the index stores. This is cheaper than a trigger: no per-statement overhead, no risk of trigger accidentally firing on unrelated updates, and the column is always consistent.
- **FTS + triggers (legacy approach)**: Triggers rebuild the vector on every relevant UPDATE. For high-write tables: (1) ensure the trigger only fires when text columns actually change (`WHEN OLD.body IS DISTINCT FROM NEW.body`), (2) consider `tsvector_update_trigger()` built-in, (3) generated columns are strictly superior on PG 12+ and should replace custom triggers.
- **FTS + `default_text_search_config`**: Do not depend on server defaults across environments; hard-code the config in DDL and queries.
- **FTS + `ts_headline`**: Highlighting rescans text; apply it after ranking and limiting candidates.
- **FTS + exact identifiers**: Product codes and issue IDs often need `simple`, trigram, or exact match logic in addition to prose search.

## Diagnostic Checklist

| Symptom | Run | Fix |
|---|---|---|
| Search returns no rows | `SELECT to_tsvector('english', 'ACME-123 running shoes');` | Verify tokenization; switch config or split identifier fields |
| Query throws parse errors | `SELECT websearch_to_tsquery('english', 'c++ "error 500"');` | Replace `to_tsquery()` for raw input on PG 11+ |
| Index not used | `EXPLAIN ANALYZE SELECT id FROM articles WHERE search_vector @@ websearch_to_tsquery('english', 'running shoes');` | Query the stored vector column, not recomputed `to_tsvector(...)` |
| Rank favors long docs | `SELECT ts_rank(search_vector, websearch_to_tsquery('english', 'foo'), 2);` | Use normalization flag `2`, not `32` |
| Results stale after updates | `SELECT title, body, search_vector FROM articles WHERE id = 42;` | Use generated stored column or trigger maintenance |
| Write-heavy table slows down | `SELECT gin_pending_list_limit;` | Tune pending-list behavior before abandoning GIN |

## Error Messages

| Error | Root cause | Fix |
|---|---|---|
| `syntax error in tsquery` | Raw user input sent to `to_tsquery()` | Use `websearch_to_tsquery()` or sanitize input first |
| `function websearch_to_tsquery(unknown, unknown) does not exist` | Server is older than PG 11 | Use `plainto_tsquery()` or upgrade |
| `operator does not exist: tsvector @@ text` | Right-hand side is text, not `tsquery` | Wrap input with `websearch_to_tsquery`, `plainto_tsquery`, or `to_tsquery` |

## Common Mistakes / Gotchas

- **[HIGH] Raw input into `to_tsquery()`**: punctuation, quotes, and operators break parsing. Default to `websearch_to_tsquery()` on PG 11+.
- **[HIGH] Wrong normalization flag**: `2` is length normalization; `32` is only score scaling.
- **[HIGH] Recomputing vectors in the predicate**: `to_tsvector(...) @@ ...` bypasses the stored/indexed vector path.
- **[HIGH] Treating FTS as fuzzy search**: typo tolerance needs `pg_trgm` or another fuzzy layer.
- **[MEDIUM] Using `english` for codes and names**: stemming and stop-word removal can hide exact tokens.
- **[MEDIUM] Assuming phrase search is near-search**: `phraseto_tsquery('big data')` requires adjacency; use `<N>` operators for distance.
- **[MEDIUM] Calling `ts_headline` on every candidate row**: rank/filter first, then highlight.

```sql
SELECT alias, token, lexemes
FROM ts_debug('english', 'ACME-123 running shoes');

EXPLAIN ANALYZE
SELECT id
FROM articles
WHERE search_vector @@ websearch_to_tsquery('english', 'running shoes');
```

## Anti-Hallucination Rules

- Do NOT confuse `ts_rank` normalization flags. Flag `2` = divide by document length. Flag `32` = `rank/(rank+1)`.
- Do NOT claim `ts_rank_cd` is always superior to `ts_rank`; it optimizes for proximity, not generic relevance.
- Do NOT use `websearch_to_tsquery` syntax without noting it requires PostgreSQL 11+.
- Do NOT claim GIN indexes can satisfy `ORDER BY rank`; they filter matches, then PostgreSQL ranks rows afterward.
- Do NOT omit the text search configuration parameter in examples; relying on `default_text_search_config` is fragile.
