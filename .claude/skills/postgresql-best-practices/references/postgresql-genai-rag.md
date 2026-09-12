---
title: "PostgreSQL GenAI RAG"
description: "Build RAG pipelines and semantic search on any PostgreSQL with pgvector — embedding storage, hybrid search, reciprocal rank fusion."
tags: [postgresql, rag, embeddings, vector, hybrid-search, semantic-search, pgvector]
---

# GenAI / RAG Patterns with pgvector

## When to use this skill

Use for production PostgreSQL RAG issues involving:
- Hybrid search design (vector + full-text with RRF)
- Chunking strategy decisions
- Stale embeddings and re-indexing patterns
- RRF smoothing factor tuning

Avoid explaining basic pgvector setup, basic vector search queries, or generic RAG architecture. The base model knows these well.

## Key Facts (what models get wrong)

| Fact | Detail |
|------|--------|
| RRF smoothing constant | Standard k=60: `1/(60 + rank)`. Adjust only if result distributions are heavily skewed |
| Hybrid beats pure vector | Pure vector misses keyword-exact matches; pure text misses semantic similarity. Always combine for production RAG |
| Chunking matters | Embedding a 10K-word doc loses detail. Chunk to 500-1000 tokens with 50-100 token overlap |
| Stale embeddings | If content updates, embeddings MUST be regenerated. Old embeddings return wrong results silently |
| Both indexes required | Hybrid search needs GIN on tsvector column AND HNSW on embedding column |
| websearch_to_tsquery for user input | Use `websearch_to_tsquery` (PG 11+) not `to_tsquery` for user-facing search (handles special chars) |

## Chunking Strategy

| Content type | Chunk size | Overlap |
|---|---|---|
| Documentation | 500-1000 tokens | 50-100 tokens |
| Code | Per function/class | None |
| Conversations | Per message or turn | 1 preceding message |
| Tables/structured | Per row or logical group | None |

## Common Mistakes

1. **[CRITICAL] No index on vector column**: Every similarity query becomes a full table scan
2. **[HIGH] Skipping hybrid search**: Pure vector search misses keyword-exact matches; pure text search misses semantic similarity. Combine both for production RAG
3. **[HIGH] Embedding dimension mismatch**: Column dimension must match model output exactly (1536 for 3-small, 3072 for 3-large)
4. **[MEDIUM] Not chunking large documents**: Embedding a 10K-word doc loses detail. Chunk to 500-1000 tokens with overlap
5. **[MEDIUM] Stale embeddings after content update**: If content changes, embeddings must be regenerated. Use triggers or batch jobs
6. **[MEDIUM] Wrong RRF constant**: k=60 is standard. k too low over-weights top results; k too high flattens rankings
7. **[HIGH] No reranking step**: Top-K vector hits are only a rough proxy. Add a cross-encoder or LLM judge for precision-critical RAG
8. **[MEDIUM] Dedup/overlap retrieval bias**: Overlapping chunks from one document can crowd out others. Dedup by `document_id` or use MMR diversification
9. **[MEDIUM] Metadata filtering vs vector interaction**: Filtering after vector search throws away relevant hits. Filter before retrieval or use a pre-filtered/hybrid index

## When to route to Azure-specific features

- **In-database embeddings (no app roundtrip)**: Route to `azure-postgresql-genai-patterns` (azure_ai extension)
- **DiskANN for filtered search at scale**: Route to `azure-postgresql-vector-diskann`
