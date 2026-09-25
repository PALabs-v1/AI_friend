"""Truth-derived questions and checkpoint answers for the life-simulation stream."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .schema import Annotation, Probe, ProbeAnswer, Turn

_LABELS = {
    "home_city": "home",
    "hometown": "hometown",
    "employer": "workplace",
    "job_title": "role",
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
    "city": "location",
    "relation": "relationship",
    "stance": "view",
    "when": "date",
}


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


def _date(value: str | datetime) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return str(value)
    return dt.strftime("%B %-d")


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
        if matches and entity in ("user", *sim.world.people.keys()) and attr in _LABELS:
            current.append((a, matches, entity, attr))
    rng.shuffle(current)
    for a, matches, entity, attr in current[:cap]:
        values = [_name(sim, a.value) if a.value.startswith("person:") else a.value]
        support = [r[0].turn_id for r in matches]
        add(
            "current",
            {"topic": _LABELS[attr]},
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
            if a.valid_to is not None and a.valid_from < at:
                historical.append((a, told, entity, attr))
            cur = tl.value_at(entity, attr, at)
            if (
                cur
                and cur.assertion_id != a.assertion_id
                and a.value != cur.value
                and attr in _LABELS
            ):
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
    for a, rows, entity, attr in historical[:cap]:
        val = _name(sim, a.value) if a.value.startswith("person:") else a.value
        add(
            "historical",
            # Not "earlier detail": one template is "the earlier {topic}?",
            # and that fallback would double up on its own framing word.
            {"topic": _LABELS.get(attr, "that detail")},
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
        curval = _name(sim, cur.value) if cur.value.startswith("person:") else cur.value
        add(
            "stale_trap",
            {"topic": _LABELS[attr]},
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
        # A solo cafe visit or a trip with nobody named carries no companion
        # at all; asking "involving {person}" against an empty name renders
        # a broken question, so only pick a sibling that actually has one.
        picked = next(
            (i for i, (ev, _) in enumerate(told) if ev.payload.get("name")), None
        )
        if picked is None:
            continue
        target, rows = told[picked]
        others = told[:picked] + told[picked + 1 :]
        p = target.payload
        person = p["name"]
        key = {
            "restaurant": "restaurant",
            "cafe": "cafe",
            "meeting": "topic",
            "outing": "place",
            "trip": "city",
        }[family]
        value = p.get(key)
        if not value:
            continue
        answer = [value]
        add(
            "interference",
            {"person": person, "date": _date(target.t)},
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
        when = tl.value_at(pid, "when", at)
        if when is None:
            continue
        rows = claims.get((pid, "when", when.assertion_id), [])
        rows = [r for r in rows if r[2].truthful]
        if rows:
            temporal.append((ev, when, rows))
    rng.shuffle(temporal)
    for ev, when, rows in temporal[:cap]:
        add(
            "temporal",
            {},
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
        if ev.category != "trivial" or ev.t >= at:
            continue
        rows = event_turns.get(ev.event_id, [])
        if rows:
            trivial.append((ev, rows, rows[-1][0].t))
    recent = [x for x in trivial if at - x[2] <= timedelta(days=3)]
    old = [x for x in trivial if at - x[2] > timedelta(days=60)]
    for category, pool in (("trivia_recent", recent), ("trivia_old", old)):
        rng.shuffle(pool)
        for ev, rows, _mention_t in pool[:cap]:
            key = next(iter(ev.payload))
            add(
                category,
                {},
                "forgettable" if category == "trivia_old" else "answer",
                [str(ev.payload[key])],
                [r[0].turn_id for r in rows],
                {"kind": "event_payload", "event_id": ev.event_id, "key": key},
            )

    # Abstention: true assertion exists, but no truthful mention has occurred by the checkpoint.
    untold = []
    told_ids = {
        key[2]
        for key, rows in claims.items()
        if key[2] and any(r[2].truthful for r in rows)
    }
    for a in tl:
        if a.valid_from >= at or a.assertion_id in told_ids:
            continue
        if (
            a.entity not in ("user", *sim.world.people.keys())
            or a.attribute not in _LABELS
        ):
            continue
        untold.append(a)
    rng.shuffle(untold)
    for a in untold[:cap]:
        add(
            "unanswerable",
            {},
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
        rows = event_turns.get(ev.event_id, [])
        if rows:
            defining.append((ev, rows))
    rng.shuffle(defining)
    for ev, rows in defining[:cap]:
        val = (
            ev.payload.get("what")
            or ev.payload.get("name")
            or ev.kind.replace("_", " ")
        )
        add(
            "relationship_defining",
            # A few templates in this family use {person} decoratively (most
            # don't); an achievement/failure/user_birthday event has no named
            # companion, and "" would render as "... about ?" if one of those
            # templates gets picked, so fall back to a value that still reads.
            {"person": ev.payload.get("name") or "myself"},
            "answer",
            [str(val)],
            [r[0].turn_id for r in rows],
            {
                "kind": "event_payload",
                "event_id": ev.event_id,
                "key": "what"
                if "what" in ev.payload
                else "name"
                if "name" in ev.payload
                else "kind",
            },
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
    rng.shuffle(open_plans)
    if open_plans:
        chosen = open_plans[:cap]
        for entity, what, when, rows in chosen:
            support = [r[0].turn_id for r in rows]
            add(
                "commitment_due",
                {},
                "answer",
                [what.value],
                support,
                {"kind": "commitments", "at": at.isoformat(), "plans": [entity]},
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
        tr, _, tc = truthful[0]
        key = (ann.turn_id, tc.assertion_id)
        if key in seen_conflicts:
            continue
        seen_conflicts.add(key)
        if len(candidates_out["contradiction_surface"]) >= cap:
            break
        add(
            "contradiction_surface",
            {},
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
