"""Output records and the public/oracle leakage boundary (R9).

Public records are the only thing a brain under test may see:

* `Turn` -- turn_id, session_id, t, speaker, text.
* `Probe` -- probe_id, t, text.

Everything else is oracle: `Annotation` (what a turn really said, meant and
felt), `ProbeAnswer` (what the right answer is and which turns support it),
the world, the timeline and the event log. Oracle files live in their own
directory so a harness can mount ``public/`` alone.

Files are written deterministically (sorted keys, fixed separators, one record
per line) so two runs of the same seed are byte-identical (R2).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

INTENTS = (
    "greeting",
    "small_talk",
    "share_fact",
    "share_update",
    "share_event",
    "share_plan",
    "request_reminder",
    "express_emotion",
    "robot_feedback",
    "correction",
    "joke",
    "reminisce",
    "question",
)

# Master prompt "Memory types" plus conversation kinds (R4). A turn can carry several.
TURN_TAGS = (
    "trivial_episodic",
    "stable_fact",
    "changing_fact",
    "temporary_state",
    "historical_fact",
    "commitment",
    "relationship_event",
    "emotional_event",
    "repeated",
    "contradiction_accidental",
    "contradiction_intentional",
    "correction",
    "uncertain",
    "misleading",
    "joke",
    "insignificant",
    "important",
)

PROBE_CATEGORIES = (
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
)

EXPECTED = ("answer", "abstain", "forgettable", "surface_conflict")


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t is not None else None


@dataclass
class Turn:
    turn_id: str
    session_id: str
    t: datetime
    text: str
    speaker: str = "user"

    def to_json(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "t": _iso(self.t),
            "speaker": self.speaker,
            "text": self.text,
        }


@dataclass
class Claim:
    """One proposition a turn states about a slot.

    ``truthful`` is judged against the timeline at ``about`` (the time the claim
    refers to, which is the turn time unless it talks about the past or a
    plan). ``assertion_id`` names the true assertion the claim is about, even
    when the stated value is wrong, so a scorer can tell a misstatement of
    fact X from a claim about something else entirely.
    """

    entity: str
    attribute: str
    value: str
    truthful: bool
    about: datetime | None = None
    assertion_id: str | None = None
    hedged: bool = False

    def to_json(self) -> dict:
        d = asdict(self)
        d["about"] = _iso(self.about)
        return d


@dataclass
class Annotation:
    turn_id: str
    intent: str
    tags: list[str]
    event_ids: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    importance: float = 0.1
    user_valence: float = 0.0
    arousal: float = 0.1
    toward_robot_valence: float | None = None
    competence_evidence: int = 0
    misstatement: bool = False
    joke: bool = False
    corrects_turn: str | None = None
    template_family: str = ""
    template_id: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["claims"] = [c.to_json() for c in self.claims]
        return d


@dataclass
class Probe:
    probe_id: str
    t: datetime
    text: str

    def to_json(self) -> dict:
        return {"probe_id": self.probe_id, "t": _iso(self.t), "text": self.text}


@dataclass
class ProbeAnswer:
    probe_id: str
    category: str
    expected: str
    answer: list[str]
    support_turn_ids: list[str]
    stale_turn_ids: list[str] = field(default_factory=list)
    distractor_turn_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    derivation: dict = field(default_factory=dict)
    template_family: str = ""
    template_id: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def dumps(obj) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def write_jsonl(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(dumps(r) + "\n" for r in rows).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, obj) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, default=str)
        + "\n"
    ).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]
