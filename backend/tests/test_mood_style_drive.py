"""Slice 4b, variant B: mood style line in the realization contract.

Gate tests D1 to D9 from `docs/brain-v4/prereg/slice-04b.md`. Deterministic, no
model, no network. Variant A (the `top_p` term) is not built in this branch, so
the A-only checks reduce to their flag-off byte-identity part (D1, D2, D3).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from itertools import pairwise, product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.cognitive.action import (
    _MOOD_STYLE_BRIEF,
    _MOOD_STYLE_ROOMY,
    ActionService,
)
from app.cognitive.behavior_contracts import BehaviorDecision, CommunicativeIntent
from app.cognitive.core import CognitiveService
from app.cognitive.decision import ActionPlan
from app.config import AppSettings, Config
from app.state.agent_state import AgentState, StateService
from app.state.interventions import clamp
from app.state.ledger import get_row
from app.state.offline_copy import OfflineCopy
from app.state.sqlite_fallback import SQLiteConnection
from evals.brainbench.baseline import defects
from evals.brainbench.mood_style import RecordingLLM, build_chat_plan

_NAN = float("nan")
_INF = float("inf")
_ABSENT = object()

# --- D1 input grids ------------------------------------------------------------

_HORMONE_VALUES = (None, 0, 0.25, 0.5, 1, "x", _NAN)
_ENDOCRINE_VALENCES = (_ABSENT, -0.8, 0.8, _NAN)
_CONTRACT_VALENCES = (
    _ABSENT,
    None,
    _NAN,
    _INF,
    -_INF,
    True,
    False,
    "0.5",
    "-0.8",
    -5,
    -1.0,
    -0.8,
    -0.4,
    -0.39,
    0,
    0.39,
    0.4,
    0.8,
    1,
    5.0,
)
_GOALS = ("ENGAGE", "COMFORT", "INFORM", "TEASE", "PROTECT", "INITIATE")
_EMOTIONS = ("neutral", "sad", "happy")
_DIRECTIVES = (None, "You are grounded and emotionally stable.")


def _canon(rows) -> str:
    return json.dumps(rows, sort_keys=True, default=repr, allow_nan=True)


def _sha(rows) -> str:
    return hashlib.sha256(_canon(rows).encode("utf-8")).hexdigest()


def _decision(goal: str) -> BehaviorDecision:
    return BehaviorDecision(
        intent=CommunicativeIntent(
            act="CHAT", goal=goal, urgency=0.8, relational_stance="warm"
        ),
        allowed_claims=["I remember you mentioned a hard day"],
        forbidden_claims=["Will never share user data"],
    )


def _endocrine_payloads():
    for cortisol, dopamine, fatigue, valence in product(
        _HORMONE_VALUES, _HORMONE_VALUES, _HORMONE_VALUES, _ENDOCRINE_VALENCES
    ):
        payload = {"cortisol": cortisol, "dopamine": dopamine, "fatigue": fatigue}
        if valence is not _ABSENT:
            payload["valence"] = valence
        yield payload


def _contract_plans():
    for goal, with_decision, directive, valence in product(
        _GOALS, (False, True), _DIRECTIVES, _CONTRACT_VALENCES
    ):
        payload: dict = {}
        if directive is not None:
            payload["state_directive"] = directive
        if valence is not _ABSENT:
            payload["valence"] = valence
        yield ActionPlan(
            action_type="RESPOND_CHAT",
            goal=goal,
            payload=payload,
            behavior_decision=_decision(goal) if with_decision else None,
        )


def endocrine_rows() -> list:
    return [
        [
            repr(sorted(p.items(), key=lambda kv: kv[0])),
            ActionService._compute_endocrine_options(dict(p)),
        ]
        for p in _endocrine_payloads()
    ]


def contract_rows() -> list:
    return [
        [
            plan.goal,
            repr(plan.payload),
            plan.behavior_decision is not None,
            emotion,
            ActionService._build_realization_contract(plan, emotion),
        ]
        for plan in _contract_plans()
        for emotion in _EMOTIONS
    ]


# --- D1 recorded hashes ----------------------------------------------------------
# Recorded from main (commit ec79dfe8, before any slice 4b code) by running
# `endocrine_rows()` and `contract_rows()` twice on unmodified `action.py`. They
# are literals on purpose: regenerating them from the code under test would make
# the test pass for any behavior.
ENDOCRINE_ROWS = 1372
ENDOCRINE_SHA = "c4248feb556a0e798c1e469b63b2dbd4a757291469159c533da05b6e906c1de6"
CONTRACT_ROWS = 1440
CONTRACT_SHA = "8484b1c484a51f0ec970d4d9120e10f14e9e73b6c783967de77dde8b16eb825a"

RECORDED_2A = (
    Path(__file__).resolve().parents[2]
    / "docs/brain-v4/results/slice-02a/defect_baselines.json"
)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(Config, "MOOD_STYLE_DRIVE_ENABLED", True)


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(Config, "MOOD_STYLE_DRIVE_ENABLED", False)


def _plan(goal="ENGAGE", valence=_ABSENT, *, decision=False, directive=None):
    payload: dict = {}
    if directive:
        payload["state_directive"] = directive
    if valence is not _ABSENT:
        payload["valence"] = valence
    return ActionPlan(
        action_type="RESPOND_CHAT",
        goal=goal,
        payload=payload,
        behavior_decision=_decision(goal) if decision else None,
    )


def _contract(valence=_ABSENT, goal="ENGAGE", emotion="neutral", **kw) -> str:
    return ActionService._build_realization_contract(
        _plan(goal, valence, **kw), emotion
    )


def _level(contract: str) -> int:
    """-1 brief, 0 no line, +1 more."""
    lines = contract.split("\n")
    brief, roomy = _MOOD_STYLE_BRIEF in lines, _MOOD_STYLE_ROOMY in lines
    assert not (brief and roomy)
    return -1 if brief else 1 if roomy else 0


# --- config and switchboard ------------------------------------------------------


def test_flag_defaults_off_and_arm_is_runnable():
    from evals.brainbench.switchboard import ARMS, apply_arm, is_runnable, resolve_arm

    assert AppSettings.model_fields["MOOD_STYLE_DRIVE_ENABLED"].default is False
    assert Config.MOOD_STYLE_DRIVE_ENABLED is False
    assert is_runnable(ARMS["+mood-style"])
    with apply_arm(resolve_arm("+mood-style")):
        assert Config.MOOD_STYLE_DRIVE_ENABLED is True
    assert Config.MOOD_STYLE_DRIVE_ENABLED is False
    # `all` is the V3 union; a V4 arm must not leak into it.
    assert "MOOD_STYLE_DRIVE_ENABLED" not in ARMS["all"].overrides


# --- D1: flag off is byte-identical to main --------------------------------------


def test_d1_endocrine_options_match_main_flag_off(flag_off):
    rows = endocrine_rows()
    assert len(rows) == ENDOCRINE_ROWS
    assert _sha(rows) == ENDOCRINE_SHA


def test_d1_realization_contract_matches_main_flag_off(flag_off):
    rows = contract_rows()
    assert len(rows) == CONTRACT_ROWS
    assert _sha(rows) == CONTRACT_SHA


def test_d1_endocrine_options_also_unchanged_flag_on(flag_on):
    """B adds no sampler term: the sampler output is main's with the flag on."""
    assert _sha(endocrine_rows()) == ENDOCRINE_SHA


def test_d1_flag_on_does_change_the_contract(flag_on):
    """Without this the flag-off hash test would pass for a dead feature."""
    assert _sha(contract_rows()) != CONTRACT_SHA


# --- D2: monotone ----------------------------------------------------------------


def test_d2_line_level_is_non_decreasing_in_valence(flag_on):
    grid = [i / 100 for i in range(-100, 101)]
    levels = [_level(_contract(v)) for v in grid]
    assert levels == sorted(levels)
    assert set(levels) == {-1, 0, 1}
    assert levels[grid.index(-0.4)] == -1 and levels[grid.index(-0.39)] == 0
    assert levels[grid.index(0.39)] == 0 and levels[grid.index(0.4)] == 1
    # exactly two level changes over the whole range
    assert sum(1 for a, b in pairwise(levels) if a != b) == 2


def test_d2_sampler_does_not_depend_on_valence(flag_on):
    base = {"cortisol": 0.5, "dopamine": 0.2, "fatigue": 0.3}
    seen = {
        json.dumps(ActionService._compute_endocrine_options({**base, "valence": v}))
        for v in [i / 100 for i in range(-100, 101)]
    }
    assert len(seen) == 1


def test_comfort_goal_skips_only_the_low_bucket(flag_on):
    assert _level(_contract(-0.8, goal="COMFORT")) == 0
    assert _level(_contract(-0.8, goal="ENGAGE")) == -1
    assert _level(_contract(0.8, goal="COMFORT")) == 1


# --- D3: bounded -----------------------------------------------------------------


def test_d3_added_text_is_one_line_of_at_most_120_chars(flag_on):
    added_total = 0
    for plan in _contract_plans():
        for emotion in _EMOTIONS:
            on = ActionService._build_realization_contract(plan, emotion)
            with patch.object(Config, "MOOD_STYLE_DRIVE_ENABLED", False):
                off = ActionService._build_realization_contract(plan, emotion)
            if on == off:
                continue
            added_total += 1
            assert on.startswith(off + "\n")
            added = on[len(off) + 1 :]
            assert "\n" not in added
            assert 0 < len(added) <= 120
            assert added in (_MOOD_STYLE_BRIEF, _MOOD_STYLE_ROOMY)
    assert added_total > 100


def test_d3_line_texts_are_plain_and_short():
    for text in (_MOOD_STYLE_BRIEF, _MOOD_STYLE_ROOMY):
        assert len(text) <= 120 and "\n" not in text
        assert chr(0x2014) not in text and text.startswith("- Reply length: ")


# --- D4: end to end through a real AgentState ------------------------------------


def _state_at(mood: float) -> AgentState:
    state = AgentState()
    state.mood = mood
    return state


def _payload_of(state: AgentState) -> dict:
    return {
        "cortisol": state.cortisol,
        "dopamine": state.dopamine,
        "fatigue": state.fatigue,
        "valence": state.mood,
    }


def test_d4_premise_table_still_holds_and_b_leaves_sampler_alone(flag_on):
    off = {}
    for mood in [i / 10 for i in range(-10, 11)]:
        payload = _payload_of(_state_at(mood))
        on = ActionService._compute_endocrine_options(payload)
        with patch.object(Config, "MOOD_STYLE_DRIVE_ENABLED", False):
            off[mood] = ActionService._compute_endocrine_options(payload)
        assert on == off[mood]
    assert off[-0.8]["temperature"] == pytest.approx(0.36, abs=0.01)
    assert off[0.8]["temperature"] == pytest.approx(0.84, abs=0.01)
    assert off[-0.8]["top_p"] == pytest.approx(0.70)


def test_d4_line_follows_the_real_emotion_label(flag_on):
    label_class = {"sad": -1, "angry": -1, "happy": 1, "excited": 1}
    checked = 0
    for i in range(-100, 101):
        mood = i / 100
        if abs(mood) == 0.4:  # label is strict, B is inclusive (README, gaps)
            continue
        state = _state_at(mood)
        label = StateService.get_emotion_label(SimpleNamespace(current_state=state))
        assert _level(_contract(state.mood, emotion=label)) == label_class.get(
            label, 0
        ), (mood, label)
        checked += 1
    assert checked == 199


# --- D5: inert inputs ------------------------------------------------------------


INERT = (None, _NAN, _INF, -_INF, True, False, "0.5", "-0.8", "x", [], {}, object())


@pytest.mark.parametrize("goal", _GOALS)
@pytest.mark.parametrize("decision", [False, True])
def test_d5_unusable_valence_is_identical_to_flag_off(flag_on, goal, decision):
    for valence in (*INERT, _ABSENT):
        on = _contract(valence, goal, decision=decision)
        with patch.object(Config, "MOOD_STYLE_DRIVE_ENABLED", False):
            off = _contract(valence, goal, decision=decision)
        assert on == off, valence


def test_d5_usable_valence_is_not_inert(flag_on):
    """Positive control: the same loop over usable inputs must see a line."""
    assert _level(_contract(-0.8)) == -1
    assert _level(_contract(0.8)) == 1
    assert _level(_contract(-1)) == -1  # int
    assert _level(_contract(1)) == 1


# --- D6: flicker -----------------------------------------------------------------


def test_d6_line_changes_only_where_the_label_class_changes(flag_on):
    rng = random.Random(20261030)
    shell = SimpleNamespace(
        current_state=SimpleNamespace(mood=0.0, energy=0.5, dominance=0.5)
    )
    plan = _plan("ENGAGE", 0.0)
    label_class = {"sad": -1, "angry": -1, "happy": 1, "excited": 1}
    label_changes = line_changes = 0
    for _ in range(1000):
        mu = rng.uniform(-0.8, 0.8)
        x = mu
        prev_label = prev_line = None
        for _ in range(100):
            x += 0.05 * (mu - x) + rng.gauss(0.0, 0.05)
            x = max(-1.0, min(1.0, x))
            shell.current_state.mood = x
            label = label_class.get(StateService.get_emotion_label(shell), 0)
            plan.payload["valence"] = x
            line = _level(ActionService._build_realization_contract(plan, "neutral"))
            if prev_line is not None:
                if line != prev_line:
                    line_changes += 1
                    assert label != prev_label, "line moved without a label change"
                if label != prev_label:
                    label_changes += 1
            prev_label, prev_line = label, line
    assert label_changes > 500, "trajectories never crossed the boundary"
    assert 0 < line_changes <= label_changes


# --- D7: no state change ---------------------------------------------------------


def test_d7_slice_2a_digests_unchanged_with_flag_on_and_off(monkeypatch):
    recorded = json.loads(RECORDED_2A.read_text("utf-8"))["envelope"]
    assert recorded["result_sha256"].startswith("627dcce6")
    assert recorded["raw_result_sha256"].startswith("cb62dc20")
    seen = []
    for flag in (False, True):
        monkeypatch.setattr(Config, "MOOD_STYLE_DRIVE_ENABLED", flag)
        env = defects.envelope(asyncio.run(defects.collect()))
        seen.append((env["result_sha256"], env["raw_result_sha256"]))
    expected = (recorded["result_sha256"], recorded["raw_result_sha256"])
    assert seen == [expected] * 2


# --- D8: proactive payload -------------------------------------------------------


class _PayloadRecorder:
    def __init__(self):
        self.payloads: list[dict] = []

    async def execute(self, plan):
        self.payloads.append(dict(plan.payload))
        yield {"type": "content", "data": "hi"}


def _proactive_core(recorder, snapshot):
    from unittest.mock import AsyncMock, MagicMock

    state = MagicMock()
    state.get_behavioral_directive.return_value = "A calm, low-pressure check-in."
    state.get_context_snapshot.return_value = snapshot
    state.current_state.energy = 0.8
    state.get_emotion_label.return_value = "calm"
    identity = MagicMock()
    identity.get_persona_prompt.return_value = "You are a careful friend."
    identity.history = {"relationship": "Friend"}
    core = CognitiveService.__new__(CognitiveService)
    core.state, core.identity, core.surfaced_memories = state, identity, []
    core.action = recorder
    core.learning = MagicMock()
    core.learning.trigger_reflection = AsyncMock(return_value=None)
    return core


def _proactive_payload(snapshot) -> dict:
    recorder = _PayloadRecorder()
    core = _proactive_core(recorder, snapshot)

    async def go():
        return [c async for c in core.generate_proactive_response()]

    asyncio.run(go())
    return recorder.payloads[0]


SNAPSHOT = {"cortisol": 0.1, "dopamine": 0.2, "mood": -0.8, "valence": -0.8}


def test_d8_flag_off_payload_has_no_valence_and_is_unchanged(flag_off):
    payload = _proactive_payload(SNAPSHOT)
    assert set(payload) == {
        "message",
        "identity_prompt",
        "emotion_state",
        "surfaced_memories",
        "cortisol",
        "dopamine",
    }
    assert payload["cortisol"] == 0.1 and payload["dopamine"] == 0.2


def test_d8_flag_on_payload_gains_only_valence(flag_on, monkeypatch):
    on = _proactive_payload(SNAPSHOT)
    monkeypatch.setattr(Config, "MOOD_STYLE_DRIVE_ENABLED", False)
    off = _proactive_payload(SNAPSHOT)
    assert on.pop("valence") == -0.8
    assert on == off
    monkeypatch.setattr(Config, "MOOD_STYLE_DRIVE_ENABLED", True)
    assert _proactive_payload({"cortisol": 0.1})["valence"] == 0.0


def _run_plan(plan, llm):
    service = ActionService(llm_service=llm, memory_store=None, self_knowledge=None)

    async def go():
        return [c async for c in service.execute(plan)]

    return asyncio.run(go())


def test_d8_proactive_prompt_carries_the_line_only_with_the_flag(monkeypatch):
    prompts = {}
    for flag in (False, True):
        monkeypatch.setattr(Config, "MOOD_STYLE_DRIVE_ENABLED", flag)
        payload = _proactive_payload(SNAPSHOT)
        plan = ActionPlan(action_type="RESPOND_CHAT", goal="INITIATE", payload=payload)
        llm = RecordingLLM()
        _run_plan(plan, llm)
        prompts[flag] = llm.calls[0].prompt
    assert _MOOD_STYLE_BRIEF not in prompts[False]
    assert _MOOD_STYLE_BRIEF in prompts[True]
    assert prompts[True].replace("\n" + _MOOD_STYLE_BRIEF, "", 1) == prompts[False]


# --- D9: clamp wiring on an offline copy ----------------------------------------

AGENT = "friend"


async def _seed(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "src"
    root.mkdir()
    db = root / "state_cache.db"
    with patch.object(Config, "REDIS_URL", ""):
        svc = StateService(graph_store=None, db_path=str(db))
    svc.current_state.mood = 0.3
    await svc.persist_state(AGENT)
    mem = root / "memories.db"
    conn = SQLiteConnection(str(mem)).conn
    conn.commit()
    conn.close()
    return {"state_cache": db, "memories": mem}


async def _snapshot_after_clamp(copy: OfflineCopy, value: float) -> dict:
    clamp(copy, "mood", value)
    svc = copy.open_state_service()
    await svc.hydrate_state(AGENT)
    return svc.get_context_snapshot()


def _calls_for(snapshot: dict, flag: bool) -> RecordingLLM:
    llm = RecordingLLM()
    plan = build_chat_plan(snapshot, "How was your day?")
    with patch.object(Config, "MOOD_STYLE_DRIVE_ENABLED", flag):
        _run_plan(plan, llm)
    return llm


@pytest.fixture
def copy_at(tmp_path):
    sources = asyncio.run(_seed(tmp_path))
    copy = OfflineCopy.create(sources, tmp_path / "copies", label="d9")
    yield copy
    if not copy.deleted:
        copy.delete(reason="test teardown")


@pytest.mark.parametrize(
    ("value", "level"), [(0.8, 1), (-0.8, -1), (0.0, 0)], ids=["pos", "neg", "zero"]
)
def test_d9_clamp_reaches_options_and_prompt(copy_at, value, level):
    snapshot = asyncio.run(_snapshot_after_clamp(copy_at, value))
    assert snapshot["mood"] == pytest.approx(value)
    off = _calls_for(snapshot, False)
    on = _calls_for(snapshot, True)
    assert len(off.calls) == len(on.calls) == 1
    # Assertion 1: sampler options. B predicts a difference of exactly zero, and
    # both equal the options derived from this snapshot by the unchanged mapping.
    predicted = ActionService._compute_endocrine_options(
        {k: snapshot[k] for k in ("cortisol", "dopamine", "fatigue")}
    )
    assert off.calls[0].options == on.calls[0].options == predicted
    # Assertion 2: the prompt differs only by variant B's line.
    expected = {-1: _MOOD_STYLE_BRIEF, 1: _MOOD_STYLE_ROOMY, 0: None}[level]
    assert off.calls[0].system == on.calls[0].system
    if expected is None:
        assert on.calls[0].prompt == off.calls[0].prompt
    else:
        assert on.calls[0].prompt != off.calls[0].prompt
        assert on.calls[0].prompt.replace("\n" + expected, "", 1) == off.calls[0].prompt
        assert on.calls[0].prompt.count(expected) == 1


def test_d9_the_two_clamps_reach_different_sampler_options(copy_at):
    """The old mood-to-sampler path still carries the clamp: this is why the
    claim is a difference of differences and not the clamp effect alone."""
    pos = asyncio.run(_snapshot_after_clamp(copy_at, 0.8))
    neg = asyncio.run(_snapshot_after_clamp(copy_at, -0.8))
    a = _calls_for(pos, True).calls[0].options
    b = _calls_for(neg, True).calls[0].options
    assert a["temperature"] > b["temperature"] + 0.4
    assert a["top_p"] > b["top_p"]


def test_self_correction_retry_keeps_the_line(flag_on):
    """The retry reuses the first prompt, so the line is in both calls."""

    def reply(index, _prompt):
        return "As an AI language model I cannot." if index == 0 else "Okay, tell me."

    llm = RecordingLLM(reply=reply)
    plan = build_chat_plan({"mood": -0.8, "cortisol": 0.5}, "hello")
    _run_plan(plan, llm)
    assert len(llm.calls) >= 2
    assert all(_MOOD_STYLE_BRIEF in call.prompt for call in llm.calls)


# --- ledger ----------------------------------------------------------------------


def test_mood_row_lists_every_known_reader():
    readers = {r.partition("::")[2] for r in get_row("mood").readers}
    assert {
        "emotional_bias",
        "cortisol_tonic",
        "dopamine_tonic",
        "get_behavioral_directive",
        "get_emotion_label",
        "_bucket_relational_stance",
        "_prepended_affect_tag",
        "_refresh_global_controls_locked",
        "derive_speech_expression",
        "_mood_style_line",
    } <= readers
