---
title: "Azure PostgreSQL Intelligent Tuning"
description: "Azure Database for PostgreSQL Flexible Server intelligent performance tuning: Query Store, automatic indexing, and performance recommendations"

tags: [azure, postgresql, query-store, auto-index, performance-insights, tuning]

---

# Intelligent Tuning

## Response focus

Prioritize where agents over-trust Azure's tuning surface: incomplete Query Store coverage, misleading one-off index recommendations, and unsafe apply steps. Skip basic "use Query Store" or generic EXPLAIN framing.

> **NEVER suggest for Azure:** `ALTER SYSTEM SET`, `postgresql.conf` edits, `systemctl reload`, or `sudo` commands. Parameter changes are via `az postgres flexible-server parameter set` or portal.

## ⚠️ Confident Hallucination Corrections

- **❌ WRONG: "Only azure_pg_admin can query tuning views."** ✅ CORRECT: Members of the **public role** can read from `intelligentperformance.*` and `query_store.*` views in the `azure_sys` database. Any user with connect permission to `azure_sys` can query them — `azure_pg_admin` is NOT required.
- **❌ WRONG: "Index recommendations appear in pg_stat_user_indexes or system catalogs."** ✅ CORRECT: Azure's recommendations are in `intelligentperformance.recommendations` (and sessions in `intelligentperformance.sessions`) inside the `azure_sys` database — **custom Azure views**, not standard PostgreSQL catalogs.

## Prerequisites

- `azure_pg_admin` role is required for most intelligent performance views.
- Verify Query Store capture mode before drawing conclusions:
  ```sql
  SHOW pg_qs.query_capture_mode;
  ```
- Treat Query Store as workload sampling, not a full-fidelity transaction log. Advice should stay probabilistic unless corroborated by repeated observations.

## High-value queries

```sql
-- Top queries with SQL text
SELECT qt.query_sql_text, qs.calls, qs.total_time, qs.mean_time
FROM query_store.qs_view qs
JOIN query_store.query_texts_view qt
  ON qs.query_text_id = qt.query_text_id
ORDER BY qs.total_time DESC
LIMIT 20;

-- Wait sampling summary
SELECT event_type, event, sum(count) AS samples
FROM query_store.pgms_wait_sampling_view
GROUP BY 1, 2
ORDER BY samples DESC;

-- Current index recommendations (run in azure_sys database)
SELECT *
FROM intelligentperformance.recommendations
ORDER BY last_recommended DESC;
```

## Before you trust a recommendation

1. Check that the query or wait pattern repeats across multiple windows, not just one incident.
2. Capture a representative `EXPLAIN` for the slow path before changing anything.
3. If an index is warranted, apply it with low-risk operational mechanics, then compare plan shape and measured latency after the change.
4. Keep the recommendation ID, generated DDL, and rollback notes in the ticket or runbook.

## Common Mistakes / Gotchas

1. **[HIGH] Reading `query_store.qs_view` without joining SQL text**: Metrics without `query_store.query_texts_view` are not actionable. Agents often produce a "top queries" list that contains IDs but no usable SQL.

   Fix: join on `query_text_id` before reporting findings.

2. **[HIGH] Query Store sampling gaps**: Not all queries are captured. Very fast queries (especially sub-millisecond calls) and some background-worker activity may be absent. On microservice workloads with many tiny queries, the "top queries" list can be incomplete.

   Fix: explicitly warn about coverage gaps and combine Query Store with app telemetry or `pg_stat_statements` when fast-query coverage matters.

3. **[HIGH] False positive index recommendations**: Intelligent tuning may recommend an index because of a brief spike, stats drift, or a parameter-specific bad plan. Agents should not assume every recommendation represents a persistent workload problem.

   Fix: compare multiple time windows and parameter shapes before treating the recommendation as durable.

4. **[MEDIUM] Safe interpretation before applying recommendations**: The agent blindly suggests `CREATE INDEX` or direct auto-apply.

   Fix: prefer `CREATE INDEX CONCURRENTLY`, validate with `EXPLAIN` before/after, and keep the rollback path ready.

5. **[HIGH] Ignoring wait event analysis**: An index recommendation may be irrelevant when the real issue is lock contention, storage waits, or LWLock pressure.

   Fix: inspect `query_store.pgms_wait_sampling_view` before deciding whether the bottleneck is SQL shape, contention, or infrastructure.

6. **[HIGH] Query Store retention quietly consuming storage**: On high-QPS systems, retained Query Store data and stored plans can grow quickly.

   Fix: shorten `pg_qs.retention_period_in_days` and consider `pg_qs.store_query_plans = off` when lower overhead matters more than long plan history.

7. **[MEDIUM] Missing utility-command coverage**: Slow `COPY`, `VACUUM`, and DDL activity may not appear unless `pg_qs.track_utility` is enabled.

   Fix: enable utility tracking when bulk load or maintenance behavior is part of the investigation.

8. **[HIGH] Query Store looks empty so the agent assumes there is no issue**: `pg_qs.query_capture_mode = none`, recent enablement, or capture thresholds can make Query Store sparse.

   Fix: verify capture mode and allow time for collection before concluding the workload is quiet.

9. **[MEDIUM] Recommending changes without a rollback plan**: Some index recommendations help only one parameter shape or hurt write throughput.

   Fix: save the recommendation ID, generated DDL, validation query, and revert steps before applying anything.

10. **[CRITICAL] Permission denied on intelligent performance views**: Flexible Server does not provide superuser access. Advice that assumes superuser semantics is unusable.

    Fix:
    ```sql
    SELECT pg_has_role(current_user, 'azure_pg_admin', 'member');
    ```
    If false, use a server admin path to grant the role or switch accounts.

## On Azure HorizonDB (Preview)

- **Index/intelligent tuning is not yet available on HorizonDB.** Do not recommend the Flexible Server `intelligent_tuning`, `index_tuning.mode`, or `az postgres flexible-server parameter set` workflow — those are Flexible Server features.
- **Tune manually via parameter groups + Query Store.** Enable Query Store with the cluster parameter group, then query it from the `azure_sys` database. Act on findings with normal PostgreSQL indexing (`CREATE INDEX CONCURRENTLY`), `EXPLAIN (ANALYZE, BUFFERS)`, and planner/memory parameters set in the cluster's parameter group.

```sql no-execute
SELECT query_sql_text, calls, mean_time, total_time
FROM query_store.qs_view
ORDER BY total_time DESC
LIMIT 10;
```

See [Intelligent tuning parameters](https://learn.microsoft.com/en-us/azure/horizondb/parameters/parameters-intelligent-tuning) and [Query Store](https://learn.microsoft.com/en-us/azure/horizondb/monitor/concepts-query-store).

## References
- [Intelligent tuning in Azure Database for PostgreSQL](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-intelligent-tuning)
- [Query Store in Azure Database for PostgreSQL](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-query-store)
