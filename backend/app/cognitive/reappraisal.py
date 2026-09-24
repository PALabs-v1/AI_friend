"""
Reappraisal Engine — Gross/Bosse Feedback Loop (psychological_layer.md §8).

Implements emotion regulation via parameter-level adaptation, not direct
emotion modification. This maintains consistency with Gross (1998) and
Bosse et al. (2010):

    dE/dt = f(S, A, E)
    Reappraisal acts by modifying A (appraisal), which changes E.

The engine evaluates the outcome of each conversation turn and adjusts
the appraisal weights used by the ALMA mood-pull equations (§2.3).

Kill-switch: REAPPRAISAL_ENABLED=false
"""

import logging
import time
from typing import Any

from ..config import Config
from ..state.adaptive_weights_store import AdaptiveWeightsStore

logger = logging.getLogger(__name__)

_WEIGHT_KEY = "reappraisal_weights"


class ReappraisalEngine:
    """
    Feedback loop that refines appraisal weights based on conversation outcomes.

    §8.1: Outcome = w₁·Δ_text + w₂·Δ_acoustic + w₃·BehavioralSignal
    §8.2: w_new = clamp(w_old - η·Δ, w_min, w_max)
    """

    def __init__(
        self,
        agent_name: str = "my friend",
        store: AdaptiveWeightsStore | None = None,
    ):
        self.enabled = getattr(Config, "REAPPRAISAL_ENABLED", True)
        # Prediction errors are always computed (they drive the endocrine
        # bursts); adapting w1/w2 from them is opt-in -- see
        # Config.REAPPRAISAL_WEIGHT_LEARNING_ENABLED for the measured reason.
        self.weight_learning = getattr(
            Config, "REAPPRAISAL_WEIGHT_LEARNING_ENABLED", False
        )
        self.learning_rate = getattr(Config, "REAPPRAISAL_LEARNING_RATE", 0.05)  # η
        self.w_min = 0.1
        self.w_max = 0.9

        # Adaptive appraisal weights (tuned over time by this engine)
        # These feed back into the StateService's mood-pull coefficients
        self.appraisal_weights: dict[str, float] = {
            "w1_g_to_v": 0.6,  # G → Valence weight
            "w2_ri_to_v": 0.4,  # RI → Valence weight
            "w3_n_to_ar": 0.6,  # N → Arousal weight
            "w4_r_to_ar": 0.4,  # R → Arousal weight
            "w5_a_to_d": 0.6,  # A → Dominance weight
            "w6_na_to_d": 0.4,  # NA → Dominance weight
        }

        # #117 / H6: these weights used to reset to the hardcoded defaults
        # above on every process restart, silently discarding whatever the
        # feedback loop had learned. `hydrate()` restores them; `evaluate_outcome`
        # persists them each time it actually changes one.
        self.agent_name = agent_name
        self._store = store or AdaptiveWeightsStore()

        # Turn-level state tracking
        self._pre_response_state: dict[str, float] | None = None
        self._expected_valence: float | None = None
        self._last_evaluation_time: float = 0.0

    async def hydrate(self) -> None:
        """Restore previously-learned weights, if this agent has any.

        Only known keys are applied, each still passed through `_clamp` — a
        row written by an older version of this engine (fewer/renamed keys,
        or values from before a bounds change) must not inject an out-of-range
        or unrecognized weight into a live appraisal.
        """
        if not self.weight_learning:
            # Weights learned under the old always-on rule can sit at either
            # clamp (runaway gain or numbed valence); with learning off the
            # authored defaults apply instead of a frozen bad state.
            logger.info(
                "[Reappraisal] Weight learning disabled; using default appraisal weights."
            )
            return
        saved = await self._store.load(self.agent_name, _WEIGHT_KEY)
        if not saved:
            return
        for key in self.appraisal_weights:
            if key in saved:
                try:
                    self.appraisal_weights[key] = self._clamp(float(saved[key]))
                except (TypeError, ValueError):
                    continue
        logger.info(
            "[Reappraisal] Hydrated learned appraisal weights for %r: %s",
            self.agent_name,
            self.appraisal_weights,
        )

    def record_pre_response_state(self, state_snapshot: dict[str, Any]):
        """
        Called before generating a response.
        Captures the emotional baseline for outcome comparison.
        """
        if not self.enabled:
            return

        self._pre_response_state = {
            "valence": state_snapshot.get("mood", state_snapshot.get("valence", 0.0)),
            "arousal": state_snapshot.get("energy", state_snapshot.get("arousal", 0.5)),
            "dominance": state_snapshot.get("dominance", 0.5),
            "trust": state_snapshot.get("trust", 0.5),
        }

    def record_expected_outcome(self, goal: str, current_valence: float):
        """
        Record what the agent expects to happen based on its chosen goal.
        The expected outcome is a function of the goal type.
        """
        if not self.enabled:
            return

        # Goal → expected valence shift
        goal_expectations = {
            "COMFORT": 0.3,  # We expect the user to feel better
            "ENGAGE": 0.1,  # Neutral positive
            "INFORM": 0.05,  # Slight positive from helpfulness
            "TEASE": 0.15,  # Fun should improve mood
            "PROTECT": -0.1,  # Boundary enforcement may cause friction
        }
        self._expected_valence = goal_expectations.get(goal, 0.1)

    async def evaluate_outcome(
        self,
        actual_text_valence: float,
        acoustic_delta: float = 0.0,
        behavioral_signal: float = 0.5,
    ) -> float | None:
        """
        §8.1: Evaluate the outcome of the agent's last response.

        ActualOutcome = w₁·Δ_text + w₂·Δ_acoustic + w₃·BehavioralSignal

        §8.2: Adjust appraisal parameters based on prediction error.
        The system updates appraisal PARAMETERS (w₁, w₂), not emotions.

        Returns the **reward prediction error** (actual − expected), or `None`
        when no comparison was made: disabled, no expectation recorded, rate
        limited, or the outcome landed within tolerance.

        The sign is deliberately flipped relative to the internal `delta`
        (expected − actual) used for weight updates. Positive here means the
        turn went *better* than predicted, which is the direction the caller
        cares about, and matching the sign convention of the literature this
        implements (Schultz) keeps the endocrine channel readable. `None` and
        `0.0` are different answers — "no comparison" versus "exactly as
        expected" — so callers must not conflate them.
        """
        if not self.enabled:
            return None

        if self._expected_valence is None or self._pre_response_state is None:
            return None

        # Rate limiting: don't evaluate more than once per 2 seconds
        now = time.time()
        if now - self._last_evaluation_time < 2.0:
            return None
        self._last_evaluation_time = now

        # §8.1: Multi-signal outcome computation
        actual_outcome = (
            0.5 * actual_text_valence + 0.3 * acoustic_delta + 0.2 * behavioral_signal
        )

        # Prediction error
        delta = self._expected_valence - actual_outcome

        # §8.2: Only adapt on significant mismatches
        if abs(delta) < 0.1:
            logger.debug(
                "[Reappraisal] Δ=%.3f — within tolerance, no adaptation.", delta
            )
            self._reset_turn_state()
            # Within tolerance: the turn went as predicted. No prediction
            # error means no phasic burst, which is the point of the model --
            # a reward that was fully expected does not fire one.
            return None

        if not self.weight_learning:
            self._reset_turn_state()
            return -delta

        # Confidence weighting: reduce learning rate for noisy signals
        confidence = min(1.0, abs(actual_text_valence) + 0.3)
        effective_lr = self.learning_rate * confidence

        # Update valence-related weights (most likely to need correction)
        self.appraisal_weights["w1_g_to_v"] = self._clamp(
            self.appraisal_weights["w1_g_to_v"] - effective_lr * delta
        )
        self.appraisal_weights["w2_ri_to_v"] = self._clamp(
            self.appraisal_weights["w2_ri_to_v"] - effective_lr * delta * 0.5
        )

        logger.info(
            "[Reappraisal] Δ=%.3f η_eff=%.4f → w1=%.3f w2=%.3f",
            delta,
            effective_lr,
            self.appraisal_weights["w1_g_to_v"],
            self.appraisal_weights["w2_ri_to_v"],
        )

        await self._store.save(
            self.agent_name, _WEIGHT_KEY, self.appraisal_weights.copy()
        )

        self._reset_turn_state()
        return -delta

    def get_weights(self) -> dict[str, float]:
        """Returns current adaptive appraisal weights."""
        return self.appraisal_weights.copy()

    def _clamp(self, value: float) -> float:
        return max(self.w_min, min(self.w_max, value))

    def _reset_turn_state(self):
        self._pre_response_state = None
        self._expected_valence = None
