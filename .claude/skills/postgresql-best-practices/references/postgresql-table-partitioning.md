---
title: "PostgreSQL Table Partitioning"
description: "Gotchas and anti-hallucination checklist for declarative partitioning, pruning, and maintenance"
tags: [postgresql, partitioning, range, list, hash, pruning]
---

# Table Partitioning — Gotchas & Corrections

> Models know partitioning fundamentals well. This reference covers only the mistakes they make.

## Version Gates

- Declarative RANGE and LIST: PG 10+
- HASH partitioning, DEFAULT partition, runtime pruning: PG 11+
- Foreign keys to/from partitioned tables: PG 12+
- `DETACH PARTITION ... CONCURRENTLY`: PG 14+ only

## Critical Gotchas

- **Hash partitioning does NOT prune by date** — use RANGE for time-series. Hash distributes evenly but cannot skip partitions for range predicates.
- **Cast mismatches break pruning** — `date` literals against `timestamptz` keys often prevent partition elimination. Use typed predicates matching the partition key type.
- **Unique constraints must include partition key** — PostgreSQL will reject `PRIMARY KEY (id)` if the table is partitioned by `created_at`. Must be `PRIMARY KEY (id, created_at)`.
- **No `DROP PARTITION` syntax** — PostgreSQL uses `ALTER TABLE ... DETACH PARTITION`, then `DROP TABLE` on the detached table.
- **`DETACH CONCURRENTLY` is PG 14+ only** — on older versions, detach takes `ACCESS EXCLUSIVE` lock. Pre-add a valid `CHECK` constraint to avoid full-table validation scan on `ATTACH`.
- **DEFAULT partition traps** — DEFAULT keeps inserts alive but blocks later partition creation if it contains rows for the new range. Move rows out first.
- **Use `ONLY` when operating on DEFAULT** — without it, DML can affect sub-partitions unexpectedly.
- **Hundreds of partitions degrade planning** — keep partition count deliberate. Planning cost rises fast.
- **No global indexes** — indexes are per-partition in PostgreSQL.
- **Logical replication** — set `publish_via_partition_root = true` if subscribers expect the parent table name.

## Common Mistakes

1. Using HASH when RANGE is needed for time-based queries (hash never prunes by range)
2. Omitting partition key from PK/UNIQUE (PostgreSQL requires it)
3. Letting DEFAULT partition accumulate data (blocks new partition creation)
4. Using `DETACH PARTITION CONCURRENTLY` on PG < 14 (syntax error)
5. Forgetting that unique constraints are local to each partition (no cross-partition uniqueness)

## DEFAULT Partition Cleanup (when rows are trapped)

```sql
-- Move trapped rows out of DEFAULT before creating new partition
WITH moved AS (
  DELETE FROM ONLY events_default
  WHERE event_date >= '2024-01-01' AND event_date < '2024-02-01'
  RETURNING *
)
INSERT INTO events SELECT * FROM moved;
```

```sql
-- Create new partition after DEFAULT is clear
CREATE TABLE events_2024_01 PARTITION OF events
  FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

## Anti-Hallucination Rules

- Do NOT claim `DETACH PARTITION CONCURRENTLY` works before PG 14.
- Do NOT claim global uniqueness works without including the partition key.
- Do NOT invent syntax such as `DROP PARTITION` or `MERGE PARTITIONS`.
- Do NOT recommend partitioning when queries do not filter on a stable key.
- Do NOT claim PG 11 or earlier supports foreign keys to/from partitioned tables.
