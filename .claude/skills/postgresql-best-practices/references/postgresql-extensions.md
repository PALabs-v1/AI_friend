---
title: "PostgreSQL Extensions"
description: "Managing PostgreSQL extensions on any deployment — install, upgrade, version checks, and common extensions."
tags: [postgresql, extensions, contrib, pg_stat_statements, pg_trgm]
---

# PostgreSQL Extensions

## When to use this skill

Use for generic PostgreSQL extension issues involving:
- `shared_preload_libraries` requirements and restart behavior
- Extension install failures on self-hosted PostgreSQL
- Extension version management and upgrade paths

For Azure-specific extension workflow (allowlisting, azure_pg_admin), route to `azure-postgresql-extension-lifecycle` instead.

Avoid explaining basic `CREATE EXTENSION` syntax. The base model knows this well.

## Key Facts (what models get wrong)

| Fact | Detail |
|------|--------|
| shared_preload_libraries needs restart | Extensions like pg_stat_statements, pg_cron, auto_explain won't work without preload + restart |
| Binary name vs marketing name | `pgvector` → `CREATE EXTENSION vector`. `PostGIS` → `CREATE EXTENSION postgis`. Always check `pg_available_extensions` |
| Extension files must exist on disk | `CREATE EXTENSION` fails if .so/.control files aren't installed via OS package manager |
| Schema ownership | Extensions install into `public` by default. Use `CREATE EXTENSION ... SCHEMA myschema;` for isolation |
| Version pinning | `CREATE EXTENSION vector VERSION '0.7.0';` ensures reproducibility across environments |

## Extensions Requiring shared_preload_libraries

| Extension | Purpose | Restart required? |
|---|---|---|
| `pg_stat_statements` | Query performance statistics | YES |
| `pg_cron` | Job scheduling | YES |
| `auto_explain` | Auto-log slow query plans | YES |
| `pg_trgm` | Trigram similarity | No |
| `vector` (pgvector) | Vector similarity search | No |
| `postgis` | Geospatial data | No |

## Common Mistakes

1. **[CRITICAL] Extension files not installed**: `apt install postgresql-16-pgvector` or equivalent before `CREATE EXTENSION`
2. **[HIGH] Forgetting shared_preload_libraries**: pg_stat_statements collects no data without preload. Requires server restart. Modifying `shared_preload_libraries` requires superuser or platform admin role
3. **[HIGH] Privilege requirement for CREATE EXTENSION**: Requires superuser or platform admin role. Non-privileged users get "permission denied to create extension"
4. **[MEDIUM] Schema pollution**: Many extensions in `public` schema. Isolate with `CREATE EXTENSION ... SCHEMA extensions;`
5. **[MEDIUM] Upgrade without checking path**: `ALTER EXTENSION ... UPDATE TO '0.8.0'` only works if update path exists. Check `pg_extension_update_paths()`
6. **[HIGH] Version incompatibility across replicas**: Upgrade extension binaries on replicas first, then primary, or extension DDL can break replication
7. **[MEDIUM] CASCADE drop removes dependent objects silently**: `DROP EXTENSION ... CASCADE` can remove views, functions, and indexes that depend on extension types
8. **[MEDIUM] Extension not in search_path**: `CREATE EXTENSION ... SCHEMA ext` works, but calls fail unless `ext` is in `search_path` or functions are schema-qualified

## Azure differences

On Azure Database for PostgreSQL, the extension workflow is entirely different:
- Extensions must be **allowlisted** before installation
- No superuser — use `azure_pg_admin` role
- `shared_preload_libraries` managed via Azure Portal/CLI, not postgresql.conf

→ Route to `azure-postgresql-extension-lifecycle` for the Azure workflow.
