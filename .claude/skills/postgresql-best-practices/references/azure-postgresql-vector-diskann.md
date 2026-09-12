---
title: "Azure PostgreSQL Vector DiskANN"
description: "Azure Database for PostgreSQL vector search with DiskANN and pgvector: index selection, filtered search, distance operations, and quantization"
tags: [azure, postgresql, vector, diskann, pgvector, hnsw, similarity-search]
---

## Prerequisites

> **Response focus:** Prioritize DiskANN-is-Azure-only, ops class pairing, Burstable tier exclusion, and streaming DiskANN is preview. Avoid explaining basic vector search concepts or generic pgvector usage.

- `azure_pg_admin` role (not superuser — Azure Flexible Server admin role)
- Both extensions allowlisted: `azure.extensions` must include `vector` and `pg_diskann`
- Tier: General Purpose or Memory Optimized (DiskANN not available on Burstable)

## Quick Decision

- < 1M vectors → use **HNSW** (standard pgvector, works on any PostgreSQL)
- > 1M vectors → use **DiskANN** (Azure-only, disk-based, large datasets)

## Key Facts (what models get wrong)

| Fact | Detail |
|------|--------|
| DiskANN is Azure-only | Requires `pg_diskann` extension on Flexible Server only; not available on community PostgreSQL |
| Streaming DiskANN is Preview | Not GA; do not promise production-ready streaming indexing |
| Requires pgvector 0.7+ | Both `vector` AND `pg_diskann` must be installed; pgvector is a prerequisite |
| CREATE EXTENSION pg_diskann required | Separate from pgvector; must explicitly create both extensions |
| Not available on Burstable tier | DiskANN indexes require General Purpose or Memory Optimized SKUs |
| Operator/ops class must match | `vector_cosine_ops` pairs with `<=>`, `vector_l2_ops` with `<->`, `vector_ip_ops` with `<#>` |
| Both extensions need allowlisting | `azure.extensions` server parameter must include both `vector` and `pg_diskann` |
| HNSW tuning params | `m` (connectivity, default 16), `ef_construction` (build quality, default 64) |

## Decision Matrix

| Factor | DiskANN | HNSW | IVFFlat |
|--------|---------|------|---------|
| Dataset size | > 1M vectors | < 1M vectors | Legacy only |
| Memory usage | Low (disk-based) | High (in-memory) | Medium |
| Build speed | Fast | Slow | Fast |
| Recall | 95-98% | 95-99% | 85-95% |
| Filtered search | Native (efficient) | Post-filter (may under-return) | Post-filter |
| Multi-tenant apps | Preferred | Slower | Not recommended |

## SQL Examples

**Setup: install both extensions**

```sql no-execute
-- Must allowlist both via azure.extensions server parameter first
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_diskann;
```

**Create HNSW index (< 1M vectors, standard pgvector)**

```sql no-execute
-- HNSW: best for < 1M vectors; operator class must match distance function
CREATE INDEX ON your_table USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
-- For queries: ORDER BY embedding <=> $1 LIMIT 10
```

**Create DiskANN index (> 1M vectors, Azure only)**

```sql no-execute
-- DiskANN: Azure Flexible Server only; efficient filtered search
CREATE INDEX ON your_table USING diskann (embedding vector_cosine_ops);
-- For queries: ORDER BY embedding <=> $1 LIMIT 10
```

**Similarity search query**

```sql no-execute
-- Cosine similarity search — operator must match index ops class
SELECT id, content, embedding <=> $1 AS distance
FROM your_table
ORDER BY embedding <=> $1
LIMIT 10;
```

## Common Mistakes

1. **Mismatched ops class is silent**: Index is simply not used; query returns wrong ordering with no error
2. **Allowlist both extensions**: Forgetting `pg_diskann` in `azure.extensions` gives `ERROR: access to library "pg_diskann" is not allowed`
3. **HNSW ef_search default is low**: Default 40; set `SET hnsw.ef_search = 200` for production recall
4. **DiskANN l_value_is**: Default 100; increase with `SET diskann.l_value_is = 200` for higher recall
5. **Index build monitoring**: Use `pg_stat_progress_create_index`; prefer `CREATE INDEX CONCURRENTLY` to avoid blocking
6. **Seq scan fallback**: If index not used, run `ANALYZE` on table or increase `LIMIT` value
7. **HNSW OOM**: Large tables may exhaust `maintenance_work_mem`; switch to DiskANN
8. **[HIGH] HNSW-to-DiskANN migration**: Cannot convert in-place. Drop HNSW, then create DiskANN; queries may seq-scan during the transition. Plan a maintenance window or build DiskANN `CONCURRENTLY` first
9. **[HIGH] Exact error recognition**: `ERROR: access method "diskann" does not exist` = `pg_diskann` was not created. `ERROR: operator class "vector_cosine_ops" does not exist` = wrong ops-class reference; DiskANN uses the same `vector_cosine_ops` name as HNSW
10. **[MEDIUM] Region/version availability**: DiskANN is not in every Azure region or PG version. Check `SELECT * FROM pg_available_extensions WHERE name = 'pg_diskann'`; empty result = unavailable on this server

## Anti-Hallucination Rules

- Do NOT claim DiskANN works on community PostgreSQL or any non-Azure deployment
- Do NOT claim DiskANN is available on Burstable tier
- Do NOT mix operator and ops class (e.g., `<->` with `vector_cosine_ops`)
- Do NOT omit `CREATE EXTENSION pg_diskann` (it is separate from pgvector)
- Do NOT claim IVFFlat is recommended for new workloads

## On Azure HorizonDB (Preview)

The DiskANN/HNSW/IVFFlat selection, operator/ops-class pairing, and tuning guidance above apply unchanged on HorizonDB (PG 17). Only the differences:

- **Enable through the cluster's parameter group** (see extension-lifecycle), not `az postgres flexible-server parameter set`. There is **no Burstable-tier exclusion** — HorizonDB has no tiers — and DiskANN is the recommended default index.
- **Up to 16000 dimensions.** Preview **filtered search** adds dot-form session GUCs — `diskann.enable_filter_hook`, `diskann.selectivity_min`, `diskann.l_value_is` — set per session with `SET`, not as a cluster-wide default.

```sql no-execute
-- HorizonDB Preview filtered search (session GUCs, dot form)
SET diskann.enable_filter_hook = on;
SET diskann.l_value_is = 100;
```

See [DiskANN vector index](https://learn.microsoft.com/en-us/azure/horizondb/ai/vector-index-diskann) and [Vector index selection guide](https://learn.microsoft.com/en-us/azure/horizondb/ai/vector-index-selection-guide).

## References
- [pg_diskann extension for Azure Database for PostgreSQL](https://learn.microsoft.com/azure/postgresql/flexible-server/how-to-use-pgvector)
- [pgvector extension](https://learn.microsoft.com/azure/postgresql/flexible-server/how-to-use-pgvector)
