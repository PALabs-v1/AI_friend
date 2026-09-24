"""Gate tests for lifesim's truth layer: timeline, world, events, schedule, splits, banks.

Deterministic and model-free. The multi-year cases are kept to a few seeds so
the file stays well under the gate budget.
"""

from __future__ import annotations

import ast
import itertools
import json
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from evals.lifesim import banks
from evals.lifesim.events import simulate
from evals.lifesim.generate import parse_horizon
from evals.lifesim.personas import ARCHETYPES, PANEL, draw_random, resolve
from evals.lifesim.rng import stream
from evals.lifesim.schedule import build_sessions
from evals.lifesim.splits import SplitError, authorize, split_of
from evals.lifesim.timeline import Timeline, TimelineError
from evals.lifesim.world import build_world

T0 = datetime(2026, 1, 1, 9)
LIFESIM = Path(__file__).resolve().parents[1] / "evals" / "lifesim"


def _run(seed: int, archetype: str, days: int):
    persona = resolve(archetype, stream(seed, "persona"))
    world = build_world(seed, persona)
    end = world.start + timedelta(days=days)
    events = simulate(world, end)
    return world, events, end


# ---- timeline ---------------------------------------------------------------


def test_supersession_keeps_history_and_projects_by_time():
    tl = Timeline()
    a = tl.assert_(
        "user", "drink", "cappuccino", T0, kind="changing", source_event="e1"
    )
    b = tl.assert_(
        "user",
        "drink",
        "black coffee",
        T0 + timedelta(days=500),
        kind="changing",
        source_event="e2",
    )
    assert b.supersedes == a.assertion_id
    assert a.valid_to == b.valid_from
    assert tl.current("user", "drink", T0 + timedelta(days=10)) == "cappuccino"
    assert tl.current("user", "drink", T0 + timedelta(days=600)) == "black coffee"
    assert [x.value for x in tl.history("user", "drink")] == [
        "cappuccino",
        "black coffee",
    ]
    assert tl.current("user", "drink", T0 - timedelta(days=1)) is None
    tl.validate()


def test_same_value_is_not_a_change_but_a_confirmation_is():
    tl = Timeline()
    a = tl.assert_(
        "plan:p001",
        "when",
        "2026-02-01T10:00",
        T0,
        kind="commitment",
        source_event=None,
        certainty=0.5,
    )
    assert (
        tl.assert_(
            "plan:p001",
            "when",
            "2026-02-01T10:00",
            T0 + timedelta(days=1),
            kind="commitment",
            source_event=None,
            certainty=0.5,
        )
        is a
    )
    c = tl.assert_(
        "plan:p001",
        "when",
        "2026-02-01T10:00",
        T0 + timedelta(days=2),
        kind="commitment",
        source_event=None,
        certainty=1.0,
    )
    assert c is not a and c.supersedes == a.assertion_id and c.certainty == 1.0
    tl.validate()


def test_history_cannot_be_rewritten_backwards():
    tl = Timeline()
    tl.assert_("user", "drink", "cappuccino", T0, kind="changing", source_event=None)
    assert not tl.can_assert("user", "drink", T0)
    with pytest.raises(TimelineError):
        tl.assert_(
            "user",
            "drink",
            "tea",
            T0 - timedelta(days=1),
            kind="changing",
            source_event=None,
        )


def test_temporary_state_ends_without_successor():
    tl = Timeline()
    tl.assert_(
        "user",
        "away_city",
        "Lisbon",
        T0,
        kind="temporary",
        source_event=None,
        until=T0 + timedelta(days=4),
    )
    assert tl.current("user", "away_city", T0 + timedelta(days=3)) == "Lisbon"
    assert tl.current("user", "away_city", T0 + timedelta(days=4)) is None
    tl.validate()


def test_validate_catches_overlap():
    tl = Timeline()
    a = tl.assert_(
        "user", "drink", "cappuccino", T0, kind="changing", source_event=None
    )
    tl.assert_(
        "user",
        "drink",
        "tea",
        T0 + timedelta(days=5),
        kind="changing",
        source_event=None,
    )
    a.valid_to = T0 + timedelta(days=9)  # corrupt: now overlaps its successor
    with pytest.raises(TimelineError):
        tl.validate()


# ---- world + events ---------------------------------------------------------


@pytest.mark.parametrize("archetype", PANEL)
def test_every_archetype_simulates_three_years_with_valid_timeline(archetype):
    world, events, end = _run(1000 + PANEL.index(archetype), archetype, 3 * 365)
    world.timeline.validate()
    assert events == sorted(events, key=lambda e: (e.t, e.event_id))
    assert all(world.start <= e.t < end for e in events)
    for e in events:
        for aid in e.effects:
            a = world.timeline.get(aid)
            assert a.source_event == e.event_id, (e.kind, aid)


def test_same_seed_is_byte_identical_and_different_seeds_differ():
    def dump(seed):
        world, events, _ = _run(seed, "socialite", 400)
        return json.dumps([e.to_json() for e in events], sort_keys=True) + json.dumps(
            [a.to_json() for a in world.timeline], sort_keys=True
        )

    assert dump(1001) == dump(1001)
    assert dump(1001) != dump(1002)


def test_longer_horizon_extends_rather_than_reshuffles_a_shorter_one():
    """Prefix stability: the first year of a 3-year life is the 1-year life."""
    w1, e1, end1 = _run(1003, "busy_parent", 365)
    w3, e3, _ = _run(1003, "busy_parent", 3 * 365)
    assert [e.to_json() for e in e1] == [e.to_json() for e in e3 if e.t < end1]
    key = lambda a: (
        a.assertion_id,
        a.entity,
        a.attribute,
        a.value,
        a.valid_from,
        a.certainty,
    )
    assert [key(a) for a in w1.timeline] == [
        key(a) for a in w3.timeline if a.valid_from < end1
    ]


def test_history_before_start_exists_for_historical_questions():
    world, _, _ = _run(1004, "steady_professional", 30)
    cities = world.timeline.history("user", "home_city")
    assert len(cities) >= 2 and cities[0].valid_from < world.start
    assert world.timeline.history("user", "university")


def test_confusable_names_appear_in_large_networks():
    from evals.lifesim.world import CONFUSABLE

    hits = 0
    for seed in range(1000, 1010):
        world = build_world(seed, ARCHETYPES["socialite"])
        names = {p.name for p in world.people.values()}
        hits += any(CONFUSABLE.get(n) in names for n in names)
    assert hits >= 3


def test_ten_year_truth_simulation_is_fast():
    t = time.perf_counter()
    _world, events, _ = _run(1005, "socialite", 3653)
    assert time.perf_counter() - t < 10
    assert len(events) > 10_000


# ---- schedule ---------------------------------------------------------------


def _sessions(seed, archetype, days):
    world, _, end = _run(seed, archetype, days)
    return world, build_sessions(seed, world.persona, world.timeline, world.start, end)


def test_no_sessions_while_the_user_is_away():
    world, sessions = _sessions(1006, "traveling_consultant", 2 * 365)
    assert sessions
    for s in sessions:
        assert world.timeline.current("user", "away_city", s.start) is None


def test_gaps_are_irregular_and_trips_make_long_silences():
    world, sessions = _sessions(1007, "steady_professional", 3 * 365)
    gaps = [
        (b.start - a.start).total_seconds() for a, b in itertools.pairwise(sessions)
    ]
    assert statistics.pstdev(gaps) / statistics.mean(gaps) > 1.0
    long = [
        (a, b)
        for a, b in itertools.pairwise(sessions)
        if b.start - a.start >= timedelta(days=5)
    ]
    assert long
    caused = sum(
        any(
            world.timeline.current("user", "away_city", a.start + timedelta(hours=h))
            for h in range(0, int((b.start - a.start).total_seconds() // 3600), 6)
        )
        for a, b in long
    )
    # A habitual, fairly frequent user: long silences should be trips, not RNG.
    assert caused >= len(long) * 0.75


def test_interaction_frequency_moves_session_rate():
    _, busy = _sessions(1008, "socialite", 365)
    _, quiet = _sessions(1008, "private_minimalist", 365)
    assert len(busy) > 3 * len(quiet)


def test_sessions_cluster_in_waking_hours():
    _, sessions = _sessions(1009, "steady_professional", 365)
    small_hours = sum(1 for s in sessions if 2 <= s.start.hour < 5)
    assert small_hours / len(sessions) < 0.05


# ---- personas, splits, horizons -------------------------------------------


def test_panel_spans_every_parameter():
    assert len(PANEL) == 12
    for field_name in (
        "verbosity",
        "forgetfulness",
        "contradiction_rate",
        "emotionality",
        "network_size",
        "lifestyle_complexity",
        "volatility",
        "interaction_frequency",
    ):
        vals = [getattr(p, field_name) for p in ARCHETYPES.values()]
        assert max(vals) > 2 * min(vals) + 1e-9, field_name
    assert len({p.style for p in ARCHETYPES.values()}) >= 5


def test_random_persona_is_seeded():
    assert draw_random(stream(5, "persona")) == draw_random(stream(5, "persona"))
    assert draw_random(stream(5, "persona")) != draw_random(stream(6, "persona"))


def test_splits_are_disjoint_and_heldout_needs_final_run(tmp_path):
    assert (
        split_of(1000) == "dev"
        and split_of(2199) == "tune"
        and split_of(3050) == "validation"
    )
    with pytest.raises(SplitError):
        split_of(42)
    log = tmp_path / "HELDOUT_LOG.md"
    with pytest.raises(SplitError):
        authorize(9000, log_path=log)
    assert not log.exists()
    assert authorize(9000, final_run=True, log_path=log, note="test") == "heldout"
    assert "seed=9000 test" in log.read_text()


@pytest.mark.parametrize(
    ("label", "days", "turns"),
    [("100t", None, 100), ("1w", 7, None), ("6m", 183, None), ("10y", 3652, None)],
)
def test_horizon_parsing(label, days, turns):
    h = parse_horizon(label)
    assert (h.days, h.turns) == (days, turns)


def test_bad_horizon_rejected():
    with pytest.raises(ValueError):
        parse_horizon("forever")


# ---- banks ------------------------------------------------------------------


def _write_bank(d: Path, text="I switched from {old} to {new}."):
    (d / "utterances.json").write_text(
        json.dumps(
            {
                "families": {
                    "pref_change": {
                        "placeholders": ["old", "new"],
                        "templates": [{"id": "pref_change#000", "text": text}],
                    }
                }
            }
        )
    )


def test_bank_freeze_verify_and_refuse_on_tamper(tmp_path):
    _write_bank(tmp_path)
    with pytest.raises(banks.BankIntegrityError):
        banks.load(tmp_path)  # never frozen
    banks.freeze(tmp_path)
    bank = banks.load(tmp_path)
    assert (
        bank.pick("pref_change", stream(1, "x")).render(old="tea", new="coffee")
        == "I switched from tea to coffee."
    )
    _write_bank(tmp_path, text="Now it's {new}, not {old}.")
    with pytest.raises(banks.BankIntegrityError, match="changed since it was frozen"):
        banks.load(tmp_path)


def test_bank_rejects_undeclared_placeholders(tmp_path):
    _write_bank(tmp_path, text="I switched to {new} because of {reason}.")
    with pytest.raises(banks.BankIntegrityError, match="undeclared"):
        banks.freeze(tmp_path)


# ---- purity -----------------------------------------------------------------


def test_lifesim_never_imports_the_brain():
    """R13: a pure generator cannot leak architecture knowledge into the benchmark."""
    for path in LIFESIM.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] if node.level == 0 else []
            else:
                continue
            for name in names:
                assert not name.startswith(
                    ("app", "evals.cognitive", "evals.brainbench")
                ), f"{path.name} imports {name}"
