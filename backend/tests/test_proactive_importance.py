"""W9 proactive importance, cooldown, goals, and starvation regressions."""

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app import clock
from app.cognitive.goals import GoalRecord, review_due_goals
from app.cognitive.subconscious import (
    SubconsciousEngine,
    _parse_candidate,
    deterministic_thought,
    estimate_importance,
)
from app.config import Config
from app.contracts import ChatInput
from app.state.agent_state import StateService


class _WatermarkRedis:
    """Small Redis-shaped store for deterministic persistence ordering tests."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def eval(self, _script: str, _key_count: int, key: str, incoming: str) -> str:
        current = self.values.get(key)
        if current is None or float(current) < float(incoming):
            self.values[key] = incoming
        return self.values[key]


def test_proactive_contract_validates_score_and_category():
    parsed = ChatInput.model_validate(
        {
            "text": "A thought",
            "metadata": {
                "source": "subconscious",
                "importance": 0.7,
                "category": "self_directed",
            },
        }
    )
    assert parsed.metadata.importance == 0.7
    assert parsed.metadata.category == "self_directed"
    with pytest.raises(ValidationError):
        ChatInput.model_validate({"text": "bad score", "metadata": {"importance": 1.1}})
    with pytest.raises(ValidationError):
        ChatInput.model_validate(
            {"text": "bad category", "metadata": {"category": "urgent"}}
        )


@pytest.mark.asyncio
async def test_redis_cooldown_watermark_survives_stale_writer_and_restart(tmp_path):
    redis_store = _WatermarkRedis()
    subconscious = StateService(
        graph_store=MagicMock(),
        db_path=str(tmp_path / "subconscious.db"),
        writer_id="subconscious_agent",
    )
    subconscious.redis_client = redis_store
    subconscious.current_state.last_proactive_attempt = 1_735_732_800.0
    await subconscious.persist_state()

    brain = StateService(
        graph_store=MagicMock(), db_path=str(tmp_path / "brain.db"), writer_id="brain"
    )
    brain.redis_client = redis_store
    await brain.persist_state()
    assert redis_store.hashes["state:my friend"]["last_proactive_attempt"] == "0.0"

    restarted_subconscious = StateService(
        graph_store=MagicMock(),
        db_path=str(tmp_path / "subconscious.db"),
        writer_id="subconscious_agent",
    )
    restarted_subconscious.redis_client = redis_store
    await restarted_subconscious._hydrate_locked("my friend")
    assert (
        restarted_subconscious.current_state.last_proactive_attempt == 1_735_732_800.0
    )


@pytest.mark.parametrize("tick_order", ["brain_first", "subconscious_first"])
@pytest.mark.asyncio
async def test_old_brain_tick_cannot_reset_subconscious_cooldown(
    tmp_path, monkeypatch, tick_order
):
    """Reproduce V-4 with both real state stores and the real brain tick handler."""
    from app.agents.brain_agent import BrainAgent

    now = datetime(2025, 1, 1, 12, tzinfo=UTC).timestamp()
    sim_clock = clock.ManualClock(datetime.fromtimestamp(now, UTC))
    monkeypatch.setattr(Config, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(Config, "PROACTIVE_IDLE_THRESHOLD_SECONDS", 7200)
    monkeypatch.setattr(Config, "PROACTIVE_COOLDOWN_SECONDS", 3600)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_ENERGY", 0.0)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.0)

    broadcasts = []

    async def capture(subject, payload):
        if subject == "state.broadcast":
            broadcasts.append(payload)

    with clock.use_clock(sim_clock):
        brain_state = StateService(
            graph_store=MagicMock(),
            db_path=str(tmp_path / "brain-state.db"),
            writer_id="brain_agent",
            publish_cb=capture,
        )
        subconscious_state = StateService(
            graph_store=MagicMock(),
            db_path=str(tmp_path / "subconscious-state.db"),
            writer_id="subconscious_agent",
        )
        brain_state.current_state.last_user_interaction = now - 4 * 3600
        subconscious_state.current_state.last_user_interaction = now - 4 * 3600

        async def brain_tick(at):
            sim_clock.set(datetime.fromtimestamp(at, UTC))
            await brain_state.handle_system_tick(
                {"timestamp": at, "interval": Config.SYSTEM_TICK_INTERVAL}
            )
            pending = tuple(brain_state._background_tasks)
            if pending:
                await asyncio.gather(*pending)
            await subconscious_state.apply_external_state(broadcasts[-1])

        tick_at = now + 60
        sim_clock.set(datetime.fromtimestamp(tick_at, UTC))
        if tick_order == "brain_first":
            await brain_tick(tick_at)
        subconscious_state.mark_proactive_attempt()
        if tick_order == "subconscious_first":
            await brain_tick(tick_at)

        # The real chat-input handler intentionally leaves the user's idle
        # clock alone; it only records the proactive thought history.
        agent = BrainAgent(graph_db=None, memory_store=None, conversation_store=None)
        agent.cognitive_core.state = brain_state
        agent.cognitive_core.generate_proactive_response = MagicMock()
        agent._stream_to_speech = AsyncMock(return_value="")
        await agent._on_chat_input(
            {
                "text": "follow up on project",
                "metadata": {
                    "source": "subconscious",
                    "importance": 0.8,
                    "category": "useful_to_user",
                    "goal_id": "g1",
                },
            }
        )
        pending = tuple(brain_state._background_tasks)
        if pending:
            await asyncio.gather(*pending)
        await subconscious_state.apply_external_state(broadcasts[-1])

        await brain_tick(now + 120)

        assert subconscious_state.current_state.last_proactive_attempt == tick_at
        assert subconscious_state.check_proactive_eligibility() is False


def test_candidate_score_clamps_and_missing_score_uses_structured_fallback():
    state = {
        "active_goals": ["submit report"],
        "user_mental_model": {"implied_goals": ["doctor visit"]},
        "unresolved_thoughts": [],
    }
    assert estimate_importance(state) == pytest.approx(0.35)
    assert estimate_importance(
        {
            **state,
            "unresolved_thoughts": [{"description": "old check-in"}],
        }
    ) == pytest.approx(0.4)
    assert estimate_importance({"active_goals": ["finish the report"]}) < (
        Config.PROACTIVE_USEFUL_IMPORTANCE_MIN
    )
    assert (
        _parse_candidate(
            '{"thought":"Ask about the report","importance":4,"category":"useful_to_user"}',
            0.4,
        ).importance
        == 1.0
    )
    assert (
        _parse_candidate(
            '{"thought":"Ask about the report","importance":"urgent"}', 0.65
        ).importance
        == 0.65
    )


@pytest.mark.asyncio
async def test_model_free_engine_uses_deterministic_contextual_thought():
    class NoModel:
        is_null_llm = True

        async def generate(self, *_args, **_kwargs):
            raise AssertionError("model-free path must not make an LLM call")

    state = {
        "timestamp": 10_000.0,
        "proactive_goals": [{"deadline": 10_300.0, "description": "finish the grant"}],
    }
    thought = await SubconsciousEngine(NoModel()).evaluate_and_think(state, True)
    assert thought == deterministic_thought(state)
    assert thought.importance == pytest.approx(0.75)
    assert "finish the grant" in thought.text
    assert estimate_importance({"timestamp": 10_000.0}) == 0.20


def test_due_disclosed_commitment_supplies_urgency_and_candidate_context():
    snapshot = {
        "timestamp": 10_000.0,
        "active_goals": ["plan:p1: submit grant; due_in_hours=3.0"],
    }
    thought = deterministic_thought(snapshot)
    assert thought.importance >= Config.PROACTIVE_USEFUL_IMPORTANCE_MIN
    assert "plan:p1" in thought.text
    assert "submit grant" in thought.text


def test_structured_output_without_a_thought_is_rejected_and_self_category_inferred():
    assert _parse_candidate('{"importance":1.0}', 0.5) is None
    thought = _parse_candidate(
        '{"thought":"I wonder whether I should explore this idea.","importance":0.6}',
        0.5,
    )
    assert thought is not None
    assert thought.importance == 0.6
    assert thought.category == "self_directed"


def test_ignored_goal_re_raise_probability_reaches_exact_zero():
    goal = GoalRecord(goal_id="goal", description="check the report")
    for index in range(Config.PROACTIVE_IGNORE_ZERO_AFTER):
        now = float(index * 20)
        goal.record_proactive_raise(now)
        review_due_goals([goal], now + 10, ignore_after_s=10)
        assert goal.re_raise_probability(
            Config.PROACTIVE_IGNORE_ZERO_AFTER
        ) == pytest.approx(
            max(0.0, 1 - (index + 1) / Config.PROACTIVE_IGNORE_ZERO_AFTER)
        )
    assert goal.proactive_ignored_count == Config.PROACTIVE_IGNORE_ZERO_AFTER
    assert goal.re_raise_probability(Config.PROACTIVE_IGNORE_ZERO_AFTER) == 0.0


def test_quiet_hours_default_yields_to_the_users_observed_active_hour(tmp_path):
    service = StateService(
        graph_store=MagicMock(), db_path=str(tmp_path / "activity-state.db")
    )
    late_hour = datetime(2025, 1, 1, 23).timestamp()
    assert service.is_quiet_hour(late_hour)
    service.current_state.user_interaction_hours = [
        23
    ] * Config.PROACTIVE_ACTIVITY_HISTORY_MINIMUM
    assert not service.is_quiet_hour(late_hour)


@pytest.mark.asyncio
async def test_user_activity_hour_history_survives_state_hydration(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Config, "REDIS_URL", "")
    database = str(tmp_path / "activity-history.db")
    first = StateService(graph_store=MagicMock(), db_path=database)
    first.current_state.user_interaction_hours = [9, 10, 12, 14]
    first.record_proactive_thought("check the report", goal_id="report")
    first.proactive_goals[0].record_proactive_outcome("ignored")
    await first.persist_state()

    restored = StateService(graph_store=MagicMock(), db_path=database)
    await restored.hydrate_state()

    assert restored.current_state.user_interaction_hours == [9, 10, 12, 14]
    assert restored.proactive_goals[0].proactive_ignored_count == 1


def test_self_directed_candidates_have_a_stricter_importance_floor(tmp_path):
    service = StateService(
        graph_store=MagicMock(), db_path=str(tmp_path / "candidate-state.db")
    )
    assert service.proactive_candidate_eligible(
        importance=0.6,
        category="useful_to_user",
        description="check the report",
    )
    assert not service.proactive_candidate_eligible(
        importance=0.6,
        category="self_directed",
        description="share my thought",
    )


@pytest.mark.asyncio
async def test_foreground_pipeline_turn_finishes_while_consolidation_is_in_flight(
    tmp_path, monkeypatch
):
    """Tick dispatch cannot await consolidation: a foreground turn completes
    while consolidation is still blocked. Proven by ordering, not by a
    wall-clock race: the elapsed time against DR-024's 500 ms interactive
    budget is printed, not asserted. A shared CI runner took 0.598 s on a cold
    turn (no Ollama there, so the embedder's failed connect is in the turn),
    and DR-024 leaves validating the budgets to the Phase 8/9 load runs."""
    from app.agents.subconscious_agent import SubconsciousAgent
    from evals.brainbench.adapters import build_cognitive_service

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_consolidation():
        entered.set()
        await release.wait()

    state = MagicMock()
    state.current_state.last_user_interaction = clock.time() - 3600
    state.get_context_snapshot.return_value = {"energy": 0.5}
    state.check_proactive_eligibility.return_value = False
    agent = SubconsciousAgent(state_service=state, graph_db=MagicMock())
    agent._last_benchmark_time = clock.time() - 600
    agent._run_consolidation_pass = blocked_consolidation
    monkeypatch.setattr(Config, "TESTING_CONSOLIDATION_BYPASS_SILENCE", True)
    await agent._on_system_tick({"timestamp": clock.time()})
    assert agent._consolidation_task is not None
    await asyncio.wait_for(entered.wait(), timeout=0.2)

    service = build_cognitive_service("architecture_only", tmp_path / "brain")

    async def foreground_turn():
        return [
            output
            async for output in service.cognitive.process_event(
                {"id": "foreground", "type": "USER_MESSAGE", "content": "hello"}
            )
        ]

    started = time.perf_counter()
    try:
        # Consolidation is released only after this returns, so a starved
        # foreground never finishes; the timeout only bounds that failure.
        outputs = await asyncio.wait_for(foreground_turn(), timeout=10.0)
        consolidation_still_blocked = not agent._consolidation_task.done()
    finally:
        release.set()
        if agent._consolidation_task is not None:
            await agent._consolidation_task
    elapsed = time.perf_counter() - started
    assert outputs
    assert consolidation_still_blocked
    print(f"foreground_turn_elapsed_s={elapsed:.6f} budget_s=0.500000")


def _thought(goal_id: str, description: str) -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        type="proactive_thought",
        source="subconscious",
        description=description,
    )


@pytest.mark.asyncio
async def test_one_malformed_goal_does_not_abort_a_state_broadcast(tmp_path):
    """Regression: goals were validated in one comprehension, so a single bad
    record raised out of apply_external_state halfway through applying it
    (and out of hydration before the mood baselines were loaded)."""
    state = StateService(
        graph_store=MagicMock(),
        db_path=str(tmp_path / "s.db"),
        writer_id="subconscious_agent",
    )
    good = _thought("g1", "ask about the exam").model_dump()
    bad = {"goal_id": "g2", "proactive_outcomes": ["exploded"]}

    await state.apply_external_state(
        {
            "proactive_goals": [good, bad],
            "interaction_count": 7,
            "baseline_valence": 0.3,
        }
    )

    assert [goal.goal_id for goal in state.proactive_goals] == ["g1"]
    assert state.current_state.interaction_count == 7
    assert state.current_state.baseline_valence == 0.3


def test_stored_goal_history_skips_a_malformed_record():
    from app.state.agent_state import _parse_proactive_goals

    raw = [_thought("g1", "a").model_dump(), {"proactive_raise_count": "many"}, 3]
    assert [goal.goal_id for goal in _parse_proactive_goals(raw)] == ["g1"]
    assert _parse_proactive_goals("{not json") is None
    assert _parse_proactive_goals(None) is None


def test_thought_history_stays_bounded_and_evicts_closed_thoughts_first(tmp_path):
    """Every distinct thought used to add a record forever, and every record
    rides in each snapshot and state.broadcast."""
    from app.state import agent_state

    state = StateService(
        graph_store=MagicMock(),
        db_path=str(tmp_path / "s.db"),
        writer_id="subconscious_agent",
    )
    closed = _thought("closed", "an old dismissed thought")
    closed.record_proactive_raise(1.0)
    closed.record_proactive_outcome("dismissed")
    state.proactive_goals = [closed]
    cap = agent_state._MAX_PROACTIVE_GOALS

    ids = [
        state.record_proactive_thought(f"distinct thought {i}")[0]
        for i in range(cap + 10)
    ]

    kept = {goal.goal_id for goal in state.proactive_goals}
    assert len(state.proactive_goals) == cap
    assert "closed" not in kept
    assert ids[-1] in kept
    assert ids[0] not in kept


@pytest.mark.parametrize(
    ("hour", "start", "end", "inside"),
    [
        (23, 22, 6, True),  # wraps midnight
        (5, 22, 6, True),
        (6, 22, 6, False),  # end is exclusive
        (12, 22, 6, False),
        (3, 1, 5, True),  # a window that does not wrap
        (12, 1, 5, False),  # was quiet all day: `hour >= 1 or hour < 5`
        (0, 0, 0, False),  # start == end: no default window
        (12, 0, 0, False),
    ],
)
def test_quiet_hour_window_bounds(hour, start, end, inside):
    from app.state.agent_state import in_hour_window

    assert in_hour_window(hour, start, end) is inside


def test_quiet_hours_follow_the_users_timezone_not_the_hosts(tmp_path, monkeypatch):
    """Regression: the default window was read in the host's local time, so a
    UTC container put an Asia/Kolkata user's quiet hours at 03:30-11:30 Kolkata time."""
    service = StateService(graph_store=MagicMock(), db_path=str(tmp_path / "tz.db"))
    at = datetime(2025, 1, 1, 17, tzinfo=UTC).timestamp()  # 22:30 in Kolkata
    monkeypatch.setattr(Config, "USER_TIMEZONE", "Asia/Kolkata")
    assert service.is_quiet_hour(at)
    monkeypatch.setattr(Config, "USER_TIMEZONE", "UTC")
    assert not service.is_quiet_hour(at)


def test_recorded_activity_hour_uses_the_users_timezone(tmp_path, monkeypatch):
    """The learned active hours and the default window must share one clock,
    or a learned hour would be compared against a shifted default."""
    monkeypatch.setattr(Config, "USER_TIMEZONE", "Asia/Kolkata")
    service = StateService(graph_store=MagicMock(), db_path=str(tmp_path / "hours.db"))
    at = datetime(2025, 1, 1, 17, tzinfo=UTC)
    with clock.use_clock(clock.ManualClock(at)):
        service.record_user_interaction()
    assert service.current_state.user_interaction_hours[-1] == 22


def test_unknown_user_timezone_falls_back_to_host_time(tmp_path, monkeypatch):
    service = StateService(graph_store=MagicMock(), db_path=str(tmp_path / "bad.db"))
    at = datetime(2025, 1, 1, 23).timestamp()  # host-local 23:00
    monkeypatch.setattr(Config, "USER_TIMEZONE", "Not/A_Zone")
    assert service.is_quiet_hour(at)
    monkeypatch.setattr(Config, "PROACTIVE_QUIET_START_HOUR", 0)
    monkeypatch.setattr(Config, "PROACTIVE_QUIET_END_HOUR", 0)
    assert not service.is_quiet_hour(at)


@pytest.mark.parametrize(("utc_hour", "eligible"), [(12, True), (23, False)])
def test_eligibility_gate_uses_the_pinned_clock_hour(
    tmp_path, monkeypatch, utc_hour, eligible
):
    """The gate is a pure function of the injected clock and USER_TIMEZONE,
    so it gives the same answer on any runner at any time of day."""
    monkeypatch.setattr(Config, "USER_TIMEZONE", "UTC")
    monkeypatch.setattr(Config, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(Config, "PROACTIVE_DEBUG_THRESHOLD_OVERRIDE", None)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_ENERGY", 0.0)
    monkeypatch.setattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.0)
    now = datetime(2025, 1, 1, utc_hour, tzinfo=UTC)
    service = StateService(graph_store=MagicMock(), db_path=str(tmp_path / "gate.db"))
    service.redis_client = None
    service.current_state.last_user_interaction = (
        now.timestamp() - Config.PROACTIVE_IDLE_THRESHOLD_SECONDS - 1
    )
    service.current_state.energy = 1.0
    with clock.use_clock(clock.ManualClock(now)):
        assert service.check_proactive_eligibility() is eligible


def test_rest_phase_night_is_the_users_night(monkeypatch):
    from app.agents.subconscious_agent import is_rest_phase

    at = datetime(2025, 1, 1, 17, tzinfo=UTC).timestamp()  # 22:30 in Kolkata
    monkeypatch.setattr(Config, "USER_TIMEZONE", "Asia/Kolkata")
    assert is_rest_phase(at, at - 3600, fatigue=0.0)
    monkeypatch.setattr(Config, "USER_TIMEZONE", "UTC")
    assert not is_rest_phase(at, at - 3600, fatigue=0.0)


def _fill_thought_history_to_the_cap(state: StateService) -> None:
    from app.state import agent_state

    for index in range(agent_state._MAX_PROACTIVE_GOALS):
        state.record_proactive_thought(f"remember to ask how trip number {index} went")
    state.proactive_goals[0].record_proactive_outcome("ignored")
    state.proactive_goals[1].deadline = 1_900_000_000.0


async def _persist_and_capture_broadcast(state: StateService) -> dict:
    broadcasts: list[dict] = []

    async def capture(subject, data):
        if subject == "state.broadcast":
            broadcasts.append(data)

    state.publish_cb = capture
    await state.persist_state()
    await asyncio.gather(*state._background_tasks)
    assert len(broadcasts) == 1
    return broadcasts[0]


@pytest.mark.asyncio
async def test_state_broadcast_at_the_goal_cap_is_small_and_round_trips(
    tmp_path, monkeypatch
):
    """Regression: every goal rode in every state.broadcast as a full dump,
    about 580 bytes of mostly empty defaults each. The brain broadcasts on
    every 60 s tick into the file-backed AI_MESSAGES stream (7-day age, 1 GiB
    cap, oldest discarded first), so at the 256-goal cap state snapshots alone
    came to about 1.4 GB a week and pushed chat history out of the stream."""
    import json

    monkeypatch.setattr(Config, "REDIS_URL", "")
    sender = StateService(
        graph_store=MagicMock(),
        db_path=str(tmp_path / "brain.db"),
        writer_id="brain_agent",
    )
    _fill_thought_history_to_the_cap(sender)

    payload = await _persist_and_capture_broadcast(sender)

    # 80 KiB a minute is about 790 MiB a week; the full dump was 149 KiB.
    assert len(json.dumps(payload)) < 80 * 1024
    receiver = StateService(
        graph_store=MagicMock(),
        db_path=str(tmp_path / "subconscious.db"),
        writer_id="subconscious_agent",
    )
    await receiver.apply_external_state(payload)
    assert receiver.proactive_goals == sender.proactive_goals
    assert receiver.proactive_goals[0].proactive_ignored_count == 1
    assert receiver.proactive_goals[1].deadline == 1_900_000_000.0


@pytest.mark.asyncio
async def test_state_row_and_thought_history_commit_together(tmp_path, monkeypatch):
    """Regression: the state row, the activity hours and the thought history
    were three separate SQLite transactions. A failure after the first left a
    state row whose thought history belonged to a different persist; now all
    three commit or none does."""
    import sqlite3

    monkeypatch.setattr(Config, "REDIS_URL", "")
    database = str(tmp_path / "atomic.db")
    state = StateService(graph_store=MagicMock(), db_path=database)
    state.current_state.mood = 0.2
    await state.persist_state()

    with sqlite3.connect(database) as conn:
        conn.execute("DROP TABLE agent_proactive_goals")
    state.current_state.mood = 0.9
    await state.persist_state()

    with sqlite3.connect(database) as conn:
        (mood,) = conn.execute("SELECT mood FROM agent_state").fetchone()
    assert mood == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_one_persist_writes_one_thought_history_everywhere(tmp_path, monkeypatch):
    """Regression: the goal list was serialized separately for Redis, SQLite
    and the broadcast, with an await between each, so a thought recorded
    mid-persist reached some destinations and not others."""
    import json
    import sqlite3

    class _MutatingRedis(_WatermarkRedis):
        def __init__(self, on_hset):
            super().__init__()
            self.on_hset = on_hset

        def hset(self, key, *, mapping):
            super().hset(key, mapping=mapping)
            self.on_hset()

    monkeypatch.setattr(Config, "REDIS_URL", "")
    database = str(tmp_path / "consistent.db")
    state = StateService(graph_store=MagicMock(), db_path=database)
    state.record_proactive_thought("ask about the exam", goal_id="exam")
    state.redis_client = _MutatingRedis(
        lambda: state.record_proactive_thought("recorded mid-persist", goal_id="late")
    )

    payload = await _persist_and_capture_broadcast(state)

    in_redis = json.loads(
        state.redis_client.hashes["state:my friend"]["proactive_goals"]
    )
    with sqlite3.connect(database) as conn:
        (stored,) = conn.execute(
            "SELECT goals_json FROM agent_proactive_goals"
        ).fetchone()
    ids = [goal["goal_id"] for goal in in_redis]
    assert ids == ["exam"]
    assert json.loads(stored) == in_redis
    assert payload["proactive_goals"] == in_redis
    # The late thought is not lost: the next persist carries it.
    assert [goal.goal_id for goal in state.proactive_goals] == ["exam", "late"]
