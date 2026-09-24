"""Retrieval scoring lab: production's ranking, decomposed so it can be varied.

`MemoryStore.search_memories` fuses candidate selection, ACT-R scoring, cue
boosts and thresholding in one pass, so asking "what if we drop the recency
term" means editing production. The lab re-expresses that pipeline as two
swappable parts over the same memory state:

    candidates = CandidateGenerator(state, query)      # which rows get scored
    ranking    = ScoringPolicy(candidates, query)      # how they are ordered

`V1Policy` + `sqlite_recency` is production on the SQLite path, term for term.
`tests/test_cognitive_bench.py` pins that claim: over whole scenarios, the lab
returns exactly the ranked list the real `MemoryStore` returns. Every other
policy is then a controlled change against a verified baseline rather than a
comparison between two unrelated implementations.

State is simulated with production's write semantics (`add_memory`):
first write -> recall_count 1, created = last_recalled = t; exact repeat ->
recall_count + 1, last_recalled = t, importance = max(old, new).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

# Production constants, imported rather than copied so a change there moves
# the lab with it.
from app.config import Config
from app.state.memory_store import (
    ACTR_EMO_DISTANCE_PENALTY,
    ACTR_EMO_PROXIMITY_WEIGHT,
    ACTR_IMPORTANCE_WEIGHT,
    ACTR_SPACING_WEIGHT,
    ACTR_STRESS_SUPPRESSION,
    ACTR_VALENCE_GAIN,
    DIRECT_CUE_BOOST,
    FIRST_PERSON_PRONOUNS,
    SEARCH_STOP_WORDS,
    SECOND_PERSON_PRONOUNS,
    MemoryStore,
)

from .metrics import ProbeResult
from .scenarios import Probe, Scenario

_WORD3 = re.compile(r"\b\w{3,}\b")
_WORD = re.compile(r"\b\w+\b")


@dataclass
class MemoryRow:
    key: str
    content: str
    vec: np.ndarray
    importance: float
    valence: float
    emotion: float
    created_h: float
    last_h: float
    recall_count: int = 1
    order: int = 0  # insertion order (SQLite rowid)


@dataclass
class QueryContext:
    probe: Probe
    vec: np.ndarray
    now_h: float
    valence: float
    arousal: float
    cortisol: float
    limit: int
    threshold: float = -1.5
    sims: dict[int, float] | None = None  # row.order -> cosine, precomputed

    def sim(self, row: MemoryRow) -> float:
        if self.sims is not None:
            return self.sims[row.order]
        return float(self.vec @ row.vec)


@dataclass
class Scored:
    row: MemoryRow
    similarity: float
    score: float
    terms: dict[str, float] = field(default_factory=dict)


def build_state(scenario: Scenario, embedder) -> list[MemoryRow]:
    rows: dict[str, MemoryRow] = {}
    for event in scenario.events:  # already time-ordered
        row = rows.get(event.text)
        if row is None:
            rows[event.text] = MemoryRow(
                key=event.key,
                content=event.text,
                vec=np.asarray(embedder.embed(event.text)),
                importance=event.importance,
                valence=event.valence,
                emotion=event.emotion,
                created_h=event.t_hours,
                last_h=event.t_hours,
                order=len(rows),
            )
        else:
            row.recall_count += 1
            row.last_h = event.t_hours
            row.importance = max(row.importance, event.importance)
    return list(rows.values())


# --------------------------------------------------------------------------
# Production's lexical machinery, reproduced
# --------------------------------------------------------------------------


def sqlite_dynamic_stop_words(state: list[MemoryRow]) -> set[str]:
    """`MemoryStore._update_dynamic_stop_words`, SQLite branch."""
    rows = sorted(state, key=lambda r: r.order)[:500]
    counter: Counter = Counter()
    for r in rows:
        counter.update(_WORD3.findall(r.content.lower()))
    cutoff = max(5, int(len(rows) * 0.15))
    return {w for w, c in counter.items() if c > cutoff}


def stop_words(state: list[MemoryRow]) -> set[str]:
    words = set(SEARCH_STOP_WORDS) | sqlite_dynamic_stop_words(state)
    words.update(_WORD3.findall(Config.AI_NAME.lower()))
    return words


def query_cues(query: str, stops: set[str]) -> tuple[list[str], list[str]]:
    """(matched_cues incl. resolved pronoun cues, goal-buffer concepts)."""
    words = _WORD3.findall(query.lower())
    matched = [w for w in words if w not in stops]
    # _resolve_pronoun_cues with an empty graph: user node "user", agent node
    # Config.AI_NAME (see MemoryStore._resolve_identity_nodes).
    all_words = _WORD.findall(query.lower())
    resolved = set()
    if any(p in all_words for p in FIRST_PERSON_PRONOUNS):
        resolved.add("user")
    if any(p in all_words for p in SECOND_PERSON_PRONOUNS):
        resolved.add(Config.AI_NAME.lower())
    for word in all_words:
        if word == "user":
            resolved.add("user")
        if word in {"ai friend", "my friend", Config.AI_NAME.lower()}:
            resolved.add(Config.AI_NAME.lower())
    for cue in resolved:
        if cue not in matched:
            matched.append(cue)
    # GoalBuffer.update_buffer on a flushed buffer: unique words in order of
    # last occurrence, last `capacity` kept.
    concepts: list[str] = []
    for w in (w for w in words if w not in stops):
        if w in concepts:
            concepts.remove(w)
        concepts.append(w)
    return matched, concepts[-5:]


# --------------------------------------------------------------------------
# Candidate generators
# --------------------------------------------------------------------------

CandidateGenerator = Callable[[list[MemoryRow], QueryContext], list[MemoryRow]]


def mrl_pool_size(ctx: QueryContext, full_pool: bool = False) -> int:
    return MemoryStore._compute_mrl_gating(
        ctx.arousal, ctx.cortisol, ctx.limit, full_pool
    )[1]


def sqlite_recency(state, ctx, full_pool: bool = False):
    """Production SQLite: ORDER BY last_recalled_at DESC LIMIT pool."""
    n = mrl_pool_size(ctx, full_pool)
    visible = [r for r in state if r.created_h <= ctx.now_h]
    return sorted(visible, key=lambda r: (r.last_h, r.order), reverse=True)[:n]


def vector_topn(state, ctx, n: int | None = None):
    """Qdrant-style: top-N by cosine similarity."""
    n = n or mrl_pool_size(ctx)
    visible = [r for r in state if r.created_h <= ctx.now_h]
    return sorted(visible, key=lambda r: ctx.sim(r), reverse=True)[:n]


def pg_actr_topn(state, ctx, n: int | None = None):
    """Postgres: surface_actr_memories() orders by the SQL ACT-R score."""
    n = n or mrl_pool_size(ctx)
    visible = [r for r in state if r.created_h <= ctx.now_h]
    scored = [(v1_activation(r, ctx, ctx.sim(r))[0], r) for r in visible]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:n]]


def all_rows(state, ctx):
    return [r for r in state if r.created_h <= ctx.now_h]


def with_validity_oracle(generator: CandidateGenerator) -> CandidateGenerator:
    """Upper bound for write-time contradiction handling (not a real policy).

    Drops every memory that a *perfect* supersession detector would already
    have closed (`valid_until` set): a fact whose `:v2` revision exists and
    predates the query. The gap between an arm with and without this wrapper
    is the most a validity-aware memory (the unwired TemporalMemoryStore)
    could add -- the value of building it, measured before building it.
    """

    def gen(state, ctx):
        revised_at: dict[str, float] = {}
        for r in state:
            if r.key.endswith(":v2"):
                base = r.key[: -len(":v2")]
                revised_at[base] = min(revised_at.get(base, r.created_h), r.created_h)
        alive = [
            r
            for r in state
            if not (r.key in revised_at and revised_at[r.key] <= ctx.now_h)
        ]
        return generator(alive, ctx)

    return gen


# --------------------------------------------------------------------------
# V1 scoring, term for term
# --------------------------------------------------------------------------


def v1_activation(row: MemoryRow, ctx: QueryContext, sim: float) -> tuple[float, dict]:
    """Rust `score_memories_actr_sqlite` / Python `_build_candidate_from_row`."""
    hours_since = max(0.001, ctx.now_h - row.last_h)
    dist = math.sqrt(
        (row.valence - ctx.valence) ** 2 + (row.emotion - ctx.arousal) ** 2
    )
    spacing = 0.0
    if row.recall_count >= 2:
        span = row.last_h - row.created_h
        if span > 0:
            spacing = ACTR_SPACING_WEIGHT * math.log(span / row.recall_count + 1.0)
    terms = {
        "frequency": math.log(row.recall_count),
        "recency": -Config.ACTR_DECAY_RATE * math.log(hours_since + 1.0),
        "importance": ACTR_IMPORTANCE_WEIGHT * row.importance,
        "emo_proximity": ACTR_EMO_PROXIMITY_WEIGHT * (1.0 - dist),
        "spacing": spacing,
        "similarity": Config.ACTR_SPREAD_WEIGHT
        * sim
        * (
            1.0
            + ACTR_VALENCE_GAIN * row.valence * row.emotion
            - ACTR_STRESS_SUPPRESSION * ctx.arousal * ctx.cortisol
        ),
        "emo_distance": -ACTR_EMO_DISTANCE_PENALTY * dist,
    }
    return sum(terms.values()), terms


@dataclass
class V1Policy:
    """Production ranking. `drop` removes named terms for ablation."""

    name: str = "v1"
    drop: frozenset[str] = frozenset()
    cue_boost: float = DIRECT_CUE_BOOST
    goal_boost: bool = True
    sim_weight: float = 1.0

    def rank(self, candidates, ctx, stops) -> list[Scored]:
        cues, concepts = query_cues(ctx.probe.query, stops)
        out = []
        for row in candidates:
            sim = ctx.sim(row)
            _, terms = v1_activation(row, ctx, sim)
            terms["similarity"] *= self.sim_weight
            for t in self.drop:
                terms[t] = 0.0
            score = sum(terms.values())
            if score <= ctx.threshold - 2.5 and row.importance < 0.7:
                continue
            content = row.content.lower()
            terms["cue"] = self.cue_boost * sum(1 for c in cues if c in content)
            if self.goal_boost and concepts:
                matches = sum(1 for c in concepts if c in content)
                terms["goal_buffer"] = matches * (1.5 / len(concepts)) * 1.2
            score = sum(terms.values())
            if score > ctx.threshold:
                out.append(Scored(row, sim, score, terms))
        out.sort(key=lambda s: s.score, reverse=True)
        return out[: ctx.limit]


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def run_lab(
    scenario: Scenario,
    embedder,
    policy,
    generator: CandidateGenerator = sqlite_recency,
    limit: int = 3,
) -> list[ProbeResult]:
    state = build_state(scenario, embedder)
    stops = stop_words(state)
    if hasattr(policy, "bm25"):
        policy.bm25 = BM25Index(state)  # per scenario: IDF is store-specific
    matrix = np.stack([r.vec for r in state]) if state else np.zeros((0, 1))
    results = []
    for probe in scenario.probes:
        v, a, c = probe.affect
        qvec = np.asarray(embedder.embed(probe.query))
        sims = matrix @ qvec if state else []
        ctx = QueryContext(
            probe,
            qvec,
            probe.t_hours,
            v,
            a,
            c,
            limit,
            sims={r.order: float(x) for r, x in zip(state, sims)},
        )
        ranked = policy.rank(generator(state, ctx), ctx, stops)
        results.append(
            ProbeResult(
                probe_key=probe.key,
                scenario_seed=scenario.seed,
                categories=tuple(sorted(probe.categories)),
                ranked=tuple(s.row.key for s in ranked),
                relevant=tuple(sorted(probe.relevant)),
                obsolete=tuple(sorted(probe.obsolete)),
                k=limit,
            )
        )
    return results


def explain(
    scenario: Scenario,
    embedder,
    probe_key: str,
    policy=None,
    generator: CandidateGenerator = all_rows,
    top: int = 8,
) -> list[dict]:
    """Per-term score breakdown for one probe (diagnostics / docs)."""
    policy = policy or V1Policy()
    state = build_state(scenario, embedder)
    stops = stop_words(state)
    probe = next(p for p in scenario.probes if p.key == probe_key)
    v, a, c = probe.affect
    ctx = QueryContext(
        probe, np.asarray(embedder.embed(probe.query)), probe.t_hours, v, a, c, top
    )
    if hasattr(policy, "bm25"):
        policy.bm25 = BM25Index(state)  # per scenario: IDF is store-specific
    ranked = policy.rank(generator(state, ctx), ctx, stops)
    return [
        {
            "key": s.row.key,
            "score": round(s.score, 3),
            "sim": round(s.similarity, 3),
            **{k: round(val, 3) for k, val in s.terms.items()},
        }
        for s in ranked
    ]


# --------------------------------------------------------------------------
# Alternative policies
# --------------------------------------------------------------------------


class BM25Index:
    """Whole-word Okapi BM25 over the memory state (the lexical control).

    Whole words, not substrings: "art" must not match "party". IDF comes from
    the whole store so a word in every memory contributes nothing, which is
    what production's dynamic stop-word list approximates by hand.
    """

    def __init__(self, state: list[MemoryRow], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = {
            r.key + "|" + r.content: _WORD.findall(r.content.lower()) for r in state
        }
        self.n = len(self.docs)
        self.avg = sum(len(d) for d in self.docs.values()) / max(1, self.n)
        self.df: Counter = Counter()
        for words in self.docs.values():
            self.df.update(set(words))

    def score(self, row: MemoryRow, query_terms: list[str]) -> float:
        words = self.docs.get(row.key + "|" + row.content) or _WORD.findall(
            row.content.lower()
        )
        if not words:
            return 0.0
        tf = Counter(words)
        out = 0.0
        for term in set(query_terms):
            f = tf.get(term, 0)
            if not f:
                continue
            df = self.df.get(term, 0)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            out += (
                idf
                * f
                * (self.k1 + 1)
                / (f + self.k1 * (1 - self.b + self.b * len(words) / self.avg))
            )
        return out


def base_level(row: MemoryRow, now_h: float, decay: float | None = None) -> float:
    """ACT-R base-level learning, the optimized-learning approximation that
    production uses: ln(n) - d*ln(age_since_last_use_hours + 1)."""
    d = Config.ACTR_DECAY_RATE if decay is None else decay
    return math.log(row.recall_count) - d * math.log(
        max(0.001, now_h - row.last_h) + 1.0
    )


def _zscores(values: list[float]) -> list[float]:
    if not values:
        return []
    mu = sum(values) / len(values)
    sd = math.sqrt(sum((v - mu) ** 2 for v in values) / len(values))
    return [(v - mu) / sd if sd > 1e-9 else 0.0 for v in values]


def _minmax(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    return [(v - lo) / (hi - lo) if hi - lo > 1e-12 else 0.0 for v in values]


def _query_terms(query: str, stops: set[str]) -> list[str]:
    return [w for w in _WORD3.findall(query.lower()) if w not in stops]


def collapse_near_duplicates(ranked: list[Scored], tau: float) -> list[Scored]:
    """Keep the newest of any pair of candidates whose embeddings are within
    cosine `tau` of each other (MMR-style redundancy removal, Carbonell &
    Goldstein 1998, with recency as the tie-break). Near-duplicates are either
    repeated mentions (keeping one frees a slot) or a fact and its later
    revision (keeping the newest is the supersession rule)."""
    kept: list[Scored] = []
    for cand in ranked:
        clash = None
        for i, k in enumerate(kept):
            if float(cand.row.vec @ k.row.vec) >= tau:
                clash = i
                break
        if clash is None:
            kept.append(cand)
        elif cand.row.created_h > kept[clash].row.created_h:
            kept[clash] = Scored(
                cand.row, cand.similarity, kept[clash].score, cand.terms
            )
    return kept


@dataclass
class GenerativeAgentsPolicy:
    """Park et al. 2023: min-max normalized recency (0.995^hours since last
    access), importance and relevance (cosine), summed with equal weights."""

    name: str = "generative_agents"
    decay_per_hour: float = 0.995

    def rank(self, candidates, ctx, stops) -> list[Scored]:
        if not candidates:
            return []
        sims = [ctx.sim(r) for r in candidates]
        rec = _minmax(
            [self.decay_per_hour ** max(0.0, ctx.now_h - r.last_h) for r in candidates]
        )
        imp = _minmax([r.importance for r in candidates])
        rel = _minmax(sims)
        out = [
            Scored(r, s, a + b + c, {"recency": a, "importance": b, "relevance": c})
            for r, s, a, b, c in zip(candidates, sims, rec, imp, rel)
        ]
        out.sort(key=lambda x: x.score, reverse=True)
        return out[: ctx.limit]


@dataclass
class RRFPolicy:
    """Reciprocal Rank Fusion (Cormack et al. 2009) over three rankings:
    cosine, whole-word BM25, and ACT-R base-level + importance."""

    name: str = "rrf"
    k: int = 60
    bm25: BM25Index | None = None

    def rank(self, candidates, ctx, stops) -> list[Scored]:
        if not candidates:
            return []
        terms = _query_terms(ctx.probe.query, stops)
        sims = [ctx.sim(r) for r in candidates]
        lex = [self.bm25.score(r, terms) if self.bm25 else 0.0 for r in candidates]
        act = [
            base_level(r, ctx.now_h) + ACTR_IMPORTANCE_WEIGHT * r.importance
            for r in candidates
        ]
        fused = [0.0] * len(candidates)
        for signal in (sims, lex, act):
            order = sorted(
                range(len(candidates)), key=lambda i: signal[i], reverse=True
            )
            for rank, i in enumerate(order):
                fused[i] += 1.0 / (self.k + rank + 1)
        out = [Scored(r, s, f, {}) for r, s, f in zip(candidates, sims, fused)]
        out.sort(key=lambda x: x.score, reverse=True)
        return out[: ctx.limit]


@dataclass
class HybridPolicy:
    """Relevance-first ACT-R hybrid (candidate for Brain V2).

    score = z(cosine)                                  -- relevance, calibrated
          + w_lex * bm25 / max(bm25)                  -- whole-word lexical, bounded [0, w_lex]
          + w_act * z(base_level + w_imp*importance)  -- ACT-R history prior
          + w_emo * |valence| * emotion               -- emotional salience (query-independent, mood-independent)

    Every term is dimensionless and on a comparable scale (z-scores within
    the candidate pool, lexical bounded to [0, 1]), so the weights say how
    much each signal is trusted relative to relevance instead of silently
    depending on each term's raw units. `tau` enables near-duplicate collapse.
    """

    name: str = "hybrid"
    w_lex: float = 1.0
    w_act: float = 0.3
    w_imp: float = 1.5
    w_emo: float = 0.0
    tau: float | None = None
    bm25: BM25Index | None = None
    # IDF over the candidate pool instead of the whole store: what production
    # can compute per query without keeping corpus-wide document frequencies.
    pool_idf: bool = False

    def rank(self, candidates, ctx, stops) -> list[Scored]:
        if not candidates:
            return []
        terms = _query_terms(ctx.probe.query, stops)
        sims = [ctx.sim(r) for r in candidates]
        z_sim = _zscores(sims)
        index = BM25Index(candidates) if self.pool_idf else self.bm25
        lex = [index.score(r, terms) if index else 0.0 for r in candidates]
        top_lex = max(lex) if lex else 0.0
        lex_n = [x / top_lex if top_lex > 0 else 0.0 for x in lex]
        z_act = _zscores(
            [base_level(r, ctx.now_h) + self.w_imp * r.importance for r in candidates]
        )
        out = []
        for r, s, zs, lx, za in zip(candidates, sims, z_sim, lex_n, z_act):
            terms_d = {
                "relevance": zs,
                "lexical": self.w_lex * lx,
                "activation": self.w_act * za,
                "emotional": self.w_emo * abs(r.valence) * r.emotion,
            }
            out.append(Scored(r, s, sum(terms_d.values()), terms_d))
        out.sort(key=lambda x: x.score, reverse=True)
        if self.tau is not None:
            out = collapse_near_duplicates(out, self.tau)
        return out[: ctx.limit]
