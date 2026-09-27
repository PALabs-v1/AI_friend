"""Hand-labelled surface valence fragments for lifesim (DR-039).

Labels in this module and ``expressed_valence.json`` are written from the
words alone. They intentionally do not use the oracle event-valence field.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

LABELS_PATH = Path(__file__).with_name("expressed_valence.json")


@lru_cache(maxsize=1)
def label_table() -> dict:
    return json.loads(LABELS_PATH.read_text(encoding="utf-8"))


def label_for(fragment_id: str) -> float:
    """Return the frozen label for a template or event-shape fragment."""
    table = label_table()
    if fragment_id in table["bank_templates"]:
        return float(table["bank_templates"][fragment_id]["label"])
    return float(table["event_shapes"][fragment_id]["label"])


def event_shape_id(kind: str) -> str:
    """Map concrete event kinds to the wording branch used by ``_event_line``."""
    table = label_table()["event_shapes"]
    if kind in table:
        return kind
    if kind in ("commitment_planned", "trip_planned", "celebration_planned"):
        return kind
    if kind.endswith("_rescheduled"):
        return "rescheduled"
    if kind.endswith("_confirmed"):
        return "confirmed"
    if kind.endswith("_cancelled"):
        return "cancelled"
    if kind.endswith("_done"):
        return "done"
    if kind.startswith(("goal_", "project_")) and kind.rsplit("_", 1)[-1] in (
        "start",
        "progress",
        "achieved",
        "abandoned",
    ):
        return kind
    raise ValueError(f"event kind has no expressed-valence shape: {kind}")


def payload_labels(text: str) -> list[float]:
    """Find non-neutral payload vocabulary present in rendered words.

    Matched on word boundaries: a value inside a longer word ("won" in
    "wonderful") is not that value, and must not lend the utterance its label.
    """
    lowered = text.casefold()
    return [
        score
        for value, score in nonzero_payload_values()
        if value in lowered and is_word_fragment_in(text, value)
    ]


@lru_cache(maxsize=1)
def nonzero_payload_values() -> tuple[tuple[str, float], ...]:
    values = []
    for category in label_table()["vocab_values"].values():
        for value, entry in category.items():
            score = float(entry["label"])
            if score:
                values.append((value.casefold(), score))
    return tuple(values)


def compose_expressed_valence(
    fragments: list[float], *, joke_wrapper: bool = False
) -> float:
    """Select the first largest-magnitude fragment, halve jokes, then clamp."""
    if not fragments:
        return 0.0
    selected = max(fragments, key=abs)
    if joke_wrapper:
        selected *= 0.5
    return max(-1.0, min(1.0, selected))


def validate_label_coverage(bank, event_kinds, vocab_module) -> None:
    """Raise ``ValueError`` for an unlabelled renderer fragment or vocab value."""
    table = label_table()
    missing_templates = {
        template.id for family in bank.families() for template in bank.templates[family]
    } - table["bank_templates"].keys()
    if missing_templates:
        raise ValueError(f"unlabelled bank templates: {sorted(missing_templates)}")

    missing_shapes = {event_shape_id(kind) for kind in event_kinds} - table[
        "event_shapes"
    ].keys()
    if missing_shapes:
        raise ValueError(f"unlabelled event shapes: {sorted(missing_shapes)}")

    missing_vocab = {}
    for category, values in table["vocab_values"].items():
        current = getattr(vocab_module, category)
        if isinstance(current, dict):
            if category == "RELATIONS":
                current_values = set(current)
            else:
                current_values = {
                    value
                    for group in current.values()
                    for value in group
                    if isinstance(value, str)
                }
        else:
            current_values = set(current)
        missing = current_values - values.keys()
        if missing:
            missing_vocab[category] = sorted(missing)
    if missing_vocab:
        raise ValueError(f"unlabelled payload vocabulary values: {missing_vocab}")


def nonzero_vocabulary_entries() -> list[tuple[str, str, float, str]]:
    """Return (category, value, label, reason) for sample construction."""
    result = []
    for category, values in label_table()["vocab_values"].items():
        for value, entry in values.items():
            if entry["label"]:
                result.append((category, value, float(entry["label"]), entry["reason"]))
    return result


def is_word_fragment_in(text: str, value: str) -> bool:
    """Boundary-aware variant available to table audits and sample selection."""
    return (
        re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text, re.IGNORECASE) is not None
    )
