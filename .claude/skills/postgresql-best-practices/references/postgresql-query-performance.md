---
title: "PostgreSQL Query Performance"
description: "Gotchas and anti-hallucination checklist for EXPLAIN ANALYZE interpretation and server tuning"
tags: [postgresql, performance, explain, work_mem, statistics]
---

# Query Performance — Gotchas & Corrections

> Models know EXPLAIN reading and tuning fundamentals well. This reference covers only the mistakes they make.

## Version Gates

- `CREATE STATISTICS` (ndistinct/dependencies): PG 10+
- JIT compilation: PG 11+
- CTE inlining by default (no longer optimization fence): PG 12+
- Incremental sort: PG 13+
- Memoize node: PG 14+

## Critical Gotchas

- **`loops` multiplier** — node time is per-loop. `0.1ms × 10,000 loops = 1 second`. Always multiply.
- **Seq Scan is not always wrong** — on low-selectivity queries or small tables, Seq Scan is the correct choice. Don't reflexively add indexes.
- **Statistics before indexes** — fix 10x row-estimate errors (`rows=` vs `actual rows=`) with `ANALYZE` or `CREATE STATISTICS` before adding access paths. Check `pg_stat_user_tables.n_mod_since_analyze` to detect stale statistics: a high value means autovacuum hasn't analyzed the table yet and estimates may be wrong.
- **`work_mem` is per-operation per-worker** — raising it globally multiplies across parallel workers and plan nodes. Prefer session/statement scope for specific spilling queries.
- **`hash_mem_multiplier`** — hash nodes can use `work_mem × hash_mem_multiplier` (default 2.0). Include this in memory math.
- **Sort by `total_exec_time`, not `mean`** — in `pg_stat_statements`, high-total-impact queries matter more than high-mean outliers.
- **JIT hurts short OLTP** — compilation cost can exceed execution time for fast queries. Raise `jit_above_cost` or disable per-session.
- **Pre-PG12 CTEs materialize** — `WITH` is an optimization fence before PG 12. On PG 12+, CTEs inline unless you force `MATERIALIZED`.
- **Prepared statements + skewed data** — generic plans can be slow for specific parameter values. Test with `SET plan_cache_mode = force_custom_plan`.
- **`effective_cache_size` is a hint, not reserved memory** — it tells the planner how much OS + shared cache to expect.
- **Check waits before tuning SQL** — if a query is blocked (`pg_stat_activity` wait events), fix the blocker first.

## Anti-Hallucination Rules

- Do NOT claim `VACUUM FULL` is routine performance maintenance.
- Do NOT recommend raising `work_mem` globally without concurrency math.
- Do NOT treat pre-PG12 and PG12+ CTE behavior as identical.
- Do NOT prescribe indexes before checking row-estimate quality and wait events.
- Do NOT claim `effective_cache_size` reserves RAM.
