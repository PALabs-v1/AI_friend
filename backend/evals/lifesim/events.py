"""Stochastic life processes on a simulated calendar.

`simulate` walks the calendar one day at a time. Each process owns its own
random stream (`rng.stream`), fires with a per-day hazard scaled by the
persona, and writes two things:

* an `Event` in the event log -- what happened, when, how much it mattered,
  how it felt; and
* timeline assertions for any state it changed (a new job, a trip's
  temporary location, a commitment's due date moving).

Follow-ups that happen later (an apology after an argument, the end of a
trip, a commitment falling due) go on a time-ordered queue and run on their
day, so every consequence is caused by something earlier in the log.

Planned things (trips, commitments, celebrations) are `plan:*` entities: the
plan exists from the day it is made, its ``when`` can be moved (a genuine
change, history kept) and its ``status`` goes planned -> done | cancelled. A
tentative plan carries certainty < 1 until it is confirmed.
"""

from __future__ import annotations

import heapq
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import vocab
from .rng import stream
from .world import ATTRIBUTES, PREFERENCE_ATTRIBUTES, PersonEntity, PetEntity, World

CATEGORIES = (
    "trivial",
    "change",
    "temporary",
    "plan",
    "relationship",
    "robot",
    "emotional",
    "goal",
    "project",
    "episode",
    "loss",
)


@dataclass
class Event:
    event_id: str
    t: datetime
    kind: str
    category: str
    importance: float
    valence: float
    arousal: float
    entities: list[str]
    payload: dict
    effects: list[str] = field(default_factory=list)
    family: str | None = None
    announce_from: datetime | None = None
    toward_robot: bool = False

    def to_json(self) -> dict:
        d = asdict(self)
        d["t"] = self.t.isoformat()
        d["announce_from"] = (
            self.announce_from.isoformat() if self.announce_from else None
        )
        return d


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


WARMTH_BUCKETS = (
    (-0.3, "cold"),
    (0.0, "cool"),
    (0.3, "neutral"),
    (0.6, "warm"),
    (9.0, "close"),
)


class Life:
    def __init__(self, world: World, end: datetime) -> None:
        self.w = world
        self.tl = world.timeline
        self.p = world.persona
        self.end = end
        self.events: list[Event] = []
        self._queue: list[tuple[datetime, int, Callable[[], None]]] = []
        self._qseq = 0
        self._eseq = 0
        self.warmth = 0.1
        self.active_goals: list[str] = []
        self.active_projects: list[str] = []
        self._rngs: dict[str, random.Random] = {}
        self.belief_ids = sorted(e for e, attr in self.tl.slots() if attr == "stance")

    # ---- plumbing -------------------------------------------------------

    def rng(self, name: str) -> random.Random:
        if name not in self._rngs:
            self._rngs[name] = stream(self.w.seed, "events", name)
        return self._rngs[name]

    def later(self, t: datetime, fn: Callable[[], None]) -> None:
        if t < self.end:
            self._qseq += 1
            heapq.heappush(self._queue, (t, self._qseq, fn))

    def emit(
        self,
        t: datetime,
        kind: str,
        category: str,
        importance: float,
        valence: float = 0.0,
        arousal: float = 0.2,
        entities: list[str] | None = None,
        payload: dict | None = None,
        effects: list[str] | None = None,
        family: str | None = None,
        announce_from: datetime | None = None,
        toward_robot: bool = False,
    ) -> Event:
        self._eseq += 1
        # Emotional persons feel events more strongly; the sign never flips.
        scale = 0.55 + 0.9 * self.p.emotionality
        ev = Event(
            event_id=f"e{self._eseq:06d}",
            t=t,
            kind=kind,
            category=category,
            importance=round(importance, 3),
            valence=round(_clip(valence * scale), 3),
            arousal=round(_clip(arousal * scale, 0.0, 1.0), 3),
            entities=entities or [],
            payload=payload or {},
            effects=effects or [],
            family=family,
            announce_from=announce_from,
            toward_robot=toward_robot,
        )
        self.events.append(ev)
        return ev

    @staticmethod
    def at(day: date, rng: random.Random, lo: float, hi: float) -> datetime:
        minutes = int(rng.uniform(lo, hi) * 60)
        return datetime(day.year, day.month, day.day) + timedelta(minutes=minutes)

    def people(self, t: datetime, min_importance: float = 0.0) -> list[PersonEntity]:
        return [
            p
            for _, p in sorted(self.w.people.items())
            if p.importance >= min_importance
            and self.w.alive(p.entity, t)
            and self.tl.current(p.entity, "relation", t) not in (None, "ex-partner")
        ]

    def employed(self, t: datetime) -> bool:
        return self.tl.current("user", "employer", t) is not None

    def away(self, t: datetime) -> bool:
        return self.tl.current("user", "away_city", t) is not None

    # ---- driver ---------------------------------------------------------

    def run(self) -> list[Event]:
        day = self.w.start.date()
        last = (self.end - timedelta(seconds=1)).date()
        processes = (
            self.trivial,
            self.preferences,
            self.beliefs,
            self.career,
            self.residence,
            self.possessions,
            self.network,
            self.user_partner,
            self.trips,
            self.health,
            self.house_guest,
            self.commitments,
            self.social,
            self.robot,
            self.emotional,
            self.goals,
            self.projects,
            self.episodes,
        )
        while day <= last:
            day_end = datetime(day.year, day.month, day.day) + timedelta(days=1)
            while self._queue and self._queue[0][0] < day_end:
                _, _, fn = heapq.heappop(self._queue)
                fn()
            for proc in processes:
                proc(day)
            day += timedelta(days=1)
        self.events = [e for e in self.events if e.t < self.end]
        self.events.sort(key=lambda e: (e.t, e.event_id))
        return self.events

    # ---- processes ------------------------------------------------------

    def trivial(self, day: date) -> None:
        r = self.rng("trivial")
        for meal, p, lo, hi in (
            ("breakfast", 0.25, 7, 10),
            ("lunch", 0.75, 12, 14.5),
            ("dinner", 0.85, 18.5, 21.5),
        ):
            if r.random() < p:
                t = self.at(day, r, lo, hi)
                self.emit(
                    t,
                    "meal",
                    "trivial",
                    0.08,
                    0.05,
                    0.1,
                    payload={"meal": meal, "food": r.choice(vocab.FOODS)},
                    family="meal",
                )
        if r.random() < 0.3:
            self.emit(
                self.at(day, r, 8, 20),
                "weather",
                "trivial",
                0.05,
                r.uniform(-0.2, 0.2),
                0.1,
                payload={"weather": r.choice(vocab.WEATHER)},
                family="weather",
            )
        if r.random() < 0.35:
            self.emit(
                self.at(day, r, 19, 23),
                "watched",
                "trivial",
                0.08,
                0.1,
                0.15,
                payload={"show": r.choice(vocab.TV_SHOWS)},
                family="watched",
            )
        if r.random() < 0.3:
            self.emit(
                self.at(day, r, 9, 19),
                "errand",
                "trivial",
                0.07,
                0.0,
                0.1,
                payload={"errand": r.choice(vocab.ERRANDS)},
                family="errand",
            )

    def preferences(self, day: date) -> None:
        r = self.rng("preferences")
        scale = 0.2 + 1.6 * self.p.volatility
        for attr in PREFERENCE_ATTRIBUTES:
            spec = ATTRIBUTES[attr]
            if r.random() < spec.change_per_day * scale:
                t = self.at(day, r, 8, 22)
                old = self.tl.current("user", attr, t)
                new = r.choice([v for v in spec.pool if v != old])
                a = self.tl.assert_(
                    "user",
                    attr,
                    new,
                    t,
                    kind="changing",
                    source_event=None,
                    importance=spec.importance,
                )
                ev = self.emit(
                    t,
                    "pref_change",
                    "change",
                    spec.importance,
                    0.1,
                    0.2,
                    ["user"],
                    {"attribute": attr, "old": old, "new": new},
                    [a.assertion_id],
                )
                a.source_event = ev.event_id

    def beliefs(self, day: date) -> None:
        r = self.rng("beliefs")
        for b in self.belief_ids:
            if r.random() < (0.5 + self.p.volatility) / 1500:
                t = self.at(day, r, 9, 22)
                topic = self.tl.current(b, "topic", t)
                old = self.tl.current(b, "stance", t)
                new = r.choice([s for s in vocab.BELIEFS[topic] if s != old])
                a = self.tl.assert_(
                    b,
                    "stance",
                    new,
                    t,
                    kind="changing",
                    source_event=None,
                    importance=0.45,
                )
                ev = self.emit(
                    t,
                    "belief_change",
                    "change",
                    0.5,
                    0.0,
                    0.3,
                    [b],
                    {"topic": topic, "old": old, "new": new},
                    [a.assertion_id],
                )
                a.source_event = ev.event_id

    def career(self, day: date) -> None:
        r = self.rng("career")
        age = day.year - self.w.birth.year
        if age >= 63:
            return
        employed = (
            self.tl.current("user", "employer", datetime(day.year, day.month, day.day))
            is not None
        )
        hazard = 1 / 1100 if employed else (1 / 250 if age >= 20 else 0.0)
        if r.random() >= hazard:
            return
        t = self.at(day, r, 9, 18)
        old = self.tl.current("user", "employer", t)
        new = r.choice([c for c in vocab.COMPANIES if c != old])
        a1 = self.tl.assert_(
            "user",
            "employer",
            new,
            t,
            kind="changing",
            source_event=None,
            importance=0.8,
        )
        old_title = self.tl.current("user", "job_title", t)
        keep_title = old_title is not None and r.random() < 0.4
        title = (
            old_title
            if keep_title
            else r.choice([j for j in vocab.JOB_TITLES if j != old_title])
        )
        created = [a1]
        if not keep_title:
            created.append(
                self.tl.assert_(
                    "user",
                    "job_title",
                    title,
                    t,
                    kind="changing",
                    source_event=None,
                    importance=0.8,
                )
            )
        ev = self.emit(
            t,
            "job_change",
            "change",
            0.85,
            0.5,
            0.6,
            ["user"],
            {
                "old_employer": old,
                "employer": new,
                "old_title": old_title,
                "title": title,
            },
            [a.assertion_id for a in created],
        )
        for a in created:
            a.source_event = ev.event_id
        if r.random() < 0.35:
            mt = t + timedelta(days=r.randint(10, 45), hours=r.randint(0, 48))
            self.later(mt, lambda mt=mt: self._move(mt, r, reason="new job"))

    def residence(self, day: date) -> None:
        r = self.rng("residence")
        if r.random() < 1 / 1600:
            self._move(self.at(day, r, 9, 18), r, reason=None)
        elif r.random() < 1 / 1200:
            t = self.at(day, r, 9, 18)
            if not self.tl.can_assert("user", "neighbourhood", t):
                return
            old = self.tl.current("user", "neighbourhood", t)
            new = r.choice([n for n in vocab.NEIGHBOURHOODS if n != old])
            a = self.tl.assert_(
                "user",
                "neighbourhood",
                new,
                t,
                kind="changing",
                source_event=None,
                importance=0.6,
            )
            ev = self.emit(
                t,
                "neighbourhood_move",
                "change",
                0.65,
                0.2,
                0.5,
                ["user"],
                {"old": old, "new": new},
                [a.assertion_id],
            )
            a.source_event = ev.event_id

    def _move(self, t: datetime, r: random.Random, reason: str | None) -> None:
        # A same-day move from the queue and from `residence` must not rewrite
        # each other's history; the later-starting one simply doesn't happen.
        if not (
            self.tl.can_assert("user", "home_city", t)
            and self.tl.can_assert("user", "neighbourhood", t)
        ):
            return
        old = self.tl.current("user", "home_city", t)
        new = r.choice([c for c in vocab.CITIES if c != old])
        a1 = self.tl.assert_(
            "user",
            "home_city",
            new,
            t,
            kind="changing",
            source_event=None,
            importance=0.85,
        )
        hood = r.choice(
            [
                n
                for n in vocab.NEIGHBOURHOODS
                if n != self.tl.current("user", "neighbourhood", t)
            ]
        )
        a2 = self.tl.assert_(
            "user",
            "neighbourhood",
            hood,
            t,
            kind="changing",
            source_event=None,
            importance=0.6,
        )
        ev = self.emit(
            t,
            "move",
            "change",
            0.85,
            0.2,
            0.6,
            ["user"],
            {"old_city": old, "city": new, "neighbourhood": hood, "reason": reason},
            [a1.assertion_id, a2.assertion_id],
        )
        a1.source_event = a2.source_event = ev.event_id

    def possessions(self, day: date) -> None:
        r = self.rng("possessions")
        for attr in ("vehicle", "phone"):
            spec = ATTRIBUTES[attr]
            if r.random() < spec.change_per_day:
                t = self.at(day, r, 9, 20)
                old = self.tl.current("user", attr, t)
                new = r.choice([v for v in spec.pool if v != old])
                a = self.tl.assert_(
                    "user",
                    attr,
                    new,
                    t,
                    kind="changing",
                    source_event=None,
                    importance=spec.importance,
                )
                ev = self.emit(
                    t,
                    "possession_change",
                    "change",
                    spec.importance,
                    0.3,
                    0.3,
                    ["user"],
                    {"attribute": attr, "old": old, "new": new},
                    [a.assertion_id],
                )
                a.source_event = ev.event_id

    def network(self, day: date) -> None:
        r = self.rng("network")
        t0 = datetime(day.year, day.month, day.day)
        user_age = day.year - self.w.birth.year
        for p in self.people(t0):
            e = p.entity
            if r.random() < 1 / 2200:
                t = self.at(day, r, 9, 21)
                old = self.tl.current(e, "city", t)
                new = r.choice([c for c in vocab.CITIES if c != old])
                a = self.tl.assert_(
                    e,
                    "city",
                    new,
                    t,
                    kind="changing",
                    source_event=None,
                    importance=p.importance * 0.7,
                )
                ev = self.emit(
                    t,
                    "person_move",
                    "change",
                    p.importance * 0.6,
                    0.0,
                    0.3,
                    [e],
                    {"old": old, "new": new},
                    [a.assertion_id],
                )
                a.source_event = ev.event_id
            if self.tl.current(e, "employer", t0) is not None and r.random() < 1 / 1600:
                t = self.at(day, r, 9, 21)
                old = self.tl.current(e, "employer", t)
                new = r.choice([c for c in vocab.COMPANIES if c != old])
                a = self.tl.assert_(
                    e,
                    "employer",
                    new,
                    t,
                    kind="changing",
                    source_event=None,
                    importance=p.importance * 0.6,
                )
                ev = self.emit(
                    t,
                    "person_job_change",
                    "change",
                    p.importance * 0.55,
                    0.1,
                    0.3,
                    [e],
                    {"old": old, "new": new},
                    [a.assertion_id],
                )
                a.source_event = ev.event_id
            bday = self.tl.current(e, "birthday", t0)
            if (
                bday
                and p.importance >= 0.6
                and bday == f"{day.strftime('%B')} {day.day}"
            ):
                self.emit(
                    self.at(day, r, 10, 21),
                    "person_birthday",
                    "emotional",
                    0.5,
                    0.4,
                    0.4,
                    [e],
                    {"name": p.name},
                    family="birthday",
                )
            # Loss: grandparents most likely, parents once the user is older, anyone else rarely.
            hazard = {"grandmother": 1 / 2600, "grandfather": 1 / 2400}.get(
                p.relation, 0.0
            )
            if p.relation in ("mother", "father") and user_age >= 50:
                hazard = 1 / 5000
            if hazard == 0.0 and not p.partner_of:
                hazard = 1 / 200000
            if r.random() < hazard:
                self._death(day, r, p)
            if (
                p.relation in ("sister", "brother", "best friend")
                and self.tl.current(e, "partner", t0) is None
                and r.random() < 1 / 2500
            ):
                self._new_partner_for(day, r, p)

    def _death(self, day: date, r: random.Random, p: PersonEntity) -> None:
        t = self.at(day, r, 6, 23)
        a = self.tl.assert_(
            p.entity,
            "status",
            "deceased",
            t,
            kind="status",
            source_event=None,
            importance=0.95,
        )
        ev = self.emit(
            t,
            "death",
            "loss",
            0.97,
            -0.9,
            0.85,
            [p.entity],
            {"name": p.name, "relation": p.relation},
            [a.assertion_id],
        )
        a.source_event = ev.event_id
        ft = t + timedelta(days=r.randint(3, 8), hours=r.randint(0, 6))
        self.later(
            ft,
            lambda: self.emit(
                ft,
                "funeral",
                "loss",
                0.85,
                -0.7,
                0.6,
                [p.entity],
                {"name": p.name, "relation": p.relation},
            ),
        )

    def _new_partner_for(self, day: date, r: random.Random, p: PersonEntity) -> None:
        t = self.at(day, r, 10, 22)
        used = {q.name for q in self.w.people.values()} | {self.w.user_name}
        free = [
            n
            for n in vocab.FEMALE_NAMES + vocab.MALE_NAMES + vocab.NEUTRAL_NAMES
            if n not in used
        ]
        if not free:
            return
        name = r.choice(free)
        qid = self.w.new_id("person")
        rel = f"{p.relation}'s partner"
        self.w.people[qid] = PersonEntity(
            qid,
            name,
            rel,
            r.choice(("f", "m", "n")),
            p.importance * 0.6,
            partner_of=p.entity,
        )
        effects = [
            self.tl.assert_(
                qid, "name", name, t, kind="stable", source_event=None, importance=0.5
            ).assertion_id,
            self.tl.assert_(
                qid,
                "relation",
                rel,
                t,
                kind="relation",
                source_event=None,
                importance=0.5,
            ).assertion_id,
            self.tl.assert_(
                qid,
                "status",
                "alive",
                t,
                kind="status",
                source_event=None,
                importance=0.5,
            ).assertion_id,
            self.tl.assert_(
                qid,
                "employer",
                r.choice(vocab.COMPANIES),
                t,
                kind="changing",
                source_event=None,
                importance=0.4,
            ).assertion_id,
            self.tl.assert_(
                qid,
                "city",
                self.tl.current(p.entity, "city", t) or r.choice(vocab.CITIES),
                t,
                kind="changing",
                source_event=None,
                importance=0.4,
            ).assertion_id,
            self.tl.assert_(
                p.entity,
                "partner",
                qid,
                t,
                kind="relation",
                source_event=None,
                importance=0.6,
            ).assertion_id,
        ]
        ev = self.emit(
            t,
            "person_new_partner",
            "relationship",
            0.55,
            0.3,
            0.4,
            [p.entity, qid],
            {"name": p.name, "partner_name": name},
            effects,
        )
        for aid in effects:
            self.tl.get(aid).source_event = ev.event_id
        if r.random() < 0.35:
            self._plan(
                t,
                r,
                "celebration",
                f"{p.name}'s wedding",
                t + timedelta(days=r.randint(150, 500)),
                where=self.tl.current(p.entity, "city", t),
                with_=p.entity,
                importance=0.8,
                valence=0.8,
            )

    def user_partner(self, day: date) -> None:
        r = self.rng("user_partner")
        t0 = datetime(day.year, day.month, day.day)
        partner = self.tl.current("user", "partner", t0)
        age = day.year - self.w.birth.year
        if partner is not None and self.w.alive(partner, t0):
            if r.random() < 1 / 3500:
                t = self.at(day, r, 12, 23)
                self.tl.end("user", "partner", t)
                a2 = self.tl.assert_(
                    partner,
                    "relation",
                    "ex-partner",
                    t,
                    kind="relation",
                    source_event=None,
                    importance=0.8,
                )
                ev = self.emit(
                    t,
                    "breakup",
                    "relationship",
                    0.95,
                    -0.8,
                    0.85,
                    [partner],
                    {"name": self.w.people[partner].name},
                    [a2.assertion_id],
                )
                a2.source_event = ev.event_id
        elif partner is None and age < 60 and r.random() < 1 / 900:
            t = self.at(day, r, 12, 23)
            used = {q.name for q in self.w.people.values()} | {self.w.user_name}
            free = [
                n
                for n in vocab.FEMALE_NAMES + vocab.MALE_NAMES + vocab.NEUTRAL_NAMES
                if n not in used
            ]
            if not free:
                return
            name = r.choice(free)
            pid = self.w.new_id("person")
            self.w.people[pid] = PersonEntity(
                pid, name, "partner", r.choice(("f", "m", "n")), 0.95
            )
            effects = [
                self.tl.assert_(
                    pid,
                    "name",
                    name,
                    t,
                    kind="stable",
                    source_event=None,
                    importance=0.95,
                ).assertion_id,
                self.tl.assert_(
                    pid,
                    "relation",
                    "partner",
                    t,
                    kind="relation",
                    source_event=None,
                    importance=0.95,
                ).assertion_id,
                self.tl.assert_(
                    pid,
                    "status",
                    "alive",
                    t,
                    kind="status",
                    source_event=None,
                    importance=0.95,
                ).assertion_id,
                self.tl.assert_(
                    pid,
                    "employer",
                    r.choice(vocab.COMPANIES),
                    t,
                    kind="changing",
                    source_event=None,
                    importance=0.6,
                ).assertion_id,
                self.tl.assert_(
                    "user",
                    "partner",
                    pid,
                    t,
                    kind="relation",
                    source_event=None,
                    importance=0.9,
                ).assertion_id,
                # Same gap as build_world's initial-partner branch: without a
                # seeded city, this partner's first person_move has nothing
                # to move from.
                self.tl.assert_(
                    pid,
                    "city",
                    self.tl.current("user", "home_city", t) or r.choice(vocab.CITIES),
                    t,
                    kind="changing",
                    source_event=None,
                    importance=0.4,
                ).assertion_id,
            ]
            ev = self.emit(
                t,
                "new_partner",
                "relationship",
                0.9,
                0.8,
                0.8,
                [pid],
                {"name": name},
                effects,
            )
            for aid in effects:
                self.tl.get(aid).source_event = ev.event_id

    def _plan(
        self,
        made: datetime,
        r: random.Random,
        kind: str,
        what: str,
        when: datetime,
        *,
        where: str | None = None,
        with_: str | None = None,
        importance: float = 0.6,
        valence: float = 0.3,
        remind_robot: bool = False,
        on_done: Callable[[datetime], bool] | None = None,
    ) -> str:
        """Create a plan now, maybe move or cancel it before it happens, then resolve it."""
        pid = self.w.new_id("plan")
        tentative = r.random() < 0.25
        certainty = round(r.uniform(0.4, 0.75), 2) if tentative else 1.0
        effects = [
            self.tl.assert_(
                pid,
                "kind",
                kind,
                made,
                kind="stable",
                source_event=None,
                importance=importance,
            ).assertion_id,
            self.tl.assert_(
                pid,
                "what",
                what,
                made,
                kind="stable",
                source_event=None,
                importance=importance,
            ).assertion_id,
            self.tl.assert_(
                pid,
                "when",
                when.isoformat(timespec="minutes"),
                made,
                kind="commitment",
                source_event=None,
                certainty=certainty,
                importance=importance,
            ).assertion_id,
            self.tl.assert_(
                pid,
                "status",
                "planned",
                made,
                kind="status",
                source_event=None,
                importance=importance,
            ).assertion_id,
        ]
        if where:
            effects.append(
                self.tl.assert_(
                    pid,
                    "where",
                    where,
                    made,
                    kind="stable",
                    source_event=None,
                    importance=importance * 0.8,
                ).assertion_id
            )
        if with_:
            effects.append(
                self.tl.assert_(
                    pid,
                    "with",
                    with_,
                    made,
                    kind="relation",
                    source_event=None,
                    importance=importance * 0.8,
                ).assertion_id
            )
        ev = self.emit(
            made,
            f"{kind}_planned",
            "plan",
            importance,
            valence * 0.5,
            0.3,
            [pid] + ([with_] if with_ else []),
            {
                "plan": pid,
                "what": what,
                "when": when.isoformat(timespec="minutes"),
                "where": where,
                "with": with_,
                "remind_robot": remind_robot,
                "tentative": tentative,
            },
            effects,
            family=kind,
            announce_from=made,
        )
        for aid in effects:
            self.tl.get(aid).source_event = ev.event_id

        lead = when - made
        roll = r.random()
        if roll < 0.18 and lead > timedelta(days=2):
            moved_at = made + lead * r.uniform(0.2, 0.8)
            new_when = when + timedelta(days=r.choice((-3, -2, -1, 1, 2, 3, 7, 14)))
            # Keep a sensible hour (the original appointment's), never
            # moved_at's arbitrary computed time: a reschedule changes the
            # date, not the hour, and a passport appointment does not move
            # to 2:31am just because that's when the user happened to call.
            while new_when <= moved_at or new_when.date() == when.date():
                new_when = datetime.combine(
                    new_when.date() + timedelta(days=1), when.time()
                )

            def reschedule(moved_at=moved_at, new_when=new_when):
                a = self.tl.assert_(
                    pid,
                    "when",
                    new_when.isoformat(timespec="minutes"),
                    moved_at,
                    kind="commitment",
                    source_event=None,
                    importance=importance,
                )
                e = self.emit(
                    moved_at,
                    f"{kind}_rescheduled",
                    "plan",
                    importance,
                    -0.1,
                    0.3,
                    [pid],
                    {
                        "plan": pid,
                        "what": what,
                        "old_when": when.isoformat(timespec="minutes"),
                        "when": new_when.isoformat(timespec="minutes"),
                    },
                    [a.assertion_id],
                    family=kind,
                )
                a.source_event = e.event_id
                self.later(
                    new_when,
                    lambda: self._resolve_plan(
                        pid, what, kind, new_when, importance, valence, on_done
                    ),
                )

            self.later(moved_at, reschedule)
        elif roll < 0.26 and lead > timedelta(days=1):
            cancel_at = made + lead * r.uniform(0.2, 0.9)

            def cancel(cancel_at=cancel_at):
                a = self.tl.assert_(
                    pid,
                    "status",
                    "cancelled",
                    cancel_at,
                    kind="status",
                    source_event=None,
                    importance=importance,
                )
                e = self.emit(
                    cancel_at,
                    f"{kind}_cancelled",
                    "plan",
                    importance,
                    -0.2,
                    0.3,
                    [pid],
                    {"plan": pid, "what": what},
                    [a.assertion_id],
                    family=kind,
                )
                a.source_event = e.event_id

            self.later(cancel_at, cancel)
        else:
            if tentative:
                confirm_at = made + lead * r.uniform(0.3, 0.9)

                def confirm(confirm_at=confirm_at):
                    a = self.tl.assert_(
                        pid,
                        "when",
                        when.isoformat(timespec="minutes"),
                        confirm_at,
                        kind="commitment",
                        source_event=None,
                        certainty=1.0,
                        importance=importance,
                    )
                    e = self.emit(
                        confirm_at,
                        f"{kind}_confirmed",
                        "plan",
                        importance * 0.8,
                        0.1,
                        0.2,
                        [pid],
                        {"plan": pid, "what": what, "when": a.value},
                        [a.assertion_id],
                        family=kind,
                    )
                    a.source_event = e.event_id

                self.later(confirm_at, confirm)
            self.later(
                when,
                lambda: self._resolve_plan(
                    pid, what, kind, when, importance, valence, on_done
                ),
            )
        return pid

    def _resolve_plan(
        self, pid, what, kind, when, importance, valence, on_done
    ) -> None:
        if self.tl.current(pid, "status", when) != "planned":
            return
        # on_done says whether the plan could actually happen (a trip can't
        # start while another is under way); if not, it was called off.
        happened = on_done(when) if on_done else True
        status = "done" if happened else "cancelled"
        a = self.tl.assert_(
            pid,
            "status",
            status,
            when,
            kind="status",
            source_event=None,
            importance=importance,
        )
        e = self.emit(
            when,
            f"{kind}_{status}",
            "plan",
            importance,
            valence if happened else -0.2,
            0.4,
            [pid],
            {"plan": pid, "what": what, "reason": None if happened else "clash"},
            [a.assertion_id],
            family=kind,
        )
        a.source_event = e.event_id

    def trips(self, day: date) -> None:
        r = self.rng("trips")
        t0 = datetime(day.year, day.month, day.day)
        if self.away(t0):
            return
        rate = self.p.lifestyle_complexity / 45 + 1 / 250
        if r.random() >= rate:
            return
        made = self.at(day, r, 9, 22)
        purpose = r.choice(vocab.TRIP_PURPOSES)
        long = (
            purpose in ("a short holiday", "a family visit", "a hiking trip")
            and r.random() < 0.5
        )
        nights = r.randint(6, 18) if long else r.randint(2, 6)
        start = datetime(day.year, day.month, day.day, r.randint(6, 20)) + timedelta(
            days=r.randint(3, 35)
        )
        home = self.tl.current("user", "home_city", made)
        city = r.choice([c for c in vocab.CITIES if c != home])
        lodging = r.choice(vocab.LODGING)

        def depart(
            t_start: datetime,
            city=city,
            lodging=lodging,
            nights=nights,
            purpose=purpose,
        ) -> bool:
            if self.away(t_start) or not self.tl.can_assert(
                "user", "away_city", t_start
            ):
                return False
            back = t_start + timedelta(days=nights)
            a1 = self.tl.assert_(
                "user",
                "away_city",
                city,
                t_start,
                kind="temporary",
                source_event=None,
                until=back,
                importance=0.6,
            )
            a2 = self.tl.assert_(
                "user",
                "lodging",
                lodging,
                t_start,
                kind="temporary",
                source_event=None,
                until=back,
                importance=0.5,
            )
            ev = self.emit(
                t_start,
                "trip_start",
                "temporary",
                0.6,
                0.3,
                0.5,
                ["user"],
                {
                    "city": city,
                    "lodging": lodging,
                    "purpose": purpose,
                    "nights": nights,
                },
                [a1.assertion_id, a2.assertion_id],
                family="trip",
            )
            a1.source_event = a2.source_event = ev.event_id
            self.later(
                back,
                lambda: self.emit(
                    back,
                    "trip_end",
                    "temporary",
                    0.4,
                    0.1,
                    0.3,
                    ["user"],
                    {"city": city, "purpose": purpose},
                    family="trip",
                ),
            )
            return True

        self._plan(
            made,
            r,
            "trip",
            purpose,
            start,
            where=city,
            importance=0.6,
            valence=0.4,
            on_done=depart,
        )

    def health(self, day: date) -> None:
        r = self.rng("health")
        t0 = datetime(day.year, day.month, day.day)
        if self.tl.current("user", "health", t0) is None and r.random() < 1 / 150:
            t = self.at(day, r, 6, 22)
            what = r.choice(vocab.HEALTH_MINOR)
            until = t + timedelta(days=r.randint(2, 8))
            a = self.tl.assert_(
                "user",
                "health",
                what,
                t,
                kind="temporary",
                source_event=None,
                until=until,
                importance=0.55,
            )
            ev = self.emit(
                t,
                "illness",
                "temporary",
                0.55,
                -0.5,
                0.4,
                ["user"],
                {"what": what, "days": (until - t).days},
                [a.assertion_id],
                family="illness",
            )
            a.source_event = ev.event_id
            self.later(
                until,
                lambda: self.emit(
                    until,
                    "recovered",
                    "temporary",
                    0.3,
                    0.4,
                    0.2,
                    ["user"],
                    {"what": what},
                    family="illness",
                ),
            )

    def house_guest(self, day: date) -> None:
        r = self.rng("house_guest")
        t0 = datetime(day.year, day.month, day.day)
        if (
            self.away(t0)
            or self.tl.current("user", "house_guest", t0) is not None
            or r.random() >= 1 / 400
        ):
            return
        close = [
            p
            for p in self.people(t0, 0.55)
            if not p.partner_of and p.relation != "partner"
        ]
        if not close:
            return
        p = r.choice(close)
        t = self.at(day, r, 10, 21) + timedelta(days=r.randint(2, 20))
        until = t + timedelta(days=r.randint(3, 14))

        def arrive(t=t, until=until, p=p):
            if self.tl.current("user", "house_guest", t) is not None or self.away(t):
                return
            a = self.tl.assert_(
                "user",
                "house_guest",
                p.entity,
                t,
                kind="temporary",
                source_event=None,
                until=until,
                importance=0.6,
            )
            ev = self.emit(
                t,
                "house_guest",
                "temporary",
                0.6,
                0.3,
                0.4,
                [p.entity],
                {"name": p.name, "relation": p.relation, "nights": (until - t).days},
                [a.assertion_id],
                family="house_guest",
            )
            a.source_event = ev.event_id

        self.later(t, arrive)

    def commitments(self, day: date) -> None:
        r = self.rng("commitments")
        t0 = datetime(day.year, day.month, day.day)
        if r.random() >= self.p.lifestyle_complexity / 6 + 1 / 30:
            return
        made = self.at(day, r, 8, 22)
        task = r.choice(vocab.COMMITMENT_TASKS)
        who = None
        if "{person}" in task:
            ppl = self.people(t0, 0.4)
            if not ppl:
                return
            person = r.choice(ppl)
            who = person.entity
            task = task.replace("{person}", person.name)
        due_day = day + timedelta(
            days=r.choice((1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60))
        )
        due = datetime(
            due_day.year,
            due_day.month,
            due_day.day,
            r.choice((9, 10, 12, 15, 17, 18, 19)),
        )
        self._plan(
            made,
            r,
            "commitment",
            task,
            due,
            with_=who,
            importance=0.65,
            valence=0.1,
            remind_robot=r.random() < 0.6,
        )

    def social(self, day: date) -> None:
        r = self.rng("social")
        t0 = datetime(day.year, day.month, day.day)
        close = self.people(t0, 0.5)
        if not close:
            return
        if r.random() < (0.2 + self.p.emotionality) / 90:
            p = r.choice(close)
            t = self.at(day, r, 9, 23)
            topic = r.choice(
                (
                    "money",
                    "a missed call",
                    "plans for the weekend",
                    "something said at dinner",
                    "being late again",
                    "politics",
                )
            )
            self.emit(
                t,
                "argument",
                "relationship",
                0.7,
                -0.6,
                0.75,
                [p.entity],
                {"name": p.name, "relation": p.relation, "about": topic},
            )
            if r.random() < 0.7:
                at = t + timedelta(days=r.randint(1, 7), hours=r.randint(0, 8))
                who = r.choice(("user", "them"))
                self.later(
                    at,
                    lambda at=at, p=p, who=who, topic=topic: self.emit(
                        at,
                        "apology",
                        "relationship",
                        0.6,
                        0.45,
                        0.4,
                        [p.entity],
                        {
                            "name": p.name,
                            "relation": p.relation,
                            "by": who,
                            "about": topic,
                        },
                    ),
                )
        if r.random() < 1 / 25:
            p = r.choice(close)
            direction = r.choice(("gave", "received"))
            self.emit(
                self.at(day, r, 9, 23),
                "support",
                "relationship",
                0.55,
                0.5,
                0.4,
                [p.entity],
                {"name": p.name, "relation": p.relation, "direction": direction},
            )
        if r.random() < 1 / 100:
            p = r.choice(close)
            t = self.at(day, r, 9, 23)
            self.emit(
                t,
                "misunderstanding",
                "relationship",
                0.5,
                -0.3,
                0.4,
                [p.entity],
                {"name": p.name, "relation": p.relation},
            )
            if r.random() < 0.8:
                at = t + timedelta(days=r.randint(1, 5))
                self.later(
                    at,
                    lambda at=at, p=p: self.emit(
                        at,
                        "cleared_up",
                        "relationship",
                        0.4,
                        0.3,
                        0.2,
                        [p.entity],
                        {"name": p.name, "relation": p.relation},
                    ),
                )

    def robot(self, day: date) -> None:
        """The user's side of the relationship with the robot, as exogenous ground truth.

        Lifesim cannot know what the brain under test will do, so these are the
        user's own moods and reactions toward the robot. Competence evidence
        (thanks, complaints) and warmth evidence (affection, confiding,
        arguments) are separate on purpose (DR-014).
        """
        r = self.rng("robot")
        t0 = datetime(day.year, day.month, day.day)
        if self.away(t0):
            return
        self.warmth += (0.2 - self.warmth) * 0.004
        fired = False
        # You can only thank or snap at the robot while talking to it.
        contact = min(1.0, 0.2 + self.p.interaction_frequency / 2.5)
        for kind, base, val, dw, comp in (
            ("robot_thanks", 1 / 8, 0.4, 0.02, 1),
            ("robot_complaint", 1 / 35, -0.4, -0.04, -1),
            ("robot_confide", (0.1 + self.p.emotionality) / 18, 0.2, 0.02, 0),
            ("robot_affection", 1 / 60, 0.6, 0.05, 0),
            ("robot_argument", 1 / 180, -0.7, -0.15, 0),
            ("robot_misunderstanding", 1 / 80, -0.2, -0.02, 0),
        ):
            if r.random() < base * contact:
                fired = True
                t = self.at(day, r, 7, 23)
                self.warmth = _clip(self.warmth + dw)
                self.emit(
                    t,
                    kind,
                    "robot",
                    0.45 if kind != "robot_argument" else 0.75,
                    val,
                    abs(val) * 0.8,
                    ["robot"],
                    {"competence_evidence": comp},
                    toward_robot=True,
                )
                if kind == "robot_argument" and r.random() < 0.6:
                    at = t + timedelta(hours=r.randint(2, 72))

                    def sorry(at=at):
                        self.warmth = _clip(self.warmth + 0.08)
                        self.emit(
                            at,
                            "robot_apology",
                            "robot",
                            0.6,
                            0.5,
                            0.4,
                            ["robot"],
                            {"competence_evidence": 0},
                            toward_robot=True,
                        )

                    self.later(at, sorry)
        if r.random() < 1 / 700:
            t = self.at(day, r, 8, 22)
            old = self.tl.current("robot", "nickname", t)
            new = r.choice([n for n in vocab.ROBOT_NICKNAMES if n != old])
            a = self.tl.assert_(
                "robot",
                "nickname",
                new,
                t,
                kind="changing",
                source_event=None,
                importance=0.7,
            )
            ev = self.emit(
                t,
                "robot_nickname",
                "robot",
                0.7,
                0.4,
                0.3,
                ["robot"],
                {"old": old, "new": new},
                [a.assertion_id],
                toward_robot=True,
            )
            a.source_event = ev.event_id
        if fired or day.day == 1:
            bucket = next(name for edge, name in WARMTH_BUCKETS if self.warmth < edge)
            t = self.at(day, r, 23.5, 23.9)
            if self.tl.current("robot", "user_warmth", t) != bucket:
                self.tl.assert_(
                    "robot",
                    "user_warmth",
                    bucket,
                    t,
                    kind="status",
                    source_event=None,
                    importance=0.6,
                )

    def emotional(self, day: date) -> None:
        r = self.rng("emotional")
        t0 = datetime(day.year, day.month, day.day)
        if day.month == self.w.birth.month and day.day == self.w.birth.day:
            self.emit(
                self.at(day, r, 9, 22),
                "user_birthday",
                "emotional",
                0.8,
                0.6,
                0.6,
                ["user"],
                {"age": day.year - self.w.birth.year},
                family="birthday",
            )
        domain = "work" if self.employed(t0) else "life"
        if r.random() < 1 / 160:
            self.emit(
                self.at(day, r, 9, 22),
                "achievement",
                "emotional",
                0.8,
                0.8,
                0.7,
                ["user"],
                {"what": r.choice(vocab.ACHIEVEMENTS), "domain": domain},
            )
        if r.random() < 1 / 220:
            self.emit(
                self.at(day, r, 9, 22),
                "failure",
                "emotional",
                0.75,
                -0.7,
                0.7,
                ["user"],
                {"what": r.choice(vocab.FAILURES), "domain": domain},
            )
        if r.random() < 1 / 150:
            made = self.at(day, r, 9, 22)
            what = r.choice(vocab.CELEBRATIONS)
            when = made + timedelta(days=r.randint(4, 40), hours=r.randint(0, 4))
            self._plan(made, r, "celebration", what, when, importance=0.7, valence=0.7)
        for pet in sorted(self.w.pets.values(), key=lambda p: p.entity):
            if (
                self.tl.current(pet.entity, "status", t0) == "alive"
                and r.random() < 1 / 4500
            ):
                t = self.at(day, r, 6, 22)
                a = self.tl.assert_(
                    pet.entity,
                    "status",
                    "deceased",
                    t,
                    kind="status",
                    source_event=None,
                    importance=0.95,
                )
                ev = self.emit(
                    t,
                    "pet_death",
                    "loss",
                    0.95,
                    -0.85,
                    0.8,
                    [pet.entity],
                    {"name": pet.name, "species": pet.species},
                    [a.assertion_id],
                )
                a.source_event = ev.event_id
        alive_pet = any(
            self.tl.current(p.entity, "status", t0) == "alive"
            for p in self.w.pets.values()
        )
        if not alive_pet and r.random() < 1 / 1200:
            t = self.at(day, r, 9, 20)
            pid = self.w.new_id("pet")
            species = r.choice(vocab.PET_SPECIES)
            used = {p.name for p in self.w.pets.values()}
            name = r.choice(
                [n for n in vocab.PET_NAMES if n not in used] or list(vocab.PET_NAMES)
            )
            self.w.pets[pid] = PetEntity(pid, name, species)
            effects = [
                self.tl.assert_(
                    pid,
                    "name",
                    name,
                    t,
                    kind="stable",
                    source_event=None,
                    importance=0.75,
                ).assertion_id,
                self.tl.assert_(
                    pid,
                    "species",
                    species,
                    t,
                    kind="stable",
                    source_event=None,
                    importance=0.6,
                ).assertion_id,
                self.tl.assert_(
                    pid,
                    "status",
                    "alive",
                    t,
                    kind="status",
                    source_event=None,
                    importance=0.7,
                ).assertion_id,
            ]
            ev = self.emit(
                t,
                "pet_adopted",
                "emotional",
                0.8,
                0.8,
                0.6,
                [pid],
                {"name": name, "species": species},
                effects,
            )
            for aid in effects:
                self.tl.get(aid).source_event = ev.event_id

    def _lifecycle(
        self,
        day: date,
        r: random.Random,
        prefix: str,
        pool: tuple[str, ...],
        active: list[str],
        new_rate: float,
        cap: int,
    ) -> None:
        t0 = datetime(day.year, day.month, day.day)
        just_created = None
        if len(active) < cap and r.random() < new_rate:
            t = self.at(day, r, 8, 22)
            gid = self.w.new_id(prefix)
            just_created = gid
            taken = {self.tl.current(g, "what", t0) for g in active}
            what = r.choice([g for g in pool if g not in taken] or list(pool))
            e1 = self.tl.assert_(
                gid, "what", what, t, kind="stable", source_event=None, importance=0.6
            )
            e2 = self.tl.assert_(
                gid,
                "status",
                "active",
                t,
                kind="status",
                source_event=None,
                importance=0.6,
            )
            ev = self.emit(
                t,
                f"{prefix}_start",
                prefix,
                0.6,
                0.4,
                0.4,
                [gid],
                {"what": what},
                [e1.assertion_id, e2.assertion_id],
            )
            e1.source_event = e2.source_event = ev.event_id
            active.append(gid)
        # A goal added to `active` above was created at a random hour today,
        # not at midnight; reading "what" at t0 for that same gid would look
        # before its own creation and find nothing. Read as of tomorrow's
        # midnight instead -- "what" never changes for a given gid, so this
        # is always safe, and it always sees today's creation.
        what_at = t0 + timedelta(days=1)
        for gid in list(active):
            what = self.tl.current(gid, "what", what_at)
            if r.random() < 1 / 30:
                self.emit(
                    self.at(day, r, 8, 22),
                    f"{prefix}_progress",
                    prefix,
                    0.35,
                    0.3,
                    0.3,
                    [gid],
                    {"what": what},
                    family=f"{prefix}_progress",
                )
            roll = r.random()
            # A goal just created today has its "active" status at a random
            # hour today too; an achieved/abandoned roll at an earlier hour
            # the same day would assert backwards in time on the same slot.
            # Simplest safe rule: a goal must survive its creation day first.
            if gid != just_created and (roll < 1 / 300 or roll > 1 - 1 / 450):
                done = roll < 1 / 300
                t = self.at(day, r, 8, 22)
                status = "achieved" if done else "abandoned"
                a = self.tl.assert_(
                    gid,
                    "status",
                    status,
                    t,
                    kind="status",
                    source_event=None,
                    importance=0.7,
                )
                ev = self.emit(
                    t,
                    f"{prefix}_{status}",
                    prefix,
                    0.75 if done else 0.55,
                    0.8 if done else -0.4,
                    0.6,
                    [gid],
                    {"what": what},
                    [a.assertion_id],
                )
                a.source_event = ev.event_id
                active.remove(gid)

    def goals(self, day: date) -> None:
        self._lifecycle(
            day, self.rng("goals"), "goal", vocab.GOALS, self.active_goals, 1 / 150, 3
        )

    def projects(self, day: date) -> None:
        self._lifecycle(
            day,
            self.rng("projects"),
            "project",
            vocab.PROJECTS,
            self.active_projects,
            self.p.lifestyle_complexity / 150,
            2,
        )

    def episodes(self, day: date) -> None:
        """Repeated same-shaped experiences: the interference families."""
        r = self.rng("episodes")
        t0 = datetime(day.year, day.month, day.day)
        if self.away(t0):
            return
        friends = [
            p
            for p in self.people(t0, 0.4)
            if p.relation not in ("grandmother", "grandfather")
        ]
        if friends and r.random() < self.p.lifestyle_complexity / 7 + 1 / 40:
            p = r.choice(friends)
            occasion = r.choice(vocab.OCCASIONS)
            self.emit(
                self.at(day, r, 12, 22),
                "restaurant_visit",
                "episode",
                0.3,
                0.3,
                0.3,
                [p.entity],
                {
                    "restaurant": r.choice(vocab.RESTAURANTS),
                    "with": p.entity,
                    "name": p.name,
                    "dish": r.choice(vocab.FOODS),
                    "occasion": occasion,
                },
                family="restaurant",
            )
        if r.random() < 1 / 9:
            p = r.choice(friends) if friends and r.random() < 0.5 else None
            self.emit(
                self.at(day, r, 8, 18),
                "cafe_visit",
                "episode",
                0.15,
                0.2,
                0.2,
                [p.entity] if p else [],
                {
                    "cafe": r.choice(vocab.CAFES),
                    "drink": r.choice(vocab.DRINKS),
                    "with": p.entity if p else None,
                    "name": p.name if p else None,
                },
                family="cafe",
            )
        if self.employed(t0) and day.weekday() < 5 and r.random() < 1 / 4:
            coworkers = [
                p for p in self.people(t0) if p.relation in ("coworker", "manager")
            ]
            if coworkers:
                p = r.choice(coworkers)
                self.emit(
                    self.at(day, r, 9, 17),
                    "meeting",
                    "episode",
                    0.25,
                    r.uniform(-0.3, 0.3),
                    0.3,
                    [p.entity],
                    {
                        "topic": r.choice(vocab.MEETING_TOPICS),
                        "with": p.entity,
                        "name": p.name,
                    },
                    family="meeting",
                )
        if friends and r.random() < 1 / 14:
            p = r.choice(friends)
            self.emit(
                self.at(day, r, 10, 22),
                "outing",
                "episode",
                0.3,
                0.4,
                0.4,
                [p.entity],
                {"place": r.choice(vocab.OUTINGS), "with": p.entity, "name": p.name},
                family="outing",
            )


def simulate(world: World, end: datetime) -> list[Event]:
    return Life(world, end).run()
