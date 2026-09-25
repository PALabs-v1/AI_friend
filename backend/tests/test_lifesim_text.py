"""Model-free behavioral gates for the lifesim observation and probe layers."""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import replace
from datetime import timedelta

from evals.lifesim import banks, observe, probes
from evals.lifesim.generate import Horizon, build, simulate_truth
from evals.lifesim.schema import dumps


def _simulation(seed: int, archetype: str, days: int):
    bank = banks.load()
    horizon = Horizon(f"{days}d", days=days)
    sim = simulate_truth(seed, archetype, horizon, bank, "dev")
    return sim


def _serialized(seed: int, archetype: str, horizon: str):
    _sim, turns, annotations, questions, answers = build(seed, archetype, horizon)
    return "\n".join(
        dumps(row.to_json()) for row in (*turns, *annotations, *questions, *answers)
    )


def test_same_seed_is_byte_identical_and_another_seed_differs():
    first = _serialized(1000, "socialite", "1m")
    assert first == _serialized(1000, "socialite", "1m")
    assert first != _serialized(1001, "socialite", "1m")


def test_one_year_observation_is_prefix_of_two_year_observation():
    one = _simulation(1003, "busy_parent", 365)
    two = _simulation(1003, "busy_parent", 730)
    one_turns, one_annotations = observe.render(one)
    two_turns, two_annotations = observe.render(two)
    assert [t.to_json() for t in one_turns] == [
        t.to_json() for t in two_turns if t.t < one.end
    ]
    assert [a.to_json() for a in one_annotations] == [
        a.to_json()
        for a in two_annotations
        if two_turns[int(a.turn_id[1:]) - 1].t < one.end
    ]


def test_claims_match_truth_and_corrections_point_back():
    for seed, archetype in ((1000, "socialite"), (1001, "forgetful_retiree")):
        sim = _simulation(seed, archetype, 120)
        turns, annotations = observe.render(sim)
        turn_by_id = {t.turn_id: t for t in turns}
        annotation_by_id = {a.turn_id: a for a in annotations}
        for ann in annotations:
            for claim in ann.claims:
                assert claim.about is not None
                truth = sim.timeline.value_at(
                    claim.entity, claim.attribute, claim.about
                )
                assert truth is not None
                assert (claim.value == truth.value) is claim.truthful
            if ann.corrects_turn:
                target = annotation_by_id[ann.corrects_turn]
                assert turn_by_id[target.turn_id].t < turn_by_id[ann.turn_id].t
                assert target.misstatement
                assert all(c.truthful for c in ann.claims)


def test_probe_answers_recompute_from_truth_for_multiple_personas():
    for seed, archetype in (
        (1000, "socialite"),
        (1001, "forgetful_retiree"),
        (1002, "terse_engineer"),
    ):
        sim, _turns, _annotations, question_rows, answer_rows = build(
            seed, archetype, "4m"
        )
        assert question_rows
        for answer in answer_rows:
            assert probes.recompute(answer.derivation, sim) == answer.answer
            if answer.expected in ("answer", "forgettable", "surface_conflict"):
                assert answer.support_turn_ids


def test_public_text_has_no_ids_or_attribute_keys():
    sim, turns, _, question_rows, _ = build(1000, "socialite", "4m")
    texts = [t.text for t in turns] + [p.text for p in question_rows]
    forbidden = re.compile(
        r"\b(?:person|pet|plan|goal|project|belief):[a-z]\d{3}\b|\ba\d{6}\b|\be\d{6}\b"
    )
    attribute_keys = {
        "home_city",
        "tv_show",
        "job_title",
        "book_genre",
        "morning_routine",
        "away_city",
        "house_guest",
        "user_warmth",
        "valid_from",
        "assertion_id",
    }
    for text in texts:
        assert not forbidden.search(text), text
        tokens = set(re.findall(r"[a-z_]+", text.lower()))
        assert tokens.isdisjoint(attribute_keys), text
    assert len(sim.world.people) > 0


def test_persona_knobs_move_observable_rates():
    sim = _simulation(1000, "socialite", 180)
    baseline = replace(
        sim.persona, verbosity=0.0, contradiction_rate=0.0, hedging=0.0, humor=0.0
    )
    sim.persona = baseline
    low_turns, low_annotations = observe.render(sim)
    low_words = sum(len(t.text.split()) for t in low_turns) / len(low_turns)
    low_mis = sum(a.misstatement for a in low_annotations)
    low_hedge = sum("uncertain" in a.tags for a in low_annotations)
    low_joke = sum(a.joke for a in low_annotations)

    sim.persona = replace(
        baseline, verbosity=1.0, contradiction_rate=1.0, hedging=1.0, humor=1.0
    )
    high_turns, high_annotations = observe.render(sim)
    high_words = sum(len(t.text.split()) for t in high_turns) / len(high_turns)
    assert high_words > low_words
    assert sum(a.misstatement for a in high_annotations) > low_mis
    assert sum("uncertain" in a.tags for a in high_annotations) > low_hedge
    assert sum(a.joke for a in high_annotations) > low_joke


def test_ten_year_render_and_probes_fit_budget():
    t0 = time.perf_counter()
    _sim, turns, _annotations, question_rows, answers = build(1005, "socialite", "10y")
    elapsed = time.perf_counter() - t0
    assert len(turns) > 10_000
    assert len(turns) == len(_annotations)
    assert len(question_rows) == len(answers)
    assert elapsed < 30


def test_probe_categories_and_checkpoint_support_are_well_formed():
    sim, turns, _annotations, question_rows, answers = build(1000, "socialite", "1y")
    counts = Counter(a.category for a in answers)
    assert len(question_rows) == len(answers)
    assert all(
        a.category
        in {
            "current",
            "historical",
            "stale_trap",
            "multi_hop",
            "interference",
            "temporal",
            "trivia_recent",
            "trivia_old",
            "unanswerable",
            "relationship_defining",
            "commitment_due",
            "contradiction_surface",
        }
        for a in answers
    )
    turn_by_id = {t.turn_id: t for t in turns}
    final_pairs = [
        (question, answer)
        for question, answer in zip(question_rows, answers)
        if question.t == sim.end - timedelta(seconds=1)
    ]
    final_counts = Counter(answer.category for _, answer in final_pairs)
    assert 40 <= len(final_pairs) <= 80
    assert final_counts["current"] >= 3
    assert final_counts["multi_hop"] >= 3
    for question, answer in zip(question_rows, answers):
        assert question.probe_id == answer.probe_id
        assert all(turn_by_id[tid].t < question.t for tid in answer.support_turn_ids)
        assert probes.recompute(answer.derivation, sim) == answer.answer
    assert counts["current"] >= 3

    stop_words = {
        "a",
        "an",
        "the",
        "i",
        "me",
        "my",
        "we",
        "our",
        "us",
        "you",
        "your",
        "what",
        "which",
        "is",
        "are",
        "was",
        "were",
        "did",
        "do",
        "does",
        "have",
        "has",
        "had",
        "to",
        "of",
        "for",
        "in",
        "on",
        "at",
        "with",
        "and",
        "or",
        "from",
        "this",
        "that",
        "it",
        "about",
        "can",
        "tell",
        "remember",
        "help",
        "could",
        "would",
        "be",
        "when",
        "who",
        "where",
        "how",
        "back",
        "now",
        "then",
        "one",
        "thing",
        "detail",
        "earlier",
        "current",
        "old",
        "present",
        "value",
        "time",
        "happened",
        "recent",
        "small",
        "mention",
    }
    names = {person.name.lower() for person in sim.world.people.values()}
    names.add(sim.world.user_name.lower())

    def content_words(text):
        return {
            word
            for word in re.findall(r"[a-z]+", text.lower())
            if len(word) > 2 and word not in stop_words and word not in names
        }

    text_by_id = {turn.turn_id: turn.text for turn in turns}
    zero_overlap = 0
    for question, answer in zip(question_rows, answers):
        question_words = content_words(question.text)
        support_words = (
            set().union(
                *(content_words(text_by_id[tid]) for tid in answer.support_turn_ids)
            )
            if answer.support_turn_ids
            else set()
        )
        zero_overlap += not bool(question_words & support_words)
    assert zero_overlap / len(question_rows) >= 0.25
    for category, total in counts.items():
        if total >= 7:
            usage = Counter(
                answer.template_id for answer in answers if answer.category == category
            )
            assert max(usage.values()) / total <= 0.15
