---
title: "PostgreSQL Logical Replication"
description: "PostgreSQL logical replication setup, publication/subscription patterns, and conflict resolution"
tags: [postgresql, replication, logical, publication, subscription, cdc]
---

# Logical Replication

## Response focus

- Prioritize slot health, lag root cause, version-gated behavior, and fail-stop conditions.
- Skip basic publication/subscription tutorials unless the user explicitly asks.

## Version History

| Version | Feature | Notes |
|---|---|---|
| PG 10 | Built-in publications and subscriptions | Core logical replication feature set |
| PG 15 | Column-list publications | `FOR TABLE t (col1, col2)` requires PG 15+ |
| PG 16 | Publishing from standby | Before PG 16, logical publishers must be primaries |
| PG 16 | `disable_on_error` and `ALTER SUBSCRIPTION ... SKIP` | Useful for stuck apply workers |
| PG 17 | Failover-safe logical replication slots | Requires `sync_replication_slots = true` on standby + `failover = true` on slot creation; standby must list slots in `standby_slot_names` |

## Parameter Correctness

| Item | Correct meaning | Why it matters |
|---|---|---|
| `wal_level = logical` | Required on publisher | Needs restart after change |
| `max_wal_senders` | Sender process budget | Separate from slot count |
| `max_replication_slots` | Slot budget | Exhaustion blocks new slots |
| `REPLICA IDENTITY FULL` | Replicate old row image without PK | Required for UPDATE/DELETE on tables without key |
| `copy_data = true` | Initial table copy during subscription/refresh | Can trigger large re-syncs |
| `publish_via_partition_root = true` | Publish parent partition identity | Important for partitioned-table naming semantics |

## Feature Interactions

- **Logical replication + DDL**: DDL is never replicated; apply subscriber schema changes first.
- **Logical replication + sequences**: Sequence state is not replicated; reset sequences after failover or cutover.
- **Logical replication + partitioning**: Partition behavior is version-sensitive; verify `REPLICA IDENTITY` and publication settings explicitly.
- **Logical replication + REPLICA IDENTITY on partitioned tables**: PG 15+ propagates parent's replica identity to partitions automatically. PG 10-14 requires setting REPLICA IDENTITY on each child partition individually; failing to do so silently drops UPDATE/DELETE operations.
- **Logical replication + failover (pre-PG 17)**: Logical slots are local to the instance. After failover, recreate slots on the new primary and expect brief data duplication or loss. Use pg_replication_origin to track what was already applied.
- **Logical replication + large transactions**: One huge transaction can create lag spikes and hold WAL for long periods.
- **Logical replication + `REFRESH PUBLICATION`**: With `copy_data = true`, newly added tables may be recopied in full.
- **Logical replication + standby publishers**: Supported only in PG 16+.

## Diagnostic Checklist

| Symptom | Run | Fix |
|---|---|---|
| Subscription never starts | `SELECT subname, status, last_msg_send_time, last_msg_receipt_time FROM pg_stat_subscription;` | If timestamps are null, check connectivity, `pg_hba.conf`, and publisher parameters |
| WAL disk usage keeps growing | `SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_retained FROM pg_replication_slots;` | Drop orphaned slots or fix stalled subscribers |
| Apply lag is increasing | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS apply_lag FROM pg_replication_slots;` | Investigate subscriber slowness or large publisher transactions |
| UPDATE/DELETE not replicating | `SELECT relname, relreplident FROM pg_class WHERE relname = 'orders';` | Add PK or `ALTER TABLE ... REPLICA IDENTITY FULL` |
| Partitioned table behaves differently across versions | `SELECT relname, relreplident FROM pg_class WHERE relname LIKE 'orders%';` | On PG 10-14, set replica identity on child partitions individually |
| Failover causes duplicate keys | `SELECT setval('orders_id_seq', (SELECT max(id) FROM orders) + 1);` | Reseat sequences on the new writer |

## Error Messages

| Error | Root cause | Fix |
|---|---|---|
| `logical decoding requires wal_level >= logical` | Publisher not configured for logical decoding | Set `wal_level = logical` and restart |
| `cannot update table "t" because it does not have a replica identity and publishes updates` | Table has no PK or replica identity | Add PK or use `REPLICA IDENTITY FULL` |
| `cannot delete from table "t" because it does not have a replica identity and publishes deletes` | Same as above for DELETE | Add PK or use `REPLICA IDENTITY FULL` |
| `CREATE SUBSCRIPTION ... WITH (create_slot = true) cannot run inside a transaction block` | Subscription creation with slot creation was run inside a transaction | Run the command outside an explicit transaction |

## Common Mistakes / Gotchas

- **[CRITICAL] Forgetting that DDL is not replicated**: apply schema changes on subscriber first.
- **[CRITICAL] Leaving orphaned replication slots behind**: inactive slots retain WAL indefinitely and fill disk.
- **[HIGH] Treating `max_wal_senders` and `max_replication_slots` as the same limit**: they fail independently.
- **[HIGH] Ignoring sequence drift**: logical replication copies row values, not sequence counters.
- **[HIGH] Missing `REPLICA IDENTITY`**: UPDATE/DELETE on tables without keys will fail or stop apply.
- **[HIGH] Assuming standby publishers work everywhere**: that is PG 16+ only.
- **[HIGH] Forgetting column-list publications are PG 15+ only**: earlier versions must publish full rows.
- **[HIGH] Partition replica identity assumptions**: in PG 15+, partitioned tables inherit from parent for logical replication; in PG 10-14, set each child explicitly.
- **[MEDIUM] `REFRESH PUBLICATION` surprise**: with `copy_data = true`, added tables may be fully recopied.

```sql
SELECT
    slot_name,
    active,
    pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_retained,
    pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS apply_lag
FROM pg_replication_slots
ORDER BY pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) DESC;
```

## Anti-Hallucination Rules

- Do NOT claim logical replication keeps sequence state synchronized; sequences must be reset after failover or migration cutover.
- Do NOT claim DDL changes replicate automatically; they do not in any PostgreSQL version.
- Do NOT assume `wal_level` changes take effect without restart.
- Do NOT claim bidirectional replication is built in; it requires external tooling or custom conflict handling.
- Do NOT use column-list publication syntax without stating it requires PostgreSQL 15+.
- Do NOT claim `REPLICA IDENTITY` behavior on partitions is version-invariant; verify the server version first.
