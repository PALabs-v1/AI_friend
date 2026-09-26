"""Gate tests for the bank-expansion validator (no `claude -p` calls: pure logic only)."""

from __future__ import annotations

from evals.lifesim.bank_expand import required_fields, validate

SPEC = {
    "placeholders": ["old", "new", "note"],
    "templates": [
        {"id": "pref_change#000", "text": "I switched from {old} to {new}."},
        {"id": "pref_change#001", "text": "These days it's {new}, not {old} anymore."},
    ],
}


def test_required_fields_is_the_intersection_across_existing_templates():
    assert required_fields(SPEC["templates"]) == {"old", "new"}
    assert required_fields([]) == set()
    assert required_fields([{"text": "just {a}"}]) == {"a"}


def test_accepts_a_valid_new_variant_with_all_required_placeholders():
    ok, rejected = validate(["Honestly? {new} now, used to be {old}."], SPEC)
    assert ok == ["Honestly? {new} now, used to be {old}."]
    assert rejected == []


def test_rejects_missing_required_placeholder():
    ok, rejected = validate(["I like {new} now."], SPEC)
    assert ok == [] and rejected[0][1].startswith("missing required")


def test_rejects_undeclared_placeholder():
    ok, rejected = validate(["Switched to {new} because of {reason}."], SPEC)
    assert ok == [] and "undeclared" in rejected[0][1]


def test_rejects_stray_brace():
    ok, rejected = validate(["I switched from {old} to {new} } oddly."], SPEC)
    assert ok == [] and "format string" in rejected[0][1]


def test_rejects_duplicate_of_an_existing_template_case_and_space_insensitive():
    ok, rejected = validate(["  I SWITCHED   from {old} to {new}.  "], SPEC)
    assert ok == [] and rejected[0][1] == "duplicate"


def test_rejects_duplicates_within_the_same_batch():
    ok, rejected = validate(
        ["Now {new}, before {old}.", "now {new}, before {old}."], SPEC
    )
    assert len(ok) == 1 and len(rejected) == 1


def test_rejects_too_short_or_too_long_or_multiline():
    ok, rejected = validate(
        ["{new}", "word " * 61 + "{old} {new}", "line one {old}\nline two {new}"], SPEC
    )
    assert ok == [] and len(rejected) == 3


def test_rejects_non_string_candidates():
    ok, rejected = validate([{"not": "a string"}, 5, None], SPEC)
    assert ok == [] and len(rejected) == 3


def test_optional_placeholder_may_be_included_or_omitted():
    ok, _ = validate(["Now {new} instead of {old}, {note}."], SPEC)
    assert ok
    ok2, _ = validate(["Now {new} instead of {old}, no real reason."], SPEC)
    assert ok2
