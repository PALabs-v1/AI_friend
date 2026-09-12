---
title: "Azure PostgreSQL Azure AI"
description: Configure the azure_ai extension and use Azure OpenAI functions (generate, classify, extract, score) directly in SQL on Azure Database for PostgreSQL.
tags: [azure, postgresql, azure-ai, azure-openai, generate, extract, summarize, classify, ai-functions, managed-identity]
---

## Key Facts (what models get wrong)

> **Response focus:** Prioritize deployment name vs model name confusion, function schema (`azure_openai` not `azure_ai`), endpoint format, managed identity RBAC, and the Azure control-plane allowlist workflow. Never use `ALTER SYSTEM` on Azure managed PostgreSQL.

| Fact | Detail |
|------|--------|
| create() vs create_embeddings() | `azure_openai.create()` = text generation (returns text). `azure_openai.create_embeddings()` = vector embeddings (returns vector type) |
| Function schema | Functions live in `azure_openai` schema (not `azure_ai`). Config via `azure_ai.set_setting()` |
| Deployment name not model name | First arg is your deployment name (e.g., `my-gpt4o`), NOT the model name (`gpt-4o`) |
| Endpoint format | `https://<resource>.openai.azure.com` with no trailing slash, no `/openai/` path |
| Managed identity RBAC | Server identity needs "Cognitive Services OpenAI User" role on the OpenAI resource; propagation up to 10 min |
| azure.extensions allowlist | Add `azure_ai` through the Azure control plane before `CREATE EXTENSION`; `ALTER SYSTEM` is not supported |
| Single-text-per-call | Each `create()` call processes one text value; batch with SQL LIMIT + loops |
| Key stored per-session | `set_setting()` values are session-scoped unless persisted via server parameters |

## Decision Matrix

| Auth Method | When to Use | Setup |
|-------------|-------------|-------|
| API Key | Dev/test, quick start | `azure_ai.set_setting('azure_openai.subscription_key', '<key>')` |
| Managed Identity | Production, no secrets in DB | Grant RBAC role to server identity; no key needed |

## Critical Gotchas

1. **Extension not in allowlist**: Add `azure_ai` to the `azure.extensions` server parameter through Azure CLI, portal, or ARM. This requires Azure control-plane permissions. The database admin role is used afterward for `CREATE EXTENSION`.

   ```bash
   # Read the current comma-separated allowlist first.
   az postgres flexible-server parameter show \
     --resource-group myRG --server-name myserver \
     --name azure.extensions --query value --output tsv

   # Set the complete updated list; this operation replaces the existing value.
   az postgres flexible-server parameter set \
     --resource-group myRG --server-name myserver \
     --name azure.extensions --value "existing_extension,azure_ai"
   ```

   Then connect as an authorized database administrator:

   ```sql
   CREATE EXTENSION IF NOT EXISTS azure_ai;
   ```
2. **Wrong endpoint format**: No trailing slash, no `/openai/` suffix
3. **Deployment name is case-sensitive**: Must match exactly what is in Azure OpenAI Studio
4. **Rate limiting (429)**: Batch with `LIMIT 50-100` per query; use `pg_sleep()` between batches
5. **RBAC propagation delay**: Managed identity role assignment takes up to 10 minutes to propagate
6. **Temperature for classification**: Always set `temperature => 0.0` for deterministic tasks
7. **NULL results**: Usually means deployment name is wrong, endpoint is wrong, or model is not deployed
8. **403 Forbidden**: RBAC not assigned or `azure_pg_admin` role missing
9. **[HIGH] Transient 401/403 retry**: Managed identity tokens can expire mid-batch. Retry auth failures with backoff too; token refresh is automatic but may lag by 1-2 seconds
10. **[MEDIUM] Quota/cost debugging beyond 429**: `azure_ai.create_embeddings()` can fail from quota exhaustion, not just rate limits. Check Azure OpenAI Portal metrics; quota is monthly/deployment-scoped

## Anti-Hallucination Rules

- Do NOT use model name (e.g., `gpt-4o`) as the first argument; use deployment name
- Do NOT use `ALTER SYSTEM SET azure.extensions`; use Azure CLI, portal, or ARM
- Do NOT claim `azure_openai.create()` returns embeddings; it returns text
- Do NOT omit the allowlist step; CREATE EXTENSION will fail without it
- Do NOT add trailing slash or path segments to the endpoint URL
- Do NOT claim batch/array input is supported; it is one text per call

## On Azure HorizonDB (Preview)

The `azure_ai`/`azure_openai` function usage, endpoint, and deployment-name rules above apply on HorizonDB. What HorizonDB adds (extension v2.2.1):

- **Auto-provisioned default models.** AI Model Management supplies `default-chat`, `default-embedding`, and `default-reranker`, so functions work without wiring up your own Azure OpenAI deployment first (pass the default name instead of a deployment). Register more with `model_registry.model_add()`.
- **Extra in-database functions** beyond `create()`/`create_embeddings()`: `azure_ai.generate()`, `azure_ai.extract()`, `azure_ai.is_true()`, and `azure_ai.rank()` (semantic rerank, default `Cohere-rerank-v4.0-fast`).
- **Enablement** is through the cluster's parameter group (see extension-lifecycle), not `az postgres flexible-server parameter set`.

See [AI functions](https://learn.microsoft.com/en-us/azure/horizondb/ai/ai-functions) and [AI model management](https://learn.microsoft.com/en-us/azure/horizondb/ai/ai-model-management).

## References
- [Azure AI extension for Azure Database for PostgreSQL](https://learn.microsoft.com/azure/postgresql/flexible-server/generative-ai-azure-overview)
- [Integrate Azure AI services](https://learn.microsoft.com/azure/postgresql/flexible-server/generative-ai-azure-openai)
