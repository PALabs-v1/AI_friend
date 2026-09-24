"""Relevance-first hybrid ranking for memory retrieval (Brain V2, ADR-001).

The V1 ranker (`MemoryStore._build_candidate_from_row` + cue boost) adds raw
terms on incompatible scales: cosine similarity spans ~0.4 across a store,
ACT-R recency spans ~4.5 and frequency ~2.3, and a substring keyword hit adds
a flat 5.0. Relevance therefore barely moved the ranking, "art" matched
"party", and a fact mentioned three times beat the fact that was asked about.
Measured on the cognitive benchmark (`python -m evals.cognitive memory`),
V1 retrieved the right memory in the top 3 for 2-4% of questions on the
SQLite path and 41-58% even with a vector candidate pool.

This ranker makes every term dimensionless before combining them:

    score = z(cosine)                                     relevance
          + W_LEX * bm25 / max(bm25 in pool)              whole-word lexical, in [0, W_LEX]
          + W_ACT * z(base_level + W_IMP * importance)    ACT-R history prior

where z() standardises within the candidate pool (so the ranking does not
depend on an embedding model's cosine baseline) and BM25's IDF is computed
over the pool (store-wide IDF changed 3 of 1,131 benchmark rankings).
Weights sit at the centre of the plateau found on tuning seeds and were
validated on held-out seeds and three embedding profiles; see
docs/brain-research/03-memory-retrieval.md for the full evidence.

Deliberately absent, each rejected by measurement:

* a query-independent emotional-salience prior (rumination: it floods every
  query with distressed memories when a store is emotionally dense);
* mood-congruence terms (zero measured effect at V1's scale);
* near-duplicate supersession by absolute cosine (costs up to 0.50 hit@3
  when fact and topic similarities overlap).

Pure functions over candidate dicts; no I/O. `MemoryStore` owns candidate
generation and calls `hybrid_rank`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

W_LEX = 1.5
W_ACT = 0.2
W_IMP = 1.5
BM25_K1 = 1.2
BM25_B = 0.75

_WORD = re.compile(r"\b\w+\b")
_WORD3 = re.compile(r"\b\w{3,}\b")


def query_terms(query_text: str, stop_words: set[str] | frozenset[str]) -> list[str]:
    """Content words of the query (3+ chars, not stop words), in order."""
    return [w for w in _WORD3.findall(query_text.lower()) if w not in stop_words]


def bm25_scores(documents: list[str], terms: list[str]) -> list[float]:
    """Okapi BM25 of each document against `terms`, IDF over `documents`.

    Whole-word matching: "art" does not match "party", "much" does not match
    "pretty much" unless the word itself is there.
    """
    if not documents or not terms:
        return [0.0] * len(documents)
    tokenized = [_WORD.findall(doc.lower()) for doc in documents]
    n = len(tokenized)
    avg_len = sum(len(t) for t in tokenized) / n or 1.0
    df: Counter = Counter()
    for words in tokenized:
        df.update(set(words))
    unique_terms = set(terms)
    scores = []
    for words in tokenized:
        if not words:
            scores.append(0.0)
            continue
        tf = Counter(words)
        total = 0.0
        for term in unique_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            norm = BM25_K1 * (1 - BM25_B + BM25_B * len(words) / avg_len)
            total += idf * f * (BM25_K1 + 1) / (f + norm)
        scores.append(total)
    return scores


def zscores(values: list[float]) -> list[float]:
    if not values:
        return []
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    if sd <= 1e-9:
        return [0.0] * len(values)
    return [(v - mean) / sd for v in values]


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    return None


def _importance(candidate: dict[str, Any]) -> float:
    """Stored importance; 0.5 only when absent. (`x or 0.5` would turn a real
    0.0 -- a fully decayed memory -- into mid importance.)"""
    value = candidate.get("importance_score")
    return 0.5 if value is None else float(value)


def base_level(candidate: dict[str, Any], now: datetime, decay: float) -> float:
    """ACT-R base-level learning, optimized-learning form used across the
    codebase: ln(n) - d * ln(hours since last use + 1)."""
    n = max(1, int(candidate.get("recall_count") or 1))
    last = _as_utc(candidate.get("last_recalled_at")) or now
    hours = max(0.001, (now - last).total_seconds() / 3600.0)
    return math.log(n) - decay * math.log(hours + 1.0)


def hybrid_rank(
    candidates: list[dict[str, Any]],
    query_text: str,
    stop_words: set[str] | frozenset[str],
    now: datetime,
    *,
    limit: int | None,
    decay: float = 0.5,
    w_lex: float = W_LEX,
    w_act: float = W_ACT,
    w_imp: float = W_IMP,
) -> list[dict[str, Any]]:
    """Rank candidate dicts; returns the top `limit`, best first.

    Each candidate needs `content` and `similarity` (cosine to the query);
    `recall_count`, `last_recalled_at` and `importance_score` feed the history
    prior and default to a single, just-created, mid-importance memory.
    Every returned dict gets `score` (the hybrid score) and `score_terms`
    (the three weighted terms), so a retrieval can be explained after the fact.
    """
    if not candidates:
        return []
    now = _as_utc(now) or datetime.now(UTC)
    terms = query_terms(query_text, stop_words)
    sims = [float(c.get("similarity") or 0.0) for c in candidates]
    z_sim = zscores(sims)
    lex = bm25_scores([str(c.get("content") or "") for c in candidates], terms)
    top_lex = max(lex) if lex else 0.0
    activation = [
        base_level(c, now, decay) + w_imp * _importance(c) for c in candidates
    ]
    z_act = zscores(activation)
    ranked = []
    for cand, zs, lx, za in zip(candidates, z_sim, lex, z_act):
        terms_d = {
            "relevance": zs,
            "lexical": w_lex * (lx / top_lex if top_lex > 0 else 0.0),
            "activation": w_act * za,
        }
        ranked.append(
            {
                **cand,
                "score": sum(terms_d.values()),
                "score_terms": {k: round(v, 4) for k, v in terms_d.items()},
            }
        )
    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked[:limit] if limit else ranked
