"""Build DR-039's fixed, stratified validation sample and first-label file."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from .valence import label_table

SAMPLE_SEED = 39039
SAMPLE_SIZE = 60
NONZERO_SIZE = 30


def validity_sample() -> tuple[list[dict], list[dict]]:
    """Return public sample rows and the corresponding private labels-a rows."""
    table = label_table()
    candidates = []

    def add(fragment_id: str, text: str, label: float, reason: str | None) -> None:
        text = re.sub(r"\{[^}]+\}", "something", text).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            candidates.append(
                {
                    "id": fragment_id,
                    "text": text,
                    "label": float(label),
                    "reason": reason,
                }
            )

    for fragment_id, entry in table["bank_templates"].items():
        add(f"bank:{fragment_id}", entry["text"], entry["label"], entry["reason"])
    for fragment_id, entry in table["emotion_openers"].items():
        add(f"emotion:{fragment_id}", entry["text"], entry["label"], entry["reason"])
    for fragment_id, entry in table["fixed_fragments"].items():
        add(f"fixed:{fragment_id}", entry["text"], entry["label"], entry["reason"])
    for fragment_id, entry in table["event_shapes"].items():
        add(f"event:{fragment_id}", entry["text"], entry["label"], entry["reason"])
    for category, entries in table["vocab_values"].items():
        for value, entry in entries.items():
            add(
                f"vocab:{category}:{value}",
                value,
                entry["label"],
                entry["reason"],
            )

    nonzero = [candidate for candidate in candidates if candidate["label"]]
    neutral = [candidate for candidate in candidates if not candidate["label"]]
    if len(nonzero) < 20 or len(neutral) < SAMPLE_SIZE - 20:
        raise ValueError(
            "the label inventory cannot supply a stratified 60-item sample"
        )

    rng = random.Random(SAMPLE_SEED)
    selected = rng.sample(nonzero, NONZERO_SIZE) + rng.sample(
        neutral, SAMPLE_SIZE - NONZERO_SIZE
    )
    rng.shuffle(selected)
    public_rows = [{"id": item["id"], "text": item["text"]} for item in selected]
    label_rows = [
        {"id": item["id"], "label": item["label"], "reason": item["reason"]}
        for item in selected
    ]
    return public_rows, label_rows


def export_validity_sample(root: Path | None = None) -> None:
    root = root or Path(__file__).resolve().parents[3]
    results = root / "docs/brain-research-v3/results"
    public_rows, label_rows = validity_sample()
    for name, rows in (
        ("dr039-validity-sample.json", public_rows),
        ("dr039-labels-a.json", label_rows),
    ):
        (results / name).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    export_validity_sample()
