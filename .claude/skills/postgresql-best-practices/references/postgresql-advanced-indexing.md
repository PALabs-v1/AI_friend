---
title: "PostgreSQL Advanced Indexing"
description: "Gotchas and anti-hallucination checklist for B-tree, GIN, GiST, BRIN, partial, and expression indexes"
tags: [postgresql, indexing, performance, gin, gist, brin]
---

# Advanced Indexing — Gotchas & Corrections

> Models know indexing fundamentals well. This reference covers only the mistakes they make.

## Version Gates

- `INCLUDE` columns: PG 11+
- `REINDEX CONCURRENTLY`: PG 12+
- B-tree deduplication: PG 13+

## Critical Gotchas

- **REINDEX in production blocks writes** — always use `REINDEX CONCURRENTLY` (PG 12+). Never inside a transaction block.
- **GIN is NOT for scalar equality** — use B-tree for `=` and range filters on normal columns.
- **Expression must match exactly** — `lower(email)` index does not help `upper(email)` or bare `email` queries.
- **Partial-index predicates** — planner uses the index only when it can prove the query predicate implies the index predicate. Use a WHERE clause matching the query's filter exactly.
- **BRIN requires physical correlation** — check `pg_stats.correlation` first. Random UPDATEs destroy ordering silently.
- **Multicolumn B-tree ordering** — equality columns first, range/sort columns last (leftmost-prefix rule).
- **No global indexes on partitioned tables** — indexes are per-partition in PostgreSQL.
- **`INCLUDE` does NOT speed writes** — it adds write amplification for read-path index-only scans. Visibility-map state still controls heap access.
- **Collation/opclass mismatch** — text index and query collation must agree or the index is unusable.

## Quick Reference

```sql
-- Partial index (only active rows)
CREATE INDEX idx_active_users ON users (email) WHERE active = true;
```

## Anti-Hallucination Rules

- Do NOT recommend GIN for ordinary scalar equality lookups.
- Do NOT suggest `CREATE INDEX CONCURRENTLY` or `REINDEX CONCURRENTLY` inside a transaction block.
- Do NOT claim `INCLUDE` eliminates all heap reads.
- Do NOT recommend BRIN without checking physical correlation.
- Do NOT assume partitioned tables have global indexes.
