---
title: "PostgreSQL Connection Management"
description: "PostgreSQL connection lifecycle, pooling strategies, idle timeout tuning, and connection exhaustion prevention"
tags: [postgresql, connections, pooling, pgbouncer, timeout]
---

# Connection Management

> **Response focus:** Diagnose connection exhaustion, pool-mode mismatch, prepared statement breakage, and idle-in-transaction cleanup before suggesting bigger limits.

## Version History

| Version | Feature | Notes |
|---------|---------|-------|
| PG 9.6 | `idle_in_transaction_session_timeout` | Kills abandoned transactions that hold locks |
| PG 14 | `idle_session_timeout` | Reclaims long-idle non-transaction sessions |

## Parameter Correctness

| Setting | Scope | Correct use | Gotcha |
|---------|-------|-------------|--------|
| `max_connections` | postmaster | Change via server config and restart | `SET max_connections` does nothing |
| `idle_in_transaction_session_timeout` | db, role, session, cluster | Set to a few minutes for app databases | Prevents idle transactions from blocking VACUUM |
| `idle_session_timeout` | db, role, session, cluster | Use for non-pooled clients on PG 14+ | Can fight external poolers if set too low |
| `statement_timeout` | db, role, session, cluster | Caps query runtime | Does not control connect time |
| `connect_timeout` | client | Limits TCP connect wait | Does not cancel slow queries |
| PgBouncer `pool_mode` | pooler | `transaction` for serverless, `session` for session state or prepared statements | Wrong mode causes subtle breakage |
| PgBouncer `server_reset_query` | pooler | Clear backend state on reuse | Missing reset leaks session state |

## Feature Interactions

- **PgBouncer transaction mode + prepared statements**: `PREPARE` and `EXECUTE` can hit different backends.
- **PgBouncer transaction mode + session state**: `SET`, temp tables, and advisory locks do not survive backend reuse.
- **`max_connections` + memory**: Each backend consumes ~5-10 MB base RSS (stack + local buffers + catalog cache). With `work_mem = 4MB` and complex queries, a backend can use 50-100 MB. At 1500 connections, worst-case private memory alone can reach 50-150 GB before shared buffers.
- **Serverless autoscaling + per-process pools**: `pool_size × instances` can exceed server limits fast.
- **Idle transactions + autovacuum**: One abandoned transaction can block cleanup on hot tables.

## Diagnostic Checklist

| Symptom | Run | Look for | Fix |
|---------|-----|----------|-----|
| `too many clients` | `SELECT state, count(*) FROM pg_stat_activity GROUP BY state ORDER BY count(*) DESC;` | Large idle population | Add pooler, lower app pool sizes, clean leaks |
| Need top connection owners | `SELECT application_name, usename, state, count(*) FROM pg_stat_activity GROUP BY 1,2,3 ORDER BY 4 DESC;` | One service consuming most slots | Cap that service first |
| Idle transactions blocking work | `SELECT pid, usename, now() - xact_start AS age, wait_event_type, query FROM pg_stat_activity WHERE state = 'idle in transaction';` | Old transactions | Set `idle_in_transaction_session_timeout`; fix app commit or rollback |
| Emergency cleanup | `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle' AND now() - state_change > interval '10 minutes';` | Requires proper role | Use only as incident response |
| Suspect PgBouncer mode issue | `SHOW pool_mode;` in PgBouncer | `transaction` with prepared statements or temp tables | Switch to `session` or remove session features |

## Error Messages

| Error | Root cause | Fix |
|-------|------------|-----|
| `FATAL: sorry, too many clients already` | All normal slots are in use | Reduce client fan-out, add pooler, increase limit only with memory headroom |
| `remaining connection slots are reserved for non-replication superuser connections` | User exhausted non-reserved slots | Same fix as above; reserve slots for admin access |
| `prepared statement "..." does not exist` | PgBouncer transaction mode moved the session to another backend | Use session mode or avoid session-scoped prepared statements |
| `terminating connection due to idle-in-transaction timeout` | Session sat in an open transaction too long | Commit or rollback sooner; raise timeout only if justified |

## Common Mistakes / Gotchas

- **Treat `max_connections` as the first fix**: It often hides pooling and leak problems.
- **Ignore total fan-out**: `pool_size_per_instance × instances` is the real load on PostgreSQL.
- **Use session mode for bursty serverless traffic**: It defeats multiplexing.
- **Use transaction mode with temp tables or session `SET`**: Backend reuse breaks the workflow.
- **Skip `idle_in_transaction_session_timeout`**: One crashed client can hold locks for hours.
- **Assume `statement_timeout` protects connection storms**: It does not limit connects.
- **Forget reset behavior in PgBouncer**: Residual session state leaks across clients.

```ini
; Minimal gotcha example for serverless
pool_mode = transaction
server_reset_query = DEALLOCATE ALL; DISCARD ALL; RESET ALL;
```

## Anti-Hallucination Rules

- Do not claim `SET max_connections` works.
- Do not claim PgBouncer transaction mode supports session-scoped prepared statements.
- Do not recommend raising `max_connections` without checking memory and fan-out math.
- Do not treat `statement_timeout` and `connect_timeout` as interchangeable.
- Do not ignore `idle in transaction` sessions when diagnosing bloat or blocked VACUUM.
