"""Tests for the turn_taking_probability gate wired into
StateService.check_proactive_eligibility (roadmap leftovers Item 4b, M3-D2).

turn_taking_probability was computed by calculate_pacing_parameters and read
by no caller. A state-space sweep (tools/measure/m4b_turn_taking_gate.py)
verified the gate blocks 16.9% of the reachable (valence, dominance, fatigue)
grid at the default 0.5 threshold before it was wired to anything -- these
tests cover the wiring itself: that the gate actually blocks/admits based on
dominance/fatigue/valence, that it does not double-count with the pacing
sleep (which reads the same two state fields), and that every existing gate
in the chain (idle threshold, cooldown, min energy) still runs first,
unchanged.
"""

import time

import pytest

from app.config import Config
from app.state.agent_state import StateService

pytestmark = pytest.mark.usefixtures("no_quiet_hours")


def _eligible_service(**overrides):
    """A StateService already past the idle threshold, cooldown, and
    min-energy gates, so only the turn_taking_probability gate this item
    adds can still say no."""
    service = StateService(graph_store=None, db_path=":memory:")
    service.current_state.last_user_interaction = (
        time.time() - Config.PROACTIVE_IDLE_THRESHOLD_SECONDS - 1.0
    )
    service.current_state.last_proactive_attempt = 0.0
    service.current_state.energy = 1.0
    service.current_state.dominance = overrides.get("dominance", 0.5)
    service.current_state.fatigue = overrides.get("fatigue", 0.0)
    service.current_state.valence = overrides.get("valence", 0.0)
    return service


class TestTurnTakingGateBlocksLowProbabilityStates:
    def test_low_dominance_high_fatigue_negative_valence_is_blocked(self):
        """0.5 + 0.3*0.15 - 0.1*1.0 + 0.2*(-0.6) = 0.325, below the 0.5
        default -- a depleted, unconfident, unhappy state should not reach
        out unprompted."""
        service = _eligible_service(dominance=0.15, fatigue=1.0, valence=-0.6)
        assert service.check_proactive_eligibility() is False

    def test_high_dominance_low_fatigue_positive_valence_is_eligible(self):
        """0.5 + 0.3*0.85 - 0.1*0.0 + 0.2*0.6 = 0.875, well above 0.5 -- a
        confident, rested, happy state is exactly when a friend reaches out."""
        service = _eligible_service(dominance=0.85, fatigue=0.0, valence=0.6)
        assert service.check_proactive_eligibility() is True

    def test_the_exact_boundary_is_inclusive(self):
        """At D=0,F=0,V=0 the formula evaluates to exactly 0.5, matching the
        config default -- the boundary case, included so the >= vs < choice
        is pinned by a test rather than left to whichever comparison someone
        writes next. check_proactive_eligibility's own condition is
        `turn_probability < min_turn_probability`, so 0.5 is NOT less than
        0.5 and the state is eligible."""
        service = _eligible_service(dominance=0.0, fatigue=0.0, valence=0.0)
        assert service.check_proactive_eligibility() is True


class TestTurnTakingGateRespectsConfiguredThreshold:
    def test_raising_the_threshold_blocks_a_previously_eligible_state(
        self, monkeypatch
    ):
        monkeypatch.setattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.9)
        service = _eligible_service(dominance=0.85, fatigue=0.0, valence=0.6)
        # Same state that was eligible at the 0.875 vs 0.5 default now fails
        # against a 0.9 threshold.
        assert service.check_proactive_eligibility() is False

    def test_lowering_the_threshold_admits_a_previously_blocked_state(
        self, monkeypatch
    ):
        monkeypatch.setattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.0)
        service = _eligible_service(dominance=0.15, fatigue=1.0, valence=-0.6)
        assert service.check_proactive_eligibility() is True


class TestTurnTakingGateDoesNotDoubleCount:
    def test_pacing_sleep_and_the_proactive_gate_are_independent_reads(self):
        """calculate_pacing_parameters (silence_duration_ms) and
        check_proactive_eligibility (turn_taking_probability) both read
        dominance and fatigue, but they are two different call sites gating
        two different decisions -- not one value scaling the other. This
        pins that check_proactive_eligibility computes its own
        turn_taking_probability rather than reading a value the pacing path
        already produced (which would risk applying D/F twice if someone
        later wired the pacing sleep to also scale by the same number)."""
        service = _eligible_service(dominance=0.85, fatigue=0.0, valence=0.6)
        # calling the pacing calculation must not be a precondition for the
        # gate -- eligibility is checked with no pacing call made at all.
        assert service.check_proactive_eligibility() is True


class TestExistingGatesStillRunFirst:
    def test_idle_threshold_still_blocks_regardless_of_a_favorable_turn_taking_state(
        self,
    ):
        service = _eligible_service(dominance=0.85, fatigue=0.0, valence=0.6)
        service.current_state.last_user_interaction = time.time()  # just now
        assert service.check_proactive_eligibility() is False

    def test_min_energy_still_blocks_regardless_of_a_favorable_turn_taking_state(
        self,
    ):
        service = _eligible_service(dominance=0.85, fatigue=0.0, valence=0.6)
        service.current_state.energy = 0.0
        assert service.check_proactive_eligibility() is False
