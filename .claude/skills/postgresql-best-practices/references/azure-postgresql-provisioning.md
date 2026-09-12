---
title: "Azure PostgreSQL Provisioning"
description: "Provision Azure Database for PostgreSQL Flexible Server: SKU selection, storage configuration, Terraform/Bicep templates"

tags: [azure, postgresql, provisioning, terraform, bicep, sku, storage]

---

# Provisioning Azure Database for PostgreSQL Flexible Server

## Ambiguous "create a server" requests

If the session already has an Azure connection and the user asks to "create a new PostgreSQL server" without saying local or Azure, default to `az postgres flexible-server create` (ask only for required specs like tier/storage) — do not build a local Docker/`initdb` server unless the user explicitly asks for a local/test/community instance. Any locally-spawned dev/test server (Docker, `initdb`) must use password authentication; never `POSTGRES_HOST_AUTH_METHOD=trust` or other trust auth.

## Response focus

Prioritize the constraints that are hard to reverse after creation: network mode, HA mode, zone placement, storage growth, backup economics, and SKU-format mismatches across tooling. Skip generic Azure provisioning walkthroughs.

## Non-obvious facts agents often miss

> **⚠️ Confident Hallucination Correction:**
> **❌ WRONG: "You can't change from Burstable to General Purpose without creating a new server."** ✅ CORRECT: You can change between **all tiers** (Burstable ↔ GP ↔ MO) in-place via `az postgres flexible-server update --tier`. The server restarts but no data migration is needed. However, Burstable has feature limitations (no provisioned IOPS, no DiskANN, no HA) — plan for these before choosing it.

- **Storage never shrinks**: manual increases and auto-grow are permanent.
- **Network mode is effectively a create-time decision**: public vs private/VNet cannot be flipped casually later.
- **SKU names differ by tool**: CLI uses names like `Standard_D4ds_v5`; Terraform uses tier-prefixed names like `GP_Standard_D4ds_v5`.
- **Burstable has feature gaps vs GP/MO**: no provisioned IOPS, no DiskANN, no HA. Tier changes between all tiers (including Burstable↔GP↔MO) are supported in-place with a restart.
- **Zone placement is sticky**: changing AZ usually means reprovision + migration.
- **Terraform storage uses MB**: `storage_mb`, not `storage_gb`.

## Provisioning decisions that deserve extra scrutiny

1. **Network first**: decide public access vs delegated subnet/VNet before you script server creation.
2. **HA + zone together**: treat HA mode and AZ placement as one decision, not two independent toggles.
3. **Storage + backup economics together**: auto-grow protects uptime but can also increase backup cost later.
4. **Post-create baseline immediately**: provisioning is not done after the `create` call; set sane parameters and observability right away.

## Immediate post-create baseline

Provisioning is not complete after `az postgres flexible-server create` or Terraform apply. Recommend an immediate baseline pass for:
- parameter review (`shared_buffers`, connection limits, Query Store capture mode)
- storage and backup alerts
- maintenance window / patching expectations
- HA validation and failover expectations
- network reachability from the real application subnet

## Critical Gotchas

1. **[HIGH] HA + zone + network constraints compound**: Choosing zone-redundant HA at creation limits later zone changes and requires networking/subnet planning that works across both zones. Treating HA, AZ, and VNet as separate decisions leads to irrecoverable conflicts.

2. **[HIGH] Storage is permanent**: Start conservative but realistic, with auto-grow enabled if downtime from full disks is worse than cost overrun. Neither provisioned storage nor auto-grown storage can be reduced later.

3. **[HIGH] Burstable has feature limitations**: Agents often pitch Burstable as equivalent to GP/MO but cheaper. In practice, Burstable lacks provisioned IOPS, DiskANN, and HA. Tier changes are supported in-place (with restart), but plan for feature gaps.

4. **[HIGH] SKU format varies by tool**: CLI omits the tier prefix; Terraform requires it. Copy-pasting the same SKU string between tools is a common failure.

5. **[MEDIUM] Parameter defaults after create**: New servers start with conservative defaults. `shared_buffers` is roughly 25% of RAM, and `max_connections` varies by SKU. The agent should recommend an immediate baseline review after provisioning instead of stopping at the create command.

6. **[MEDIUM] Backup cost surprise**: Backup storage beyond 1x provisioned size is billed. Write-heavy or churn-heavy workloads accumulate WAL and snapshot history faster than teams expect, and backup usage can exceed provisioned storage within weeks.

7. **[MEDIUM] Auto-grow hides future cost and IOPS changes**: Auto-grow prevents outages, but every growth step is permanent and changes your storage footprint. Monitor `storage_percent` and forecast growth instead of waiting for emergency expansion.

8. **[MEDIUM] IOPS advice must be tier-aware**: Burstable has a fixed cap. Extra IOPS provisioning is for GP/MO scenarios; suggesting it on Burstable is wrong.

9. **[MEDIUM] HA doubles more than the architecture diagram suggests**: Budgeting only for the primary node misses the extra compute cost and operational constraints that come with HA.

## Anti-Hallucination Rules

- Do NOT claim storage can be reduced after provisioning.
- Do NOT claim VNet/public connectivity can be freely changed later.
- Do NOT use CLI SKU format (`Standard_D4ds_v5`) in Terraform; use the tier-prefixed format.
- Do NOT claim Burstable supports provisioned IOPS or DiskANN.
- Do NOT claim Burstable ↔ GP/MO tier change is impossible — it IS supported in-place (with restart).
- Do NOT invent exact `max_connections` values without checking the chosen SKU.
- Do NOT claim Terraform uses `storage_gb`; the field is `storage_mb`.
- When exact limits vary by SKU or region, say so explicitly instead of guessing.

## On Azure HorizonDB (Preview)

HorizonDB is a **cluster** (`Microsoft.HorizonDB/clusters`), not a Flexible Server, and provisions very differently:

- **CLI is `az horizondb`, not `az postgres flexible-server`.** Install with `az extension add --name horizondb`. Many operations aren't in the CLI yet and use `az rest` against `Microsoft.HorizonDB` at api-version `2026-01-20-preview`.
- **Compute and storage are independent.** Scale compute with `az horizondb update --v-cores <n>` (causes a restart); storage is untouched.
- **Compute and storage are independent.** Scale compute with `az horizondb cluster update --v-cores <n>` (causes a restart); storage is untouched.
- **HA at create** with `--replica-count` and `--zone-placement-policy` (`BestEffort` | `Strict`); replicas double as read scale-out (up to 15).

```bash
# Create a HorizonDB cluster (no tier/SKU, no storage size)
az extension add --name horizondb
az horizondb create \
  --resource-group myRG --name mycluster --location eastus \
  --version 17 --v-cores 2 --replica-count 2 --zone-placement-policy Strict \
  --administrator-login myadmin --administrator-login-password '<pwd>'

# Scale compute (brief restart); storage is automatic
az horizondb cluster update --resource-group myRG --name mycluster --v-cores 4
```

Not yet available on HorizonDB (do not recommend): VNet injection, customer-managed keys, configurable backup retention, cross-region/geo replicas, and built-in PgBouncer. See [Create an Azure HorizonDB cluster](https://learn.microsoft.com/en-us/azure/horizondb/configure-maintain/quickstart-create-cluster) and [Scale compute](https://learn.microsoft.com/en-us/azure/horizondb/configure-maintain/how-to-scale-compute).

## References
- [Compute and storage options](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-compute-storage)
