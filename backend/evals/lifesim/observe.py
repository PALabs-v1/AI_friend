"""Render the oracle timeline and event log as a deterministic human text stream."""

from __future__ import annotations

import random
import re
from collections import defaultdict
from datetime import datetime, timedelta

from .events import Event
from .schema import Annotation, Claim, Turn
from .world import ATTRIBUTES

_ATTR_LABELS = {
    "name": "name",
    "birthday": "birthday",
    "hometown": "hometown",
    "home_city": "home",
    "neighbourhood": "neighbourhood",
    "employer": "job",
    "job_title": "job title",
    "university": "university",
    "degree": "degree",
    "drink": "usual order",
    "food": "favorite dish",
    "cuisine": "favorite cuisine",
    "music": "taste in music",
    "tv_show": "show",
    "book_genre": "books",
    "sport": "game",
    "weekend": "weekends",
    "hobby": "pastime",
    "morning_routine": "morning routine",
    "exercise": "workout",
    "vehicle": "ride",
    "phone": "mobile",
    "partner": "partner",
    "away_city": "current location",
    "lodging": "lodging",
    "health": "state of health",
    "house_guest": "guest",
    "relation": "relationship",
    "city": "location",
    "status": "status",
    "species": "kind of pet",
    "nickname": "nickname",
    "user_warmth": "how close we feel",
    "kind": "plan",
    "what": "task",
    "when": "date",
    "where": "place",
    "with": "companion",
    "topic": "view",
    "stance": "view",
}

_EVENT_FAMILY = {
    "meal": "trivial",
    "weather": "trivial",
    "watched": "trivial",
    "errand": "trivial",
    "pref_change": "update",
    "belief_change": "update",
    "job_change": "update",
    "move": "update",
    "neighbourhood_move": "update",
    "possession_change": "update",
    "person_move": "update",
    "person_job_change": "update",
    "robot_nickname": "update",
    "new_partner": "relationship",
    "breakup": "relationship",
    "person_new_partner": "relationship",
    "argument": "relationship",
    "apology": "relationship",
    "support": "relationship",
    "misunderstanding": "relationship",
    "cleared_up": "relationship",
    "death": "loss",
    "funeral": "loss",
    "pet_death": "loss",
    "user_birthday": "emotion",
    "person_birthday": "emotion",
    "achievement": "emotion",
    "failure": "emotion",
    "pet_adopted": "emotion",
    "trip_start": "temporary",
    "trip_end": "temporary",
    "illness": "temporary",
    "recovered": "temporary",
    "house_guest": "temporary",
    "robot_thanks": "robot",
    "robot_complaint": "robot",
    "robot_confide": "robot",
    "robot_affection": "robot",
    "robot_argument": "robot",
    "robot_misunderstanding": "robot",
    "robot_apology": "robot",
    "goal_start": "lifecycle",
    "goal_progress": "lifecycle",
    "goal_achieved": "lifecycle",
    "goal_abandoned": "lifecycle",
    "project_start": "lifecycle",
    "project_progress": "lifecycle",
    "project_achieved": "lifecycle",
    "project_abandoned": "lifecycle",
    "restaurant_visit": "episode",
    "cafe_visit": "episode",
    "meeting": "episode",
    "outing": "episode",
}


def _person(sim, raw: str | None) -> str:
    if raw is None:
        return ""
    if raw == "user":
        return "I"
    person = sim.world.people.get(raw)
    if person:
        return person.name
    pet = sim.world.pets.get(raw)
    if pet:
        return pet.name
    if raw.startswith(("person:", "pet:", "plan:", "goal:", "project:", "belief:")):
        return "someone"
    return raw


def _shown(sim, attr: str, value: str) -> str:
    if value is None:
        return ""
    if attr in ("partner", "with", "house_guest") or value.startswith(
        ("person:", "pet:")
    ):
        return _person(sim, value)
    return value


def _plan_np(what: str) -> str | None:
    """A plan description as a noun phrase, or None if it's a verb phrase.
    Plans mix both: "submit the tax form" vs "a hiking trip"."""
    if what.startswith("a friend's "):
        return "my " + what[2:]
    for article in ("a ", "an "):
        if what.startswith(article):
            return "the " + what[len(article) :]
    return None


def _plan_ref(what: str) -> str:
    """How a plan is referred to mid-sentence: "the hiking trip", "my plan to
    submit the tax form" -- never the bare verb phrase ("I moved cancel the
    gym trial")."""
    return _plan_np(what) or f"my plan to {what}"


def _aim_ref(kind: str, what: str) -> str:
    """Goals are verb phrases ("visit Japan" -> "my goal to visit Japan");
    projects are gerunds or noun phrases ("renovating the balcony", "a
    plant-tracking app") and read correctly on their own."""
    return f"my goal to {what}" if kind == "goal" else what


def _first(sim, entity: str, attribute: str) -> str | None:
    hist = sim.timeline.history(entity, attribute)
    return hist[0].value if hist else None


def _nonperson_line(sim, entity: str, attribute: str, value: str) -> str | None:
    """Plans, goals, projects and beliefs are not people: "someone's task is
    pick Sasha up from the airport" was what the person-shaped line produced
    for them (about 3.5% of all user speech)."""
    kind = entity.split(":", 1)[0]
    if kind == "plan":
        what = _first(sim, entity, "what") or "something"
        np = _plan_np(what)
        if attribute == "when":
            when = _pretty_when(value)
            return f"{np} is on {when}" if np else f"I’m going to {what} on {when}"
        if attribute in ("what", "kind"):
            return f"I have {what} coming up" if np else f"I need to {what}"
        if attribute == "with":
            who = _person(sim, value)
            return (
                f"I’m going to {np or what} with {who}"
                if np
                else f"I’m going to {what} with {who}"
            )
        if attribute == "where":
            return f"{np} is in {value}" if np else f"I’m going to {what} in {value}"
        if attribute == "status":
            return {
                "planned": f"{np} is still on" if np else f"I still plan to {what}",
                "confirmed": f"{np} is confirmed"
                if np
                else f"it’s confirmed that I’ll {what}",
                "cancelled": f"{np} is off"
                if np
                else f"I’m not going to {what} after all",
                "done": f"{np} happened" if np else f"I managed to {what}",
            }.get(value, f"{_plan_ref(what)} is {value}")
    if kind in ("goal", "project"):
        what = _first(sim, entity, "what") or "something"
        ref = _aim_ref(kind, what)
        if attribute == "what":
            return (
                f"one of my goals is to {what}"
                if kind == "goal"
                else f"one of my projects is {what}"
            )
        if attribute == "status":
            return {
                "active": f"I’m still working on {ref}",
                "achieved": f"I achieved {ref}"
                if kind == "goal"
                else f"I finished {ref}",
                "done": f"I finished {ref}",
                "abandoned": f"I gave up on {ref}",
            }.get(value, f"{ref} is {value}")
    if kind == "belief":
        topic = _first(sim, entity, "topic")
        if attribute == "stance" and topic:
            return f"my view on {topic} is that {value}"
        if attribute == "topic":
            return f"I have strong views on {value}"
    return None


def _slot_line(sim, entity: str, attribute: str, value: str) -> str:
    if entity != "user" and entity.split(":", 1)[0] in (
        "plan",
        "goal",
        "project",
        "belief",
    ):
        line = _nonperson_line(sim, entity, attribute, value)
        if line:
            return line
    label = _ATTR_LABELS.get(attribute, "that detail")
    if entity == "user":
        # No home_city special case needed: _ATTR_LABELS["home_city"] is
        # already "home", so the uniform "my {label}" reads as "my home".
        # A prior version also swapped the subject itself to "home" here,
        # producing "home home is Melbourne".
        subject = "my"
    else:
        subject = _person(sim, entity) + "’s"
    return f"{subject} {label} is {_shown(sim, attribute, value)}"


def _past_slot_line(sim, entity: str, attribute: str, value: str) -> str:
    """A superseded value, said as past. Replaces "I used to have {value} as
    my {label}", which ignored whose fact it was ("I used to have Bogota as
    my location" about a friend's city) and printed plan times as raw ISO."""
    if entity == "user" or entity in sim.world.people or entity in sim.world.pets:
        owner = "my" if entity == "user" else _person(sim, entity) + "’s"
        label = _ATTR_LABELS.get(attribute, "that detail")
        return f"{owner} {label} used to be {_shown(sim, attribute, value)}"
    if entity.startswith("plan:") and attribute == "when":
        what = _first(sim, entity, "what") or "something"
        np = _plan_np(what)
        when = _pretty_when(value)
        return (
            f"{np} was going to be on {when}"
            if np
            else f"I was going to {what} on {when}"
        )
    return "at one point, " + _slot_line(sim, entity, attribute, value)


def _event_line(sim, ev: Event) -> str:
    p = ev.payload
    name = p.get("name") or ""
    who = _person(sim, p.get("with")) or name or "a friend"
    kind = ev.kind
    if kind == "meal":
        return f"I had {p['food']} for {p['meal']}"
    if kind == "weather":
        return f"it was {p['weather']}"
    if kind == "watched":
        return f"I watched {p['show']}"
    if kind == "errand":
        return f"I {p['errand']}"
    if kind == "pref_change":
        attr = _ATTR_LABELS.get(p["attribute"], "that")
        return f"I switched my {attr} from {p['old']} to {p['new']}"
    if kind == "belief_change":
        return f"my view on {p['topic']} shifted from {p['old']} to {p['new']}"
    if kind == "job_change":
        return f"I changed jobs, from {p.get('old_title') or 'my old role'} at {p.get('old_employer') or 'my old workplace'} to {p['title']} at {p['employer']}"
    if kind == "move":
        return f"I moved from {p['old_city']} to {p['city']}"
    if kind == "neighbourhood_move":
        return f"I moved from {p['old']} to {p['new']}"
    if kind == "possession_change":
        return (
            f"I replaced my {_ATTR_LABELS.get(p['attribute'], 'item')} with {p['new']}"
        )
    if kind in ("person_move", "person_job_change"):
        # Neither event carries a "name" in its payload (events.py only puts
        # old/new there); the subject is the event's own entity.
        who_moved = _person(sim, ev.entities[0]) if ev.entities else "someone"
        verb = "moved" if kind == "person_move" else "changed employers"
        return f"{who_moved} {verb} from {p['old']} to {p['new']}"
    if kind == "new_partner":
        return f"I started seeing {name}"
    if kind == "breakup":
        return f"{name} and I broke up"
    if kind == "person_new_partner":
        partner_id = ev.entities[1] if len(ev.entities) > 1 else None
        employer = (
            sim.timeline.current(partner_id, "employer", ev.t) if partner_id else None
        )
        works = f", who works at {employer}" if employer else ""
        return f"{name} is seeing {p['partner_name']}{works}"
    if kind == "argument":
        return f"{name} and I argued about {p['about']}"
    if kind == "apology":
        return f"{name if p['by'] == 'them' else 'I'} apologized about {p['about']}"
    if kind == "support":
        return f"I {p['direction']} support from {name}"
    if kind == "misunderstanding":
        return f"{name} and I misunderstood each other"
    if kind == "cleared_up":
        return f"{name} and I cleared up the misunderstanding"
    if kind == "death":
        return f"{name}, my {p['relation']}, died"
    if kind == "funeral":
        return f"we held {name}’s funeral"
    if kind == "pet_death":
        return f"my {p['species']} {name} died"
    if kind == "user_birthday":
        return f"I turned {p['age']}"
    if kind == "person_birthday":
        return f"we celebrated {name}’s birthday"
    if kind == "achievement":
        return f"I {p['what']}"
    if kind == "failure":
        return f"I {p['what']}"
    if kind == "pet_adopted":
        return f"I adopted a {p['species']} named {name}"
    if kind in ("commitment_planned", "trip_planned", "celebration_planned"):
        when = _pretty_when(p["when"])
        companion = f" with {_person(sim, p['with'])}" if p.get("with") else ""
        where = f" in {p['where']}" if p.get("where") else ""
        hedge = "might " if p.get("tentative") else "will "
        if _plan_np(p["what"]):
            # "I will a friend's wedding" -- a noun phrase needs "have".
            hedge = "might have " if p.get("tentative") else "have "
        return f"I {hedge}{p['what']} on {when}{where}{companion}"
    if kind.endswith("_rescheduled"):
        return f"I moved {_plan_ref(p['what'])} from {_pretty_when(p['old_when'])} to {_pretty_when(p['when'])}"
    if kind.endswith("_confirmed"):
        np = _plan_np(p["what"])
        when = _pretty_when(p["when"])
        return (
            f"{np} is confirmed for {when}"
            if np
            else f"it’s confirmed: I’ll {p['what']} on {when}"
        )
    if kind.endswith("_cancelled"):
        return f"I cancelled {_plan_ref(p['what'])}"
    if kind.endswith("_done"):
        np = _plan_np(p["what"])
        return f"{np} happened" if np else f"I managed to {p['what']}"
    if kind == "trip_start":
        return f"I’m in {p['city']} for {p['purpose']} and staying at {p['lodging']}"
    if kind == "trip_end":
        return f"I’m back from {p['purpose']} in {p['city']}"
    if kind == "illness":
        return f"I have {p['what']} and expect it to last {p['days']} days"
    if kind == "recovered":
        return f"I’ve recovered from {p['what']}"
    if kind == "house_guest":
        return f"{name}, my {p['relation']}, is staying for {p['nights']} nights"
    if kind == "robot_thanks":
        return "I really appreciate your help"
    if kind == "robot_complaint":
        return "I’m frustrated with how that went"
    if kind == "robot_confide":
        return "I trust you with something personal"
    if kind == "robot_affection":
        return "I’m fond of you"
    if kind == "robot_argument":
        return "I’m angry with you"
    if kind == "robot_misunderstanding":
        return "I think we misunderstood each other"
    if kind == "robot_apology":
        return "I’m sorry about our argument"
    if kind == "robot_nickname":
        if p.get("old"):
            return f"I’m calling you {p['new']} now, not {p['old']}"
        return f"I’ve decided to call you {p['new']}"
    if kind.startswith(("goal_", "project_")):
        # Was f"I {action} {what}": "I start visit Japan", "I progress run a
        # half marathon".
        prefix = "goal" if kind.startswith("goal_") else "project"
        action = kind[len(prefix) + 1 :]
        what = p["what"]
        ref = _aim_ref(prefix, what)
        return {
            "start": f"I’ve set myself a new goal: to {what}"
            if prefix == "goal"
            else f"I’ve started a new project: {what}",
            "progress": f"I made some progress on {ref}",
            "achieved": f"I achieved {ref}"
            if prefix == "goal"
            else f"I finished {ref}",
            "abandoned": f"I gave up on {ref}",
        }.get(action, f"{ref} is {action}")
    if kind == "restaurant_visit":
        return f"I went to {p['restaurant']} with {who} for {p['occasion']} and had {p['dish']}"
    if kind == "cafe_visit":
        return f"I had {p['drink']} at {p['cafe']}" + (
            f" with {who}" if p.get("name") else ""
        )
    if kind == "meeting":
        return f"I met with {who} about {p['topic']}"
    if kind == "outing":
        return f"I went to {p['place']} with {who}"
    return "I had a lot on my mind"


def _pretty_when(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%A, %B %-d at %-I:%M %p")
    except (TypeError, ValueError):
        return str(raw)


def _spoken_forms(sim, value: str) -> tuple[str, ...]:
    if value.startswith("person:"):
        return (_person(sim, value),)
    return (value, _pretty_when(value))


def _event_claims(sim, ev: Event, line: str = "") -> list[Claim]:
    """Claims for what the event makes true, plus -- when ``line`` actually
    says it -- the value it replaced. "I switched my usual drink from mango
    lassi to masala chai" tells the listener both values; annotating only the
    new one made the oracle believe the old value was never said, which left
    almost no fact eligible for stale-trap or historical probes."""
    claims = []
    tl = sim.timeline
    for aid in ev.effects:
        a = tl.get(aid)
        claims.append(
            Claim(a.entity, a.attribute, a.value, True, ev.t, a.assertion_id, False)
        )
        prev = tl.value_at(
            a.entity, a.attribute, a.valid_from - timedelta(microseconds=1)
        )
        if (
            prev is not None
            and prev.value != a.value
            and line
            and any(
                form and re.search(rf"(?<!\w){re.escape(form)}(?!\w)", line)
                for form in _spoken_forms(sim, prev.value)
            )
        ):
            claims.append(
                Claim(
                    prev.entity,
                    prev.attribute,
                    prev.value,
                    True,
                    prev.valid_from,
                    prev.assertion_id,
                    False,
                )
            )
    return claims


def _wrong_value(sim, claim: Claim, rng: random.Random) -> str | None:
    if claim.attribute in ATTRIBUTES and ATTRIBUTES[claim.attribute].pool:
        options = [v for v in ATTRIBUTES[claim.attribute].pool if v != claim.value]
    elif claim.attribute == "when":
        try:
            options = [
                (datetime.fromisoformat(claim.value) + timedelta(days=d)).isoformat(
                    timespec="minutes"
                )
                for d in (-2, -1, 1, 2)
            ]
        except ValueError:
            return None
    elif claim.attribute in (
        "employer",
        "city",
        "job_title",
        "what",
        "food",
        "drink",
        "stance",
    ):
        pool = {
            "employer": "COMPANIES",
            "city": "CITIES",
            "job_title": "JOB_TITLES",
            "food": "FOODS",
            "drink": "DRINKS",
        }.get(claim.attribute)
        if pool:
            from . import vocab

            options = [v for v in getattr(vocab, pool) if v != claim.value]
        else:
            return None
    else:
        options = []
    return rng.choice(options) if options else None


def _tags(
    ev: Event | None,
    *,
    chatter=False,
    repeated=False,
    hedged=False,
    correction=False,
    misstatement=False,
    joke=False,
):
    tags = []
    if ev:
        mapping = {
            "trivial": "trivial_episodic",
            "change": "changing_fact",
            "temporary": "temporary_state",
            "plan": "commitment",
            "relationship": "relationship_event",
            "loss": "relationship_event",
            "emotional": "emotional_event",
            "goal": "changing_fact",
            "project": "changing_fact",
            "episode": "trivial_episodic",
            "robot": "relationship_event",
        }
        if ev.category in mapping:
            tags.append(mapping[ev.category])
        if ev.importance >= 0.7:
            tags.append("important")
    if chatter:
        tags.append("insignificant")
    if repeated:
        tags.append("repeated")
    if hedged:
        tags.append("uncertain")
    if correction:
        tags.append("correction")
    if misstatement:
        tags.extend(("contradiction_accidental",))
    if joke:
        tags.append("joke")
    return list(dict.fromkeys(tags))


def _pick_text(sim, fam: str, rng, avoid: set[str], **values):
    template = sim.bank.pick(fam, rng, avoid)
    avoid.add(template.id)
    return template.render(**values), template


_STUTTER_RX = re.compile(r"\b([A-Za-z][A-Za-z', ]{2,24} )\1", re.IGNORECASE)


def _stutters(text: str) -> bool:
    """True if `text` repeats a short phrase immediately -- the tell that a
    style-wrap's own opener collided with an identical discourse marker
    already present at the start of the line it wrapped (e.g. two
    independently-written banks both reaching for "Oh, and ...")."""
    return bool(_STUTTER_RX.search(text))


def render(sim) -> tuple[list[Turn], list[Annotation]]:
    rngs = {}
    avoid = defaultdict(set)
    turns = []
    annotations = []
    known = []
    loss_memory = []
    multi_fact_shared = set()
    pending = []
    event_i = 0
    events = sim.events
    seq = 0
    for si, session in enumerate(sim.sessions):
        rng = sim.rng("observe", session.session_id)
        rngs[session.session_id] = rng
        eligible = []
        while event_i < len(events) and events[event_i].t <= session.start:
            eligible.append(events[event_i])
            event_i += 1
        trivial_now = [item for item in eligible if item.category == "trivial"]
        pending.extend(eligible)
        # Routine daily episodes are considered at their next conversation and
        # dropped if not selected; retaining the whole life here is quadratic.
        pending = [
            item
            for item in pending
            if not isinstance(item, Event)
            or (
                item.category != "trivial"
                and session.start - item.t <= timedelta(days=30)
            )
        ]
        # Corrections are queued as (eligible_time, originating turn, claim, true value, event).
        slots = session.base_turns + 6
        selected = []
        # Important news gets priority, with seeded jitter to avoid fixed positions.
        candidates = [e for e in pending if isinstance(e, Event)] + trivial_now
        rng.shuffle(candidates)
        candidates.sort(
            key=lambda e: (e.importance + (0.15 * rng.random()), -e.t.timestamp()),
            reverse=True,
        )
        for e in candidates:
            age = sum(
                1 for s in sim.sessions[max(0, si - 2) : si + 1] if s.start >= e.t
            )
            important = e.importance >= 0.7 and age <= 3 and rng.random() < 0.92
            ordinary = e.importance >= 0.45 and rng.random() < 0.55
            episode = e.category == "episode" and rng.random() < 0.35
            trivial = e.category == "trivial" and rng.random() < 0.45
            if important or ordinary or episode or trivial:
                selected.append(e)
            if len(selected) >= min(slots, 6):
                break
        for e in selected:
            if e in pending:
                pending.remove(e)
        # A small number of start-state facts are disclosed, including genuine history.
        seed_facts = []
        if si < 10 and rng.random() < 0.32:
            eligible_assertions = [
                a
                for a in sim.timeline
                if a.valid_from <= session.start
                and a.entity in ("user", *sim.world.people.keys())
                and a.attribute
                in (
                    "home_city",
                    "hometown",
                    "university",
                    "degree",
                    "employer",
                    "drink",
                    "hobby",
                    "partner",
                    "city",
                )
            ]
            if eligible_assertions:
                a = rng.choice(eligible_assertions)
                seed_facts.append(a)
        items = [("event", e) for e in selected] + [("fact", a) for a in seed_facts]
        if si < 12 and len(multi_fact_shared) < 3:
            for person_id, person in sorted(sim.world.people.items()):
                if person_id in multi_fact_shared:
                    continue
                if person.relation not in (
                    "sister",
                    "brother",
                    "best friend",
                    "cousin",
                ):
                    continue
                partner = sim.timeline.value_at(person_id, "partner", session.start)
                if partner is None or not partner.value.startswith("person:"):
                    continue
                employer = sim.timeline.value_at(
                    partner.value, "employer", session.start
                )
                if employer is None:
                    continue
                items.append(("multi_fact", (person_id, partner, employer)))
                multi_fact_shared.add(person_id)
                break
        rng.shuffle(items)
        # Allocate turns for news, corrections, facts, then ordinary conversation.
        session_items = []
        for kind, obj in items[:slots]:
            session_items.append((kind, obj))
        # Deliver delayed deliberate corrections when their day has arrived.
        due = [x for x in pending if isinstance(x, tuple) and x[0] <= session.start]
        for correction in due[: max(0, slots - len(session_items))]:
            pending.remove(correction)
            session_items.append(("delayed_correction", correction))
        if (
            loss_memory
            and len(session_items) < slots
            and rng.random() < 0.35 * sim.persona.emotionality
        ):
            session_items.append(("loss_recall", rng.choice(loss_memory)))
        total = max(session.base_turns, len(session_items))
        total = min(slots, total)
        while len(session_items) < total:
            session_items.append(("filler", None))
        rng.shuffle(session_items)
        clock = session.start
        for idx, (kind, obj) in enumerate(session_items):
            if idx:
                clock += timedelta(seconds=rng.randint(20, 180))
            seq += 1
            tid = f"u{seq:07d}"
            ev = None
            claims = []
            tags = []
            intent = "small_talk"
            family = "small_talk"
            importance = 0.1
            valence = 0.0
            arousal = 0.1
            robot_val = None
            competence = 0
            correction_for = None
            misstated = False
            is_joke = False
            hedged = False
            obj_c = None
            if kind == "event":
                ev = obj
                line = _event_line(sim, ev)
                claims = _event_claims(sim, ev, line)
                if ev.category in ("emotional", "relationship", "loss"):
                    if ev.valence <= -0.7:
                        line = f"I’m heartbroken; {line}"
                    elif ev.valence <= -0.3:
                        line = f"I’m sad about this; {line}"
                    elif ev.valence >= 0.65:
                        line = f"I’m thrilled; {line}"
                    elif ev.valence >= 0.3:
                        line = f"I’m glad; {line}"
                importance = ev.importance
                valence = ev.valence
                arousal = ev.arousal
                family = _EVENT_FAMILY.get(ev.kind, "share")
                if ev.category == "plan":
                    intent = "share_plan"
                elif ev.toward_robot:
                    intent = "robot_feedback"
                    robot_val = ev.valence
                    competence = int(ev.payload.get("competence_evidence", 0))
                elif ev.category in ("emotional", "relationship", "loss"):
                    intent = "express_emotion" if ev.valence else "share_event"
                elif ev.kind in ("meal", "weather", "watched", "errand"):
                    intent = "share_event"
                elif ev.kind.startswith(("goal_", "project_")) or ev.kind in (
                    "trip_start",
                    "illness",
                    "house_guest",
                ):
                    intent = "share_update"
                else:
                    intent = (
                        "share_update" if ev.category == "change" else "share_event"
                    )
                uncertain_plan = ev.kind.endswith("_planned") and bool(
                    ev.payload.get("tentative")
                )
                hedged = uncertain_plan and rng.random() < min(
                    1.0, 0.25 + 0.65 * sim.persona.hedging
                )
                if hedged:
                    claims = [
                        Claim(
                            c.entity,
                            c.attribute,
                            c.value,
                            c.truthful,
                            c.about,
                            c.assertion_id,
                            True,
                        )
                        for c in claims
                    ]
                if hedged:
                    line = "maybe " + line
                # Misstatements alter only the surface and annotation, never the timeline.
                factual = [c for c in claims if c.attribute not in ("status",)]
                if factual and rng.random() < sim.persona.contradiction_rate:
                    original = rng.choice(factual)
                    wrong = _wrong_value(sim, original, rng)
                    if wrong:
                        misstated = True
                        intentional = rng.random() < sim.persona.deception_rate / max(
                            0.01, sim.persona.contradiction_rate
                        )
                        line = _slot_line(
                            sim, original.entity, original.attribute, wrong
                        )
                        claim = Claim(
                            original.entity,
                            original.attribute,
                            wrong,
                            False,
                            original.about,
                            original.assertion_id,
                            False,
                        )
                        claims = [claim]
                        family = "misstatement"
                        if intentional:
                            tags.append("misleading")
                            pending.append(
                                (
                                    ev.t + timedelta(days=rng.randint(2, 8)),
                                    tid,
                                    original,
                                    wrong,
                                    ev,
                                )
                            )
                        elif rng.random() < 0.7:
                            if original.attribute == "when":
                                # correction later in this same conversation, preserving the false-to-truth contrast.
                                obj_c = (tid, original, wrong, ev)
                            else:
                                obj_c = (tid, original, wrong, ev)
                        else:
                            obj_c = None
                    else:
                        obj_c = None
                else:
                    obj_c = None
                if kind != "event":
                    obj_c = None
                if ev.kind in ("death", "pet_death", "funeral"):
                    family = "loss"
                    importance = max(importance, 0.9)
                text, tpl = _pick_text(sim, family, rng, avoid[family], line=line)
                if ev.toward_robot:
                    tags.extend(_tags(ev))
                    tags.append("important") if ev.importance >= 0.7 else None
                else:
                    tags.extend(_tags(ev, hedged=hedged))
                if misstated:
                    tags.append(
                        "contradiction_intentional"
                        if intentional
                        else "contradiction_accidental"
                    )
                if hedged:
                    intent = "share_plan" if ev.category == "plan" else intent
                if misstated:
                    known.append((claims[0], tid, ev.t, ev.event_id, False))
                else:
                    known.extend((c, tid, ev.t, ev.event_id, True) for c in claims)
                event_ids = [ev.event_id]
                if ev.category == "loss":
                    tags.append("emotional_event")
                    loss_memory.append(ev)
                if obj_c:
                    if len(session_items) < slots:
                        session_items.insert(idx + 1, ("immediate_correction", obj_c))
                        total += 1
                    else:
                        pending.append(
                            (
                                clock + timedelta(days=1),
                                obj_c[0],
                                obj_c[1],
                                obj_c[2],
                                obj_c[3],
                            )
                        )
            elif kind == "loss_recall":
                ev = obj
                line = _event_line(sim, ev)
                text, tpl = _pick_text(sim, "loss", rng, avoid["loss"], line=line)
                event_ids = [ev.event_id]
                intent = "express_emotion"
                importance = ev.importance
                valence = ev.valence
                arousal = ev.arousal
                tags = _tags(ev)
                tags.append("emotional_event")
            elif kind == "fact":
                a = obj
                ev = None
                claims = [
                    Claim(
                        a.entity,
                        a.attribute,
                        a.value,
                        True,
                        a.valid_from,
                        a.assertion_id,
                        False,
                    )
                ]
                historical = a.valid_from < sim.start and a.attribute in (
                    "home_city",
                    "hometown",
                    "employer",
                    "university",
                    "degree",
                )
                line = _slot_line(sim, a.entity, a.attribute, a.value)
                if historical:
                    # Every "reminisce" template already frames its own past
                    # tense ("I was remembering...", "Back then,...", "Years
                    # ago,..."); prepending one here too duplicates whichever
                    # gets picked (reminisce#002 IS "Back then, {line}").
                    family = "reminisce"
                    intent = "reminisce"
                    tags.append("historical_fact")
                else:
                    family = "share"
                    intent = "share_fact"
                    tags.append("stable_fact")
                text, tpl = _pick_text(sim, family, rng, avoid[family], line=line)
                tags.extend(_tags(None))
                known.append((claims[0], tid, a.valid_from, None, True))
                event_ids = []
            elif kind == "multi_fact":
                person_id, partner, employer = obj
                relation = sim.world.people[person_id].relation
                line = (
                    f"My {relation} {_person(sim, person_id)} has a partner named "
                    f"{_person(sim, partner.value)}, who works at {employer.value}"
                )
                claims = [
                    Claim(
                        person_id,
                        "partner",
                        partner.value,
                        True,
                        partner.valid_from,
                        partner.assertion_id,
                    ),
                    Claim(
                        partner.value,
                        "employer",
                        employer.value,
                        True,
                        employer.valid_from,
                        employer.assertion_id,
                    ),
                ]
                text, tpl = _pick_text(sim, "share", rng, avoid["share"], line=line)
                family = "share"
                intent = "share_fact"
                tags = ["stable_fact"]
                event_ids = []
                known.extend((c, tid, c.about, None, True) for c in claims)
            elif kind in ("immediate_correction", "delayed_correction"):
                origin, claim, wrong, source = (
                    obj
                    if kind == "immediate_correction"
                    else (obj[1], obj[2], obj[3], obj[4])
                )
                # delayed queue tuple: (eligible, origin turn, correct claim, source event)
                true_value = claim.value
                line = _slot_line(sim, claim.entity, claim.attribute, true_value)
                if claim.attribute == "when":
                    wrong_display = _pretty_when(wrong)
                    true_display = _pretty_when(true_value)
                    text, tpl = _pick_text(
                        sim,
                        "correction_plan",
                        rng,
                        avoid["correction_plan"],
                        value=true_display,
                        wrong=wrong_display,
                    )
                else:
                    text, tpl = _pick_text(
                        sim, "correction", rng, avoid["correction"], line=line
                    )
                claims = [
                    Claim(
                        claim.entity,
                        claim.attribute,
                        true_value,
                        True,
                        claim.about,
                        claim.assertion_id,
                        False,
                    )
                ]
                family = tpl.family
                intent = "correction"
                tags = _tags(None, correction=True)
                correction_for = origin
                importance = source.importance if source else 0.4
                valence = 0.0
                arousal = 0.2
                event_ids = [source.event_id] if source else []
                known.append(
                    (
                        claims[0],
                        tid,
                        claim.about,
                        source.event_id if source else None,
                        True,
                    )
                )
            else:
                # Filler discourse uses the same bank, with humour and verbosity as measurable knobs.
                ev = None
                event_ids = []
                if known and rng.random() < sim.persona.forgetfulness * 0.22:
                    prior = rng.choice(known)
                    c = prior[0]
                    if (
                        c.truthful
                        and sim.timeline.get(c.assertion_id).valid_from <= clock
                    ):
                        assertion = sim.timeline.get(c.assertion_id)
                        if (
                            assertion.valid_to is not None
                            and assertion.valid_to <= clock
                        ):
                            line = _past_slot_line(sim, c.entity, c.attribute, c.value)
                        else:
                            line = _slot_line(sim, c.entity, c.attribute, c.value)
                        text, tpl = _pick_text(
                            sim, "repeat", rng, avoid["repeat"], line=line
                        )
                        claims = [
                            Claim(
                                c.entity,
                                c.attribute,
                                c.value,
                                True,
                                c.about,
                                c.assertion_id,
                                False,
                            )
                        ]
                        family = "repeat"
                        intent = "share_fact"
                        tags = _tags(None, repeated=True)
                        importance = 0.2
                        known.append((claims[0], tid, c.about, prior[3], True))
                    else:
                        kind = "filler"
                if not claims:
                    if rng.random() < sim.persona.humor * 0.14:
                        line = "I’m quitting to become a lighthouse keeper"
                        text, tpl = _pick_text(
                            sim, "joke", rng, avoid["joke"], line=line
                        )
                        family = "joke"
                        intent = "joke"
                        tags = _tags(None, joke=True)
                        is_joke = True
                    elif idx == 0 and si % 4 == 0:
                        text, tpl = _pick_text(sim, "greeting", rng, avoid["greeting"])
                        family = "greeting"
                        intent = "greeting"
                        tags = []
                    elif rng.random() < 0.28:
                        text, tpl = _pick_text(sim, "question", rng, avoid["question"])
                        family = "question"
                        intent = "question"
                        tags = []
                    else:
                        line = "I’ve had a full day and wanted a quiet minute"
                        text, tpl = _pick_text(
                            sim, "small_talk", rng, avoid["small_talk"], line=line
                        )
                        family = "small_talk"
                        intent = "small_talk"
                        tags = ["insignificant"]
            # Both knobs below scale with verbosity but never reach 1.0: a
            # verbose persona still says plenty of plain, unadorned things.
            # An earlier version applied both unconditionally once verbosity
            # crossed a threshold, gluing a generic opener and trailer onto
            # every single turn regardless of content -- including one-line
            # weather remarks -- which read as robotic and incoherent.
            # At most one tail, never a chain: each tail-bank entry is written
            # to follow the statement directly (some start with "and"), so
            # two in a row -- or the same one twice -- reads as a stutter.
            tail_p = 0.5 * sim.persona.verbosity
            if rng.random() < tail_p:
                tail, _tpl_tail = _pick_text(
                    sim, "verbosity_tail", rng, avoid["verbosity_tail"]
                )
                text += tail
            style_family = f"style_{sim.persona.style}"
            wrap_p = min(0.6, 0.1 + 0.5 * sim.persona.verbosity)
            if rng.random() < wrap_p:
                wrapped, wrapped_tpl = _pick_text(
                    sim, style_family, rng, avoid[style_family], line=text
                )
                # A style opener can coincidentally repeat a discourse marker
                # the line already opens with ("Oh, and" + "Oh, and ..."):
                # discard the wrap rather than ship a stutter.
                if not _stutters(wrapped):
                    text, tpl = wrapped, wrapped_tpl
            if kind == "event" and ev.toward_robot:
                robot_val = ev.valence
                competence = int(ev.payload.get("competence_evidence", 0))
            turns.append(Turn(tid, session.session_id, clock, text))
            annotations.append(
                Annotation(
                    tid,
                    intent,
                    tags,
                    event_ids,
                    claims,
                    importance,
                    valence,
                    arousal,
                    robot_val,
                    competence,
                    misstated,
                    is_joke,
                    correction_for,
                    tpl.family,
                    tpl.id,
                )
            )
    # Every claim remains tied to the event time or the historical assertion time.
    return turns, annotations
