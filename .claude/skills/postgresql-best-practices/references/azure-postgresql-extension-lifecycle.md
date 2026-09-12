---
title: "Azure PostgreSQL Extension Lifecycle"
description: "Manage PostgreSQL extensions on Azure Database for PostgreSQL Flexible Server: allowlisting, shared_preload_libraries, CREATE EXTENSION, and version upgrades"

tags: [azure, postgresql, extensions, allowlist, shared-preload, pgvector]

---

# Extension Lifecycle

> **Response focus:** Prioritize the allowlist-then-create workflow, binary name mapping, and shared_preload restart requirement. Avoid explaining basic `CREATE EXTENSION` syntax or generic PostgreSQL extension concepts.

## Prerequisites

- `azure_pg_admin` role (default admin role; never superuser)
- **Shell execution:** Steps 1-3 below require az CLI (execute directly via shell). Step 4 (`CREATE EXTENSION`) is executable via `postgres_mcp_modify`. Server restart requires user confirmation.

## Instructions

**Step 1: Check if extension is available**

```sql
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'vector'
ORDER BY name;
```

**Step 2: Allowlist the extension (Azure-specific requirement)**

```bash
# Add to azure.extensions server parameter
az postgres flexible-server parameter set \
    --resource-group myRG --server-name myserver \
    --name azure.extensions --value "vector,pg_stat_statements,pg_cron"
```

> WARNING: This overwrites the current list. Always GET current value first and append.

**Step 3: For extensions requiring preload (pg_stat_statements, pg_cron, auto_explain)**

```bash
# Requires server RESTART
az postgres flexible-server parameter set \
    --resource-group myRG --server-name myserver \
    --name shared_preload_libraries --value "pg_stat_statements,pg_cron"

az postgres flexible-server restart --resource-group myRG --name myserver
```

**Step 4: Create the extension**

```sql
-- Use binary name, not marketing name
CREATE EXTENSION vector;           -- NOT pgvector
CREATE EXTENSION pg_stat_statements;
CREATE EXTENSION azure_ai;
```

**Step 5: Upgrade an extension**

```sql
ALTER EXTENSION vector UPDATE TO '0.8.0';
```

### Verify

```sql
-- Confirm extension installed
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';

-- List all installed extensions
SELECT extname, extversion FROM pg_extension ORDER BY extname;

-- Verify shared_preload_libraries
SHOW shared_preload_libraries;
```

## Common Mistakes

1. **[CRITICAL] Allowlist append workflow**: `az postgres flexible-server parameter show --name azure.extensions` returns current list. You must append, not replace: `az postgres flexible-server parameter set --name azure.extensions --value "vector,pg_diskann,pg_trgm,NEW_EXT"`. Setting `--value "NEW_EXT"` alone REMOVES all existing extensions

   Wrong:
   ```bash
   # OVERWRITES entire list removes all other extensions!
   az postgres flexible-server parameter set --name azure.extensions --value "pg_cron"
   ```

   Right:
   ```bash
   # First GET current list, then APPEND
   az postgres flexible-server parameter show --name azure.extensions --query value
   # Returns: "vector,pg_stat_statements"
   az postgres flexible-server parameter set --name azure.extensions --value "vector,pg_stat_statements,pg_cron"
   ```

2. **[HIGH] `shared_preload_libraries` restart behavior**: Adding to this parameter requires server restart. Plan maintenance window. Current value check: `SHOW shared_preload_libraries`. Common values needing preload: `pg_cron`, `pg_stat_statements`, `auto_explain`
3. **[HIGH] Binary name vs marketing name mapping**: `pgvector` `CREATE EXTENSION vector`. `pg_partman` `CREATE EXTENSION pg_partman`. `PostGIS` `CREATE EXTENSION postgis`. Always verify with `SELECT * FROM pg_available_extensions WHERE name LIKE '%search%'`

   Wrong:
   ```sql
   CREATE EXTENSION pgvector;
   -- ERROR: extension "pgvector" is not available
   ```

   Right:
   ```sql
   CREATE EXTENSION vector;  -- binary name, not marketing name
   ```

4. **[MEDIUM] Version pinning for reproducibility**: `CREATE EXTENSION vector VERSION '0.7.0'` ensures same version across environments. Without version, server's `default_version` is used which auto-updates with server patches. Pin in migration scripts
5. **[MEDIUM] Extension availability matrix**: Available: `vector`, `pg_diskann`, `postgis`, `pg_cron`, `pg_partman`, `pg_stat_statements`, `pg_trgm`, `hstore`, `uuid-ossp`, `azure_ai`. NOT available: `file_fdw`, `plpython3u`, `adminpack`, `dblink` (to external). Check: `SELECT * FROM pg_available_extensions ORDER BY name`
6. **[HIGH] `azure_pg_admin` role limitations**: You have `azure_pg_admin`, not superuser. Cannot: `CREATE EXTENSION` for unlisted extensions, load custom C libraries, modify `pg_hba.conf`. Can: create any extension in the allowlist, manage roles, create databases

   Wrong:
   ```sql
   -- Attempting superuser-only operations
   ALTER SYSTEM SET shared_preload_libraries = 'pg_cron';
   -- ERROR: must be superuser to execute this command
   ```

   Right:
   ```bash
   # Use Azure CLI for postmaster-level GUCs
   az postgres flexible-server parameter set --name shared_preload_libraries --value "pg_cron"
   az postgres flexible-server restart --resource-group myRG --name myserver
   ```

7. **[CRITICAL] Dependency checking before DROP**: `SELECT classid::regclass, objid, deptype FROM pg_depend WHERE refobjid = (SELECT oid FROM pg_extension WHERE extname = 'vector')` shows what depends on the extension. CASCADE drops all dependent objects (indexes, columns)
8. **[HIGH] Extension update path**: `ALTER EXTENSION vector UPDATE TO '0.8.0'` only works if update path exists. Check: `SELECT * FROM pg_extension_update_paths('vector') WHERE source = '0.7.0'`. Some updates require DROP + CREATE (data loss for extension-managed types)
9. **[CRITICAL] "extension not allowlisted" error**: Run Step 2 to add the extension to the `azure.extensions` server parameter. Remember to include all existing extensions in the value
10. **[HIGH] "must be loaded via shared_preload_libraries" error**: Run Step 3 and restart the server. The restart is required for the parameter change to take effect
11. **[HIGH] Exact error recognition**: `ERROR: extension "X" is not available` = not in allowlist; run `SHOW azure.extensions`. `ERROR: could not open extension control file` = allowlisted but `shared_preload_libraries` missing; add it and restart. `ERROR: permission denied to create extension` = session user lacks `azure_pg_admin`
12. **[HIGH] Version/region extension availability drift**: An extension available in East US may still be missing in West Europe. Check `SELECT * FROM pg_available_extensions` on the specific target server
13. **[MEDIUM] Extension dependency chains on drop/upgrade**: `DROP EXTENSION vector CASCADE` also removes `pg_diskann` indexes. For upgrades, dependent extensions may need updates before `ALTER EXTENSION ... UPDATE`
14. **[MEDIUM] CLI vs Portal parameter precedence**: CLI and Portal write the same backend setting, but the Portal can lag by 1-2 minutes. Verify the live value with `SHOW` after changes

## On Azure HorizonDB (Preview)

- **The `azure.extensions` allowlist is set on a parameter group, not per-server.** Parameter groups are first-class resources attached to the cluster (default `default_pg17`). To change `azure.extensions`, create a new parameter group with the desired allowlist and attach it to the cluster, then run `CREATE EXTENSION`. Preload-required libraries still go in `shared_preload_libraries` (static → restart).

- **The `azure.extensions` allowlist is set on a parameter group, not per-server.** A parameter group is a first-class Azure resource attached to the cluster (default `default_pg17`); edit `azure.extensions` there instead of `az postgres flexible-server parameter set`, then run `CREATE EXTENSION`. Preload-required libraries still go in `shared_preload_libraries` (static → restart).
- PostgreSQL **17 only**; HorizonDB additionally ships `pg_textsearch` (BM25 full-text) and `pg_diskann`.

```bash
# HorizonDB: allowlist lives on the cluster's parameter group
az horizondb parameter-group update \
  --resource-group myRG --name default_pg17 \
  --parameters '[{"name":"azure.extensions","value":"vector,pg_diskann,pg_textsearch"}]'
```

See [Extensions in HorizonDB](https://learn.microsoft.com/en-us/azure/horizondb/extensions/concepts-extensions), [Allow extensions](https://learn.microsoft.com/en-us/azure/horizondb/extensions/how-to-allow-extensions), and [Parameter groups](https://learn.microsoft.com/en-us/azure/horizondb/parameters/concepts-parameter-groups).

## References
- [Extensions in Azure Database for PostgreSQL](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-extensions)
- [How to use extensions](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-extensions)

## Anti-Hallucination Rules

- Do NOT claim an extension is available on Azure without verification via `SELECT * FROM pg_available_extensions WHERE name = '...'`. Extension availability varies by PG major version, region, and service tier.
- Do NOT invent extension names. Always verify the binary name (e.g., `vector` not `pgvector`, `postgis` not `PostGIS`).
- Do NOT claim `ALTER SYSTEM` works on Azure Flexible Server — it requires superuser which is not available.
- Do NOT suggest granting superuser or creating superuser roles — Azure only provides `azure_pg_admin`.
- Do NOT assume `shared_preload_libraries` can be changed without a server restart.
- Do NOT claim extensions from community PostgreSQL are automatically available on Azure — they must be in the Azure allowlist.
- Do NOT confuse extension availability (visible in `pg_available_extensions`) with allowlist status (visible in `SHOW azure.extensions`). An extension must be allowlisted AND available.
- Do NOT claim `dblink` to external servers, `file_fdw`, `plpython3u`, or `adminpack` are available — they are blocked on Azure.
- When uncertain about extension support, say "verify availability with `SELECT * FROM pg_available_extensions`" rather than asserting it exists.