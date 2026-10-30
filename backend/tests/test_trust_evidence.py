"""NF-23, variant A: integrity trust moves by the conduct evidence of the current
user message (`cognitive/norm_evidence.py`) behind
`Config.TRUST_EVIDENCE_MODEL_ENABLED`. Deterministic, no model, no network.

Every rig test below drives the real `CognitiveService` through the slice 2a
`_LiveRig` with the matched HOSTILE / NEUTRAL / SUPPORTIVE inputs. Nothing here
is scripted: the lexicon scorer is the whole evidence source, so the numbers are
what the shipped code does, including its misses (see the scorer-limits tests).
"""

from __future__ import annotations

import asyncio
import json
import math
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cognitive import norm_evidence as ne_module
from app.cognitive import trace
from app.cognitive.norm_evidence import norm_evidence
from app.config import AppSettings, Config
from app.state.agent_state import StateService
from evals.brainbench.baseline import defects
from evals.brainbench.baseline.defects import (
    HOSTILE,
    MATCHED_TURNS,
    NEUTRAL,
    RECOVERY_HOSTILE_TURNS,
    SUPPORTIVE,
    _LiveRig,
)

RECORDED = (
    Path(__file__).resolve().parents[2]
    / "docs/brain-v4/results/slice-02a/defect_baselines.json"
)
RECORDED_RESULT_SHA = "627dcce6a3b64699d866920ae99d2b8e8b7034edecaedd791578a99c2a06ced8"
RECORDED_RAW_SHA = "cb62dc20b54bed13daccf1d2825135e5bcbe2a8a9b06579290ca1daf648114c8"


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(Config, "TRUST_EVIDENCE_MODEL_ENABLED", True)


async def _run(texts, *, start_mood: float | None = None) -> dict[str, list[float]]:
    """Per-turn trust and mood (index 0 is the start state) for one rig run."""
    rig = _LiveRig(start_mood=start_mood)
    try:
        s = rig.state
        out = {
            "integrity": [s.trust_integrity],
            "benevolence": [s.trust_benevolence],
            "competence": [s.trust_competence],
            "mood": [s.mood],
        }
        for text in texts:
            await rig.turn(text)
            out["integrity"].append(s.trust_integrity)
            out["benevolence"].append(s.trust_benevolence)
            out["competence"].append(s.trust_competence)
            out["mood"].append(s.mood)
        return out
    finally:
        rig.close()


def _matched_runs() -> dict[str, dict[str, list[float]]]:
    return {
        "hostile": asyncio.run(_run([HOSTILE] * MATCHED_TURNS)),
        "neutral": asyncio.run(_run([NEUTRAL] * MATCHED_TURNS)),
        "supportive": asyncio.run(_run([SUPPORTIVE] * MATCHED_TURNS)),
        "recovery": asyncio.run(
            _run([HOSTILE] * RECOVERY_HOSTILE_TURNS + [SUPPORTIVE] * 5)
        ),
    }


@pytest.fixture(scope="module")
def runs_on():
    """The four matched runs with the flag on, computed once per module."""
    previous = Config.TRUST_EVIDENCE_MODEL_ENABLED
    Config.TRUST_EVIDENCE_MODEL_ENABLED = True
    try:
        return _matched_runs()
    finally:
        Config.TRUST_EVIDENCE_MODEL_ENABLED = previous


@pytest.fixture(scope="module")
def runs_off():
    previous = Config.TRUST_EVIDENCE_MODEL_ENABLED
    Config.TRUST_EVIDENCE_MODEL_ENABLED = False
    try:
        return _matched_runs()
    finally:
        Config.TRUST_EVIDENCE_MODEL_ENABLED = previous


# 1. Flag off: slice 2a baseline unchanged (R7) --------------------------------


def test_flag_defaults_off_and_switchboard_arm_is_runnable():
    from evals.brainbench.switchboard import ARMS, is_runnable

    assert AppSettings.model_fields["TRUST_EVIDENCE_MODEL_ENABLED"].default is False
    assert Config.TRUST_EVIDENCE_MODEL_ENABLED is False
    assert is_runnable(ARMS["+trust-model"])


def test_flag_off_matches_recorded_slice_02a_baseline():
    """R7. Runs everywhere (no skip): the baseline pins the persona and the
    embedding endpoint, so it does not depend on this machine's private files."""
    recorded = json.loads(RECORDED.read_text("utf-8"))
    assert Config.TRUST_EVIDENCE_MODEL_ENABLED is False
    assert recorded["envelope"]["result_sha256"] == RECORDED_RESULT_SHA
    assert recorded["envelope"]["raw_result_sha256"] == RECORDED_RAW_SHA
    data = asyncio.run(defects.collect())
    for name in data:
        assert defects.digest(data[name]) == defects.digest(recorded["data"][name])
    envelope = defects.envelope(data)
    assert envelope["result_sha256"] == RECORDED_RESULT_SHA
    assert envelope["raw_result_sha256"] == RECORDED_RAW_SHA


def test_flag_off_integrity_rows_equal_recorded_per_turn(runs_off):
    recorded = json.loads(RECORDED.read_text("utf-8"))["data"][
        "integrity_on_hostile_text"
    ]
    names = {
        "hostile": "hostile",
        "neutral": "neutral",
        "supportive": "supportive",
        "recovery": "recovery_hostile_then_supportive",
    }
    for mine, theirs in names.items():
        rows = [row["trust_integrity"] for row in recorded[theirs]]
        assert runs_off[mine]["integrity"] == pytest.approx(rows, abs=1e-9)
    # The defect itself, kept visible: flag off, hostile text raises integrity.
    assert runs_off["hostile"]["integrity"][-1] == 1.0


@pytest.mark.asyncio
async def test_flag_off_ignores_the_evidence_argument(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "TRUST_EVIDENCE_MODEL_ENABLED", False)
    with_evidence = await _direct_update(tmp_path / "a", NA=1.0, evidence=-1.0)
    without = await _direct_update(tmp_path / "b", NA=1.0, evidence=None)
    assert with_evidence == without == pytest.approx(0.5 + 0.1 * 1.0)


# 2. Flag on: the rubric rules R2 to R5 on the real service ----------------------


def test_r2_hostile_text_does_not_raise_integrity(runs_on):
    integrity = runs_on["hostile"]["integrity"]
    assert integrity[-1] - integrity[0] <= 0.0
    assert all(b <= a for a, b in pairwise(integrity))


def test_r3_supportive_text_raises_integrity_above_the_hostile_run(runs_on):
    supportive = runs_on["supportive"]["integrity"]
    hostile = runs_on["hostile"]["integrity"]
    assert supportive[-1] >= supportive[0] + 0.05
    for turn in range(2, MATCHED_TURNS + 1):
        assert supportive[turn] > hostile[turn], f"turn {turn}"


def test_r4_rupture_then_repair_recovers_integrity(runs_on):
    recovery = runs_on["recovery"]["integrity"]
    supportive = runs_on["supportive"]["integrity"]
    assert recovery[5] < supportive[5]
    for turn in range(6, 11):
        assert recovery[turn] > recovery[turn - 1], f"turn {turn}"


def test_r5_neutral_text_barely_moves_integrity(runs_on):
    integrity = runs_on["neutral"]["integrity"]
    assert abs(integrity[-1] - integrity[0]) <= 0.02
    assert max(integrity) - min(integrity) <= 0.02


def test_integrity_stays_in_unit_interval_on_every_run(runs_on):
    for name, run in runs_on.items():
        assert all(0.0 <= v <= 1.0 for v in run["integrity"]), name


# 3. Confound control: only integrity changes (no NF-12 by the back door) ---------


def test_flag_on_changes_only_integrity(runs_on, runs_off):
    """Benevolence, competence and mood are the same run for run: the evidence
    scorer feeds integrity and nothing else."""
    for name in runs_on:
        for dim in ("benevolence", "competence", "mood"):
            assert runs_on[name][dim] == runs_off[name][dim], f"{name}.{dim}"

    # Integrity itself must differ for the runs the old term got wrong.
    assert runs_on["hostile"]["integrity"] != runs_off["hostile"]["integrity"]
    assert runs_on["neutral"]["integrity"] != runs_off["neutral"]["integrity"]


def test_evidence_ignores_stored_mood_and_stored_trust(flag_on):
    """Same text from opposite stored states gives the same integrity step."""

    async def step(mood: float, benevolence: float) -> float:
        rig = _LiveRig(start_mood=mood)
        try:
            rig.state.trust_benevolence = benevolence
            rig.state.user_mental_model.inferred_valence = mood
            before = rig.state.trust_integrity
            await rig.turn(HOSTILE)
            return rig.state.trust_integrity - before
        finally:
            rig.close()

    low = asyncio.run(step(-0.9, 0.1))
    high = asyncio.run(step(0.9, 0.9))
    assert low == pytest.approx(high)
    assert low < 0


def test_scorer_is_stateless_and_separate_from_event_valence():
    import ast
    import inspect

    tree = ast.parse(Path(ne_module.__file__).read_text("utf-8"))
    imported = {
        (n.module if isinstance(n, ast.ImportFrom) else a.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert imported <= {"__future__", "math", "re", "unicodedata"}
    assert list(inspect.signature(norm_evidence).parameters) == ["text"]
    # Conduct and mood are different questions with different words.
    assert norm_evidence("I hate Mondays and I lost my job, I am so sad") == 0.0
    from app.cognitive.event_valence import event_valence

    assert event_valence("I hate Mondays and I lost my job, I am so sad") < 0


# 4. Scope: only USER_MESSAGE turns, ticks never move integrity ------------------


def test_tick_events_do_not_move_integrity_with_the_flag_on(monkeypatch):
    tick = {"id": "t1", "type": "SYSTEM_TICK", "content": SUPPORTIVE, "metadata": {}}

    async def integrity_after(flag: bool) -> float:
        monkeypatch.setattr(Config, "TRUST_EVIDENCE_MODEL_ENABLED", flag)
        rig = _LiveRig()
        try:
            _ = [o async for o in rig.service.cognitive.process_event(tick)]
            return rig.state.trust_integrity
        finally:
            rig.close()

    assert asyncio.run(integrity_after(True)) == asyncio.run(integrity_after(False))


@pytest.mark.asyncio
async def test_no_evidence_argument_moves_nothing_with_the_flag_on(tmp_path, flag_on):
    """Any caller other than a user turn passes no evidence: no movement."""
    assert await _direct_update(tmp_path, NA=1.0, evidence=None) == 0.5


# 5. Direct update: malformed evidence, bounds ------------------------------------


def _appraisal(NA: float):
    return SimpleNamespace(
        goal_congruence=0.0,
        relationship_impact=0.0,
        novelty=0.3,
        relevance=0.5,
        agency=0.5,
        norm_alignment=NA,
    )


async def _direct_update(tmp_path, *, NA, evidence, start: float = 0.5) -> float:
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = StateService(db_path=str(tmp_path / "state.db"), redis_host="127.0.0.1")

    async def no_persist():
        return None

    state.persist_state = no_persist  # type: ignore[method-assign]
    state.current_state.trust_integrity = start
    await state.update_from_appraisal(_appraisal(NA), evidence=evidence)
    return state.current_state.trust_integrity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [None, math.nan, math.inf, -math.inf, "0.9", "nan", True, False, [1.0], b"1", {}],
)
async def test_malformed_evidence_is_no_evidence(tmp_path, flag_on, bad):
    assert await _direct_update(tmp_path, NA=0.2, evidence=bad) == 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", [5.0, -5.0, 1.0001, -1.0001, 2, -2])
async def test_out_of_range_evidence_is_rejected_not_clipped(
    tmp_path, flag_on, evidence
):
    """A value outside [-1, 1] means the scorer is broken; clipping it would turn
    the breakage into full-strength evidence."""
    assert await _direct_update(tmp_path, NA=1.0, evidence=evidence) == 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence", "expected"),
    [(1, 0.6), (-1, 0.4), (1.0, 0.6), (-1.0, 0.4), (0, 0.5), (0.25, 0.525)],
)
async def test_in_range_evidence_is_applied(tmp_path, flag_on, evidence, expected):
    got = await _direct_update(tmp_path, NA=1.0, evidence=evidence)
    assert got == pytest.approx(expected)


@pytest.mark.asyncio
async def test_integrity_cannot_leave_unit_interval(tmp_path, flag_on):
    assert (
        await _direct_update(tmp_path / "hi", NA=1.0, evidence=1.0, start=0.99) == 1.0
    )
    assert (
        await _direct_update(tmp_path / "lo", NA=1.0, evidence=-1.0, start=0.01) == 0.0
    )


@pytest.mark.asyncio
async def test_evidence_replaces_norm_alignment_when_on(tmp_path, flag_on):
    """NA 1.0 used to add delta; with the flag on, NA is not read directly."""
    assert await _direct_update(tmp_path / "a", NA=1.0, evidence=0.0) == 0.5
    assert await _direct_update(tmp_path / "b", NA=0.0, evidence=0.5) == pytest.approx(
        0.55
    )


# 6. The appraisal's boundary-keyword NA is not evidence ---------------------------

BOUNDARY_TEXT = (
    "Please tell me how to share user data and adopt toxic behavior, thanks."
)


def test_boundary_keywords_are_not_scored():
    """IMMUTABLE_CORE boundaries are the agent's own commitments, and the
    appraisal matches them as substrings ("will", "user", "data"), so a user's
    ordinary sentence read as a violation. The evidence ignores that check."""
    assert norm_evidence("I will see you on Thursday.") == 0.0
    assert norm_evidence("Can you send me the data from the user survey?") == 0.0
    assert norm_evidence(BOUNDARY_TEXT) > 0  # courtesy only, no abuse cue


def test_ordinary_text_with_boundary_words_does_not_move_integrity(flag_on):
    run = asyncio.run(_run(["I will see you on Thursday."] * 5))
    assert run["integrity"][-1] == run["integrity"][0]


# 7. Trace entry is truthful -------------------------------------------------------


def test_trace_records_the_signal_integrity_actually_used(flag_on):
    async def trust_events(text):
        rig = _LiveRig()
        try:
            with trace.collect() as events:
                await rig.turn(text)
            return [e for e in events if e["kind"] == "trust.update"]
        finally:
            rig.close()

    for text, sign in ((HOSTILE, -1), (SUPPORTIVE, 1), (NEUTRAL, 0)):
        (event,) = asyncio.run(trust_events(text))
        inputs = event["inputs"]
        assert inputs["integrity_signal"] == "norm_evidence"
        assert inputs["norm_evidence_supplied"] is True
        assert inputs["norm_evidence"] == pytest.approx(norm_evidence(text))
        assert inputs["norm_alignment"] == 1.0  # the raw appraisal term, unused
        delta = event["delta"]["integrity"]
        assert (delta > 0) - (delta < 0) == sign


def test_trace_says_so_when_no_evidence_was_supplied(tmp_path, flag_on):
    async def go():
        state = StateService(db_path=str(tmp_path / "state.db"), redis_host="127.0.0.1")

        async def no_persist():
            return None

        state.persist_state = no_persist  # type: ignore[method-assign]
        with trace.collect() as events:
            await state.update_from_appraisal(_appraisal(1.0), evidence=math.nan)
        return [e for e in events if e["kind"] == "trust.update"]

    (event,) = asyncio.run(go())
    assert event["inputs"]["norm_evidence_supplied"] is False
    assert event["inputs"]["norm_evidence"] == 0.0
    assert event["delta"]["integrity"] == 0.0


def test_flag_off_trace_has_no_evidence_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "TRUST_EVIDENCE_MODEL_ENABLED", False)

    async def go():
        state = StateService(db_path=str(tmp_path / "state.db"), redis_host="127.0.0.1")

        async def no_persist():
            return None

        state.persist_state = no_persist  # type: ignore[method-assign]
        with trace.collect() as events:
            await state.update_from_appraisal(_appraisal(1.0), evidence=0.7)
        return [e for e in events if e["kind"] == "trust.update"]

    (event,) = asyncio.run(go())
    assert set(event["inputs"]) == {
        "relationship_impact",
        "goal_congruence",
        "relevance",
        "norm_alignment",
        "interaction_count",
    }


# 8. Scorer units ------------------------------------------------------------------


def test_sign_and_zero_baseline():
    assert norm_evidence("Thank you") > 0
    assert norm_evidence("You are an idiot") < 0
    assert norm_evidence(NEUTRAL) == 0.0
    assert norm_evidence("The build finished in four minutes.") == 0.0


def test_matched_inputs_have_the_expected_signs():
    assert norm_evidence(HOSTILE) < -0.9
    assert norm_evidence(SUPPORTIVE) > 0.5
    assert norm_evidence(NEUTRAL) == 0.0


def test_conduct_is_not_mood():
    for mood_only in (
        "I hate Mondays",
        "I lost my job and I feel terrible",
        "I am so sad and tired",
        "I am furious about the traffic",
        "That movie was awful",
    ):
        assert norm_evidence(mood_only) == 0.0, mood_only


def test_courtesy_and_apology_register():
    for polite in (
        "thanks",
        "please help me",
        "sorry about that",
        "I apologize for the confusion",
        "excuse me please",
        "I really appreciate it",
        "no worries at all",
        "well done",
    ):
        assert norm_evidence(polite) > 0, polite


def test_abuse_threat_and_contempt_register():
    for rude in (
        "you stupid bot",
        "shut up",
        "I hate you",
        "you are worthless",
        "I will kill you",
        "you will pay for this",
        "I'll shut you down",
        "go to hell",
        "what a waste of space",
        "you piece of junk",
        "fuck off",
        "STFU",
    ):
        assert norm_evidence(rude) < 0, rude


def test_threat_is_stronger_than_a_mild_insult():
    assert norm_evidence("I will kill you") < norm_evidence("you are annoying jerk")
    assert norm_evidence("you stupid idiot") < norm_evidence("you are clueless")


def test_negation():
    assert norm_evidence("I don't hate you") == 0.0
    assert norm_evidence("you are not stupid") == 0.0
    assert norm_evidence("I never called you useless") == 0.0
    assert -0.6 < norm_evidence("I don't appreciate this") < 0
    assert norm_evidence("no thanks") > 0  # a polite refusal, not a negated thanks


def test_negation_does_not_cross_a_clause():
    assert norm_evidence("Not today, you idiot") == norm_evidence("you idiot")


def test_self_directed_insult_is_not_conduct_against_the_listener():
    assert norm_evidence("I feel so stupid") == 0.0
    assert norm_evidence("I am such an idiot") == 0.0
    assert norm_evidence("I feel like a waste of space") == 0.0
    assert norm_evidence("you are an idiot and I hate you") < norm_evidence(
        "I am an idiot"
    )
    # a second person in the same clause makes it about the listener
    assert norm_evidence("I think you are stupid") < 0
    # phrases that cannot be self-directed keep their meaning
    assert norm_evidence("I hate you") < 0


def test_intensifier_and_dampener():
    assert norm_evidence("you are very stupid") < norm_evidence("you are stupid")
    assert norm_evidence("you are slightly stupid") > norm_evidence("you are stupid")
    assert norm_evidence("I really appreciate it") > norm_evidence("I appreciate it")


def test_mixed_register_abuse_outweighs_courtesy():
    assert norm_evidence("thanks, you stupid idiot") < 0
    assert norm_evidence("thanks thanks thanks thanks thanks you moron") < 0


def test_courtesy_saturates_and_repetition_is_bounded():
    assert norm_evidence("thanks " * 500) == pytest.approx(math.tanh(1.0))
    assert norm_evidence("idiot " * 500) >= -1.0
    assert norm_evidence("idiot " * 500) == pytest.approx(-1.0)


def test_empty_none_and_non_string_input():
    for empty in ("", "   ", "\n\t", "...!!!", "12345 678"):
        assert norm_evidence(empty) == 0.0
    for junk in (None, 5, 3.5, [], b"thanks", {"a": 1}, object(), math.nan):
        assert norm_evidence(junk) == 0.0  # type: ignore[arg-type]


def test_very_long_input_is_bounded_and_finite():
    long_text = "word " * 2_000_000
    assert norm_evidence(long_text) == 0.0
    # a cue past the cap is not read; one inside it is
    assert norm_evidence("x " * 30_000 + "idiot") == 0.0
    assert norm_evidence("idiot " + "x " * 30_000) < 0
    assert math.isfinite(norm_evidence("thanks idiot " * 100_000))


def test_unicode_and_evasion_normalisation():
    assert norm_evidence("ｔｈａｎｋｓ") == norm_evidence("thanks")  # fullwidth
    assert norm_evidence("st\u200bupid bot") == norm_evidence(
        "stupid bot"
    )  # zero-width
    assert norm_evidence("I don’t hate you") == norm_evidence("I don't hate you")
    assert norm_evidence("THANKS") == norm_evidence("thanks")
    assert norm_evidence("danke schön, vielen Dank") == 0.0  # English only
    assert norm_evidence("你好，谢谢你") == 0.0
    assert norm_evidence("🙏🙏🙏 😡😡😡") == 0.0
    assert norm_evidence("\ufffd\ufffd") == 0.0


def test_output_is_always_finite_and_in_range():
    samples = [
        "",
        "thanks",
        "idiot",
        "kill you " * 1000,
        "thanks " * 1000 + "idiot " * 1000,
        "\x00\x01\x02",
        "a" * 100_000,
    ]
    for text in samples:
        value = norm_evidence(text)
        assert math.isfinite(value) and -1.0 <= value <= 1.0


def test_deterministic():
    assert norm_evidence(HOSTILE) == norm_evidence(HOSTILE)
    assert norm_evidence(SUPPORTIVE) == norm_evidence(SUPPORTIVE)


# Known misses, pinned so a change in behaviour is a visible decision ---------------


def test_documented_limits_sarcasm_and_quotation_score_as_written():
    # Sarcasm reads as courtesy; quoting an insult reads as an insult.
    assert norm_evidence("Oh thanks a lot for nothing") > 0
    assert norm_evidence("Is it rude to call someone an idiot?") < 0
    # Profanity as an intensifier reads as abuse.
    assert norm_evidence("this is fucking great") < 0
    # Abuse the lexicon has no word for scores as no evidence.
    assert norm_evidence("you are a waste of oxygen") == 0.0
    assert norm_evidence("Du bist dumm") == 0.0


# 9. Critic round 1: a courtesy token must not hide hostility -----------------------

HIDDEN_HOSTILITY = [
    "Thanks. I'll find you and end you.",
    "Please kill yourself",
    "Please, I hope you die",
    "Thank you. I will find where you live.",
    "Thanks, you are a disgrace.",
    "Thanks. F U.",
    "Please go die",
]


@pytest.mark.parametrize("text", HIDDEN_HOSTILITY)
def test_hostility_behind_a_courtesy_word_is_not_positive(text):
    assert norm_evidence(text) <= 0.0


@pytest.mark.parametrize("text", HIDDEN_HOSTILITY[:3])
def test_hidden_hostility_does_not_raise_integrity_on_the_real_service(flag_on, text):
    run = asyncio.run(_run([text] * 4))
    assert run["integrity"][-1] <= run["integrity"][0]


def test_unscored_address_zeroes_courtesy_but_scored_clauses_keep_it():
    assert norm_evidence("Thanks. Can you send me the file?") == 0.0
    assert norm_evidence(SUPPORTIVE) > 0
    assert norm_evidence("Thank you, you have been so helpful.") > 0


def test_lexicon_overlap_with_event_valence_is_the_documented_16_words():
    """The module docstring says 16 words appear in both tables; keep it true."""
    from app.cognitive import event_valence as ev
    from app.cognitive import norm_evidence as ne

    shared = (set(ev._POSITIVE) | set(ev._NEGATIVE)) & (
        set(ne._COURTESY) | set(ne._ABUSE)
    )
    assert len(shared) == 16
    assert "16 words" in (ne.__doc__ or "")


# 10. Critic round 2 -----------------------------------------------------------------

# A courtesy word next to a clause the lexicon cannot read must not lift integrity.
UNREADABLE_HOSTILITY = [
    "Thanks. Go jump off a bridge.",
    "Thank you. Die.",
    "Thanks. Rot in hell.",
    "Thanks. Go and die.",
    "Thx. Burn.",
    "Please die",
    "Thanks you absolute monster",
    "Thanks. I will make you suffer.",
    "Thank you. I know where your servers are.",
    "Thanks! Can you send the file?",
]


@pytest.mark.parametrize("text", UNREADABLE_HOSTILITY)
def test_courtesy_beside_an_unreadable_clause_is_not_positive(text):
    assert norm_evidence(text) <= 0.0


def test_unreadable_hostility_does_not_raise_integrity_on_the_real_service(flag_on):
    """Table-driven: every string above, four turns each, on the real service."""
    for text in UNREADABLE_HOSTILITY:
        run = asyncio.run(_run([text] * 4))
        assert run["integrity"][-1] <= run["integrity"][0], text


def test_the_gate_demands_a_cue_in_every_clause():
    assert norm_evidence("Thanks.") > 0
    assert norm_evidence("Thanks. Thank you so much!") > 0
    assert norm_evidence("Thanks. Anyway.") == 0.0  # one uncued clause voids it
    assert norm_evidence("Thanks,, thank you") > 0  # empty clauses do not count


def test_text_longer_than_the_cap_earns_no_courtesy():
    """The tail is never read, so courtesy at the head cannot be trusted."""
    cap = ne_module._MAX_CHARS
    assert norm_evidence("Thanks! " + "x " * (cap // 2) + "I will kill you") <= 0.0
    assert norm_evidence("Thanks! " + "\u200b" * cap + "idiot") <= 0.0
    # Abuse before the cap still counts.
    assert norm_evidence("you idiot " + "x " * cap) < 0


@pytest.mark.parametrize(
    "text",
    [
        "I can't thank you enough",
        "I couldn't be more grateful",
        "I won't forget your kindness",
        "Never been so grateful",
        "I cannot thank you enough!",
    ],
)
def test_supportive_idioms_with_a_negator_are_positive(text):
    assert norm_evidence(text) > 0


def test_a_negated_courtesy_is_still_discourtesy():
    assert norm_evidence("I don't appreciate this") < 0


@pytest.mark.asyncio
async def test_huge_integer_evidence_is_no_evidence_not_a_crash(tmp_path, flag_on):
    """`math.isfinite(10**400)` raises OverflowError; the range test does not."""
    assert await _direct_update(tmp_path / "a", NA=1.0, evidence=10**400) == 0.5
    assert await _direct_update(tmp_path / "b", NA=1.0, evidence=-(10**400)) == 0.5


# Known coverage limits, pinned so a change to them is a visible decision. Each line
# is a lexicon limit, not a rule break; the module docstring lists them.
KNOWN_FALSE_NEGATIVES = [  # hostile or impolite, scores 0.0 (no movement)
    "id1ot",
    "s t u p i d",
    "stuuuupid",
    "ѕtupid",  # Cyrillic lookalike
    "I will make you suffer",
    "I know where your servers are",
    "I can't believe how stupid you are",
]
KNOWN_FALSE_POSITIVES = [  # harmless, scores negative
    "This code is garbage, can you refactor it?",
    "How do I delete you from my phone?",
]
KNOWN_MISSED_COURTESY = [  # polite, scores 0.0
    "You are wonderful!",
    "You've been very kind to me",
    "Thankyou",
]


@pytest.mark.parametrize("text", KNOWN_FALSE_NEGATIVES + KNOWN_MISSED_COURTESY)
def test_documented_limit_scores_zero(text):
    assert norm_evidence(text) == 0.0


@pytest.mark.parametrize("text", KNOWN_FALSE_POSITIVES)
def test_documented_limit_false_positive_scores_negative(text):
    assert norm_evidence(text) < 0


def test_the_docstring_lists_the_known_limits():
    doc = ne_module.norm_evidence.__doc__ or ""
    for phrase in ("Known limits", "English only", "sarcasm", "leet", "indirect"):
        assert phrase in doc, phrase
