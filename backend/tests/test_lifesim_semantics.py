"""Semantic gate tests for lifesim: is every probe answerable as posed, about
the right entity, with evidence that actually contains the answer -- and is
the user's speech free of machine artifacts?

Phase 5's integrity suite checked that answers derive from the timeline and
that public files don't leak, but never whether a *question* identifies its
target. BrainBench (Phase 6), the first real consumer, surfaced that about
60% of probe templates named nothing ("When did that begin?"), fact probes
said "my" about other people's facts, trivia answered "lunch" instead of the
food, and plan facts were spoken as "someone's date is 2026-03-30T19:00".
Every test here fails on that code.
"""

import re
from collections import defaultdict
from datetime import datetime

import pytest

from evals.lifesim import bank_expand, banks
from evals.lifesim.generate import build
from evals.lifesim.schema import PROBE_CATEGORIES

PANEL = [
    (1001, "chatty_student", "6m"),
    (1003, "forgetful_retiree", "6m"),
    (1005, "anxious_hedger", "6m"),
    (1008, "socialite", "1y"),
    (1010, "traveling_consultant", "6m"),
]


@pytest.fixture(scope="module")
def lives():
    return [build(seed, arch, hz) for seed, arch, hz in PANEL]


def _spoken_forms(value: str) -> list[str]:
    forms = [value]
    try:
        dt = datetime.fromisoformat(value)
        forms.append(dt.strftime("%A, %B %-d at %-I:%M %p"))
    except ValueError:
        pass
    return forms


def _contains(text: str, value: str) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(f)}(?!\w)", text, re.I) for f in _spoken_forms(value)
    )


# ---- the bank --------------------------------------------------------------------


def test_every_probe_family_requires_a_target_placeholder():
    bank = banks.load()
    for family in PROBE_CATEGORIES:
        assert bank.required[family], f"{family} declares no required target placeholder"


def test_bank_rejects_a_template_that_omits_a_required_placeholder():
    with pytest.raises(banks.BankIntegrityError, match="missing required"):
        banks.Bank(
            {
                "temporal": {
                    "placeholders": ["what"],
                    "required": ["what"],
                    "templates": [{"id": "temporal#000", "text": "When did that begin?"}],
                }
            }
        )


def test_bank_expand_rejects_llm_variants_that_omit_the_target():
    """The old rule inferred "required" as the intersection of the existing
    templates' placeholders; one target-less template in a family made that
    intersection empty and let every target-less LLM variant through."""
    spec = {
        "placeholders": ["what"],
        "required": ["what"],
        "templates": [
            {"id": "temporal#000", "text": "When is {what}?"},
            {"id": "temporal#001", "text": "When was it scheduled to happen?"},
        ],
    }
    ok, rejected = bank_expand.validate(
        ["When did that begin?", "What date did I set for {what}?"], spec
    )
    assert ok == ["What date did I set for {what}?"]
    assert "missing required" in rejected[0][1]


# ---- probes ----------------------------------------------------------------------


_EVENT_FAMILY_OF = {
    "restaurant_visit": "restaurant",
    "cafe_visit": "cafe",
    "meeting": "meeting",
    "outing": "outing",
}


def test_event_probes_identify_exactly_one_told_event(lives):
    """The independent ambiguity check. Event probes name their target by
    kind plus companion plus day (interference, trivia) or kind plus month
    (relationship-defining). Counted straight from the oracle event log and
    the turns actually said before the probe: exactly one told event may
    match, or the question has more than one right answer."""
    for sim, turns, ann, probes, answers in lives:
        told_before = defaultdict(set)
        turn_t = {t.turn_id: t.t for t in turns}
        for a in ann:
            for eid in a.event_ids:
                told_before[eid].add(turn_t[a.turn_id])
        for p, a in zip(probes, answers, strict=True):
            if a.derivation.get("kind") != "event_payload":
                continue
            target = sim.events_by_id[a.derivation["event_id"]]
            if a.category == "relationship_defining":
                same = lambda ev: ev.t.strftime("%Y-%m") == target.t.strftime("%Y-%m")  # noqa: E731
            else:
                same = lambda ev: ev.t.date() == target.t.date()  # noqa: E731
            matches = [
                ev
                for ev in sim.events
                if ev.kind == target.kind
                and ev.payload.get("name") == target.payload.get("name")
                and ev.payload.get("meal") == target.payload.get("meal")
                and same(ev)
                and any(t < p.t for t in told_before.get(ev.event_id, ()))
            ]
            assert len(matches) == 1, (a.category, p.text, [m.event_id for m in matches])


def test_the_same_question_at_the_same_time_has_the_same_answer(lives):
    """A cheap backstop, not the detector: identical question text at one
    checkpoint must expect one answer. (The old bank never repeated a template
    at a checkpoint, so it could not see "When did that begin?" vs "When was
    it scheduled?" -- that is what the test above is for.)"""
    for sim, _turns, _ann, probes, answers in lives:
        by_question = defaultdict(set)
        for p, a in zip(probes, answers, strict=True):
            by_question[(p.t, p.text)].add(tuple(sorted(a.answer)))
        ambiguous = {k: v for k, v in by_question.items() if len(v) > 1}
        assert not ambiguous, f"{sim.archetype}: {list(ambiguous.items())[:3]}"


def test_fact_probes_name_whose_fact_it_is(lives):
    for sim, _turns, _ann, probes, answers in lives:
        for p, a in zip(probes, answers, strict=True):
            entity = a.derivation.get("entity")
            if a.derivation.get("kind") not in ("timeline", "untold_assertion") or not entity:
                continue
            if entity == "user":
                assert re.search(r"\bmy\b", p.text), p.text
            elif entity in sim.world.people:
                assert sim.world.people[entity].name in p.text, (entity, p.text)
            elif entity.startswith("plan:"):
                what = sim.timeline.history(entity, "what")[0].value
                core = what[2:] if what.startswith(("a ", "an ")) else what
                assert core.split("'s ")[-1] in p.text, (what, p.text)


def test_expected_answers_appear_in_their_support_turns(lives):
    """Every answer the scorer will accept must actually have been said in the
    turns cited as its evidence -- not just derivable from the hidden world."""
    for sim, turns, _ann, _probes, answers in lives:
        text_of = {t.turn_id: t.text for t in turns}
        for a in answers:
            if a.expected == "abstain":
                continue
            said = " ".join(text_of[t] for t in a.support_turn_ids)
            for value in a.answer:
                assert _contains(said, value), (a.category, value, said[:200])


def test_trivia_answers_are_the_fact_not_the_meal_name(lives):
    for *_, answers in lives:
        for a in answers:
            if a.category.startswith("trivia"):
                assert not {v.lower() for v in a.answer} & {"breakfast", "lunch", "dinner", "brunch"}


def test_commitment_due_is_one_probe_listing_every_due_plan(lives):
    for sim, _turns, _ann, probes, answers in lives:
        per_checkpoint = defaultdict(int)
        for p, a in zip(probes, answers, strict=True):
            if a.category == "commitment_due":
                per_checkpoint[p.t] += 1
                assert len(a.answer) == len(a.derivation["plans"])
        assert all(n == 1 for n in per_checkpoint.values()), sim.archetype


def test_abstention_values_were_never_said_about_their_owner(lives):
    """Annotations under-count speech ("my friend Farah" states a relation no
    claim records), so an abstention probe must also be clean at text level."""
    for sim, turns, _ann, probes, answers in lives:
        for p, a in zip(probes, answers, strict=True):
            if a.expected != "abstain":
                continue
            truth = sim.timeline.get(a.derivation["assertion_id"])
            value = truth.value
            if value.startswith("person:"):
                value = sim.world.people[value].name
            owner = None if truth.entity == "user" else sim.world.people[truth.entity].name
            for t in turns:
                if t.t >= p.t or (owner and owner not in t.text):
                    continue
                assert not _contains(t.text, value), (p.text, value, t.text)


# ---- speech ----------------------------------------------------------------------

ARTIFACTS = {
    "someone's (non-person entity rendered as a person)": r"someone’s|someone's",
    "raw ISO timestamp": r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
    "fallback label": r"that detail",
    "bare goal verb (I start visit Japan)": r"\bI (start|progress) [a-z]",
    "bare plan verb (I moved cancel the gym trial)": r"\bI (moved|cancelled|finished) (call|submit|sign|get|book|cancel|take|reply|check|pay|renew|pick|send|buy|water|return|help)\b",
    "noun plan after will (I will a friend's wedding)": r"\bI (will|might) (a|an) ",
    "ownerless past fact (I used to have X as my Y)": r"used to have .* as my",
    "literal None": r"\bNone\b",
}


def test_user_speech_has_no_machine_artifacts(lives):
    for sim, turns, *_ in lives:
        for t in turns:
            for name, pattern in ARTIFACTS.items():
                assert not re.search(pattern, t.text), f"{sim.archetype} {name}: {t.text}"


def test_change_utterances_annotate_the_value_they_replace(lives):
    """"I switched my usual drink from mango lassi to masala chai" tells the
    listener both values; the annotation must say so, or the oracle believes
    the old value was never said and no stale-fact probe can be built."""
    checked = 0
    for sim, turns, ann, *_ in lives:
        text_of = {t.turn_id: t.text for t in turns}
        for a in ann:
            evs = [sim.events_by_id[e] for e in a.event_ids if e in sim.events_by_id]
            for ev in evs:
                if ev.kind != "pref_change" or a.misstatement:
                    continue
                old = ev.payload["old"]
                if old in text_of[a.turn_id]:
                    checked += 1
                    assert any(c.value == old and c.truthful for c in a.claims), text_of[a.turn_id]
    assert checked > 0
