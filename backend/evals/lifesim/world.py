"""The simulated human and everything around them, at the start of the simulation.

`build_world` produces the entities (user, people, pets, robot) and seeds the
timeline with every fact that is true at ``start``, plus the history that led
there: the hometown, the university city and the previous employer are real
assertions that were superseded before ``start``, so historical questions have
ground truth from turn one.

Entity ids are opaque (``person:p07``) and never appear in observation text.
Attribute names are registered in `ATTRIBUTES` so every other module agrees on
what a slot means, what kind of fact it is, and which vocabulary it draws from.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import vocab
from .personas import Persona
from .rng import stream
from .timeline import Timeline

CONFUSABLE: dict[str, str] = {}
for _a, _b in (
    ("Priya", "Priyanka"),
    ("Anna", "Ana"),
    ("Maya", "Mia"),
    ("Leena", "Lena"),
    ("Kavya", "Kavita"),
    ("Sofia", "Sophie"),
    ("Meera", "Mira"),
    ("Fatima", "Farah"),
    ("Rohan", "Rohit"),
    ("Jon", "John"),
    ("Arjun", "Aryan"),
    ("Sam", "Samir"),
    ("Marco", "Mario"),
    ("Kiran", "Karan"),
    ("Tomas", "Thomas"),
    ("Ben", "Benji"),
    ("Chris", "Kris"),
    ("Nikhil", "Nikita"),
    ("Aditi", "Aditya"),
    ("Ravi", "Ravina"),
    ("Yuki", "Yuka"),
    ("Daniel", "Danielle"),
    ("Ali", "Alina"),
):
    CONFUSABLE[_a] = _b
    CONFUSABLE[_b] = _a


@dataclass(frozen=True)
class AttributeSpec:
    attribute: str
    kind: str  # timeline kind
    pool: tuple[str, ...] | None
    importance: float
    change_per_day: float = (
        0.0  # base hazard before persona scaling (0 = never changes on its own)
    )
    volatile: bool = False  # scaled by persona.volatility


# User-level slots. People, pets, plans, goals and projects have their own
# attributes, listed in `ENTITY_ATTRIBUTES`.
ATTRIBUTES: dict[str, AttributeSpec] = {
    s.attribute: s
    for s in (
        AttributeSpec("name", "stable", None, 0.9),
        AttributeSpec("birthday", "stable", None, 0.9),
        AttributeSpec("hometown", "stable", vocab.CITIES, 0.7),
        AttributeSpec("home_city", "changing", vocab.CITIES, 0.85),
        AttributeSpec("neighbourhood", "changing", vocab.NEIGHBOURHOODS, 0.6),
        AttributeSpec("employer", "changing", vocab.COMPANIES, 0.8),
        AttributeSpec("job_title", "changing", vocab.JOB_TITLES, 0.8),
        AttributeSpec("university", "historical", vocab.UNIVERSITIES, 0.6),
        AttributeSpec("degree", "historical", vocab.DEGREES, 0.55),
        AttributeSpec("drink", "changing", vocab.DRINKS, 0.5, 1 / 300, True),
        AttributeSpec("food", "changing", vocab.FOODS, 0.5, 1 / 300, True),
        AttributeSpec("cuisine", "changing", vocab.CUISINES, 0.45, 1 / 400, True),
        AttributeSpec("music", "changing", vocab.MUSIC, 0.45, 1 / 350, True),
        AttributeSpec("tv_show", "changing", vocab.TV_SHOWS, 0.4, 1 / 200, True),
        AttributeSpec("book_genre", "changing", vocab.BOOK_GENRES, 0.4, 1 / 450, True),
        AttributeSpec("sport", "changing", vocab.SPORTS, 0.4, 1 / 600, True),
        AttributeSpec("weekend", "changing", vocab.WEEKEND, 0.4, 1 / 300, True),
        AttributeSpec("hobby", "changing", vocab.HOBBIES, 0.55, 1 / 500, True),
        AttributeSpec(
            "morning_routine", "changing", vocab.MORNING_ROUTINES, 0.4, 1 / 500, True
        ),
        AttributeSpec("exercise", "changing", vocab.EXERCISE, 0.45, 1 / 500, True),
        AttributeSpec("vehicle", "changing", vocab.CARS, 0.5, 1 / 1500),
        AttributeSpec("phone", "changing", vocab.PHONES, 0.35, 1 / 700),
        AttributeSpec("partner", "relation", None, 0.9),
        # temporary states: value present only while the state lasts
        AttributeSpec("away_city", "temporary", vocab.CITIES, 0.6),
        AttributeSpec("lodging", "temporary", vocab.LODGING, 0.5),
        AttributeSpec("health", "temporary", vocab.HEALTH_MINOR, 0.6),
        AttributeSpec("house_guest", "temporary", None, 0.6),
    )
}
PREFERENCE_ATTRIBUTES = tuple(a for a, s in ATTRIBUTES.items() if s.volatile)

ENTITY_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "person": (
        "name",
        "relation",
        "city",
        "employer",
        "job_title",
        "birthday",
        "partner",
        "status",
    ),
    "pet": ("name", "species", "status"),
    "robot": ("nickname", "user_warmth"),
    "plan": ("kind", "what", "when", "where", "with", "status"),
    "goal": ("what", "status"),
    "project": ("what", "status"),
    "belief": ("topic", "stance"),
}


@dataclass
class PersonEntity:
    entity: str
    name: str
    relation: str
    gender: str
    importance: float
    partner_of: str | None = None  # entity id, for in-laws/partners of network members


@dataclass
class PetEntity:
    entity: str
    name: str
    species: str


@dataclass
class World:
    seed: int
    persona: Persona
    start: datetime
    birth: date
    user_name: str
    user_gender: str
    people: dict[str, PersonEntity] = field(default_factory=dict)
    pets: dict[str, PetEntity] = field(default_factory=dict)
    timeline: Timeline = field(default_factory=Timeline)
    counters: dict[str, int] = field(default_factory=dict)

    def new_id(self, prefix: str) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}:{prefix[0]}{n:03d}"

    def person_by_relation(self, relation: str) -> list[PersonEntity]:
        return [p for p in self.people.values() if p.relation == relation]

    def alive(self, entity: str, t: datetime) -> bool:
        return self.timeline.current(entity, "status", t) != "deceased"

    def to_json(self) -> dict:
        return {
            "seed": self.seed,
            "persona": self.persona.to_json(),
            "start": self.start.isoformat(),
            "birth": self.birth.isoformat(),
            "user_name": self.user_name,
            "user_gender": self.user_gender,
            "people": {k: asdict(v) for k, v in sorted(self.people.items())},
            "pets": {k: asdict(v) for k, v in sorted(self.pets.items())},
        }


AGE_RANGES = {
    "chatty_student": (18, 24),
    "forgetful_retiree": (64, 78),
    "night_owl_gamer": (19, 30),
    "busy_parent": (30, 45),
    "steady_professional": (28, 50),
    "traveling_consultant": (30, 50),
    "emotional_caregiver": (35, 60),
}


def _pick_name(rng: random.Random, gender: str, used: set[str]) -> str:
    pool = {"f": vocab.FEMALE_NAMES, "m": vocab.MALE_NAMES}.get(
        gender, vocab.FEMALE_NAMES + vocab.MALE_NAMES + vocab.NEUTRAL_NAMES
    )
    # A fifth of names deliberately collide with an existing one's confusable twin.
    twins = [
        CONFUSABLE[n]
        for n in sorted(used)
        if n in CONFUSABLE and CONFUSABLE[n] not in used and CONFUSABLE[n] in pool
    ]
    if twins and rng.random() < 0.2:
        return rng.choice(twins)
    free = [n for n in pool if n not in used]
    return rng.choice(free or list(pool))


def _gender_of(rng: random.Random, pool_gender: str) -> str:
    return pool_gender if pool_gender in ("f", "m") else rng.choice(("f", "m", "n"))


def _month_day(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}"


def build_world(seed: int, persona: Persona, start: datetime | None = None) -> World:
    rng = stream(seed, "world")
    if start is None:
        start = datetime(2026, 1, 5) + timedelta(days=rng.randrange(0, 365))
    lo, hi = AGE_RANGES.get(persona.archetype, (22, 68))
    age = rng.randint(lo, hi)
    birth = date(start.year - age, rng.randint(1, 12), rng.randint(1, 28))
    user_gender = rng.choice(("f", "m", "n"))
    used: set[str] = set()
    user_name = _pick_name(rng, user_gender, used)
    used.add(user_name)
    w = World(
        seed=seed,
        persona=persona,
        start=start,
        birth=birth,
        user_name=user_name,
        user_gender=user_gender,
    )
    tl = w.timeline
    born = datetime(birth.year, birth.month, birth.day)

    tl.assert_(
        "user",
        "name",
        user_name,
        born,
        kind="stable",
        source_event=None,
        importance=0.9,
    )
    tl.assert_(
        "user",
        "birthday",
        _month_day(birth),
        born,
        kind="stable",
        source_event=None,
        importance=0.9,
    )
    hometown = rng.choice(vocab.CITIES)
    tl.assert_(
        "user",
        "hometown",
        hometown,
        born,
        kind="stable",
        source_event=None,
        importance=0.7,
    )

    # Residence history: hometown -> (university city) -> current city.
    tl.assert_(
        "user",
        "home_city",
        hometown,
        born,
        kind="changing",
        source_event=None,
        importance=0.85,
    )
    adult = datetime(birth.year + 18, 8, 1)
    if age >= 22:
        uni_city = rng.choice([c for c in vocab.CITIES if c != hometown])
        if adult < start:
            tl.assert_(
                "user",
                "home_city",
                uni_city,
                adult,
                kind="changing",
                source_event=None,
                importance=0.85,
            )
        tl.assert_(
            "user",
            "university",
            rng.choice(vocab.UNIVERSITIES),
            adult,
            kind="historical",
            source_event=None,
            importance=0.6,
        )
        tl.assert_(
            "user",
            "degree",
            rng.choice(vocab.DEGREES),
            adult,
            kind="historical",
            source_event=None,
            importance=0.55,
        )
        grad = datetime(birth.year + 22, 6, 15)
        first_job_start = grad + timedelta(days=rng.randint(30, 200))
        moved = start - timedelta(days=rng.randint(200, 1500))
        if moved > grad:
            current_city = rng.choice(
                [c for c in vocab.CITIES if c not in (hometown, uni_city)]
            )
            tl.assert_(
                "user",
                "home_city",
                current_city,
                moved,
                kind="changing",
                source_event=None,
                importance=0.85,
            )
        if age < 62 and first_job_start < start - timedelta(days=400):
            prev_emp = rng.choice(vocab.COMPANIES)
            tl.assert_(
                "user",
                "employer",
                prev_emp,
                first_job_start,
                kind="changing",
                source_event=None,
                importance=0.8,
            )
            tl.assert_(
                "user",
                "job_title",
                rng.choice(vocab.JOB_TITLES),
                first_job_start,
                kind="changing",
                source_event=None,
                importance=0.8,
            )
            switched = start - timedelta(days=rng.randint(60, 380))
            if switched > first_job_start:
                tl.assert_(
                    "user",
                    "employer",
                    rng.choice([c for c in vocab.COMPANIES if c != prev_emp]),
                    switched,
                    kind="changing",
                    source_event=None,
                    importance=0.8,
                )
                tl.assert_(
                    "user",
                    "job_title",
                    rng.choice(vocab.JOB_TITLES),
                    switched,
                    kind="changing",
                    source_event=None,
                    importance=0.8,
                )
    elif persona.archetype == "chatty_student" or age < 22:
        tl.assert_(
            "user",
            "university",
            rng.choice(vocab.UNIVERSITIES),
            adult if adult < start else start - timedelta(days=90),
            kind="historical",
            source_event=None,
            importance=0.6,
        )
        tl.assert_(
            "user",
            "degree",
            rng.choice(vocab.DEGREES),
            adult if adult < start else start - timedelta(days=90),
            kind="historical",
            source_event=None,
            importance=0.55,
        )

    tl.assert_(
        "user",
        "neighbourhood",
        rng.choice(vocab.NEIGHBOURHOODS),
        start - timedelta(days=rng.randint(30, 700)),
        kind="changing",
        source_event=None,
        importance=0.6,
    )

    for attr in PREFERENCE_ATTRIBUTES:
        spec = ATTRIBUTES[attr]
        tl.assert_(
            "user",
            attr,
            rng.choice(spec.pool),
            start - timedelta(days=rng.randint(10, 900)),
            kind="changing",
            source_event=None,
            importance=spec.importance,
        )
    tl.assert_(
        "user",
        "vehicle",
        rng.choice(vocab.CARS),
        start - timedelta(days=rng.randint(100, 2000)),
        kind="changing",
        source_event=None,
        importance=0.5,
    )
    tl.assert_(
        "user",
        "phone",
        rng.choice(vocab.PHONES),
        start - timedelta(days=rng.randint(30, 700)),
        kind="changing",
        source_event=None,
        importance=0.35,
    )

    for topic, stances in sorted(vocab.BELIEFS.items()):
        if rng.random() < 0.6:
            b = w.new_id("belief")
            tl.assert_(
                b,
                "topic",
                topic,
                start - timedelta(days=400),
                kind="stable",
                source_event=None,
                importance=0.4,
            )
            tl.assert_(
                b,
                "stance",
                rng.choice(stances),
                start - timedelta(days=rng.randint(30, 700)),
                kind="changing",
                source_event=None,
                importance=0.45,
            )

    _build_network(w, rng)

    if rng.random() < 0.55:
        pet = w.new_id("pet")
        species = rng.choice(vocab.PET_SPECIES)
        name = rng.choice(vocab.PET_NAMES)
        w.pets[pet] = PetEntity(pet, name, species)
        adopted = start - timedelta(days=rng.randint(60, 2500))
        tl.assert_(
            pet,
            "name",
            name,
            adopted,
            kind="stable",
            source_event=None,
            importance=0.75,
        )
        tl.assert_(
            pet,
            "species",
            species,
            adopted,
            kind="stable",
            source_event=None,
            importance=0.6,
        )
        tl.assert_(
            pet,
            "status",
            "alive",
            adopted,
            kind="status",
            source_event=None,
            importance=0.7,
        )

    tl.assert_(
        "robot",
        "user_warmth",
        "neutral",
        start,
        kind="status",
        source_event=None,
        importance=0.6,
    )
    return w


def _build_network(w: World, rng: random.Random) -> None:
    tl = w.timeline
    start = w.start
    age = start.year - w.birth.year
    target = w.persona.network_size
    used = {w.user_name}
    order: list[str] = [
        "mother",
        "father",
        "best friend",
        "sister",
        "brother",
        "friend",
        "friend",
        "coworker",
        "manager",
    ]
    if age < 45:
        order += ["grandmother", "grandfather"]
    extra = [r for r in vocab.RELATIONS for _ in range(vocab.RELATIONS[r][1])]
    rng.shuffle(extra)
    counts: dict[str, int] = {}
    for rel in order + extra:
        if len(w.people) >= target:
            break
        gpool, cap, importance = vocab.RELATIONS[rel]
        if counts.get(rel, 0) >= cap:
            continue
        if (
            rel in ("coworker", "manager")
            and tl.current("user", "employer", start) is None
        ):
            continue
        counts[rel] = counts.get(rel, 0) + 1
        gender = _gender_of(rng, gpool)
        name = _pick_name(rng, gender, used)
        used.add(name)
        pid = w.new_id("person")
        w.people[pid] = PersonEntity(pid, name, rel, gender, importance)
        known = start - timedelta(days=rng.randint(200, 8000))
        tl.assert_(
            pid,
            "name",
            name,
            known,
            kind="stable",
            source_event=None,
            importance=importance,
        )
        tl.assert_(
            pid,
            "relation",
            rel,
            known,
            kind="relation",
            source_event=None,
            importance=importance,
        )
        tl.assert_(
            pid,
            "status",
            "alive",
            known,
            kind="status",
            source_event=None,
            importance=importance,
        )
        tl.assert_(
            pid,
            "city",
            rng.choice(vocab.CITIES),
            known + timedelta(days=rng.randint(0, 150)),
            kind="changing",
            source_event=None,
            importance=importance * 0.7,
        )
        if rel not in ("grandmother", "grandfather") and rng.random() < 0.8:
            tl.assert_(
                pid,
                "employer",
                rng.choice(vocab.COMPANIES),
                known + timedelta(days=rng.randint(0, 150)),
                kind="changing",
                source_event=None,
                importance=importance * 0.6,
            )
            tl.assert_(
                pid,
                "job_title",
                rng.choice(vocab.JOB_TITLES),
                known + timedelta(days=rng.randint(0, 150)),
                kind="changing",
                source_event=None,
                importance=importance * 0.6,
            )
        if importance >= 0.8 or rng.random() < 0.3:
            bd = date(2000, rng.randint(1, 12), rng.randint(1, 28))
            tl.assert_(
                pid,
                "birthday",
                _month_day(bd),
                known,
                kind="stable",
                source_event=None,
                importance=importance * 0.8,
            )

    # Partners of close family and friends: second-degree people for multi-hop probes.
    for pid, p in sorted(w.people.items()):
        if (
            p.relation in ("sister", "brother", "best friend", "cousin")
            and rng.random() < 0.6
        ):
            gender = rng.choice(("f", "m", "n"))
            name = _pick_name(rng, gender, used)
            used.add(name)
            qid = w.new_id("person")
            w.people[qid] = PersonEntity(
                qid,
                name,
                f"{p.relation}'s partner",
                gender,
                p.importance * 0.6,
                partner_of=pid,
            )
            since = start - timedelta(days=rng.randint(100, 3000))
            tl.assert_(
                qid,
                "name",
                name,
                since,
                kind="stable",
                source_event=None,
                importance=p.importance * 0.6,
            )
            tl.assert_(
                qid,
                "relation",
                f"{p.relation}'s partner",
                since,
                kind="relation",
                source_event=None,
                importance=p.importance * 0.6,
            )
            tl.assert_(
                qid,
                "status",
                "alive",
                since,
                kind="status",
                source_event=None,
                importance=p.importance * 0.6,
            )
            tl.assert_(
                pid,
                "partner",
                qid,
                since,
                kind="relation",
                source_event=None,
                importance=p.importance * 0.7,
            )
            tl.assert_(
                qid,
                "employer",
                rng.choice(vocab.COMPANIES),
                since,
                kind="changing",
                source_event=None,
                importance=0.4,
            )
            tl.assert_(
                qid,
                "job_title",
                rng.choice(vocab.JOB_TITLES),
                since,
                kind="changing",
                source_event=None,
                importance=0.4,
            )
            tl.assert_(
                qid,
                "city",
                tl.current(pid, "city", start) or rng.choice(vocab.CITIES),
                since,
                kind="changing",
                source_event=None,
                importance=0.4,
            )

    if age >= 24 and rng.random() < 0.55:
        gender = rng.choice(("f", "m", "n"))
        name = _pick_name(rng, gender, used)
        used.add(name)
        pid = w.new_id("person")
        w.people[pid] = PersonEntity(pid, name, "partner", gender, 0.95)
        since = start - timedelta(days=rng.randint(200, 5000))
        tl.assert_(
            pid, "name", name, since, kind="stable", source_event=None, importance=0.95
        )
        tl.assert_(
            pid,
            "relation",
            "partner",
            since,
            kind="relation",
            source_event=None,
            importance=0.95,
        )
        tl.assert_(
            pid,
            "status",
            "alive",
            since,
            kind="status",
            source_event=None,
            importance=0.95,
        )
        tl.assert_(
            "user",
            "partner",
            pid,
            since,
            kind="relation",
            source_event=None,
            importance=0.9,
        )
        # Every other person in the network gets a seeded "city" (world.py's
        # own invariant that events.py's person_move relies on); this branch
        # was the one place that didn't, so the first move for the user's own
        # partner had no prior value to move from.
        tl.assert_(
            pid,
            "city",
            tl.current("user", "home_city", since) or rng.choice(vocab.CITIES),
            since,
            kind="changing",
            source_event=None,
            importance=0.7,
        )
        tl.assert_(
            pid,
            "employer",
            rng.choice(vocab.COMPANIES),
            since,
            kind="changing",
            source_event=None,
            importance=0.6,
        )
        tl.assert_(
            pid,
            "job_title",
            rng.choice(vocab.JOB_TITLES),
            since,
            kind="changing",
            source_event=None,
            importance=0.6,
        )
        bd = date(2000, rng.randint(1, 12), rng.randint(1, 28))
        tl.assert_(
            pid,
            "birthday",
            _month_day(bd),
            since,
            kind="stable",
            source_event=None,
            importance=0.85,
        )
