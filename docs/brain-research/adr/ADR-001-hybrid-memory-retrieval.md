# ADR-001 — Relevance-first hybrid memory retrieval

**Status:** Accepted, shipped as default (`MEMORY_RANKING_POLICY="hybrid"`).
**Date:** 2026-09-24. **Evidence:** `../03-memory-retrieval.md`.

## Problem

V1 retrieval returned the memory a question was about in its top 3 for 2–4%
of questions on the SQLite path (M-1, M-2) and 41–58% even when the candidate
pool was chosen by vector similarity. Two independent defects:

1. **Candidate selection.** SQLite scored only the 20 most recently touched
   rows; Postgres took the top 20 by an ACT-R score in which similarity is a
   minor term. The relevant memory was usually never scored.
2. **Scoring.** Raw terms on incompatible scales were added: cosine spans ~0.4
   across a store, recency ~4.5, frequency ~2.3, a substring keyword hit +5.0.
   Relevance barely moved the ranking, "much" in "pretty much" earned +5, and
   a fact repeated three times beat the fact asked about.

## Alternatives (all measured on identical scenarios)

| Option | Worst cell (held-out) | Why not / why |
|---|---:|---|
| V1 as is (SQLite path) | 0.02 | the problem |
| V1 scoring over a vector pool | 0.34 | scoring still dominated by history and substring cues |
| Plain cosine | 0.55 | strong on clean embeddings, weakest under ambiguity; ignores lexical evidence and history |
| Retune V1's two scale constants (E5 grid) | 0.45 | swings with the embedding model's cosine baseline; must be re-fitted per model |
| Generative Agents scoring (Park et al., 2023) | 0.32 | depends on authored importance, which production writes as a flat 0.6 |
| Reciprocal Rank Fusion (Cormack et al., 2009) | 0.22 | discards score magnitudes; a weak signal's rank counts as much as a strong one's |
| **Hybrid (this ADR)** | **0.69** | best worst case and best mean (0.91) |

## Decision

1. **Candidates by similarity** on every backend: Qdrant top-N; Postgres
   `ORDER BY embedding <=> q LIMIT N` (also lets pgvector use its HNSW index);
   SQLite via an in-process float32 vector index over the wing's most recent
   `MEMORY_SQLITE_SCAN_LIMIT` (5,000) rows, with O(1) staleness detection so
   writes from other processes are seen. `N = MEMORY_CANDIDATE_POOL = 60`.
2. **Score** (`app/state/memory_ranking.py`):
   `z(cos) + 1.5·BM25/max(BM25) + 0.2·z(ln n − 0.5·ln(h+1) + 1.5·importance)`,
   z-scores and BM25 IDF computed over the pool. Weights are the centre of the
   tuning plateau, validated on held-out seeds and three embedding profiles.
3. **Archived memories** compete in the same pool and are promoted only if
   they win a slot.
4. **Observability:** `MemoryStore.last_search_trace` records backend, pool
   size, latency and each winner's id and per-term scores (no memory text).
5. V1 stays selectable (`actr_v1`) for A/B comparison and rollback; its tests
   are pinned to it.

## Trade-offs

* Lexical evidence costs ~0.03 hit@3 on very clean embeddings (it can lift an
  obsolete fact that shares the query's keyword) in exchange for +0.05 to
  +0.07 when embeddings are ambiguous.
* The ACT-R history prior is small (0.2) and mixed (−0.05 to +0.08 by regime).
  It is kept for the worst case, not the mean.
* Scores are relative within the pool: there is no absolute relevance
  threshold, so the ranker always returns `limit` memories (open question,
  07 §6). V1's `threshold` argument is accepted and ignored by the hybrid.
* The graph/PageRank leg is not part of the hybrid. It was unreachable in
  production anyway (M-4), and its value is unmeasured (07 §2).
* Mood-dependent retrieval is removed; see ADR-002 and E-R for why.
* SQLite memory cost: 768 × 4 B × 5,000 ≈ 15 MB per process per wing.

## Performance

SQLite, 5,000 memories: p50 4.1 ms / p95 7.1 ms (V1: 57.8 / 70.7 ms). The first
search after start or after a deletion rebuilds the index (~1 s at 5,000);
agents warm it at startup. Postgres and Qdrant paths are index-backed.

## Reversal conditions

* GPU run H-R1: hybrid − V1 < 0.30 hit@3 with the real `nomic-embed-text` on
  the summary regime → re-open.
* GPU run H-R2: best weights more than 0.02 hit@3 away from `1.5 : 0.2` →
  move to the new plateau centre and re-validate held-out.
* Live A/B (`MEMORY_RANKING_POLICY=actr_v1` on a shadow agent) showing worse
  answer grounding on real conversations → re-open.
* Rollback: set `MEMORY_RANKING_POLICY=actr_v1` and restart the brain and surfacing agents.
