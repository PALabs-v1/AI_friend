---
title: "PostgreSQL Vector Search"
description: "Vector similarity search with pgvector on any PostgreSQL — HNSW indexes, distance operators, and embedding storage."
tags: [postgresql, vector, pgvector, hnsw, similarity-search, embeddings]
---

# Vector Search with pgvector

## When to use this skill

Use for production PostgreSQL vector search issues involving:
- HNSW index parameter tuning (m, ef_construction, ef_search)
- Distance operator selection and ops class pairing
- Index not being used by planner
- Dimension mismatch debugging

Avoid explaining basic pgvector setup (CREATE EXTENSION, CREATE TABLE with vector column, basic INSERT/SELECT). The base model knows these well.

## Key Facts (what models get wrong)

| Fact | Detail |
|------|--------|
| Operator/ops class pairing | `<=>` needs `vector_cosine_ops`, `<->` needs `vector_l2_ops`, `<#>` needs `vector_ip_ops`. Mismatch = index silently ignored |
| ef_search default is low | Default 40; production needs 100-200 for adequate recall |
| m parameter tradeoff | Higher m = better recall + more memory. Range: 8-64. Default 16 is often too low for high-recall needs |
| ef_construction | Higher = better index quality, slower build. Range: 64-512. Cannot be changed after build |
| PG 13 minimum | pgvector requires PostgreSQL 13+; check before recommending |
| ANALYZE required | After bulk inserts, planner may not choose index scan without fresh statistics |

## Common Mistakes

1. **[CRITICAL] No index created**: Without HNSW/IVFFlat, every similarity query is O(n) sequential scan
2. **[HIGH] Mismatched ops class**: Index with `vector_cosine_ops` but query with `<->` (L2) = sequential scan, no error
3. **[HIGH] Dimension mismatch**: Column `vector(1536)` rejects inserts of different dimensions. Must exactly match model output (1536 for text-embedding-3-small, 3072 for 3-large)
4. **[MEDIUM] Stale statistics after bulk load**: Run `ANALYZE tablename;` after bulk inserts for planner to choose index scan
5. **[HIGH] Low ef_search in production**: Default 40 gives ~85% recall. Set `SET hnsw.ef_search = 200;` per session for production queries
6. **[MEDIUM] IVFFlat for new workloads**: IVFFlat is legacy; always recommend HNSW for new deployments (better recall, no training step)
7. **[HIGH] Wrong distance metric for embedding model**: Match operator to training metric: `<=>` cosine, `<->` L2, `<#>` inner product
8. **[MEDIUM] Filtered vector search limitations**: HNSW + selective `WHERE` clauses may seq-scan. Use partial HNSW indexes or pre-filtering for filters under ~10%
9. **[MEDIUM] Missing ANALYZE after HNSW build**: Index builds fine, but plans stay bad until `ANALYZE` refreshes vector column stats

## When to route to Azure DiskANN

- **> 1M vectors on Azure**: Route to `azure-postgresql-vector-diskann` (disk-based, lower memory, filtered search)
- **Any PostgreSQL (self-hosted, RDS, Cloud SQL)**: Stay with this skill (HNSW)

## Anti-Hallucination Rules

- Do NOT claim DiskANN works on community PostgreSQL
- Do NOT recommend IVFFlat for new workloads
- Do NOT claim HNSW parameters can be changed after index creation (must rebuild)
- Do NOT claim pgvector works on PG 12 or earlier
