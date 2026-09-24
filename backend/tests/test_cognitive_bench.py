"""Gate tests for the cognitive benchmark suite (`evals/cognitive`).

They protect the benchmark's validity rather than any one result:

* the corpus measures what it claims (paraphrase queries share no content
  word with the fact they ask about);
* scenarios and embeddings are deterministic, so two runs are comparable;
* the lab reproduces the production ranker exactly, for both policies, so a
  lab ablation is a statement about production;
* regression thresholds on the headline retrieval and affect results.
"""

import asyncio
import itertools
import logging

import numpy as np
import pytest

from app.state.memory_store import SEARCH_STOP_WORDS
from evals.cognitive import affect_sim
from evals.cognitive.corpus import FACTS
from evals.cognitive.embedder import PROFILES, LatentTopicEmbedder, cosine
from evals.cognitive.harness import run_production
from evals.cognitive.lab import (
    HybridPolicy,
    V1Policy,
    run_lab,
    sqlite_recency,
    vector_topn,
)
from evals.cognitive.metrics import ProbeResult, aggregate
from evals.cognitive.scenarios import build_history, content_words, register_embeddings


def _scenario(seed=1, regime="summary", profile="hard", days=21):
    sc = build_history(seed, days=days, n_facts=10, regime=regime)
    emb = LatentTopicEmbedder(profile, seed=seed)
    register_embeddings(sc, emb)
    return sc, emb


# --- validity of the benchmark itself --------------------------------------


@pytest.mark.parametrize("fact", FACTS, ids=lambda f: f.facet)
def test_paraphrase_query_shares_no_content_word_with_its_fact(fact):
    overlap = (content_words(fact.para) & content_words(fact.text)) - SEARCH_STOP_WORDS
    assert not overlap, f"paraphrase leaks keywords {overlap}"
    for text in filter(None, (fact.update,)):
        assert not (
            (content_words(fact.para) & content_words(text)) - SEARCH_STOP_WORDS
        )


@pytest.mark.parametrize("fact", FACTS, ids=lambda f: f.facet)
def test_keyword_query_does_share_a_content_word(fact):
    assert (content_words(fact.kw) & content_words(fact.text)) - SEARCH_STOP_WORDS


def test_scenarios_are_deterministic_per_seed():
    a, b = build_history(7, regime="summary"), build_history(7, regime="summary")
    assert [(e.key, e.text, e.t_hours) for e in a.events] == [
        (e.key, e.text, e.t_hours) for e in b.events
    ]
    assert [(p.key, p.query) for p in a.probes] == [(p.key, p.query) for p in b.probes]
    assert build_history(8, regime="summary").events != a.events


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_embedder_hits_its_profile_cosines(name):
    profile = PROFILES[name]
    emb = LatentTopicEmbedder(name, seed=3)
    for t, f, i in itertools.product(range(4), range(3), range(4)):
        emb.register(f"t{t}f{f}i{i}", f"T{t}", f"F{f}")
    same_fact, same_topic, unrelated = [], [], []
    for a, b in itertools.combinations(list(emb._specs), 2):
        (ta, fa), (tb, fb) = emb._specs[a], emb._specs[b]
        c = cosine(emb.embed(a), emb.embed(b))
        (
            same_fact if (ta, fa) == (tb, fb) else same_topic if ta == tb else unrelated
        ).append(c)
    tol = 0.05 if profile.effective_dim < 768 else 0.02
    assert abs(np.mean(same_fact) - profile.expected_cosine(True, True)) < tol
    assert abs(np.mean(same_topic) - profile.expected_cosine(True, False)) < tol
    assert abs(np.mean(unrelated) - profile.expected_cosine(False, False)) < tol


def test_embedder_refuses_unregistered_text():
    with pytest.raises(KeyError):
        LatentTopicEmbedder().embed("never registered")


def test_obsolete_win_counts_only_an_obsolete_fact_outranking_the_current_one():
    r = ProbeResult("p", 1, ("updated",), ("old", "new", "x"), ("new",), ("old",), 3)
    assert r.metrics()["obsolete_win"] == 1.0
    r2 = ProbeResult("p", 1, ("updated",), ("new", "old", "x"), ("new",), ("old",), 3)
    assert r2.metrics()["obsolete_win"] == 0.0 and r2.metrics()["hit@1"] == 1.0
    assert aggregate([r, r2])["all"]["obsolete_win"]["mean"] == 0.5


# --- the lab is production, for both policies -----------------------------


def _quiet():
    logging.disable(logging.WARNING)


@pytest.mark.parametrize("regime", ["verbatim", "summary"])
def test_lab_v1_reproduces_production_v1_exactly(regime):
    _quiet()
    sc, emb = _scenario(regime=regime, profile="nomic_like")
    prod = asyncio.run(run_production(sc, emb, ranking_policy="actr_v1"))
    lab = run_lab(sc, emb, V1Policy(), sqlite_recency)
    assert [p.ranked for p in prod] == [l.ranked for l in lab]


def test_lab_hybrid_reproduces_production_hybrid():
    _quiet()
    sc, emb = _scenario()
    prod = asyncio.run(run_production(sc, emb, ranking_policy="hybrid"))
    lab = run_lab(
        sc,
        emb,
        HybridPolicy(w_lex=1.5, w_act=0.2, pool_idf=True),
        lambda s, c: vector_topn(s, c, 60),
    )
    same = sum(p.ranked == l.ranked for p, l in zip(prod, lab))
    # Rust parses stored JSON embeddings; numpy uses the originals. Only a
    # float-level tie may flip (1 of 558 in the full parity run).
    assert same >= len(prod) - 1
    assert aggregate(prod)["all"]["hit@3"]["mean"] == pytest.approx(
        aggregate(lab)["all"]["hit@3"]["mean"], abs=0.02
    )


# --- regression gates on the headline results ------------------------------


def test_gate_hybrid_retrieval_beats_v1_by_a_wide_margin():
    _quiet()
    v1, hybrid = [], []
    for seed in (1, 2):
        sc, emb = _scenario(seed=seed, days=30)
        v1 += run_lab(sc, emb, V1Policy(), sqlite_recency)
        hybrid += run_lab(
            sc,
            emb,
            HybridPolicy(w_lex=1.5, w_act=0.2, pool_idf=True),
            lambda s, c: vector_topn(s, c, 60),
        )
    v1_hit = aggregate(v1)["all"]["hit@3"]["mean"]
    hybrid_hit = aggregate(hybrid)["all"]["hit@3"]["mean"]
    assert v1_hit < 0.15  # documents the defect the hybrid replaces
    assert hybrid_hit >= 0.60
    assert hybrid_hit - v1_hit >= 0.45


def test_gate_affect_weight_learning_runaway_is_off_by_default():
    """With the V1 always-on rule, 200 neutral turns from mood 0.6 saturate
    valence at +1.0; with the default (learning off) mood decays normally."""
    _quiet()
    default = asyncio.run(
        affect_sim.simulate(
            "neutral", "agent_mood", 200, 0, initial_mood=0.6, learn=None
        )
    ).metrics()
    assert default["turns_saturated"] == 0 and default["final_mood"] < 0.1
    forced = asyncio.run(
        affect_sim.simulate(
            "neutral", "agent_mood", 200, 0, initial_mood=0.6, learn=True
        )
    ).metrics()
    assert forced["turns_saturated"] > 100 and forced["loop_gain_final"] > 1.0


def test_intervals_resample_scenarios_not_probes():
    """Review finding: probes in one scenario are correlated (and mood probes
    repeat paraphrase questions), so CIs must resample scenario seeds."""
    from evals.cognitive.metrics import _bootstrap_ci, _cluster_bootstrap_ci

    # 10 scenarios, 50 identical probes each, scenario means 0 or 1.
    values = [float(s % 2) for s in range(10) for _ in range(50)]
    clusters = [s for s in range(10) for _ in range(50)]
    lo_c, hi_c = _cluster_bootstrap_ci(values, clusters)
    lo_p, hi_p = _bootstrap_ci(values)
    assert (hi_c - lo_c) > 3 * (hi_p - lo_p)
