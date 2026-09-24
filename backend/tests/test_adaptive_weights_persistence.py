"""
#117 (H6) / #118 (H7): `ReappraisalEngine.appraisal_weights` and
`DecisionService.goal_utilities` used to reset to hardcoded defaults on every
process restart, discarding whatever the feedback loop / TD-learning had
accumulated -- both engines are constructed once per agent-process lifetime,
so this meant every redeploy, not just a rare crash. These tests cover the
fix: both now persist through `AdaptiveWeightsStore` and restore via
`hydrate()`.
"""

import pytest

from app.cognitive.decision import GOALS, DecisionService
from app.cognitive.perception import CognitiveEvent
from app.cognitive.reappraisal import ReappraisalEngine
from app.state.adaptive_weights_store import AdaptiveWeightsStore


@pytest.fixture
def store(tmp_path):
    return AdaptiveWeightsStore(db_path=str(tmp_path / "weights.db"))


@pytest.fixture
def weight_learning_on(monkeypatch):
    """Reappraisal weight learning is opt-in (ADR-002); the persistence tests
    below cover the opt-in path, so they switch it on explicitly."""
    from app.config import Config

    monkeypatch.setattr(Config, "REAPPRAISAL_WEIGHT_LEARNING_ENABLED", True)


# --- AdaptiveWeightsStore itself -------------------------------------------


@pytest.mark.asyncio
async def test_load_returns_none_when_nothing_was_ever_saved(store):
    assert await store.load("my friend", "reappraisal_weights") is None


@pytest.mark.asyncio
async def test_save_then_load_round_trips_the_same_dict(store):
    weights = {"a": 0.25, "b": 0.75}
    await store.save("my friend", "reappraisal_weights", weights)

    assert await store.load("my friend", "reappraisal_weights") == weights


@pytest.mark.asyncio
async def test_different_weight_keys_do_not_clobber_each_other(store):
    """One agent has two independent dicts (reappraisal weights, goal
    utilities) living in the same table -- a primary key that ignored
    `weight_key` would make the second save overwrite the first."""
    await store.save("my friend", "reappraisal_weights", {"w1": 0.6})
    await store.save("my friend", "goal_utilities", {"ENGAGE": 1.0})

    assert await store.load("my friend", "reappraisal_weights") == {"w1": 0.6}
    assert await store.load("my friend", "goal_utilities") == {"ENGAGE": 1.0}


@pytest.mark.asyncio
async def test_saving_again_updates_rather_than_duplicates(store):
    await store.save("my friend", "reappraisal_weights", {"w1": 0.6})
    await store.save("my friend", "reappraisal_weights", {"w1": 0.42})

    assert await store.load("my friend", "reappraisal_weights") == {"w1": 0.42}


# --- ReappraisalEngine (#117 / H6) ------------------------------------------


@pytest.mark.asyncio
async def test_hydrate_restores_previously_learned_appraisal_weights(store, weight_learning_on):
    await store.save(
        "my friend", "reappraisal_weights", {"w1_g_to_v": 0.73, "w2_ri_to_v": 0.22}
    )
    engine = ReappraisalEngine(agent_name="my friend", store=store)

    await engine.hydrate()

    assert engine.appraisal_weights["w1_g_to_v"] == pytest.approx(0.73)
    assert engine.appraisal_weights["w2_ri_to_v"] == pytest.approx(0.22)
    # Untouched keys keep their hardcoded defaults.
    assert engine.appraisal_weights["w3_n_to_ar"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_hydrate_with_nothing_saved_keeps_hardcoded_defaults(store):
    engine = ReappraisalEngine(agent_name="my friend", store=store)

    await engine.hydrate()

    assert engine.appraisal_weights["w1_g_to_v"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_evaluate_outcome_persists_the_updated_weights(store, weight_learning_on):
    """If a real restart lost this write, a fresh engine's `hydrate()` would
    see nothing and reset to the hardcoded defaults -- reproducing #117."""
    engine = ReappraisalEngine(agent_name="my friend", store=store)
    engine.record_pre_response_state({"mood": 0.0, "energy": 0.5, "dominance": 0.5})
    engine.record_expected_outcome("COMFORT", 0.0)  # expects +0.3

    # actual_text_valence=-0.8 -> actual_outcome well below the +0.3
    # expectation, so |delta| clears the 0.1 tolerance and weights adapt.
    prediction_error = await engine.evaluate_outcome(actual_text_valence=-0.8)
    assert prediction_error is not None

    persisted = await store.load("my friend", "reappraisal_weights")
    assert persisted is not None
    assert persisted["w1_g_to_v"] == pytest.approx(
        engine.appraisal_weights["w1_g_to_v"]
    )
    assert persisted != {
        "w1_g_to_v": 0.6,
        "w2_ri_to_v": 0.4,
        "w3_n_to_ar": 0.6,
        "w4_r_to_ar": 0.4,
        "w5_a_to_d": 0.6,
        "w6_na_to_d": 0.4,
    }


# --- DecisionService (#118 / H7) --------------------------------------------


@pytest.mark.asyncio
async def test_decision_hydrate_restores_previously_learned_goal_utilities(store):
    await store.save("my friend", "goal_utilities", {"ENGAGE": 0.987, "COMFORT": 1.1})
    service = DecisionService(agent_name="my friend", weights_store=store)

    await service.hydrate()

    assert service.goal_utilities["ENGAGE"] == pytest.approx(0.987)
    assert service.goal_utilities["COMFORT"] == pytest.approx(1.1)
    # Untouched goals keep their hardcoded default.
    assert service.goal_utilities["PROTECT"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_decision_hydrate_with_nothing_saved_keeps_hardcoded_defaults(store):
    service = DecisionService(agent_name="my friend", weights_store=store)

    await service.hydrate()

    assert all(service.goal_utilities[g] == pytest.approx(1.0) for g in GOALS)


@pytest.mark.asyncio
async def test_decide_persists_goal_utilities_after_maut_scoring(store):
    """If a real restart lost this write, a fresh service's `hydrate()` would
    see nothing and reset every goal utility to 1.0 -- reproducing #118."""
    service = DecisionService(agent_name="my friend", weights_store=store)
    service._previous_goal = "ENGAGE"
    event = CognitiveEvent(
        event_id="ev-1",
        event_type="USER_MESSAGE",
        raw_content="hello",
        metadata={"gaze": 0.8, "appraisal": {"relevance": 0.5}},
        intent="CHAT",
    )
    state = {
        "mood": 0.5,
        "energy": 0.5,
        "trust": 0.5,
        "inferred_valence": 0.8,
        "emotion": "neutral",
    }

    await service.decide(event, state)

    persisted = await store.load("my friend", "goal_utilities")
    assert persisted is not None
    assert persisted["ENGAGE"] == pytest.approx(service.goal_utilities["ENGAGE"])
    assert persisted["ENGAGE"] != pytest.approx(1.0)


# --- ADR-002: weight learning off by default ---------------------------------


@pytest.mark.asyncio
async def test_default_ignores_previously_learned_weights(store):
    """Weights persisted under the old always-on rule can sit at a clamp
    (runaway gain or numbed valence); with learning off, defaults apply."""
    await store.save("my friend", "reappraisal_weights", {"w1_g_to_v": 0.9, "w2_ri_to_v": 0.9})
    engine = ReappraisalEngine(agent_name="my friend", store=store)
    await engine.hydrate()
    assert engine.appraisal_weights["w1_g_to_v"] == 0.6
    assert engine.appraisal_weights["w2_ri_to_v"] == 0.4


@pytest.mark.asyncio
async def test_default_still_reports_prediction_error_but_does_not_learn(store):
    engine = ReappraisalEngine(agent_name="my friend", store=store)
    engine.record_pre_response_state({"mood": 0.0})
    engine.record_expected_outcome("COMFORT", 0.0)
    rpe = await engine.evaluate_outcome(actual_text_valence=-0.8)
    assert rpe is not None and rpe < 0  # worse than expected: cortisol still fires
    assert engine.appraisal_weights["w1_g_to_v"] == 0.6
    assert await store.load("my friend", "reappraisal_weights") is None

