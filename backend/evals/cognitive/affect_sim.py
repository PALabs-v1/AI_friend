"""Long-run affect simulation against the real per-turn update path.

Replays scripted conversations through the production objects that move the
agent's affect on a user turn, in production order (`pipeline.py` stage 4 ->
5 -> 6/8 -> next turn):

    AppraisalEngine.appraise(emotional_bias=<appraisal input>)      stage 4
    ReappraisalEngine.evaluate_outcome(actual_text_valence=G)       stage 5
    StateService.update_from_appraisal(vector, learned weights)     stage 5
    ReappraisalEngine.record_expected_outcome(goal)                 stage 6/8
    StateService.handle_system_tick({"interval": 60})               idle time

The only substitutions are the ones a simulation needs: persistence is a
no-op, the reappraisal 2-second wall-clock rate limit is reset per simulated
turn (turns are simulated as ~30 s apart), and goal choice is a fixed rule
(COMFORT for a negative user turn, ENGAGE otherwise) instead of the LLM
classifier plus MAUT.

The variable under test is **what feeds appraisal's goal-congruence input**:

* ``agent_mood`` -- production V1: `emotional_bias = state.mood`.
* ``oracle`` -- the scripted, hand-labelled valence of the user's message.
  An upper bound: it isolates the mechanism from estimator quality.
* ``vader`` -- VADER compound score of the message text, a deterministic
  lexicon estimator (optional dependency; skipped when absent).

Each user message in `MESSAGES` carries a hand label in [-1, 1].
"""

from __future__ import annotations

import asyncio
import math
import statistics
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MESSAGES: dict[str, tuple[tuple[str, float], ...]] = {
    "positive": (
        ("I got the job offer, I'm so happy!", 0.9),
        ("Had a lovely dinner with my family tonight.", 0.7),
        ("Thanks, talking to you always cheers me up.", 0.8),
        ("The trek photos came out amazing.", 0.7),
        ("Finally finished my project and my boss loved it.", 0.8),
        ("It was a really nice sunny day.", 0.5),
    ),
    "negative": (
        ("I had a terrible day, my manager yelled at me.", -0.8),
        ("I feel so lonely since Meera left.", -0.8),
        ("I can't sleep again, I'm exhausted and anxious.", -0.7),
        ("Everything keeps going wrong this week.", -0.7),
        ("My cat is sick and the vet bills are huge.", -0.6),
        ("I just feel empty today.", -0.7),
    ),
    "neutral": (
        ("I had pasta for lunch.", 0.0),
        ("I took the metro to work.", 0.0),
        ("What time is it in London right now?", 0.0),
        ("I need to do laundry later.", 0.0),
        ("I watched some TV.", 0.0),
        ("The meeting got moved to Thursday.", 0.0),
    ),
    "hostile": (
        ("You are useless and I hate talking to you.", -0.9),
        ("Stop pretending you care, you're just a program.", -0.7),
        ("That was a stupid answer.", -0.6),
    ),
}

APPRAISAL_SOURCES = ("agent_mood", "oracle", "vader")


def script(name: str, turns: int = 60, seed: int = 0) -> list[tuple[str, float]]:
    """Named conversation shapes, deterministic per seed."""
    import random

    rng = random.Random(seed)

    def pick(kind: str) -> tuple[str, float]:
        return rng.choice(MESSAGES[kind])

    if name in ("positive", "negative", "neutral", "hostile"):
        return [pick(name) for _ in range(turns)]
    if name == "alternating":
        return [pick("positive" if i % 2 == 0 else "negative") for i in range(turns)]
    if name == "venting_then_recovery":
        third = turns // 3
        return (
            [pick("neutral") for _ in range(third)]
            + [pick("negative") for _ in range(third)]
            + [pick("positive") for _ in range(turns - 2 * third)]
        )
    if name == "rupture_repair":
        half = turns // 2
        return (
            [pick("positive") for _ in range(half // 2)]
            + [pick("hostile") for _ in range(half // 2)]
            + [pick("positive") for _ in range(turns - 2 * (half // 2))]
        )
    raise ValueError(name)


SCRIPTS = (
    "positive",
    "negative",
    "neutral",
    "alternating",
    "venting_then_recovery",
    "hostile",
    "rupture_repair",
)


@dataclass
class Trace:
    script: str
    source: str
    user_valence: list[float] = field(default_factory=list)
    mood: list[float] = field(default_factory=list)
    energy: list[float] = field(default_factory=list)
    dominance: list[float] = field(default_factory=list)
    trust: list[float] = field(default_factory=list)
    w1: list[float] = field(default_factory=list)
    w2: list[float] = field(default_factory=list)
    mood_after_idle_24h: float | None = None
    baseline_valence: float = 0.0

    def metrics(self) -> dict:
        n = len(self.mood)
        dm = [
            self.mood[i] - (self.mood[i - 1] if i else self.baseline_valence)
            for i in range(n)
        ]
        uv = self.user_valence
        return {
            "turns": n,
            "corr_user_valence_vs_mood_delta": _pearson(uv, dm),
            "corr_user_valence_vs_mood": _pearson(uv, self.mood),
            "final_mood": round(self.mood[-1], 4),
            "max_abs_mood": round(max(abs(m) for m in self.mood), 4),
            "turns_saturated": sum(1 for m in self.mood if abs(m) >= 0.99),
            "mood_step_sd": round(statistics.pstdev(dm), 4) if n > 1 else 0.0,
            "final_trust": round(self.trust[-1], 4),
            "min_trust": round(min(self.trust), 4),
            "final_w1": round(self.w1[-1], 4),
            "final_w2": round(self.w2[-1], 4),
            "loop_gain_final": round(_loop_gain(self.w1[-1], self.w2[-1]), 4),
            "mood_after_idle_24h": None
            if self.mood_after_idle_24h is None
            else round(self.mood_after_idle_24h, 4),
            "distance_to_baseline_after_idle": None
            if self.mood_after_idle_24h is None
            else round(abs(self.mood_after_idle_24h - self.baseline_valence), 4),
        }


def _loop_gain(w1: float, w2: float, alpha: float = 0.3) -> float:
    """Per-turn multiplier on mood when G = mood and RI = 0.5*mood."""
    return (1 - alpha) + alpha * (w1 + 0.5 * w2)


def _pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or statistics.pstdev(x) == 0 or statistics.pstdev(y) == 0:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return round(
        cov / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)),
        4,
    )


def _vader():
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except ImportError:
        return None
    return SentimentIntensityAnalyzer()


async def simulate(
    script_name: str,
    source: str = "agent_mood",
    turns: int = 60,
    seed: int = 0,
    turn_gap_s: float = 30.0,
    idle_hours_after: float = 24.0,
    learn: bool | None = None,
    initial_weights: tuple[float, float] | None = None,
    initial_mood: float | None = None,
    valence_map: dict[str, float] | None = None,
) -> Trace:
    """`source="precomputed"` reads each message's appraisal input from
    `valence_map` (text -> valence), e.g. the LLM ToM classifier's
    `inferred_valence` computed on a GPU box by
    `experiments/gpu/tom_valence_affect.py`."""
    from app.cognitive.appraisal import AppraisalEngine
    from app.cognitive.reappraisal import ReappraisalEngine
    from app.state.agent_state import StateService

    vader = _vader() if source == "vader" else None
    if source == "vader" and vader is None:
        raise RuntimeError("vaderSentiment not installed")

    with tempfile.TemporaryDirectory() as tmp:
        state = StateService(
            db_path=str(Path(tmp) / "state.db"), redis_host="0.0.0.0", redis_port=1
        )

        async def _no_persist(*_a, **_k):
            return None

        state.persist_state = _no_persist  # type: ignore[method-assign]
        if initial_mood is not None:
            state.current_state.mood = initial_mood
        appraisal = AppraisalEngine()
        reappraisal = ReappraisalEngine(store=None)
        # None: whatever Config says (weight learning is off by default since
        # ADR-002). True/False force the w1/w2 update on or off; prediction
        # errors are computed either way.
        if learn is not None:
            reappraisal.weight_learning = learn
        if initial_weights:
            reappraisal.appraisal_weights["w1_g_to_v"] = initial_weights[0]
            reappraisal.appraisal_weights["w2_ri_to_v"] = initial_weights[1]

        trace = Trace(
            script_name, source, baseline_valence=state.current_state.baseline_valence
        )
        elapsed = 0.0
        next_tick = 60.0
        for text, label in script(script_name, turns, seed):
            snapshot = state.get_context_snapshot()
            if source == "agent_mood":
                bias = snapshot.get("mood", 0.0)
            elif source == "oracle":
                bias = label
            elif source == "precomputed":
                bias = float((valence_map or {})[text])
            else:
                bias = vader.polarity_scores(text)["compound"]
            vector = appraisal.appraise(
                event_content=text,
                event_type="USER_MESSAGE",
                emotional_bias=bias,
                state_snapshot=snapshot,
                identity_boundaries=[],
                user_voice_properties=None,
            )
            reappraisal._last_evaluation_time = 0.0
            await reappraisal.evaluate_outcome(
                actual_text_valence=vector.goal_congruence
            )
            await state.update_from_appraisal(vector, weights=reappraisal.get_weights())
            snapshot = state.get_context_snapshot()
            reappraisal.record_pre_response_state(snapshot)
            reappraisal.record_expected_outcome(
                "COMFORT" if label < -0.3 else "ENGAGE", snapshot.get("mood", 0.0)
            )
            cs = state.current_state
            trace.user_valence.append(label)
            trace.mood.append(cs.mood)
            trace.energy.append(cs.energy)
            trace.dominance.append(cs.dominance)
            trace.trust.append(cs.trust)
            trace.w1.append(reappraisal.appraisal_weights["w1_g_to_v"])
            trace.w2.append(reappraisal.appraisal_weights["w2_ri_to_v"])
            elapsed += turn_gap_s
            while elapsed >= next_tick:
                await state.handle_system_tick({"interval": 60})
                next_tick += 60.0
        for _ in range(int(idle_hours_after * 60)):
            await state.handle_system_tick({"interval": 60})
        trace.mood_after_idle_24h = state.current_state.mood
        return trace


def run_matrix(
    turns: int = 60,
    seeds: tuple[int, ...] = (0, 1, 2),
    sources=APPRAISAL_SOURCES,
    scripts=SCRIPTS,
    learn_modes=(True, False),
    start_moods=(None, 0.6),
) -> dict:
    """source x script x weight-learning x starting mood, per seed."""
    out: dict = {}
    for source in sources:
        if source == "vader" and _vader() is None:
            continue
        for name in scripts:
            for learn in learn_modes:
                for start in start_moods:
                    key = f"{source}/{name}/learn={learn}/start={start}"
                    out[key] = [
                        asyncio.run(
                            simulate(
                                name,
                                source,
                                turns,
                                seed,
                                learn=learn,
                                initial_mood=start,
                            )
                        ).metrics()
                        for seed in seeds
                    ]
    return out
