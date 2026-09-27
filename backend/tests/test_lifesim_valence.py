"""DR-039's human-authored surface valence labels and deterministic contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evals.lifesim import banks, vocab
from evals.lifesim.dr039_sample import validity_sample
from evals.lifesim.generate import build
from evals.lifesim.observe import _EVENT_FAMILY
from evals.lifesim.valence import (
    compose_expressed_valence,
    label_for,
    label_table,
    payload_labels,
    validate_label_coverage,
)


def _event_kind_contract() -> set[str]:
    kinds = set(_EVENT_FAMILY)
    kinds.update(
        f"{family}_{suffix}"
        for family in ("commitment", "trip", "celebration")
        for suffix in ("planned", "rescheduled", "confirmed", "cancelled", "done")
    )
    return kinds


def _utterance_bank():
    utterances = Path(banks.BANK_DIR / "utterances.json")
    return banks.Bank(json.loads(utterances.read_text())["families"])


def test_every_renderer_bank_event_shape_and_vocab_value_is_labelled():
    validate_label_coverage(_utterance_bank(), _event_kind_contract(), vocab)

    table = label_table()
    assert set(table["emotion_openers"]) == {"heartbroken", "sad", "thrilled", "glad"}
    assert set(table["fixed_fragments"]) == {"filler_event_line", "joke_seed_line"}
    for group in (
        "bank_templates",
        "emotion_openers",
        "fixed_fragments",
        "event_shapes",
    ):
        for fragment_id, entry in table[group].items():
            assert -1.0 <= entry["label"] <= 1.0, fragment_id
            if entry["label"] != 0.0:
                assert entry["reason"], fragment_id
    for category, entries in table["vocab_values"].items():
        for value, entry in entries.items():
            assert -1.0 <= entry["label"] <= 1.0, (category, value)
            if entry["label"] != 0.0:
                assert entry["reason"], (category, value)


def test_hand_checked_golden_utterances_and_payload_wording():
    table = label_table()
    small_talk = {
        entry["text"]: (template_id, entry["label"])
        for template_id, entry in table["bank_templates"].items()
    }
    lovely_id, lovely_label = small_talk["The light looks lovely today."]
    greeting_id, greeting_label = small_talk["Hey, good to see you."]

    assert lovely_id == "small_talk#002"
    assert greeting_id == "greeting#001"
    assert (
        compose_expressed_valence(
            [label_for(lovely_id), *payload_labels("The light looks lovely today.")]
        )
        == 0.55
    )
    assert (
        compose_expressed_valence(
            [label_for(greeting_id), *payload_labels("Hey, good to see you.")]
        )
        == 0.0
    )
    assert lovely_label == 0.55
    assert greeting_label == 0.0
    assert payload_labels("it was a beautiful clear sky") == [0.55]


def test_a_style_wrap_keeps_the_label_of_the_sentence_it_wraps():
    # The wrap replaced the template the base label was read from, so every
    # wrapped utterance lost its sentence's label: "Here is the thing: The
    # light looks lovely today." was composed 0.0 (found by a blind
    # whole-utterance labelling, which read it +0.5).
    lovely = "The light looks lovely today."
    seen = 0
    for archetype in ("steady_professional", "volatile_creative"):
        _sim, turns, annotations, _probes, _answers = build(1000, archetype, "1m")
        for turn, annotation in zip(turns, annotations, strict=True):
            if lovely in turn.text:
                seen += 1
                # A joke wrapper may halve it; nothing may zero it.
                assert annotation.expressed_valence >= 0.55 / 2, turn.text
    assert seen >= 3


def test_payload_vocabulary_matches_whole_words_only():
    # A labelled value inside a longer word is a different word: "won the
    # office quiz" is in "we won the office quizzes" only as a substring.
    won = float(
        label_table()["vocab_values"]["ACHIEVEMENTS"]["won the office quiz"]["label"]
    )
    assert payload_labels("we won the office quiz!") == [won]
    assert payload_labels("we won the office quizzes") == []


def test_composition_uses_first_largest_label_joke_half_and_clamp():
    assert compose_expressed_valence([0.4, -0.4, 0.3]) == 0.4
    assert compose_expressed_valence([-0.9, 0.4], joke_wrapper=True) == -0.45
    assert compose_expressed_valence([1.4]) == 1.0
    assert compose_expressed_valence([-1.4]) == -1.0


def test_validity_export_is_fixed_seed_stratified_and_label_free():
    public_rows, label_rows = validity_sample()
    assert len(public_rows) == len(label_rows) == 60
    assert [row["id"] for row in public_rows] == [row["id"] for row in label_rows]
    assert all(set(row) == {"id", "text"} for row in public_rows)
    assert sum(bool(row["label"]) for row in label_rows) == 30
    results = Path(__file__).resolve().parents[2] / "docs/brain-research-v3/results"
    assert (
        json.loads((results / "dr039-validity-sample.json").read_text()) == public_rows
    )
    assert json.loads((results / "dr039-labels-a.json").read_text()) == label_rows


def test_schema_addition_preserves_existing_lifesim_output_bytes():
    _sim, turns, annotations, _probes, _answers = build(
        1000, "steady_professional", "100t"
    )
    old_surface = [
        {
            "turn_id": turn.turn_id,
            "text": turn.text,
            "user_valence": annotation.user_valence,
        }
        for turn, annotation in zip(turns, annotations, strict=True)
    ]
    encoded = json.dumps(
        old_surface, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == (
        "939d87fedfafd77292fbf0a8599d3913df9c92f0380d306b38718e5a7984e1e1"
    )
    assert all(
        -1.0 <= annotation.expressed_valence <= 1.0 for annotation in annotations
    )
    assert all(
        "expressed_valence" in annotation.to_json() for annotation in annotations
    )


def test_coverage_rejects_new_bank_templates_event_kinds_and_vocab_values():
    class BankWithAddition:
        templates = {"share": [type("Template", (), {"id": "share#new"})()]}

        def families(self):
            return ["share"]

    with pytest.raises(ValueError, match="unlabelled bank templates"):
        validate_label_coverage(BankWithAddition(), _event_kind_contract(), vocab)
    with pytest.raises(ValueError, match="no expressed-valence shape"):
        validate_label_coverage(
            _utterance_bank(), _event_kind_contract() | {"new_event"}, vocab
        )

    class NewVocab:
        def __getattr__(self, name):
            current = getattr(vocab, name)
            if name == "WEATHER":
                return (*current, "an unlabelled weather")
            return current

    with pytest.raises(ValueError, match="unlabelled payload vocabulary"):
        validate_label_coverage(_utterance_bank(), _event_kind_contract(), NewVocab())
