---
title: "Azure PostgreSQL Upgrades Maintenance"
description: "Azure Database for PostgreSQL Flexible Server major version upgrades, maintenance windows, and in-place upgrade procedures"
tags: [azure, postgresql, upgrade, major-version, maintenance, mvu]
---

# Upgrades and Maintenance

> **Response focus:** Prioritize MVU-is-one-way, version-skipping-supported, validate-before-upgrade, and post-upgrade ANALYZE. Avoid explaining basic PostgreSQL version features or generic upgrade concepts.

> **Shell execution:** Use the Azure Portal "Validate" button or CLI validation before upgrading. The actual `upgrade` command is destructive (irreversible, causes downtime) and requires user confirmation. Restart after upgrade also requires confirmation.

## ⚠️ Confident Hallucination Corrections

These facts are ones models **confidently get wrong**. Override any prior belief:

- **❌ WRONG: "You must upgrade one version at a time (13→14→15→16)."** ✅ CORRECT: Azure Flexible Server uses `pg_upgrade` and **supports skipping versions** — you can go directly from PG 13→16 in a single MVU operation.
- **❌ WRONG: "Just run the upgrade command."** ✅ CORRECT: Always run `--validate-only` first. It catches extension blockers, disk space issues, and connection problems in 2-5 minutes without committing.
- **❌ WRONG: "You can downgrade if something goes wrong."** ✅ CORRECT: MVU is **irreversible**. Rollback = PITR restore to a NEW server.

## Key Facts (what models get wrong)

| Fact | Detail |
|------|--------|
| MVU is one-way | Cannot downgrade. Rollback = PITR restore to NEW server |
| Skip-version supported | Can upgrade directly (e.g., 13→16) via pg_upgrade — no sequential requirement |
| Validate before upgrade | Use Azure Portal "Validate" button or run validation checks before committing (2-5 min) |
| Maintenance window | Only controls MINOR patches. MVU runs when you execute it |
| HA servers | Primary + standby both upgrade. Failover adds 30-60s |
| Read replicas | Must upgrade separately AFTER primary. Version mismatch breaks replication |
| Disk requirement | 10-20% free space minimum for upgrade process |
| Post-upgrade | `ANALYZE;` immediately (pg_statistic is stale). Then update extensions |

## Downtime Estimates

| Scenario | Expected Downtime |
|----------|------------------|
| Simple (no HA) | 5-15 min |
| HA (zone-redundant) | 15-30 min |
| Large databases (>500GB) | 30+ min |

## Version EOL Dates

| Version | End of Life |
|---------|-------------|
| PG 13 | Nov 2025 |
| PG 14 | Nov 2026 |
| PG 15 | Nov 2027 |
| PG 16 | Nov 2028 |

## Critical Gotchas

1. **Validate before upgrade**: Use Azure Portal "Validate" button to catch extension blockers, disk space issues, and connection problems before committing (2-5 min check)
2. **Extension compatibility**: `pg_partman`, `postgis` are common MVU blockers. Check before, update after
3. **ANALYZE after upgrade**: Planner has no stats for new version. Queries regress until you run `ANALYZE;`
4. **ALTER EXTENSION UPDATE**: Run for each extension post-MVU to get PG-version-compatible builds
5. **No ALTER SYSTEM**: Use `az postgres flexible-server parameter set` or Portal. OS-level tools unavailable
6. **[HIGH] Extension-specific upgrade blockers**: `pg_partman`, `postgis`, and `timescaledb` commonly block MVU. Use the Portal's Validate button to check, then update blockers before the real upgrade
7. **[MEDIUM] App SQL behavior changes between major versions**: PG 15 changed default `public` schema permissions and PG 14 tightened some `GROUP BY` behavior. Test app queries, not just the upgrade command
8. **[MEDIUM] Blue-green upgrade with read replicas**: Replica -> upgrade replica -> promote -> switch DNS is a valid low-downtime path. Offer it when MVU downtime is unacceptable

## Anti-Hallucination Rules

- Cannot downgrade after MVU
- Supports skipping versions (e.g., 13→16 directly via pg_upgrade)
- Maintenance windows do NOT control MVU timing
- Read replicas do NOT auto-upgrade with primary
- **10-20% free disk space required** for MVU to proceed (pre-check fails otherwise)

## On Azure HorizonDB (Preview)

- **PostgreSQL 17 only.** There is no major-version upgrade path to configure — do not use the Flexible Server `az postgres flexible-server upgrade` flow. Clusters are created on PG 17 and stay there.
- **Minor updates are applied by the platform** through planned failovers, so keep ≥2 replicas to make patching non-disruptive.
- **Maintenance windows exist but are system-managed;** a fully customer-configurable window is not yet available. Plan around platform maintenance rather than scheduling `az postgres flexible-server` maintenance.

See [Maintenance](https://learn.microsoft.com/en-us/azure/horizondb/configure-maintain/concepts-maintenance) and [Supported PostgreSQL versions](https://learn.microsoft.com/en-us/azure/horizondb/parameters/parameters-version-platform-compatibility-postgresql-versions).

## References
- [Major version upgrades](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-major-version-upgrade)
- [Scheduled maintenance](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-maintenance)
