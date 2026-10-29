"""NF-12, variant B: appraisal goal congruence from the signed valence of the
current user message (`cognitive/event_valence.py`) behind
`Config.AFFECT_USER_INPUT_ENABLED`. Deterministic, no model, no network.
"""

from __future__ import annotations

import asyncio
import json
import random
from itertools import pairwise
from pathlib import Path

import pytest

from app.cognitive import event_valence as ev_module
from app.cognitive.event_valence import event_valence
from app.config import AppSettings, Config
from evals.brainbench.baseline import defects
from evals.brainbench.baseline.defects import MOOD_TEXTS, SHUFFLE_SEEDS, _LiveRig

RECORDED = (
    Path(__file__).resolve().parents[2]
    / "docs/brain-v4/results/slice-02a/defect_baselines.json"
)

POSITIVE = (
    "Thank you so much, you really helped me and I love talking with you.",
    "I got the promotion today and I am so happy!",
    "That joke made me laugh for ten minutes.",
)
NEGATIVE = (
    "You are useless and I hate you, shut up you stupid bot.",
    "I just lost my job and I feel terrible about everything.",
    "Nobody ever listens to me and I am so tired of it.",
)


async def _trajectory(texts, start_mood: float) -> list[float]:
    rig = _LiveRig(start_mood=start_mood)
    try:
        moods = [rig.state.mood]
        for text in texts:
            await rig.turn(text)
            moods.append(rig.state.mood)
        return moods
    finally:
        rig.close()


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(Config, "AFFECT_USER_INPUT_ENABLED", True)


# 1. Flag off: slice 2a baseline unchanged -------------------------------------


def test_flag_defaults_off_and_switchboard_arm_is_runnable():
    from evals.brainbench.switchboard import ARMS, is_runnable

    assert AppSettings.model_fields["AFFECT_USER_INPUT_ENABLED"].default is False
    assert is_runnable(ARMS["+affect-input"])


STATIC_SECTIONS = (
    "scenario",
    "behavioral_signal",
    "baseline_pad_writers",
    "subscriber_counts",
)
LIVE_SECTIONS = ("mood_self_feed", "integrity_on_hostile_text")


def test_flag_off_matches_recorded_slice_02a_baseline():
    recorded = json.loads(RECORDED.read_text("utf-8"))
    assert Config.AFFECT_USER_INPUT_ENABLED is False
    data = asyncio.run(defects.collect())
    for name in STATIC_SECTIONS:
        assert defects.digest(data[name]) == defects.digest(recorded["data"][name])
    # The live sections start from persona and config values of the machine that
    # recorded them. If the untouched starting trust differs, this machine cannot
    # reproduce the recording, with or without this change (same on HEAD).
    start = defects.digest(data["integrity_on_hostile_text"]["hostile"][0])
    if start != defects.digest(
        recorded["data"]["integrity_on_hostile_text"]["hostile"][0]
    ):
        pytest.skip("live baseline sections not reproducible on this machine")
    for name in LIVE_SECTIONS:
        assert defects.digest(data[name]) == defects.digest(recorded["data"][name])
    assert (
        defects.envelope(data)["result_sha256"] == recorded["envelope"]["result_sha256"]
    )


# 2. Flag on: text direction reaches mood, text order matters ---------------------


def test_positive_text_raises_and_negative_text_lowers_mood(flag_on):
    up = asyncio.run(_trajectory(POSITIVE, 0.0))
    down = asyncio.run(_trajectory(NEGATIVE, 0.0))
    assert all(b > a for a, b in pairwise(up))
    assert up[-1] > 0.05
    assert all(b < a for a, b in pairwise(down))
    assert down[-1] < -0.05


def test_real_text_order_differs_from_every_seeded_shuffle(flag_on):
    real = asyncio.run(_trajectory(MOOD_TEXTS, 0.0))
    for seed in SHUFFLE_SEEDS:
        order = list(range(len(MOOD_TEXTS)))
        random.Random(seed).shuffle(order)
        shuffled = asyncio.run(_trajectory([MOOD_TEXTS[i] for i in order], 0.0))
        gap = max(abs(a - b) for a, b in zip(real, shuffled, strict=True))
        assert gap > 0.01, f"seed {seed}: trajectory ignored text order"


# 3. Mood is no longer a function of prior mood alone ---------------------------


def test_start_moods_converge_toward_user_valence(flag_on):
    texts = POSITIVE * 4
    low = asyncio.run(_trajectory(texts, -0.6))
    high = asyncio.run(_trajectory(texts, 0.6))
    assert abs(high[0] - low[0]) == pytest.approx(1.2)
    assert abs(high[-1] - low[-1]) < abs(high[0] - low[0]) * 0.5
    assert low[-1] > low[0] and high[-1] > 0
    # Pure decay would keep mood_t / mood_(t-1) constant; a user-driven mood
    # changes that ratio as the text changes.
    ratios = [b / a for a, b in pairwise(high) if a]
    assert max(ratios) - min(ratios) > 0.01


def test_same_start_mood_different_text_diverges(flag_on):
    up = asyncio.run(_trajectory(POSITIVE, 0.3))
    down = asyncio.run(_trajectory(NEGATIVE, 0.3))
    assert up[-1] > 0.3 > down[-1]


def test_flag_off_mood_still_ignores_text():
    up = asyncio.run(_trajectory(POSITIVE, 0.0))
    down = asyncio.run(_trajectory(NEGATIVE, 0.0))
    assert up == down == [0.0] * (len(POSITIVE) + 1)


# 4. Scorer ---------------------------------------------------------------------


def test_sign():
    assert event_valence("I am happy") > 0
    assert event_valence("I am sad") < 0
    assert event_valence("The meeting is at three") == 0.0


def test_negation_flips_and_softens():
    pos, neg = event_valence("I am happy"), event_valence("I am not happy")
    assert neg < 0 < pos
    assert abs(neg) < abs(pos)
    assert event_valence("I don't like this, I'm not sad") >= 0
    assert event_valence("it isn't terrible") > 0


def test_negation_does_not_cross_a_clause():
    assert event_valence("Not today, I am happy") == event_valence("I am happy")


def test_intensifier_and_dampener():
    base = event_valence("I am happy")
    assert event_valence("I am very happy") > base
    assert event_valence("I am slightly happy") < base
    assert event_valence("I am very sad") < event_valence("I am sad")


def test_phrase_overrides_word_reading():
    assert event_valence("I cannot wait") > 0
    assert event_valence("shut up") < 0


def test_mixed_cues_partially_cancel():
    mixed = event_valence("I am happy but also sad")
    assert abs(mixed) < max(
        abs(event_valence("I am happy")), abs(event_valence("I am sad"))
    )


def test_bounds_and_empty_input():
    spam = "love " * 500
    assert event_valence(spam) <= 1.0
    assert event_valence("hate " * 500) >= -1.0
    for empty in ("", "   ", "...!!!"):
        assert event_valence(empty) == 0.0
    assert event_valence(None) == 0.0  # type: ignore[arg-type]


def test_deterministic_and_case_insensitive():
    assert event_valence("I LOVE this") == event_valence("i love this")
    assert event_valence("I love this") == event_valence("I love this")
    assert event_valence("I don’t love this") == event_valence("I don't love this")


def test_scorer_reads_no_agent_state():
    """The module imports only the standard library and takes only text."""
    import ast
    import inspect

    tree = ast.parse(Path(ev_module.__file__).read_text("utf-8"))
    imported = {
        (n.module if isinstance(n, ast.ImportFrom) else a.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert imported <= {"__future__", "math", "re"}
    assert list(inspect.signature(event_valence).parameters) == ["text"]


def test_scorer_ignores_agent_mood(flag_on):
    """Same text scored with the agent at opposite moods gives one value."""
    rig = _LiveRig(start_mood=0.9)
    try:
        high = event_valence(MOOD_TEXTS[3])
        rig.state.mood = -0.9
        assert event_valence(MOOD_TEXTS[3]) == high
    finally:
        rig.close()
