---
title: "PostgreSQL Row Level Security"
description: "Gotchas and anti-hallucination checklist for RLS policies and multi-tenant isolation"
tags: [postgresql, rls, security, multi-tenant, policies]
---

# Row-Level Security — Gotchas & Corrections

> Models know RLS fundamentals well. This reference covers only the mistakes they make.

## Version Gates

- RLS introduced: PG 9.5+
- `AS RESTRICTIVE` policies (AND-style): PG 10+
- `security_invoker = true` views: PG 15+

## Critical Gotchas

- **Owner bypasses RLS** — table owner ignores all policies unless you run `ALTER TABLE t FORCE ROW LEVEL SECURITY`. Tests pass as owner, production fails for app roles.
- **Session `SET` unsafe with transaction pooling** — use `SET LOCAL app.current_tenant = '...'` inside a transaction. Session-level `SET` disappears on the next PgBouncer statement in transaction mode.
- **`USING` vs `WITH CHECK`** — `USING` filters reads; `WITH CHECK` controls allowed writes. A policy with only `USING` copies it to `WITH CHECK` for `ALL` commands, but a `SELECT`-only policy does NOT enable INSERT.
- **Permissive policies OR together** — same-command permissive policies combine with OR, not AND. For AND logic, use `AS RESTRICTIVE` (PG 10+).
- **`SECURITY DEFINER` bypasses RLS** — functions run with owner privileges and can leak cross-tenant data.
- **Views use owner context** — pre-PG15, views always execute as the view owner, bypassing caller's RLS. Use `security_invoker = true` on PG 15+.
- **`BYPASSRLS` attribute** — avoid granting unless deliberate. Check with: `SELECT rolname, rolbypassrls FROM pg_roles`.
- **Enable before adding policy = immediate deny** — turning on RLS before creating a policy gives non-owner roles default-deny immediately.
- **Index the policy column** — without an index on `tenant_id`, RLS turns every request into a filtered Seq Scan.
- **Use `current_setting('app.tenant', true)`** — the `true` (missing_ok) avoids hard errors when the variable is unset; returns NULL instead.

## Minimal Setup Pattern

```sql
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON orders
  USING (tenant_id = current_setting('app.tenant_id')::uuid);

-- Per-request: set_config is transaction-safe with pooling
SELECT set_config('app.tenant_id', $1, true);
```

```sql
-- Verify RLS is enforced for a role
SET ROLE app_user;
SELECT * FROM orders;  -- only sees own tenant rows
RESET ROLE;
```

## Anti-Hallucination Rules

- Do NOT claim table owners obey RLS unless `FORCE ROW LEVEL SECURITY` is enabled.
- Do NOT claim `USING` alone controls INSERT or UPDATE acceptance.
- Do NOT treat `SECURITY DEFINER` as RLS-safe by default.
- Do NOT assume views respect caller RLS on PG versions before `security_invoker = true` views.
- Do NOT recommend session `SET` for tenant context behind transaction pooling.
