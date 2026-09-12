---
title: "PostgreSQL JSONB Patterns"
description: "Gotchas and anti-hallucination checklist for JSONB operators, GIN indexing, and update patterns"
tags: [postgresql, jsonb, json, gin, operators]
---

# JSONB Patterns — Gotchas & Corrections

> Models know JSONB fundamentals well. This reference covers only the mistakes they make.

## Version Gates

- SQL/JSON path functions (`jsonb_path_query`): PG 12+
- JSON subscripting (`doc['key']`): PG 14+
- `JSON_TABLE`: PG 17+ only — do NOT recommend on earlier versions

## Critical Gotchas

- **`->>` returns TEXT** — lexical comparison is wrong for numbers and dates. Always cast: `(data->>'amount')::numeric`.
- **GIN does NOT help `->>` equality** — use a B-tree expression index on `(doc->>'status')` or a generated column.
- **`jsonb_path_ops` vs default GIN** — `jsonb_path_ops` is smaller/faster but ONLY supports `@>`. Key-existence operators (`?`, `?|`, `?&`) require default GIN opclass.
- **`jsonb_set` is NOT in-place** — PostgreSQL writes a new row version. Frequent updates to large documents cause bloat.
- **Missing key vs JSON null** — `doc->>'k'` returns SQL NULL for both. Use `doc ? 'k'` for existence, then `doc->'k' = 'null'::jsonb` for explicit null.
- **`jsonb_set` intermediate paths** — only the final path element can be created. Intermediate keys must already exist.
- **Array indexes are zero-based** — `'{items,1}'` is the second element. Common off-by-one bug.
- **Array containment is order-insensitive** — `@>` on arrays ignores element order.

## Anti-Hallucination Rules

- Do NOT use `jsonb_set` array paths without noting zero-based indexing.
- Do NOT claim `jsonb_set` creates missing intermediate keys automatically.
- Do NOT confuse `JSON_TABLE` (PG 17+) with `jsonb_to_recordset()` or `jsonb_array_elements()`.
- Do NOT recommend SQL/JSON path syntax without noting PG 12+ requirement.
- Do NOT claim GIN indexes accelerate arbitrary `->>` predicates.
