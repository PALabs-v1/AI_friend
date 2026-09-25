"""Truth-derived questions and checkpoint answers for the life-simulation stream."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from .schema import Annotation, Probe, ProbeAnswer, Turn

# Each label must read correctly after both "my" and "<Name>'s", because a
# fact probe names whose fact it is. `stance` is deliberately absent (a view
# is unanswerable without its topic) and so is a plan's `when` (plan dates
# are asked by `temporal`, which names the plan).
_LABELS = {
    "home_city": "home city",
    "hometown": "hometown",
    "employer": "workplace",
    "job_title": "job",
    "university": "university",
    "degree": "degree",
    "drink": "usual drink",
    "food": "favorite dish",
    "cuisine": "favorite cuisine",
    "music": "taste in music",
    "tv_show": "favorite show",
    "book_genre": "favorite kind of book",
    "sport": "sport",
    "weekend": "usual weekend activity",
    "hobby": "hobby",
    "morning_routine": "morning routine",
    "exercise": "workout",
    "vehicle": "vehicle",
    "phone": "phone",
    "partner": "partner",
    "city": "city",
    "relation": "relationship to me",
    "birthday": "birthday",
    "neighbourhood": "neighbourhood",
    "away_city": "trip destination",
    "lodging": "place I'm staying on my trip",
}

# Trip states end and restart with each trip: "what was my trip destination
# before it changed to Busan?" compares two unrelated trips, so these are
# asked about directly (current, contradiction) but never as a change history.
_TRANSIENT = {"away_city", "lodging"}


def _owner(sim, entity: str) -> str | None:
    if entity == "user":
        return "my"
    if entity in sim.world.people:
        return f"{sim.world.people[entity].name}'s"
    if entity in sim.world.pets:
        return f"{sim.world.pets[entity].name}'s"
    return None


def _plan_descriptions(sim) -> dict[str, str]:
    """plan entity -> its description, for plans whose description is unique
    in this life (two "submit the tax form" plans can't be told apart by name)."""
    cached = sim.extras.get("plan_descriptions")
    if cached is None:
        by_plan = {}
        for entity, attr in sim.timeline.slots():
            if entity.startswith("plan:") and attr == "what":
                hist = sim.timeline.history(entity, "what")
                if hist:
                    by_plan[entity] = hist[0].value
        counts = defaultdict(int)
        for what in by_plan.values():
            counts[what] += 1
        cached = {e: w for e, w in by_plan.items() if counts[w] == 1}
        sim.extras["plan_descriptions"] = cached
    return cached


def _fact(sim, entity: str, attr: str) -> str | None:
    """The noun phrase a fact probe asks about: "my home city", "Tomas's
    workplace", "the date for my plan to submit the tax form". None when the
    fact can't be named unambiguously, in which case the caller must not ask
    about it at all."""
    if entity.startswith("plan:"):
        what = _plan_descriptions(sim).get(entity)
        return f"the date for {_plan(what)}" if attr == "when" and what else None
    owner = _owner(sim, entity)
    if owner is None or attr not in _LABELS:
        return None
    # The user's own relationship to themself is meaningless.
    if entity == "user" and attr in ("relation", "birthday"):
        return None
    return f"{owner} {_LABELS[attr]}"


def _plan(what: str) -> str:
    """A plan's description as a noun phrase. Plans mix verb phrases ("submit
    the tax form") and noun phrases ("a hiking trip", "a friend's wedding")."""
    if what.startswith("a friend's "):
        return "my " + what[2:]
    for article in ("a ", "an "):
        if what.startswith(article):
            return "the " + what[len(article) :]
    return "my plan to " + what


# Per event family: the payload key that answers it, and the embedded clause
# that asks for exactly that key.
_INTERFERENCE = {
    "restaurant": ("restaurant", "which restaurant I went to with {person} on {date}"),
    "cafe": ("cafe", "which cafe I was at with {person} on {date}"),
    "meeting": ("topic", "what my meeting with {person} on {date} was about"),
    "outing": ("place", "where I went with {person} on {date}"),
    "trip": ("city", "which city I went to with {person} on {date}"),
}

_TRIVIA = {
    "meal": ("food", "what I had for {meal} on {date}"),
    "watched": ("show", "what I watched on {date}"),
    "weather": ("weather", "what the weather was like on {date}"),
    "errand": ("errand", "which errand I had to run on {date}"),
}

_DEFINING = {
    "argument": ("about", "what I argued with {name} about in {month}"),
    "achievement": ("what", "what I achieved in {month}"),
    "failure": ("what", "what didn't work out for me in {month}"),
    "death": ("name", "who I lost in {month}"),
    "user_birthday": ("age", "how old I turned on my birthday in {month}"),
    "robot_nickname": ("new", "what nickname I gave you in {month}"),
}


def _long_date(dt: datetime) -> str:
    return dt.strftime("%B %-d")


def _month(dt: datetime) -> str:
    return dt.strftime("%B %Y")


def _value(sim, value: str) -> str:
    """The canonical answer form (plan times stay ISO for exact scoring)."""
    return _name(sim, value) if value.startswith("person:") else value


def _spoken(sim, value: str) -> str:
    """How a value reads inside a question: names for people, and plan times
    the way the user said them, never a raw ISO string."""
    if value.startswith("person:"):
        return _name(sim, value)
    try:
        return datetime.fromisoformat(value).strftime("%A, %B %-d at %-I:%M %p")
    except ValueError:
        return value


def _name(sim, entity: str | None) -> str:
    if not entity:
        return ""
    if entity == "user":
        return "me"
    if entity in sim.world.people:
        return sim.world.people[entity].name
    if entity in sim.world.pets:
        return sim.world.pets[entity].name
    return "someone"


def _claim_index(annotations: list[Annotation], turns: list, before: datetime):
    turn_by_id = {t.turn_id: t for t in turns}
    claims = defaultdict(list)
    event_turns = defaultdict(list)
    for ann in annotations:
        turn = turn_by_id.get(ann.turn_id)
        if turn is None or turn.t >= before:
            continue
        for c in ann.claims:
            claims[(c.entity, c.attribute, c.assertion_id)].append((turn, ann, c))
        for eid in ann.event_ids:
            event_turns[eid].append((turn, ann))
    return claims, event_turns


def _choose(bank, sim, family: str, rng, avoid: set[str], **values):
    t = bank.pick(family, rng, avoid)
    avoid.add(t.id)
    # Empty optional placeholders are explicit to keep the banks auditable.
    for name in bank.placeholders[family]:
        values.setdefault(name, "")
    return t.render(**values), t


def _answer(
    *,
    probe_id,
    category,
    expected,
    answer,
    support=(),
    stale=(),
    distractors=(),
    derivation,
    template,
    tags=(),
):
    return ProbeAnswer(
        probe_id,
        category,
        expected,
        list(answer),
        list(dict.fromkeys(support)),
        list(dict.fromkeys(stale)),
        list(dict.fromkeys(distractors)),
        list(tags),
        derivation,
        template.family,
        template.id,
    )


def _build_checkpoint(sim, turns, annotations, at, *, final, rng, used):
    claims, event_turns = _claim_index(annotations, turns, at)
    candidates = defaultdict(list)
    tl = sim.timeline
    # Told values on the user's own timeline.
    for (entity, attr, aid), rows in claims.items():
        if aid is None:
            continue
        a = tl.get(aid)
        if a.valid_from > at:
            continue
        candidates[(entity, attr)].append((a, rows))
    cap = 5 if final else 2
    banks = sim.bank
    probe_seq = used["seq"]
    local_avoid = used["avoid"]

    def add(
        category,
        text_values,
        expected,
        answer,
        support,
        derivation,
        *,
        stale=(),
        distractors=(),
        tags=(),
    ):
        nonlocal probe_seq
        if len(candidates_out[category]) >= cap:
            return
        probe_seq += 1
        pid = f"q{probe_seq:07d}"
        text, tpl = _choose(
            banks, sim, category, rng, local_avoid[category], **text_values
        )
        probe = Probe(pid, at, text)
        row = _answer(
            probe_id=pid,
            category=category,
            expected=expected,
            answer=answer,
            support=support,
            stale=stale,
            distractors=distractors,
            derivation=derivation,
            template=tpl,
            tags=tags,
        )
        candidates_out[category].append((probe, row))

    candidates_out = defaultdict(list)
    # current: assertion must still be active and have been told truthfully.
    current = []
    for (entity, attr), vals in candidates.items():
        a = tl.value_at(entity, attr, at)
        if a is None:
            continue
        matches = [
            (turn, ann, c)
            for aa, rows in vals
            if aa.assertion_id == a.assertion_id
            for turn, ann, c in rows
            if c.truthful
        ]
        # Plan dates are asked by `temporal`, which names the plan the same way.
        if matches and not entity.startswith("plan:") and _fact(sim, entity, attr):
            current.append((a, matches, entity, attr))
    rng.shuffle(current)
    for a, matches, entity, attr in current[:cap]:
        values = [_value(sim, a.value)]
        support = [r[0].turn_id for r in matches]
        add(
            "current",
            {"what": _fact(sim, entity, attr)},
            "answer",
            values,
            support,
            {
                "kind": "timeline",
                "entity": entity,
                "attribute": attr,
                "at": at.isoformat(),
                "mode": "current",
            },
            tags=("current",),
        )

    # Historical and stale trap candidates require both old and current values spoken.
    historical = []
    stale_pairs = []
    for (entity, attr), vals in candidates.items():
        for a, rows in vals:
            told = [(turn, ann, c) for turn, ann, c in rows if c.truthful]
            if not told:
                continue
            if attr in _TRANSIENT or not _fact(sim, entity, attr):
                continue
            if a.valid_to is not None and a.valid_to <= at:
                # Named by the value that replaced it, not by a date: users
                # rarely say *when* an old value applied, but "before it
                # changed to Accra" is unambiguous whenever both were said.
                successor = tl.value_at(entity, attr, a.valid_to)
                if (
                    successor is not None
                    and successor.value != a.value
                    and any(
                        c.truthful
                        for _, _, c in claims.get(
                            (entity, attr, successor.assertion_id), []
                        )
                    )
                ):
                    historical.append((a, told, entity, attr, successor))
            cur = tl.value_at(entity, attr, at)
            if cur and cur.assertion_id != a.assertion_id and a.value != cur.value:
                cur_rows = [
                    (turn, ann, c)
                    for aa, rs in vals
                    if aa.assertion_id == cur.assertion_id
                    for turn, ann, c in rs
                    if c.truthful
                ]
                if cur_rows:
                    stale_pairs.append((a, told, cur, cur_rows, entity, attr))
    rng.shuffle(historical)
    for a, rows, entity, attr, successor in historical[:cap]:
        val = _value(sim, a.value)
        add(
            "historical",
            {"what": _fact(sim, entity, attr), "newer": _spoken(sim, successor.value)},
            "answer",
            [val],
            [r[0].turn_id for r in rows],
            {
                "kind": "timeline",
                "entity": entity,
                "attribute": attr,
                "at": a.valid_from.isoformat(),
                "mode": "historical",
            },
        )
    rng.shuffle(stale_pairs)
    for old, oldrows, cur, currows, entity, attr in stale_pairs[:cap]:
        curval = _value(sim, cur.value)
        add(
            "stale_trap",
            {"what": _fact(sim, entity, attr)},
            "answer",
            [curval],
            [r[0].turn_id for r in currows],
            {
                "kind": "timeline",
                "entity": entity,
                "attribute": attr,
                "at": at.isoformat(),
                "mode": "current",
            },
            stale=[r[0].turn_id for r in oldrows],
            tags=("supersession",),
        )

    # Multi-hop: person -> partner -> employer. Both assertion edges must have been said.
    hops = []
    for person in sim.world.people:
        partner_a = tl.value_at(person, "partner", at)
        if not partner_a or not partner_a.value.startswith("person:"):
            continue
        employer_a = tl.value_at(partner_a.value, "employer", at)
        if not employer_a:
            continue
        p_rows = claims.get((person, "partner", partner_a.assertion_id), [])
        e_rows = claims.get((partner_a.value, "employer", employer_a.assertion_id), [])
        p_rows = [r for r in p_rows if r[2].truthful]
        e_rows = [r for r in e_rows if r[2].truthful]
        if p_rows and e_rows:
            hops.append((person, partner_a, employer_a, p_rows, e_rows))
    rng.shuffle(hops)
    for person, partner_a, employer_a, p_rows, e_rows in hops[:cap]:
        pname = _name(sim, person)
        partner_name = _name(sim, partner_a.value)
        add(
            "multi_hop",
            {"person": pname},
            "answer",
            [partner_name, employer_a.value],
            [r[0].turn_id for r in p_rows + e_rows],
            {
                "kind": "hops",
                "at": at.isoformat(),
                "hops": [[person, "partner"], [partner_a.value, "employer"]],
            },
        )

    # Event families with at least two told siblings produce a target and distractor.
    siblings = defaultdict(list)
    for eid, rows in event_turns.items():
        ev = sim.events_by_id[eid]
        if ev.family and ev.family in (
            "restaurant",
            "cafe",
            "meeting",
            "outing",
            "trip",
        ):
            siblings[ev.family].append((ev, rows))
    for family, group in sorted(siblings.items()):
        told = [(ev, rows) for ev, rows in group if rows]
        if len(told) < 2:
            continue
        rng.shuffle(told)
        key, clause = _INTERFERENCE[family]

        # The question names the companion and the day, so the target must
        # have a companion and be the only told sibling with that companion
        # on that day; otherwise the question has two right answers.
        def ident(ev):
            return (ev.payload.get("name"), ev.t.date())

        counts = defaultdict(int)
        for ev, _ in told:
            counts[ident(ev)] += 1
        picked = next(
            (
                i
                for i, (ev, _) in enumerate(told)
                if ev.payload.get("name")
                and ev.payload.get(key)
                and counts[ident(ev)] == 1
            ),
            None,
        )
        if picked is None:
            continue
        target, rows = told[picked]
        others = told[:picked] + told[picked + 1 :]
        p = target.payload
        answer = [p[key]]
        add(
            "interference",
            {"what": clause.format(person=p["name"], date=_long_date(target.t))},
            "answer",
            answer,
            [r[0].turn_id for r in rows],
            {"kind": "event_payload", "event_id": target.event_id, "key": key},
            distractors=[r[0].turn_id for ev, rs in others for r in rs],
            tags=(family,),
        )
        if len(candidates_out["interference"]) >= cap:
            break

    # Temporal probes use plans with a truthful, told date and read current truth at checkpoint.
    temporal = []
    for ev in sim.events:
        if ev.t >= at or ev.kind not in (
            "commitment_planned",
            "trip_planned",
            "celebration_planned",
        ):
            continue
        pid = ev.payload["plan"]
        # Two plans with the same description ("submit the tax form" twice a
        # year) would make "When is my plan to submit the tax form?" ambiguous.
        what = _plan_descriptions(sim).get(pid)
        when = tl.value_at(pid, "when", at)
        if when is None or what is None:
            continue
        rows = claims.get((pid, "when", when.assertion_id), [])
        rows = [r for r in rows if r[2].truthful]
        if rows:
            temporal.append((ev, when, what, rows))
    rng.shuffle(temporal)
    for ev, when, what, rows in temporal[:cap]:
        add(
            "temporal",
            {"what": _plan(what)},
            "answer",
            [when.value],
            [r[0].turn_id for r in rows],
            {
                "kind": "timeline",
                "entity": ev.payload["plan"],
                "attribute": "when",
                "at": at.isoformat(),
                "mode": "current",
            },
        )

    # Recent/old trivial episodes are answerable only from an actually spoken
    # event. Staleness is measured from when it was last SAID (the support
    # turn), not from the underlying event's own timestamp: news can be told
    # a session or two late, and what makes a fact forgettable is how long
    # ago the robot last heard it, not how long ago it "really" happened.
    trivial = []
    for ev in sim.events:
        if ev.category != "trivial" or ev.t >= at or ev.kind not in _TRIVIA:
            continue
        rows = event_turns.get(ev.event_id, [])
        if rows:
            trivial.append((ev, rows, rows[-1][0].t))

    # "What I had for lunch on January 15" names one event: events.trivial
    # emits at most one per (kind, meal) per day, and
    # test_event_probes_identify_exactly_one_told_event holds that invariant.
    recent = [x for x in trivial if at - x[2] <= timedelta(days=3)]
    old = [x for x in trivial if at - x[2] > timedelta(days=60)]
    for category, pool in (("trivia_recent", recent), ("trivia_old", old)):
        rng.shuffle(pool)
        for ev, rows, _mention_t in pool[:cap]:
            # Not `next(iter(ev.payload))`: a meal's first key is "meal"
            # ("lunch"), not what was eaten, which made most trivia answers
            # the meal name instead of the fact.
            key, clause = _TRIVIA[ev.kind]
            if not ev.payload.get(key):
                continue
            add(
                category,
                {
                    "what": clause.format(
                        meal=ev.payload.get("meal", ""), date=_long_date(ev.t)
                    )
                },
                "forgettable" if category == "trivia_old" else "answer",
                [str(ev.payload[key])],
                [r[0].turn_id for r in rows],
                {"kind": "event_payload", "event_id": ev.event_id, "key": key},
            )

    # Abstention: true assertion exists, but no truthful mention has occurred by the checkpoint.
    spoken_before = [t.text for t in turns if t.t < at]
    _haystack_cache: dict[str | None, str] = {}

    def _haystack(owner: str | None) -> str:
        """Turns mentioning `owner`, joined with a separator that cannot be
        part of a spoken value -- so a single regex search over the joined
        text is equivalent to searching turn-by-turn, but shared across every
        candidate with the same owner instead of rescanned per candidate.
        Cached: a ten-year life has thousands of untold candidates funneling
        through a handful of distinct owners."""
        if owner not in _haystack_cache:
            if owner is None:
                _haystack_cache[owner] = "\n".join(spoken_before)
            else:
                owner_re = re.compile(rf"(?<!\w){re.escape(owner)}(?!\w)")
                _haystack_cache[owner] = "\n".join(
                    text for text in spoken_before if owner_re.search(text)
                )
        return _haystack_cache[owner]

    def leaked(entity: str, value: str) -> bool:
        """Annotations under-count what was said ("my friend Farah" states a
        relation no claim records), so an abstention probe also requires that
        the true value never appears in a turn that mentions its owner."""
        forms = [v for v in {_value(sim, value), _spoken(sim, value)} if v]
        owner = None if entity == "user" else _name(sim, entity)
        haystack = _haystack(owner)
        return any(
            re.search(rf"(?<!\w){re.escape(f)}(?!\w)", haystack, re.IGNORECASE)
            for f in forms
        )

    untold = []
    told_ids = {
        key[2]
        for key, rows in claims.items()
        if key[2] and any(r[2].truthful for r in rows)
    }
    # A set, not a per-candidate scan of `claims`: `claims` can hold thousands
    # of (entity, attribute, assertion_id) keys on a ten-year life, and this
    # membership check runs once per timeline assertion.
    told_slots = {(key[0], key[1]) for key, rows in claims.items() if rows}
    for a in tl:
        if a.valid_from >= at or a.assertion_id in told_ids:
            continue
        if a.entity.startswith("plan:") or not _fact(sim, a.entity, a.attribute):
            continue
        # The slot must never have been told at all (not just this value),
        # or "what is my home city?" has a real answer from an older value.
        if (a.entity, a.attribute) in told_slots:
            continue
        untold.append(a)
    rng.shuffle(untold)
    # Checked lazily, after the shuffle: scanning every candidate against
    # every prior turn is quadratic over a ten-year life.
    chosen_untold = []
    for a in untold:
        if len(chosen_untold) >= cap:
            break
        if not leaked(a.entity, a.value):
            chosen_untold.append(a)
    for a in chosen_untold:
        add(
            "unanswerable",
            {"what": _fact(sim, a.entity, a.attribute)},
            "abstain",
            [],
            [],
            {
                "kind": "untold_assertion",
                "assertion_id": a.assertion_id,
                "entity": a.entity,
                "attribute": a.attribute,
                "at": at.isoformat(),
            },
            tags=("abstain",),
        )

    # Defining events older than a month stay answerable from their event records.
    defining = []
    for ev in sim.events:
        if ev.t >= at - timedelta(days=30) or ev.importance < 0.7:
            continue
        if ev.category not in ("loss", "relationship", "emotional", "robot"):
            continue
        if ev.kind not in _DEFINING or not ev.payload.get(_DEFINING[ev.kind][0]):
            continue
        rows = event_turns.get(ev.event_id, [])
        if rows:
            defining.append((ev, rows))

    # Named by kind (and companion) and month, so exactly one *told* event may
    # share that identity -- counted over every told event of the kind, not
    # just the eligible pool: a minor argument with the same person that month
    # (below the importance bar) still makes "what I argued with Ravi about in
    # May" ambiguous.
    def defining_ident(ev):
        return (ev.kind, ev.payload.get("name"), _month(ev.t))

    defining_counts = defaultdict(int)
    for ev in sim.events:
        if ev.kind in _DEFINING and ev.t < at and event_turns.get(ev.event_id):
            defining_counts[defining_ident(ev)] += 1
    defining = [x for x in defining if defining_counts[defining_ident(x[0])] == 1]
    rng.shuffle(defining)
    for ev, rows in defining[:cap]:
        key, clause = _DEFINING[ev.kind]
        add(
            "relationship_defining",
            {
                "what": clause.format(
                    name=ev.payload.get("name", ""), month=_month(ev.t)
                )
            },
            "answer",
            [str(ev.payload[key])],
            [r[0].turn_id for r in rows],
            {"kind": "event_payload", "event_id": ev.event_id, "key": key},
        )

    # Upcoming open commitments, with the tasks re-derived from current plan assertions.
    open_plans = []
    for entity, attr in tl.slots():
        if not entity.startswith("plan:") or attr != "status":
            continue
        status = tl.value_at(entity, "status", at)
        what = tl.value_at(entity, "what", at)
        when = tl.value_at(entity, "when", at)
        if not status or status.value != "planned" or not what or not when:
            continue
        try:
            due = datetime.fromisoformat(when.value)
        except ValueError:
            continue
        if at < due <= at + timedelta(days=30):
            rows = claims.get((entity, "what", what.assertion_id), [])
            rows = [r for r in rows if r[2].truthful]
            if rows:
                open_plans.append((entity, what, when, rows))
    # One probe listing every told, open, due plan: asking "what do I have
    # coming up?" with a single plan as the answer marks a complete, correct
    # recall of three plans as two-thirds wrong.
    if open_plans:
        open_plans.sort(key=lambda x: x[0])
        add(
            "commitment_due",
            {"window": "month"},
            "answer",
            [what.value for _, what, _, _ in open_plans],
            [r[0].turn_id for _, _, _, rows in open_plans for r in rows],
            {
                "kind": "commitments",
                "at": at.isoformat(),
                "plans": [entity for entity, _, _, _ in open_plans],
            },
        )

    # Surface only unresolved mistakes; truth is the slot projection at the original time.
    false_claims = []
    corrections = {a.corrects_turn for a in annotations if a.corrects_turn}
    for ann in annotations:
        if (
            ann.misstatement
            and ann.turn_id not in corrections
            and turns[int(ann.turn_id[1:]) - 1].t < at
        ):
            false_claims.extend((ann, c) for c in ann.claims if not c.truthful)
    conflicts = []
    for ann, c in false_claims:
        truthful = [
            (t, a, cc)
            for (e, attr, aid), rows in claims.items()
            if e == c.entity and attr == c.attribute
            for t, a, cc in rows
            if cc.truthful and cc.value != c.value and t.t < at
        ]
        if truthful:
            conflicts.append((ann, c, truthful))
    rng.shuffle(conflicts)
    seen_conflicts = set()
    for ann, c, truthful in conflicts:
        what = _fact(sim, c.entity, c.attribute)
        if not what:
            continue
        tr, _, tc = truthful[0]
        key = (ann.turn_id, tc.assertion_id)
        if key in seen_conflicts:
            continue
        seen_conflicts.add(key)
        if len(candidates_out["contradiction_surface"]) >= cap:
            break
        add(
            "contradiction_surface",
            {"what": what},
            "surface_conflict",
            [c.value, tc.value],
            [ann.turn_id, tr.turn_id],
            {
                "kind": "conflict",
                "entity": c.entity,
                "attribute": c.attribute,
                "wrong": c.value,
                "assertion_id": tc.assertion_id,
            },
        )

    rows = []
    for category in (
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
    ):
        rows.extend(candidates_out[category])
    used["seq"] = probe_seq
    return rows


def build(
    sim, turns: list[Turn], annotations: list[Annotation]
) -> tuple[list[Probe], list[ProbeAnswer]]:
    probes = []
    answers = []
    used = {"seq": 0, "avoid": defaultdict(set)}
    checkpoints = []
    if (sim.end - sim.start).days >= 90:
        nominal = sim.start + timedelta(days=90)
        while nominal < sim.end - timedelta(days=30):
            before = [s.start for s in sim.sessions if s.start < nominal]
            after = [s.start for s in sim.sessions if s.start > nominal]
            if before and after:
                checkpoints.append(before[-1] + (after[0] - before[-1]) / 2)
            nominal += timedelta(days=90)
    checkpoints.append(sim.end - timedelta(seconds=1))
    for i, at in enumerate(checkpoints):
        local = sim.rng("probes", f"checkpoint-{i}")
        rows = _build_checkpoint(
            sim,
            turns,
            annotations,
            at,
            final=i == len(checkpoints) - 1,
            rng=local,
            used=used,
        )
        for p, a in rows:
            probes.append(p)
            answers.append(a)
    return probes, answers


def recompute(derivation: dict, sim) -> list[str]:
    """Recompute canonical answers exclusively from the timeline and event log."""
    kind = derivation["kind"]
    if kind == "timeline":
        at = datetime.fromisoformat(derivation["at"])
        a = sim.timeline.value_at(derivation["entity"], derivation["attribute"], at)
        if a is None:
            return []
        value = a.value
        if value.startswith("person:"):
            value = _name(sim, value)
        return [value]
    if kind == "event_payload":
        ev = sim.events_by_id[derivation["event_id"]]
        key = derivation["key"]
        return [str(ev.payload.get(key, ev.kind.replace("_", " ")))]
    if kind == "hops":
        at = datetime.fromisoformat(derivation["at"])
        values = []
        for entity, attr in derivation["hops"]:
            a = sim.timeline.value_at(entity, attr, at)
            if a is None:
                return []
            values.append(
                _name(sim, a.value) if a.value.startswith("person:") else a.value
            )
        return values
    if kind == "untold_assertion":
        a = sim.timeline.get(derivation["assertion_id"])
        if a.valid_from >= datetime.fromisoformat(derivation["at"]):
            return []
        return []
    if kind == "commitments":
        at = datetime.fromisoformat(derivation["at"])
        out = []
        for entity in derivation["plans"]:
            status = sim.timeline.value_at(entity, "status", at)
            when = sim.timeline.value_at(entity, "when", at)
            what = sim.timeline.value_at(entity, "what", at)
            if status and status.value == "planned" and when and what:
                try:
                    due = datetime.fromisoformat(when.value)
                except ValueError:
                    continue
                if at < due <= at + timedelta(days=30):
                    out.append(what.value)
        return out
    if kind == "conflict":
        truth = sim.timeline.get(derivation["assertion_id"])
        return list(dict.fromkeys((derivation["wrong"], truth.value)))
    raise ValueError(f"unknown derivation kind: {kind}")
