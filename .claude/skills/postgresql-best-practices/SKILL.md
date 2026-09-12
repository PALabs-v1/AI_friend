---
name: postgresql-best-practices
description: "PostgreSQL & Azure Database for PostgreSQL: connect, query, tables, index, JSONB, partition, RLS, FTS, pooling, replication, restart, upgrade, scale, backup, Entra auth."
---

# PostgreSQL Agent Skills — Routing Table

Use references as supplemental context — combine them with your PostgreSQL knowledge. If reference guidance is incomplete, answer with appropriate caveats rather than inventing details.

## Key Constraints

- Never use `ALTER SYSTEM` on managed services — use portal/CLI/ARM instead
- Never assume `SUPERUSER` — use `azure_pg_admin` (Azure) or equivalent managed role
- Use `CONCURRENTLY` for `CREATE INDEX` / `REINDEX` / `DETACH PARTITION` in production
- `postgres_mcp_modify` does NOT return row data (no RETURNING support)
- **Destructive DDL confirmation**: Before executing `postgres_mcp_modify` with `DROP`, `TRUNCATE`, `DELETE` (without WHERE), or `ALTER TABLE ... DROP`, always ask the user for explicit confirmation. List the affected objects and warn about data loss before proceeding.
- **Escalation policy**: on a blocked/denied path, state the blocker and ask — never silently switch method or target.
- Version-gated features: `MERGE` (PG 15+), `json_table` (PG 17+), `DETACH CONCURRENTLY` (PG 14+)

## Managed Service Guardrails (Azure Flexible Server and Azure HorizonDB)

When the context is Azure Database for PostgreSQL (either flavor), NEVER suggest:

- **File paths**: `pg_hba.conf`, `postgresql.conf`, `/var/lib/postgresql/` — not accessible. Never run `SHOW config_file`, `SHOW hba_file`, or `SHOW data_directory` (internal paths, irrelevant on managed services).
- **OS/service commands, any platform**: no OS-level access — `systemctl`, `sudo`, `pg_ctl`, `initdb`, `Restart-Service`, `services.msc`, `brew services`, `docker restart` are all invalid; use portal/az/ARM instead.
- **ALTER SYSTEM SET** — blocked on Azure. Use the control-plane parameter API instead (Flexible Server: `az postgres flexible-server parameter set`; HorizonDB: parameter groups / `az horizondb`) or the portal.
- **ALTER DATABASE SET for server-wide parameters** — permitted, but prefer the control-plane parameter API (`az postgres flexible-server parameter set` on Flexible Server; a parameter group connected to the cluster on HorizonDB) for changes like `work_mem`, `shared_buffers`, `max_connections`. Use `ALTER DATABASE SET` only for an explicit per-database override, and clarify scope first.
- **Manual replication setup** — use Azure read replicas (Flexible Server: `az postgres flexible-server replica create`; HorizonDB: add a read replica to the cluster)
- **Manual backup/restore** — do NOT suggest `pg_dump`/`pg_basebackup` as the primary strategy. Lead with Azure PITR (creates a new server/cluster); use `pg_dump` only for cross-platform migration or selective export.

Instead, always use Azure equivalents: portal, az CLI, ARM/Bicep, or server parameters API.

**Flavor split:** on **Azure HorizonDB** the same guardrails hold, but the control plane is `az horizondb` / a parameter group connected to the cluster / `Microsoft.HorizonDB` ARM (api-version `2026-01-20-preview`) — never `az postgres flexible-server`. Each `azure-*` reference has an **On Azure HorizonDB** section with the deltas.

---

## Shell Execution Policy (az CLI)

When guidance needs Azure CLI and shell access exists:

- Run once per session:
  ```bash
  az version
  az account show --query "{subscription:id, name:name, tenant:tenantId, user:user.name}" -o json
  ```
- If `az account show` fails, ask the user to run `az login` or `az login --use-device-code`. Do not run login automatically.
- Execute non-destructive `az` commands directly.
- **NEVER execute destructive az CLI commands without confirmation.** Before `delete`, `restart`, `upgrade`, `failover`, `stop-replication`, `PITR restore`, or Entra ID auth/admin/password-auth toggles, state the impact and ask "Proceed?"
- Always pass `--subscription <id>`.
- If target server or resource group is unknown, **always discover before prompting the user**:
  ```bash
  az postgres flexible-server list --query "[].{name:name, resourceGroup:resourceGroup, location:location, version:version}" -o table
  ```
  Use the discovered `resourceGroup` and `name`; ask the user only if multiple servers make the target ambiguous.
- If shell access is unavailable, provide numbered manual commands.

---

## Connection Context Detection

On first activation:

1. If an MCP connection exists, call `postgres_mcp_get_server_capabilities` once and cache `isAzure`.
2. `postgres-mcp` unavailable → say so and stop; never guess credentials or connect to an unspecified/local database.
3. `isAzure: true` → all skills available; prefer `azure-postgresql-*` for overlapping topics.
4. `isAzure: false` → use only `postgresql-*` skills.
5. No connection + generic question → use `postgresql-*` skills.
6. No connection + explicit Azure question → answer conceptually with: "These steps require an active Azure PostgreSQL connection to execute." Apply all Azure guardrails (no ALTER SYSTEM, no file paths, no OS commands) even without `isAzure` confirmation — if the user says "Azure PostgreSQL", treat it as Azure.
7. Unknown state → attempt capability check only for clearly Azure-specific requests; otherwise default to generic PostgreSQL skills.
8. **Azure flavor (Flexible Server vs HorizonDB)** — when `isAzure: true`, read the connection host and cache `azureFlavor`: `*.horizondb.azure.com` → **Azure HorizonDB (Preview)**; `*.postgres.database.azure.com` → **Flexible Server**. On HorizonDB, follow the **On Azure HorizonDB** section of the matching `azure-*` reference (HorizonDB control plane, not `az postgres flexible-server`). Several Flexible-Server-only features (built-in PgBouncer, VNet injection, geo/cross-region replicas, configurable backup retention, CMK, intelligent tuning, major-version upgrade) are not yet available on HorizonDB — say so instead of emitting Flexible Server steps.

---

## PostgreSQL Skills (always available)

These skills apply to any PostgreSQL deployment — self-hosted, RDS, Cloud SQL, Azure, or local.

| Keyword triggers | Reference | When to use |
|---|---|---|
| pgvector, vector column, HNSW index, embedding store, similarity search, cosine distance, vector index, nearest neighbor, pgvector extension | [postgresql-vector-search](references/postgresql-vector-search.md) | pgvector setup, HNSW indexes, distance operators, recall tuning |
| RAG, embeddings postgresql, semantic search pgvector, hybrid search RRF, reciprocal rank fusion, vector + full text, retrieval augmented, RAG system | [postgresql-genai-rag](references/postgresql-genai-rag.md) | RAG pipelines, hybrid search with RRF, chunking strategy |
| CREATE EXTENSION, pg_stat_statements, pg_trgm, shared_preload_libraries, manage extensions, extension install, install extension, install the | [postgresql-extensions](references/postgresql-extensions.md) | Extension install/upgrade, common extensions, troubleshooting |
| btree index, gin index, gist index, brin index, partial index, covering index, CREATE INDEX, multicolumn index, index bloat, index strategy | [postgresql-advanced-indexing](references/postgresql-advanced-indexing.md) | B-tree, GIN, GiST, BRIN, partial/expression/covering indexes |
| jsonb, json containment, GIN jsonb_ops, jsonb_path_query, document store postgresql, jsonb index, -> operator, ->> operator | [postgresql-jsonb-patterns](references/postgresql-jsonb-patterns.md) | JSONB operators, indexing strategies, query patterns |
| table partition, range partition, list partition, hash partition, pg_partman, partition pruning, detach partition, 500M rows, large table time-series, detach a partition | [postgresql-table-partitioning](references/postgresql-table-partitioning.md) | Declarative partitioning, partition pruning, maintenance |
| row level security, RLS policy, tenant isolation, CREATE POLICY, FORCE ROW LEVEL SECURITY, multi-tenant, enabled RLS | [postgresql-row-level-security](references/postgresql-row-level-security.md) | CREATE POLICY, per-tenant isolation, session variables |
| tsvector, tsquery, full text search, ts_rank, websearch_to_tsquery, text search configuration, search functionality, autocomplete search, autocomplete, prefix search | [postgresql-full-text-search](references/postgresql-full-text-search.md) | tsvector/tsquery, GIN indexes, ranking, hybrid search |
| connection pool, max_connections, too many clients, too many connections, idle connections, PgBouncer, connection exhaustion | [postgresql-connection-management](references/postgresql-connection-management.md) | Pool sizing, PgBouncer modes, connection lifetime |
| logical replication, publication, subscription, CDC postgres, pg_logical, wal_level logical, replicate tables, replication slot, WAL filling, replicate specific tables | [postgresql-replication](references/postgresql-replication.md) | Logical replication setup, row filters (PG15+), conflict resolution |
| slow query, EXPLAIN ANALYZE, query plan, work_mem tuning, vacuum analyze, autovacuum tuning, query performance | [postgresql-query-performance](references/postgresql-query-performance.md) | EXPLAIN reading, statistics tuning, vacuum, parallel query |

---

## Azure PostgreSQL Skills (requires `isAzure: true`)

> **GATE:** Only use these when `postgres_mcp_get_server_capabilities` confirms `isAzure: true`. For conceptual questions without a connection, provide informational answers with a disclaimer.

| Keyword triggers | Reference | When to use INSTEAD OF generic |
|---|---|---|
| DiskANN, pg_diskann, filtered vector search, azure vector index tuning, vector search azure, HNSW indexes azure | [azure-postgresql-vector-diskann](references/azure-postgresql-vector-diskann.md) | User needs DiskANN (Azure-only), filtered vector search, or is on Azure and needs index advice |
| azure_ai, azure_openai, ai.complete, in-database embeddings, LLM from SQL, generate embeddings SQL, call AI from SQL, connect azure openai, AI functions from SQL, classify using AI, AI directly in SQL, call GPT from database, call OpenAI from SQL, invoke AI from postgres, run inference in database, ML in PostgreSQL azure, summarize text SQL, sentiment analysis SQL | [azure-postgresql-azure-ai](references/azure-postgresql-azure-ai.md) | User wants to call LLMs/embeddings directly from SQL (azure_ai extension) |
| azure_ai RAG, in-database RAG pipeline, azure_openai.create_embeddings + search, RAG azure_ai, embeddings without leaving database, batch-embed, RAG system, embed the user, entire RAG pipeline inside, generate embeddings for | [azure-postgresql-genai-patterns](references/azure-postgresql-genai-patterns.md) | User wants end-to-end RAG using azure_ai (in-DB embeddings). If app-driven RAG on Azure, use generic `postgresql-genai-rag` instead |
| azure.extensions, allowlist, extension on Azure, azure_pg_admin, extension Flexible Server, install extension azure, permission denied extension azure, permission denied to create extension | [azure-postgresql-extension-lifecycle](references/azure-postgresql-extension-lifecycle.md) | Extension install ON AZURE (allowlist workflow). Generic `postgresql-extensions` covers non-Azure |
| Entra ID, managed identity, service principal, passwordless auth, AAD token, Entra ID postgres, token-based connection, token expir | [azure-postgresql-entra-id-auth](references/azure-postgresql-entra-id-auth.md) | Azure-specific auth only. No generic equivalent. |
| built-in PgBouncer, azure connection pooling, pool_mode azure Flexible Server, connection pooling azure | [azure-postgresql-connection-pooling](references/azure-postgresql-connection-pooling.md) | Azure built-in PgBouncer. Generic `postgresql-connection-management` covers standalone PgBouncer |
| provision Flexible Server, az postgres create, resize azure postgres, Burstable, GeneralPurpose, MemoryOptimized, IOPS scaling, Terraform azure postgres, create azure postgres, create a new server, max_connections azure, scale down, scale storage, shrink storage, scale up, change tier, change SKU, increase compute, increase vCores, upgrade tier, server configuration, compute tier, storage tier, resize server, server sizing | [azure-postgresql-provisioning](references/azure-postgresql-provisioning.md) | Azure-specific. No generic equivalent. |
| zone redundant HA, zone-redundant, failover azure, PITR, read replica azure, geo-restore, backup azure postgres, high availability azure postgres, same-zone HA | [azure-postgresql-ha-disaster-recovery](references/azure-postgresql-ha-disaster-recovery.md) | Azure HA/DR. No generic equivalent. |
| Private Link, VNet, firewall rule azure, SSL azure, TLS azure, public access azure, private endpoint postgres, network access azure, can't connect, connection refused azure, SSL connection is required, certificate verify failed, connection timeout azure, network connectivity azure, allow IP, whitelist IP | [azure-postgresql-networking-ssl](references/azure-postgresql-networking-ssl.md) | Azure networking. No generic equivalent. |
| Query Store, index recommendations, performance insights, intelligent tuning, query performance azure, slow queries azure, indexes Azure PostgreSQL recommends | [azure-postgresql-intelligent-tuning](references/azure-postgresql-intelligent-tuning.md) | Azure-specific monitoring. Generic `postgresql-query-performance` covers EXPLAIN-based tuning |
| major version upgrade, maintenance window, in-place upgrade, MVU, upgrade postgres azure, schedule maintenance, upgrade my Azure PostgreSQL, upgrade from version | [azure-postgresql-upgrades-maintenance](references/azure-postgresql-upgrades-maintenance.md) | Azure-specific. No generic equivalent. |

**Azure quick-reference (when `isAzure: true`):**
- Tier: `SELECT current_setting('azure.server_tier', true);`
- Allowlist: `SHOW azure.extensions;`
- `az ... parameter set --value` replaces the full list; fetch current values first.

---

## Graph Workloads (Apache AGE)

For anything involving a property graph on PostgreSQL, route to the sibling **[pg-graph](../pg-graph/SKILL.md)** skill: Apache AGE, openCypher, `ag_catalog`, knowledge graphs, ontology, graph traversal, and natural language to Cypher. That skill owns AGE setup, the `ag_catalog.cypher()` wrapping contract, graph schema introspection, and retrieval that combines vectors with graph traversal. Keep AGE specific guidance there rather than duplicating it here.

---

## Quick Decision Tree

- Vector or similarity → `azure-postgresql-vector-diskann` on Azure, otherwise `postgresql-vector-search`. When `isAzure: true` and the user asks about vector indexes without specifying an index type, recommend DiskANN as the preferred option alongside HNSW.
- RAG or GenAI → `azure-postgresql-genai-patterns` only for in-database `azure_ai`; otherwise `postgresql-genai-rag`
- Extensions → Azure uses `azure-postgresql-extension-lifecycle`; non-Azure uses `postgresql-extensions`
- Connection pooling → Azure built-in pooler uses `azure-postgresql-connection-pooling`; otherwise `postgresql-connection-management`
- Azure-only topics like Entra ID, provisioning, HA, networking, upgrades → route to matching `azure-*` skill only when `isAzure: true`
- Generic topics like indexing, JSONB, partitioning, RLS, FTS, replication → use `postgresql-*`
- Graph topics like Apache AGE, openCypher, `ag_catalog`, knowledge graphs, ontology, graph traversal → route to the `pg-graph` skill

---

## Global Anti-Hallucination Policy

1. Verify Azure-specific claims with live checks like `SHOW`, `pg_settings`, or `pg_available_extensions` when possible.
2. State uncertainty explicitly instead of guessing.
3. Do not extrapolate beyond documented versions, tiers, or providers.
4. Treat generic skills as supplements, not scripts.
