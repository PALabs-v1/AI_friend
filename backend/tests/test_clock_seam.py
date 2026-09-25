"""Gate tests for the injectable clock seam (`app/clock.py`, Phase 6 BrainBench).

Two things must both hold: production code must observe exactly the same
wall-clock behavior it always has (nobody calls `use_clock` in a request
path), and a caller that DOES enter `use_clock` must see every `clock.time()`
/ `clock.now()` read in that context resolve against the simulated clock,
including inside code the caller doesn't own (agent_state.py's hormone
decay, memory_store.py's search calls, etc.) via the ContextVar seam.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import clock


def test_default_clock_is_wall_time():
    before = clock.time()
    real = __import__("time").time()
    after = clock.time()
    assert before <= real <= after or abs(real - before) < 1.0


def test_manual_clock_starts_at_given_instant():
    start = datetime(2030, 1, 1, 12, 0, 0)
    c = clock.ManualClock(start)
    assert c.now() == start
    assert c.time() == start.timestamp()


def test_manual_clock_defaults_to_wall_time_if_unset():
    c = clock.ManualClock()
    assert abs(c.time() - __import__("time").time()) < 5.0


def test_manual_clock_advance_moves_forward():
    c = clock.ManualClock(datetime(2030, 1, 1))
    c.advance(3600)
    assert c.now() == datetime(2030, 1, 1, 1, 0, 0)


def test_manual_clock_advance_rejects_negative():
    c = clock.ManualClock(datetime(2030, 1, 1))
    with pytest.raises(ValueError):
        c.advance(-1)


def test_manual_clock_set_rejects_going_backwards():
    c = clock.ManualClock(datetime(2030, 1, 1))
    c.set(datetime(2030, 1, 2))
    with pytest.raises(ValueError):
        c.set(datetime(2030, 1, 1))


def test_manual_clock_now_with_naive_instant_labels_tz_without_shifting():
    c = clock.ManualClock(datetime(2030, 6, 15, 8, 0, 0))
    aware = c.now(UTC)
    assert aware.tzinfo is UTC
    assert aware.hour == 8  # not shifted by the host's local offset


def test_manual_clock_now_with_aware_instant_converts():
    start = datetime(2030, 6, 15, 8, 0, 0, tzinfo=UTC)
    c = clock.ManualClock(start)
    same_instant = c.now(UTC)
    assert same_instant == start


def test_use_clock_overrides_module_level_reads():
    sim = clock.ManualClock(datetime(2031, 3, 1))
    assert clock.now() != sim.now()
    with clock.use_clock(sim):
        assert clock.now() == sim.now()
        assert clock.time() == sim.time()
    # Context exits: back to wall time, not stuck on the simulated instant.
    assert clock.now() != sim.now()


def test_use_clock_is_reentrant_and_restores_prior_clock():
    outer = clock.ManualClock(datetime(2031, 1, 1))
    inner = clock.ManualClock(datetime(2032, 1, 1))
    with clock.use_clock(outer):
        assert clock.now() == outer.now()
        with clock.use_clock(inner):
            assert clock.now() == inner.now()
        assert clock.now() == outer.now()
    assert clock.now() != outer.now()


def test_use_clock_restores_on_exception():
    sim = clock.ManualClock(datetime(2031, 1, 1))
    with pytest.raises(RuntimeError):
        with clock.use_clock(sim):
            raise RuntimeError("boom")
    assert clock.now() != sim.now()


def test_agent_state_hormone_decay_reads_the_simulated_clock():
    """The actual seam test: a production dataclass's own method, never
    touched to take a clock parameter, still resolves against `use_clock`.
    """
    from app.state.agent_state import AgentState

    sim = clock.ManualClock(datetime(2030, 1, 1))
    with clock.use_clock(sim):
        state = AgentState()
        state.release_cortisol(0.5)
        peak_at = state.cortisol_phasic_at
        assert peak_at == sim.time()

        sim.advance(state.cortisol_halflife_s)
        decayed = state.cortisol_phasic
        # Half a half-life has elapsed at the simulated clock, not real time.
        assert decayed == pytest.approx(state.cortisol_phasic_peak * 0.5, rel=1e-6)


def test_agent_state_hormone_decay_does_not_advance_with_real_wall_time():
    """Without entering `use_clock`, a `ManualClock` sitting unused elsewhere
    must not affect production reads — the ContextVar default is real time.
    """
    from app.state.agent_state import AgentState

    state = AgentState()
    state.release_cortisol(0.5)
    assert abs(state.cortisol_phasic_at - __import__("time").time()) < 5.0


def test_no_bare_wall_clock_calls_remain_in_seamed_modules():
    """Regression guard for the specific files this migration touched: a
    future edit that reintroduces `time.time()`/`datetime.now()` there
    silently breaks BrainBench's ability to simulate years, with no other
    signal until a simulated run mysteriously reads real wall-clock time.
    """
    import ast
    import inspect

    modules = [
        "app.state.agent_state",
        "app.state.person_model",
        "app.state.memory_store",
        "app.cognitive.background_scheduler",
        "app.cognitive.core",
        "app.agents.subconscious_agent",
    ]
    for modname in modules:
        mod = __import__(modname, fromlist=["_"])
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr == "time" and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "time":
                    raise AssertionError(
                        f"{modname}:{node.lineno} calls time.time() directly, "
                        "bypassing app.clock"
                    )
            if node.func.attr == "now" and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "datetime":
                    raise AssertionError(
                        f"{modname}:{node.lineno} calls datetime.now() directly, "
                        "bypassing app.clock"
                    )
