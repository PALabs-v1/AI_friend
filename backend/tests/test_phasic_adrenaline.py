"""
Phasic adrenaline — a startle/interruption/shock response with no tonic term.

Bucket 11 (voice remediation Phase 3, item 2). Unlike dopamine/cortisol, which
are both derived continuously from valence and arousal and then get a phasic
burst layered on top, adrenaline has no ambient baseline at all: nothing about
resting mood produces a resting adrenaline level. It is purely reactive,
firing on a startle, a genuine interruption, or a shock, and decaying on its
own timescale (default 120s — between dopamine's 90s reward glow and
cortisol's 4500s stress hangover).

Its other structural difference from the other two: dopamine/cortisol are
consumed downstream as LLM sampling parameters (`_compute_endocrine_options`
in `cognitive/action.py`). Adrenaline instead feeds back into `arousal`
itself as a bounded, self-fading lift — "a short, sharp arousal raise" per
the remediation plan — which is why several tests below exercise `arousal`
rather than a sampling-parameter mapping.
"""

import asyncio
import math
import time
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.persona import PersonaProfile, Tier
from app.state.agent_state import AgentState, StateService

# --------------------------------------------------------------------------
# no tonic term
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mood,energy,fatigue",
    [(0.8, 0.5, 0.0), (-0.5, 0.9, 0.4), (1.0, 1.0, 1.0), (-1.0, 0.0, 0.0)],
)
def test_adrenaline_tonic_is_always_zero(mood, energy, fatigue):
    """Adrenaline has no ambient baseline, unlike dopamine/cortisol.

    If this ever became a function of mood/arousal/fatigue the way the other
    two are, a merely excited or merely unhappy agent would read as
    perpetually startled, which is not what this channel is for.
    """
    state = AgentState(mood=mood, energy=energy, fatigue=fatigue)
    assert state.adrenaline_tonic == 0.0


def test_a_fresh_state_carries_no_outstanding_adrenaline():
    """A restored or newly constructed agent must not start out startled."""
    assert AgentState().adrenaline_phasic_peak == 0.0
    assert AgentState().adrenaline_phasic == 0.0
    assert AgentState().adrenaline == 0.0


# --------------------------------------------------------------------------
# release
# --------------------------------------------------------------------------


def test_releasing_adrenaline_raises_the_level_by_the_requested_amount():
    """A startle event must actually move the hormone."""
    state = AgentState(mood=0.0, energy=0.0, fatigue=0.0)
    before = state.adrenaline
    after = state.release_adrenaline(0.3)
    assert after == pytest.approx(before + 0.3)


def test_adrenaline_cannot_be_driven_above_one():
    """Saturation is the ceiling of the range, not an unbounded accumulator."""
    state = AgentState(mood=0.0, energy=0.0, fatigue=0.0)
    state.release_adrenaline(0.9)
    assert state.release_adrenaline(0.9) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# rejection of unusable amounts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [0.0, -0.1, -5.0])
def test_a_non_positive_release_is_ignored(amount):
    """`release_adrenaline` fires a startle response; it is not a way to
    calm one down."""
    state = AgentState(mood=0.2)
    before = state.adrenaline
    assert state.release_adrenaline(amount) == pytest.approx(before)
    assert state.adrenaline_phasic_peak == 0.0


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_release_cannot_fire_a_maximum_startle_response(amount):
    """NaN defeats every guard downstream and would saturate the hormone.

    `nan <= 0.0` is False so it passes the positivity check, and
    `min(1.0, nan)` returns 1.0 — so without an explicit finiteness guard a
    malformed startle signal produces the *maximum possible* startle, the
    worst available reading of a caller bug. It would then pin arousal to a
    sharp, sustained-for-a-half-life lift for no real reason.
    """
    state = AgentState(mood=0.2)
    assert not math.isnan(state.release_adrenaline(amount))
    assert state.adrenaline_phasic_peak == 0.0


@pytest.mark.parametrize("amount", ["a lot", None, object()])
def test_a_non_numeric_release_is_ignored(amount):
    """A malformed payload must not crash the cognitive turn."""
    state = AgentState(mood=0.2)
    before = state.adrenaline
    assert state.release_adrenaline(amount) == pytest.approx(before)
    assert state.adrenaline_phasic_peak == 0.0


def test_a_non_finite_release_does_not_clear_an_existing_burst():
    """A bad signal must leave real outstanding startle untouched."""
    state = AgentState(mood=0.0)
    state.release_adrenaline(0.5)
    peak = state.adrenaline_phasic_peak
    state.release_adrenaline(float("nan"))
    assert state.adrenaline_phasic_peak == pytest.approx(peak)


# --------------------------------------------------------------------------
# decay
# --------------------------------------------------------------------------


def test_startle_halves_after_exactly_one_half_life():
    """Decay is computed from wall-clock, so it is correct even if ticks stall."""
    state = AgentState(mood=0.0, adrenaline_halflife_s=100.0)
    state.release_adrenaline(0.4)
    peak = state.adrenaline_phasic_peak
    state.adrenaline_phasic_at = time.time() - 100.0
    assert state.adrenaline_phasic == pytest.approx(peak / 2.0, abs=1e-3)


def test_startle_decays_toward_zero_exactly_not_toward_a_tonic_floor():
    """Unlike cortisol, which decays toward its tonic reading, a fully-decayed
    startle must reach exactly zero — there is no floor to decay toward."""
    state = AgentState(mood=0.0, adrenaline_halflife_s=10.0)
    state.release_adrenaline(0.5)
    state.adrenaline_phasic_at = time.time() - 10_000.0
    assert state.adrenaline_phasic == pytest.approx(0.0, abs=1e-6)
    assert state.adrenaline == pytest.approx(0.0, abs=1e-6)


def test_a_longer_half_life_holds_the_startle_longer():
    """Half-life is the knob that makes one temperament slower to settle."""
    elapsed = 60.0
    quick = AgentState(mood=0.0, adrenaline_halflife_s=30.0)
    slow = AgentState(mood=0.0, adrenaline_halflife_s=600.0)
    for state in (quick, slow):
        state.release_adrenaline(0.5)
        state.adrenaline_phasic_at = time.time() - elapsed
    assert slow.adrenaline_phasic > quick.adrenaline_phasic


def test_reading_then_writing_derived_arousal_does_not_bake_transients_into_energy():
    """A-4: transient arousal components stay out of base energy."""
    state = AgentState(energy=0.5, fatigue=0.5)
    state.release_adrenaline(0.4)
    energy_before = state.energy
    displayed_arousal = state.arousal

    state.arousal = displayed_arousal

    assert state.energy == pytest.approx(energy_before)
    assert state.arousal == pytest.approx(displayed_arousal)


def test_adrenaline_clears_between_dopamine_and_cortisol_by_default():
    """The plan's own framing: a 1-3 minute timescale sitting between
    dopamine's reward glow and cortisol's stress hangover. If this ordering
    ever inverted, the channel would no longer occupy the gap it was built
    to fill."""
    state = AgentState()
    assert state.dopamine_halflife_s < state.adrenaline_halflife_s
    assert state.adrenaline_halflife_s < state.cortisol_halflife_s


# --------------------------------------------------------------------------
# feeds arousal as a short, sharp, self-fading lift
# --------------------------------------------------------------------------


def test_a_startle_raises_arousal_above_its_pre_release_value():
    """The whole behavioural point of this channel: a startle must be
    *felt*, not just recorded as an inert number nothing reads."""
    state = AgentState(mood=0.0, energy=0.4, fatigue=0.0)
    before = state.arousal
    state.release_adrenaline(0.6)
    assert state.arousal > before


def test_the_arousal_lift_is_bounded_not_a_direct_pass_through():
    """A maximum startle must not by itself saturate arousal — it is a
    lift on top of `energy`, not a replacement for it, so a bounded weight
    is applied rather than adding the raw hormone value one-for-one."""
    state = AgentState(mood=0.0, energy=0.0, fatigue=0.0)
    state.release_adrenaline(1.0)
    assert state.adrenaline == pytest.approx(1.0)
    assert state.arousal < 1.0


def test_the_arousal_lift_fades_as_the_burst_decays():
    """A startle must stop colouring arousal once the burst has genuinely
    passed, exactly like cortisol/dopamine's phasic terms already do for
    their own consumers."""
    state = AgentState(mood=0.0, energy=0.4, fatigue=0.0, adrenaline_halflife_s=60.0)
    state.release_adrenaline(0.6)
    lifted = state.arousal
    state.adrenaline_phasic_at = time.time() - 6000.0
    assert state.arousal < lifted
    assert state.arousal == pytest.approx(0.4, abs=1e-6)


# --------------------------------------------------------------------------
# half-life is persona, not deployment config
# --------------------------------------------------------------------------


def test_adrenaline_half_life_is_a_constitutional_persona_field():
    """How long a startle lingers is temperament, not an env var — the same
    judgement already applied to dopamine/cortisol's half-lives."""
    assert PersonaProfile.tier_of("adrenaline_halflife_s") is Tier.CONSTITUTIONAL


def test_state_service_seeds_adrenaline_half_life_from_the_persona():
    """The persona's value must actually reach the state that decays."""
    persona = PersonaProfile(adrenaline_halflife_s=200.0)
    service = StateService(graph_store=None, persona=persona, db_path=":memory:")
    assert service.current_state.adrenaline_halflife_s == 200.0


def test_a_persona_cannot_set_a_half_life_so_short_the_burst_is_never_seen():
    """A near-zero half-life is a broken hormone, not a fast temperament: the
    release would decay below any useful threshold before the next turn
    could read it."""
    with pytest.raises(ValidationError):
        PersonaProfile(adrenaline_halflife_s=0.0)
    with pytest.raises(ValidationError):
        PersonaProfile(adrenaline_halflife_s=0.001)


def test_a_persona_cannot_set_a_half_life_that_outlives_a_startle_response():
    """A startle lasting hours would colour a session it has nothing to do
    with — the ceiling matches dopamine's, since both are short-timescale
    reactive bursts rather than an hours-scale mood."""
    with pytest.raises(ValidationError):
        PersonaProfile(adrenaline_halflife_s=99_999.0)


def test_an_unconfigured_deployment_gets_the_120_second_default():
    """120s (2 minutes) is the low end of the plan's own "1-3 min timescale"
    for this channel; must not silently drift."""
    assert PersonaProfile.from_config().adrenaline_halflife_s == 120.0


# --------------------------------------------------------------------------
# the locked service API
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_service_releases_adrenaline_under_the_state_lock():
    """Affect mutation must go through StateService, holding `_state_lock` —
    the same requirement `release_cortisol`/`release_dopamine` already
    enforce, for the same reason: a fire-and-forget System-2 appraisal
    writes affect concurrently with the synchronous path (finding A2)."""
    service = StateService(graph_store=None, db_path=":memory:")

    assert not service._state_lock.locked()
    level = await service.release_adrenaline(0.3, reason="test")
    assert level == pytest.approx(0.3)
    assert service.current_state.adrenaline_phasic_peak > 0.0
    assert not service._state_lock.locked()


@pytest.mark.asyncio
async def test_a_release_waits_for_a_held_state_lock():
    """Proves the wrapper actually acquires the lock, rather than merely
    leaving it free afterwards."""
    service = StateService(graph_store=None, db_path=":memory:")

    async with service._state_lock:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(service.release_adrenaline(0.3), timeout=0.25)
        assert service.current_state.adrenaline_phasic_peak == 0.0

    assert await service.release_adrenaline(0.3) > 0.0
    assert service.current_state.adrenaline_phasic_peak > 0.0


@pytest.mark.asyncio
async def test_facial_reflex_does_not_deadlock_on_the_release_wrapper():
    """Mirrors `test_somatic_perception_does_not_deadlock_on_the_release_wrapper`
    for dopamine: `apply_facial_reflex` already holds `_state_lock` across its
    own dopamine burst, so any future change routing a facial-reflex signal
    through `release_adrenaline` inside that same block must use the
    unlocked `AgentState` primitive, not this wrapper, or it would deadlock
    on a non-reentrant `asyncio.Lock`. This test exists so that mistake is
    caught immediately rather than only under real concurrent load.
    """
    service = StateService(graph_store=None, db_path=":memory:")
    service._persist_sensory_state_if_due = AsyncMock(return_value=None)

    await asyncio.wait_for(
        service.apply_facial_reflex({"name": "startle", "arousal_delta": 0.06}),
        timeout=5.0,
    )
    assert not service._state_lock.locked()
