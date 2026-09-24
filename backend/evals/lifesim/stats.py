"""Measure a generated lifesim directory from its files alone.

Reads ``public/`` and ``oracle/`` output, never the generator's objects, so
the numbers describe what a harness would actually receive. Used by the CLI
(``python -m evals.lifesim stats DIR``) and by the integrity gates.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from .schema import PROBE_CATEGORIES, TURN_TAGS, read_jsonl

_STOPWORDS_TEXT = """
    a about after again all am an and any are as at be been before being but by can could did
    do does doing for from had has have having he her here hers him his how i if in into is it its
    just me more most my no not now of off on once only or other our out over own same she should
    so some such than that the their them then there these they this those through to too under
    until up very was we were what when where which while who whom why will with would you your
    yours yourself still ever got get also really been going go went last like one know think
    tell told say said
"""
STOPWORDS = frozenset(_STOPWORDS_TEXT.split())
_WORD = re.compile(r"[a-z0-9']+")


def content_words(text: str, names: frozenset[str] = frozenset()) -> set[str]:
    return {
        w
        for w in _WORD.findall(text.lower())
        if w not in STOPWORDS and w not in names and len(w) > 2
    }


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load(d: Path) -> dict:
    d = Path(d)
    return {
        "turns": read_jsonl(d / "public/turns.jsonl"),
        "probes": read_jsonl(d / "public/probes.jsonl"),
        "annotations": read_jsonl(d / "oracle/annotations.jsonl"),
        "answers": read_jsonl(d / "oracle/answers.jsonl"),
        "events": read_jsonl(d / "oracle/events.jsonl"),
        "timeline": read_jsonl(d / "oracle/timeline.jsonl"),
    }


def describe(d: Path) -> dict:
    data = load(d)
    turns, anns, probes, answers = (
        data["turns"],
        data["annotations"],
        data["probes"],
        data["answers"],
    )
    names = frozenset(
        a["value"].lower() for a in data["timeline"] if a["attribute"] == "name"
    )

    tags = Counter(tag for a in anns for tag in a["tags"])
    intents = Counter(a["intent"] for a in anns)
    claims = [c for a in anns for c in a["claims"]]
    words = [len(t["text"].split()) for t in turns]

    starts: dict[str, datetime] = {}
    for t in turns:
        starts.setdefault(t["session_id"], _t(t["t"]))
    ordered = sorted(starts.values())
    gaps = [(b - a).total_seconds() for a, b in pairwise(ordered)]
    span_days = (
        max(1.0, (ordered[-1] - ordered[0]).total_seconds() / 86400) if ordered else 1.0
    )

    turn_text = {t["turn_id"]: t["text"] for t in turns}
    zero_overlap = with_overlap = 0
    for p, a in zip(probes, answers, strict=True):
        if a["expected"] != "answer" or not a["support_turn_ids"]:
            continue
        q = content_words(p["text"], names)
        s = set().union(
            *(
                content_words(turn_text[i], names)
                for i in a["support_turn_ids"]
                if i in turn_text
            )
        )
        if q & s:
            with_overlap += 1
        else:
            zero_overlap += 1

    family_uses: dict[str, Counter] = defaultdict(Counter)
    for a in anns:
        family_uses[a["template_family"]][a["template_id"]] += 1
    probe_uses: dict[str, Counter] = defaultdict(Counter)
    for a in answers:
        probe_uses[a["category"]][a["template_id"]] += 1

    def dominance(uses: dict[str, Counter], min_n: int) -> dict[str, float]:
        return {
            f: round(max(c.values()) / sum(c.values()), 3)
            for f, c in sorted(uses.items())
            if sum(c.values()) >= min_n
        }

    n_claims = max(1, len(claims))
    return {
        "counts": {
            "turns": len(turns),
            "sessions": len(starts),
            "probes": len(probes),
            "claims": len(claims),
        },
        "tags": {t: tags.get(t, 0) for t in TURN_TAGS},
        "missing_tags": [t for t in TURN_TAGS if not tags.get(t)],
        "intents": dict(sorted(intents.items())),
        "probe_categories": {
            c: sum(1 for a in answers if a["category"] == c) for c in PROBE_CATEGORIES
        },
        "missing_categories": [
            c for c in PROBE_CATEGORIES if not any(a["category"] == c for a in answers)
        ],
        "expected": dict(Counter(a["expected"] for a in answers)),
        "words_per_turn": round(statistics.mean(words), 2) if words else 0.0,
        "misstatement_rate": round(
            sum(1 for a in anns if a["misstatement"]) / max(1, len(anns)), 4
        ),
        "untruthful_claim_rate": round(
            sum(1 for c in claims if not c["truthful"]) / n_claims, 4
        ),
        "hedge_rate": round(sum(1 for c in claims if c["hedged"]) / n_claims, 4),
        "joke_rate": round(sum(1 for a in anns if a["joke"]) / max(1, len(anns)), 4),
        "sessions_per_week": round(len(starts) / span_days * 7, 3),
        "turns_per_session": round(len(turns) / max(1, len(starts)), 2),
        "gap_cv": round(statistics.pstdev(gaps) / statistics.mean(gaps), 3)
        if len(gaps) > 1
        else 0.0,
        "max_gap_days": round(max(gaps) / 86400, 2) if gaps else 0.0,
        "probe_support_overlap": {"zero": zero_overlap, "some": with_overlap},
        "turn_template_dominance": dominance(family_uses, 20),
        "probe_template_dominance": dominance(probe_uses, 7),
    }
