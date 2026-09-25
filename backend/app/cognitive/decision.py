"""
Decision Layer -- MAUT + Intent Persistence (psychological_layer.md Section 3).

Intent selection uses Multi-Attribute Utility Theory (Keeney & Raiffa, 1976):
    U(Intent) = w1*GoalAlignment + w2*EmotionalFit + w3*IdentityAlignment + w4*ContextRelevance

Intent persistence uses temporal smoothing (Section 3.2):
    Intent_t = (1 - rho) * Intent_{t-1} + rho * Intent_new
    With context gating: if ContextShift > theta -> hard reset
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import Config
from ..state.adaptive_weights_store import AdaptiveWeightsStore
from .action_candidate import ActionCandidate, CandidateSelector
from .behavior_contracts import BehaviorDecision, CommunicativeIntent
from .bt import Action, Condition, NodeStatus, Selector, Sequence
from .deterministic_responses import evaluate_deterministic_response
from .intent_classifier import get_intent_classifier
from .json_extract import extract_first_json_value
from .memory_activation import MemoryActivation
from .perception import CognitiveEvent

logger = logging.getLogger(__name__)

# Available goals for MAUT scoring
GOALS = ["ENGAGE", "COMFORT", "INFORM", "TEASE", "PROTECT"]

_WEIGHT_KEY = "goal_utilities"

# 4C fix: category -> event.intent for a short-circuited deterministic plan.
_DETERMINISTIC_PLAN_INTENTS = {"backchannel": "ACKNOWLEDGE", "refusal": "REFUSE"}


@dataclass
class ActionPlan:
    action_type: str  # e.g., "RESPOND_CHAT", "STORE_MEMORY", "BACKGROUND_CONSOLIDATION"
    payload: dict[str, Any]
    goal: str
    priority: int = 1
    # 1B: typed sibling of goal/payload -- what this turn is trying to do and
    # what it may/may not claim, before persona/policy.py's precheck
    # (pipeline.py stage 7) and action.py's realization see it. Defaulted so
    # every existing keyword/short-positional ActionPlan(...) call keeps
    # working unchanged.
    behavior_decision: BehaviorDecision | None = None


# Ordered coarsest -> warmest; _bucket_relational_stance clamps into this.
_RELATIONAL_STANCES: tuple[str, ...] = (
    "distant",
    "guarded",
    "neutral",
    "warm",
    "close",
)

# Urgency at/above this maps a turn's interruption_policy to "reflex" --
# naming, for conversational turns, the same class of "high-arousal signal
# competes for the workspace immediately" judgment
# `is_facial_reflex_interruption_worthy` already makes for vision's `startle`
# reflex, rather than reintroducing a second threshold with different logic.
_REFLEX_URGENCY_THRESHOLD = 0.75

# Memory activation disputation threshold: a MemoryActivation at or above this relevance, whose
# contradiction_state is not "NONE", is treated as active memory disputing
# what the turn is about to assert -- proposing ASK (clarify) as a candidate.
_HIGH_RELEVANCE_THRESHOLD = 0.75

# Baseline scores for the two candidates decide() always generates. WAIT is
# deliberately low -- it is the constraint-safe fallback, not a competitive
# default -- and only wins when nothing else survives filter_constraints.
_SPEAK_BASELINE_SCORE = 0.5
_WAIT_FALLBACK_SCORE = 0.1
# An ASK candidate raised by disputed active memory must outscore the SPEAK
# baseline whenever relevance clears _HIGH_RELEVANCE_THRESHOLD; 0.6 plus a
# relevance-scaled bonus guarantees that at the threshold (0.75 * 0.3 =
# 0.225) it already exceeds 0.5, and it only grows with relevance.
_ASK_BASE_SCORE = 0.6
_ASK_RELEVANCE_BONUS = 0.3

# Acute distress thresholds for emotion-regulation candidate generation.
# Both must hold together -- a strongly negative mood alone is ordinary low mood,
# not the acute, aroused distress regulation candidates exist to address.
_DISTRESS_VALENCE_THRESHOLD = -0.5
_DISTRESS_AROUSAL_THRESHOLD = 0.4

# Regulation candidates score above the SPEAK baseline (0.5) so they can
# actually win a distressed turn rather than only ever being generated and
# rejected; REAPPRAISE outranks REDIRECT_ATTENTION as the more direct
# response to acute distress, and the distress-specific WAIT sits below
# both but still above the ordinary WAIT fallback (0.1) -- pausing is a
# more deliberate choice under distress than the default safety floor.
_REAPPRAISE_SCORE = 0.55
_REDIRECT_ATTENTION_SCORE = 0.45
_DISTRESS_WAIT_SCORE = 0.4
_REGULATION_CONSTRAINT_CLAIM = "emotion_regulation_response"

# Fix round (Codex review B3): common, low-signal words filtered out of a
# turn's content before it is treated as a set of proposed topic claims --
# without this every SPEAK candidate would carry claims like "you"/"have"/
# "the" that trivially word-boundary-match all manner of unrelated
# forbidden claims.
_TOPIC_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "do",
        "does",
        "did",
        "you",
        "your",
        "yours",
        "im",
        "is",
        "are",
        "am",
        "to",
        "of",
        "in",
        "on",
        "and",
        "or",
        "have",
        "has",
        "had",
        "that",
        "this",
        "it",
        "what",
        "how",
        "why",
        "who",
        "when",
        "can",
        "could",
        "will",
        "would",
        "should",
        "with",
        "for",
        "me",
        "my",
        "mine",
        "we",
        "us",
        "our",
        "be",
        "been",
        "being",
        "was",
        "were",
        "not",
        "no",
        "yes",
        "but",
        "so",
        "just",
        "really",
    }
)
_TOPIC_WORD_PATTERN = re.compile(r"[A-Za-z']+")
# ASK proposes a request for clarification, never an assertion about a
# forbidden topic -- a fixed, generic claim rather than an empty list, so
# filter_constraints has something to evaluate for every generated
# candidate (Codex review B3's core complaint), not only for SPEAK.
_ASK_CONSTRAINT_CLAIM = "request_clarification"


def _extract_topic_claims(text: str) -> list[str]:
    """Deterministic, LLM-free proxy for "what a SPEAK response engaging
    with this turn might claim or discuss": the turn's own significant
    words, lowercased, stopword-filtered, and deduplicated in order of
    first appearance.

    This is not natural-language understanding of what the eventual
    response will actually say -- it is a conservative pre-generation
    signal so `filter_constraints` has real, content-derived claims to
    evaluate before Stage 8 generates anything (Codex review B3), rather
    than an empty list that let every candidate through unfiltered. A
    forbidden-boundary word appearing in the user's own turn is treated as
    reason enough to make the corresponding SPEAK candidate contest that
    boundary; it is deliberately cautious rather than semantically precise.
    """
    words = (match.group(0).lower() for match in _TOPIC_WORD_PATTERN.finditer(text))
    seen: set[str] = set()
    claims: list[str] = []
    for word in words:
        if len(word) <= 2 or word in _TOPIC_STOPWORDS or word in seen:
            continue
        seen.add(word)
        claims.append(word)
    return claims


_CLARIFY_OUTCOME_PREFIX = "clarify:"


def _clarification_subject_from_candidate(selected_candidate: dict[str, Any]) -> str:
    """Recover the human-readable clarification subject an ASK candidate's
    `predicted_outcomes` was built with (see `_build_candidates`), from the
    plain dict `ActionCandidate.model_dump()` produces -- `selected_candidate`
    on `BehaviorDecision` is a dict, not the original `ActionCandidate`
    instance, since it must survive `.model_dump()` for the `ActionIntent`
    trace (`pipeline.py::_commit_action_intent`)."""
    for outcome in selected_candidate.get("predicted_outcomes", []):
        if isinstance(outcome, str) and outcome.startswith(_CLARIFY_OUTCOME_PREFIX):
            subject = outcome[len(_CLARIFY_OUTCOME_PREFIX) :].strip()
            return subject or "that"
    return "that"


def _clarification_subject(activation: MemoryActivation) -> str:
    """Best-effort human-readable label for what an ASK candidate wants
    clarified, drawn from whatever structured_value happens to carry --
    MemoryActivation.structured_value has no fixed schema across record
    types, so this degrades to a generic label rather than raising when a
    caller's structured_value omits every field it checks."""
    value = activation.structured_value or {}
    for key in ("summary", "subject", "topic", "description"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "that"


def _is_acute_distress(state_snapshot: dict[str, Any]) -> bool:
    """Severe negative valence plus high arousal (Architecture Sections 9,
    10, 21, 38): the acute-distress condition emotion-regulation candidates
    exist to catch. Reads the same state_snapshot keys _score_goals_maut
    already reads (`mood` for valence, `energy` for arousal) rather than
    inventing a second naming convention for the same fields.
    """
    valence = float(state_snapshot.get("mood", 0.0))
    arousal = float(state_snapshot.get("energy", 0.5))
    return (
        valence < _DISTRESS_VALENCE_THRESHOLD and arousal > _DISTRESS_AROUSAL_THRESHOLD
    )


def _bucket_relational_stance(trust: float, attachment: float, mood: float) -> str:
    """Clamp+bucket trust/attachment/mood into a named stance -- the same
    clamp-then-map shape `persona/compiler.py::_infer_temperament` uses for
    turning continuous dimension scores into bounded fields, applied here to
    pick one of `_RELATIONAL_STANCES` instead of a numeric field."""
    trust = max(0.0, min(1.0, trust))
    attachment = max(0.0, min(1.0, attachment))
    mood_unit = max(0.0, min(1.0, (mood + 1.0) / 2.0))
    score = (trust + attachment + mood_unit) / 3.0
    index = min(int(score * len(_RELATIONAL_STANCES)), len(_RELATIONAL_STANCES) - 1)
    return _RELATIONAL_STANCES[index]


def _build_communicative_intent(
    event: CognitiveEvent, blackboard: dict[str, Any]
) -> CommunicativeIntent:
    """Stage 6: derive what this turn is trying to do from the event and the
    state snapshot already on the blackboard -- no new state reads."""
    state = blackboard.get("state") or {}
    metadata = event.metadata or {}
    tom = metadata.get("tom_inferences") or {}
    urgency = max(0.0, min(1.0, float(tom.get("inferred_arousal", 0.5))))
    stance = _bucket_relational_stance(
        trust=float(state.get("trust", 0.5)),
        attachment=float(state.get("attachment", 0.1)),
        mood=float(state.get("mood", 0.0)),
    )
    return CommunicativeIntent(
        act=event.intent or "CHAT",
        goal=metadata.get("suggested_goal", "ENGAGE"),
        urgency=urgency,
        relational_stance=stance,
        interruption_policy=(
            "reflex" if urgency >= _REFLEX_URGENCY_THRESHOLD else "deliberative"
        ),
    )


class DecisionService:
    """
    The Decision Layer.
    Uses LLM-based Intent Classification, MAUT goal scoring, and a Behavior Tree for routing.
    """

    def __init__(
        self,
        llm_service=None,
        memory_store=None,
        agent_name: str = "my friend",
        weights_store: AdaptiveWeightsStore | None = None,
        identity_manager=None,
    ):
        self.llm = llm_service
        self.memory = memory_store
        # 4C: live reference, not a snapshot -- immutable_core can be
        # rebuilt by IdentityManager._refresh_immutable_core after this.
        self._identity_manager = identity_manager
        self.root = self._build_bt()

        # MAUT Weights (Section 3.1)
        self.w_goal = Config.MAUT_W_GOAL
        self.w_emotion = Config.MAUT_W_EMOTION
        self.w_identity = Config.MAUT_W_IDENTITY
        self.w_context = Config.MAUT_W_CONTEXT

        # ACT-R Goal Utility Reinforcement Learning
        self.goal_utilities = {g: 1.0 for g in GOALS}
        self.alpha_rl = 0.1  # TD learning rate

        # #118 / H7: `goal_utilities` used to reset to 1.0 for every goal on
        # every process restart, discarding whatever reinforcement learning had
        # accumulated. `hydrate()` restores it; `decide()` persists it whenever
        # `_score_goals_maut` updates a utility.
        self.agent_name = agent_name
        self._weights_store = weights_store or AdaptiveWeightsStore()
        # Constraint-first candidate filtering and scoring selector
        self._candidate_selector = CandidateSelector()

        # Intent Persistence (Section 3.2)
        self.persistence_rate = Config.INTENT_PERSISTENCE_RATE  # rho
        self.shift_threshold = Config.CONTEXT_SHIFT_THRESHOLD  # theta_shift
        self._previous_goal: str | None = None
        self._goal_scores: dict[str, float] = {g: 0.0 for g in GOALS}
        self.intent_classifier = get_intent_classifier(self)

    async def hydrate(self) -> None:
        """Restore previously-learned goal utilities, if this agent has any.

        Only known goals are applied, so a row from an older `GOALS` list (a
        renamed or retired goal) cannot inject an untracked key that
        `_score_goals_maut` never reads back out.
        """
        saved = await self._weights_store.load(self.agent_name, _WEIGHT_KEY)
        if not saved:
            return
        for goal in GOALS:
            if goal in saved:
                try:
                    self.goal_utilities[goal] = float(saved[goal])
                except (TypeError, ValueError):
                    continue
        logger.info(
            "[Decision] Hydrated learned goal utilities for %r: %s",
            self.agent_name,
            self.goal_utilities,
        )

    def _build_bt(self):
        """Constructs the Behavior Tree."""
        return Selector(
            "RootDecision",
            [
                Sequence(
                    "SystemTasks",
                    [
                        Condition(
                            "IsSystemTick", lambda b: b["event"].intent == "REFLECT"
                        ),
                        Action("PlanReflection", self._plan_reflection),
                    ],
                ),
                Sequence(
                    "MemoryCommands",
                    [
                        Condition(
                            "IsRememberIntent",
                            lambda b: b["event"].intent == "REMEMBER",
                        ),
                        Action("PlanStorage", self._plan_storage),
                    ],
                ),
                Sequence(
                    "SocialReasoning",
                    [
                        Condition(
                            "IsChatIntent", lambda b: b["event"].intent == "CHAT"
                        ),
                        Selector(
                            "ResponseStrategy",
                            [
                                Sequence(
                                    "DeterministicResponse",
                                    [
                                        Condition(
                                            "HasDeterministicResponse",
                                            self._check_deterministic_response,
                                        ),
                                        Action(
                                            "RespondDeterministic",
                                            self._apply_deterministic_response,
                                        ),
                                    ],
                                ),
                                Action(
                                    "DetermineGoalAndResponse",
                                    self._plan_social_response,
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

    def _immutable_core(self) -> dict[str, Any]:
        return getattr(self._identity_manager, "immutable_core", {}) or {}

    def _check_deterministic_response(self, blackboard: dict[str, Any]) -> bool:
        """4C: evaluate once, cache on the blackboard for the Action sibling."""
        plan = evaluate_deterministic_response(
            blackboard["event"], blackboard["state"], self._immutable_core()
        )
        blackboard["_deterministic_plan"] = plan
        return plan is not None

    def _apply_deterministic_response(self, blackboard: dict[str, Any]) -> bool:
        blackboard["plan"] = blackboard.pop("_deterministic_plan")
        return True

    async def decide(
        self,
        event: CognitiveEvent,
        state_snapshot: dict[str, Any],
        memory_activations: list[MemoryActivation] | None = None,
        global_controls: Any | None = None,
        metacognitive_directive: str = "PROCEED",
        privacy_filter: Callable[[ActionCandidate], bool] | None = None,
    ) -> ActionPlan:
        """Main decision loop with MAUT scoring and intent persistence.

        `memory_activations` (Phase 02 Package B) is optional and additive:
        every caller that predates it keeps working unchanged, and its
        contents only influence the plan when Config.MEMORY_TRUTH_ENABLED
        is True (see `_plan_social_response`).

        `global_controls` (Phase 03 Package B, Architecture Sections 9, 10,
        21) is likewise optional and additive: it only reaches
        `CandidateSelector.score_and_select` when Config.AFFECT_CONTROL_ENABLED
        is True (see `_select_action_candidate`), and `state_snapshot`
        (already a required parameter here) doubles as the
        acute-distress signal for regulation-candidate generation.

        `metacognitive_directive` and `privacy_filter` (Phase 04 Package B)
        reach `CandidateSelector.score_and_select` unconditionally, gated
        only by the memory-truth/affect-control flags that govern candidate
        selection -- their defaults
        ("PROCEED", None) reproduce exact prior scoring for every caller
        that omits them.
        """
        # 0. Deterministic short-circuit -- zero LLM calls, before classification.
        if event.event_type == "USER_MESSAGE":
            deterministic_plan = evaluate_deterministic_response(
                event, state_snapshot, self._immutable_core()
            )
            if deterministic_plan is not None:
                category = str(deterministic_plan.payload.get("category") or "")
                event.intent = _DETERMINISTIC_PLAN_INTENTS.get(category, "ACKNOWLEDGE")
                return deterministic_plan

        # 1. Hybrid Routing: Fast Path for Greetings
        if self._is_simple_greeting(event.raw_content):
            event.intent = "CHAT"
            event.metadata["suggested_goal"] = "ENGAGE"
            event.metadata["preferred_model"] = Config.LLM_FAST_MODEL
        elif event.event_type == "USER_MESSAGE":
            await self.intent_classifier.classify(event, state_snapshot)

        # 2. MAUT Goal Scoring (Section 3.1) -- replaces raw keyword goal
        appraisal = event.metadata.get("appraisal", {})
        if appraisal and event.intent == "CHAT":
            maut_goal = self._score_goals_maut(
                appraisal, state_snapshot, event.metadata
            )
            event.metadata["suggested_goal"] = maut_goal
            # #118 / H7: `_score_goals_maut` just updated one entry of
            # `goal_utilities` via TD learning (unless this was the very first
            # turn, when `_previous_goal` was still None). Persisting here
            # rather than inside that method keeps it a pure scoring function.
            await self._weights_store.save(
                self.agent_name, _WEIGHT_KEY, self.goal_utilities.copy()
            )

        # 3. Tick BT
        blackboard: dict[str, Any] = {
            "event": event,
            "state": state_snapshot,
            "plan": None,
            "memory_activations": memory_activations or [],
            "global_controls": global_controls,
            "metacognitive_directive": metacognitive_directive,
            "privacy_filter": privacy_filter,
        }
        status = await self.root.tick(blackboard)

        if status == NodeStatus.SUCCESS and blackboard["plan"]:
            return blackboard["plan"]

        fallback_goal = event.metadata.get("suggested_goal", "ENGAGE")
        return ActionPlan(
            "RESPOND_CHAT",
            {
                "message": event.raw_content,
                "surfaced_memories": event.metadata.get("surfaced_memories", []),
            },
            fallback_goal,
        )

    def _score_goals_maut(
        self,
        appraisal: dict[str, float],
        state: dict[str, Any],
        event_metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Multi-Attribute Utility Theory (Section 3.1).
        Scores each goal and applies temporal persistence (Section 3.2).
        Includes dynamic ACT-R Goal Utility Reinforcement Learning updates.
        """
        V = state.get("mood", 0.0)
        Ar = state.get("energy", 0.5)
        T = state.get("trust", 0.5)
        R = appraisal.get("relevance", 0.5)
        G = appraisal.get("goal_congruence", 0.0)
        N = appraisal.get("novelty", 0.3)
        NA = appraisal.get("norm_alignment", 1.0)

        # 1. Update utility of previous goal if it exists (TD learning: U_g(t) = U_g(t-1) + alpha * [Reward - U_g(t-1)])
        V_user = state.get("inferred_valence", 0.0)
        if not V_user and "user_mental_model" in state:
            V_user = state["user_mental_model"].get("inferred_valence", 0.0)

        gaze = 0.5
        if event_metadata:
            gaze = event_metadata.get(
                "gaze",
                event_metadata.get(
                    "gaze_duration", event_metadata.get("user_gaze", 0.5)
                ),
            )

        norm_valence = (V_user + 1.0) / 2.0
        reward = 0.7 * norm_valence + 0.3 * gaze

        if self._previous_goal in self.goal_utilities:
            prev_u = self.goal_utilities[self._previous_goal]
            self.goal_utilities[self._previous_goal] = prev_u + self.alpha_rl * (
                reward - prev_u
            )

        scores: dict[str, float] = {}

        # ENGAGE: Best for neutral/positive states, high energy, novel topics
        scores["ENGAGE"] = (
            self.w_goal * max(0, G + 0.5)
            + self.w_emotion * (0.5 + V * 0.3 + Ar * 0.2)
            + self.w_identity * NA
            + self.w_context * R
            + self.goal_utilities["ENGAGE"]
        )

        # COMFORT: Best when user seems distressed -- favored at low arousal (calm tone)
        scores["COMFORT"] = (
            self.w_goal * max(0, -G + 0.5)
            + self.w_emotion * max(0, -V + 0.5) * (1.2 - Ar * 0.4)
            + self.w_identity * NA
            + self.w_context * R * 0.8
            + self.goal_utilities["COMFORT"]
        )

        # INFORM: Best for high relevance, novel content -- arousal-neutral
        scores["INFORM"] = (
            self.w_goal * max(0, G * 0.5 + 0.3)
            + self.w_emotion * (0.4 + Ar * 0.2)
            + self.w_identity * NA
            + self.w_context * R * N
            + self.goal_utilities["INFORM"]
        )

        # TEASE: Only when trust is high, mood positive, and energy high
        scores["TEASE"] = (
            self.w_goal * max(0, G * 0.3)
            + self.w_emotion * max(0, V * 0.3 + Ar * 0.2)
            + self.w_identity * NA * T
            + self.w_context * (1 - R) * 0.3
            + self.goal_utilities["TEASE"]
        )

        # PROTECT: When norm alignment is low -- arousal-neutral (boundary enforcement)
        scores["PROTECT"] = (
            self.w_goal * 0.2
            + self.w_emotion * (0.2 + Ar * 0.1)
            + self.w_identity * max(0, 1.0 - NA)
            + self.w_context * R * 0.5
            + self.goal_utilities["PROTECT"]
        )

        # Section 3.2: Intent Persistence with Context Gating
        new_goal = max(scores, key=lambda g: scores[g])
        context_shift = N  # Novelty serves as a proxy for context shift

        if self._previous_goal is not None and context_shift < self.shift_threshold:
            # Apply temporal smoothing: blend previous goal scores
            rho = self.persistence_rate
            for g in GOALS:
                prev_score = self._goal_scores.get(g, 0.0)
                scores[g] = (1 - rho) * prev_score + rho * scores[g]
            new_goal = max(scores, key=lambda g: scores[g])
            logger.debug(
                "[Decision] Persistence applied (shift=%.2f < theta=%.2f): %s -> %s",
                context_shift,
                self.shift_threshold,
                self._previous_goal,
                new_goal,
            )
        else:
            if self._previous_goal:
                logger.debug(
                    "[Decision] Context shift detected (%.2f >= theta=%.2f): hard reset to %s",
                    context_shift,
                    self.shift_threshold,
                    new_goal,
                )

        self._previous_goal = new_goal
        self._goal_scores = scores

        logger.info(
            "[Decision] MAUT scores: %s -> selected: %s",
            {g: f"{s:.3f}" for g, s in scores.items()},
            new_goal,
        )
        return new_goal

    def _is_simple_greeting(self, text: str) -> bool:
        greetings = {"hi", "hello", "hey", "hola", "namaste", "yo"}
        clean_text = text.lower().strip().strip("!").strip(".")
        return clean_text in greetings

    def _apply_heuristic_intent_and_goal(self, event: CognitiveEvent):
        """Cheap intent defaults to avoid unnecessary LLM calls on every turn."""
        text = (event.raw_content or "").lower().strip()

        # Only classify as REMEMBER if it contains remember/memorize and is NOT a question
        is_question = text.endswith("?") or any(
            text.startswith(w)
            for w in [
                "what",
                "where",
                "how",
                "why",
                "who",
                "when",
                "did",
                "is",
                "can",
                "do you",
                "do",
                "are",
                "have you",
                "has",
            ]
        )

        if ("remember" in text or "memorize" in text) and not is_question:
            event.intent = "REMEMBER"
            event.metadata.setdefault("suggested_goal", "RECALL")
            event.metadata.setdefault("preferred_model", Config.LLM_FAST_MODEL)
            return

        event.intent = "CHAT"
        event.metadata.setdefault("suggested_goal", "ENGAGE")
        event.metadata.setdefault("preferred_model", Config.LLM_CHAT_MODEL)

    async def _classify_intent_and_goal(
        self, event: CognitiveEvent, state: dict[str, Any]
    ):
        """Uses LLM to classify intent and suggested goal, enriching with Theory of Mind inferences."""
        prompt = f"""
        Analyze user input and current agent state.
        Input: "{event.raw_content}"
        Mood: {state["emotion"]} (Valence: {state["mood"]})

        Classify into:
        - intent: REMEMBER, CHAT, COMMAND
          * REMEMBER: ONLY when the user explicitly instructs you to memorize or store a new fact (e.g., "remember that my cat's name is Lily").
          * CHAT: When the user is conversing, sharing, or asking a question (e.g., "do you remember my hometown?", "is my favorite dessert sweet?", "hey there").
          * COMMAND: Explicit control instructions.
        - goal: COMFORT, INFORM, ENGAGE, TEASE, PROTECT

        Also infer Theory of Mind (ToM) details:
        - inferred_valence: float between -1.0 and 1.0 (mood valence of the user)
        - inferred_arousal: float between 0.0 and 1.0 (arousal level of the user)
        - implied_goals: up to 2 implied immediate user goals (list of strings like "seek_reassurance", "express_frustration", "learn_concept", "chat_socially")

        First, output a brief chain-of-thought analysis enclosed in <thought>...</thought> (maximum 45 tokens) analyzing the input, intent, and ToM.
        Then, output the JSON block:
        {{
          "intent": "...",
          "goal": "...",
          "inferred_valence": 0.0,
          "inferred_arousal": 0.5,
          "implied_goals": ["..."]
        }}
        """.strip()

        try:
            response = await self.llm.generate(
                prompt,
                model=Config.LLM_FAST_MODEL,
                options_override={"num_predict": 256},
            )

            json_str = response
            if "</thought>" in response:
                json_str = response.split("</thought>")[-1].strip()
            elif "</think>" in response:
                json_str = response.split("</think>")[-1].strip()

            data = extract_first_json_value(json_str, brackets="{")
            if data is not None:
                # Sanitize/normalize intent: must be CHAT, REMEMBER, or COMMAND
                raw_intent = data.get("intent", "").upper().strip()
                if raw_intent in ["CHAT", "REMEMBER", "COMMAND"]:
                    event.intent = raw_intent
                else:
                    # If it's a goal or invalid value, fall back to CHAT
                    event.intent = "CHAT"

                event.metadata["suggested_goal"] = data.get("goal", "ENGAGE")
                event.metadata["preferred_model"] = (
                    Config.LLM_CHAT_MODEL
                    if event.intent == "CHAT"
                    else Config.LLM_FAST_MODEL
                )

                # Normalize implied_goals to avoid splitting strings into character arrays
                val = data.get("implied_goals", None)
                if val is None:
                    implied_goals = []
                elif isinstance(val, str):
                    implied_goals = [val]
                elif isinstance(val, (list, tuple)):
                    implied_goals = list(val)
                else:
                    implied_goals = []
                    logger.warning(
                        f"[Decision] Unexpected type for implied_goals: {type(val)}. Falling back to empty list."
                    )

                tom_inferences = {
                    "inferred_valence": float(data.get("inferred_valence", 0.0)),
                    "inferred_arousal": float(data.get("inferred_arousal", 0.5)),
                    "implied_goals": implied_goals,
                }
                event.metadata["tom_inferences"] = tom_inferences

                logger.info(f"[Decision] Fast Classified with ToM: {data}")
            else:
                logger.warning(
                    f"[Decision] Failed to find JSON block in LLM response for intent classification. Raw response: {response!r}"
                )
        except Exception as e:
            logger.error(f"Intent and ToM classification failed: {e}")

    # --- BT Actions ---

    def is_speculative_stop_confirmed(
        self, backbone_text: str, perception_keywords: list[str] | None = None
    ) -> bool:
        """
        Hardened Semantic Conflict Resolver for AI Friend.
        Logic: Distinguish between Agent Commands and Conversational Context.
        """
        if not backbone_text:
            return False

        if perception_keywords is None:
            perception_keywords = [
                "stop",
                "wait",
                "hold",
                "listen",
                "sunno",
                "ruko",
                "quiet",
            ]

        clean_text = backbone_text.lower().strip()
        raw_words = clean_text.split()
        words = [w.strip("!.,?;:") for w in raw_words]

        conversational_connectors = [
            "i agree",
            "i think",
            "i actually",
            "i just",
            "i am",
            "but",
            "though",
            "it is",
            "raining",
            "singing",
            "working",
            "playing",
            "for",
            "to",
            "be",
        ]
        call_signs = ["hey", "friend", "listen"]

        for kw in perception_keywords:
            kw = kw.lower()
            if kw in words:
                idx = words.index(kw)

                if idx + 1 < len(words):
                    next_words = " ".join(words[idx + 1 : idx + 3])
                    if any(conn in next_words for conn in conversational_connectors):
                        logger.debug(
                            f"[ConflictResolver] Rejected '{kw}' - Conversational context: '{next_words}'"
                        )
                        continue

                is_pivot = (idx == 0) or all(words[w] in call_signs for w in range(idx))

                if not is_pivot:
                    logger.debug(
                        f"[ConflictResolver] Rejected '{kw}' - Buried intent in: '{clean_text}'"
                    )
                    continue

                if len(words) <= 4:
                    logger.info(
                        f"[ConflictResolver] Stop CONFIRMED (Concise Command): '{kw}' in '{clean_text}'"
                    )
                    return True

                if idx == 0:
                    logger.info(
                        f"[ConflictResolver] Stop CONFIRMED (Pivot Match): '{kw}' in '{clean_text}'"
                    )
                    return True

        logger.warning(
            f"[ConflictResolver] Speculative stop REJECTED. Backbone text '{clean_text}' contradicts early perception."
        )
        return False

    def is_facial_reflex_interruption_worthy(self, reflex_name: str) -> bool:
        """Only `startle` -- the compound, highest-arousal reflex signal
        (`app/vision/reflex.py`) -- is salient enough to compete for the
        workspace and interrupt an in-flight turn. `smile`/`brow_furrow`
        stay background-only: an agent that stopped talking because the
        user smiled would be wrong, not attentive.
        """
        return reflex_name == "startle"

    async def _plan_social_response(self, blackboard: dict[str, Any]) -> bool:
        event = blackboard["event"]
        goal = event.metadata.get("suggested_goal", "ENGAGE")
        intent = _build_communicative_intent(event, blackboard)
        behavior_decision = BehaviorDecision(intent=intent)
        action_type = "RESPOND_CHAT"
        clarification_subject: str | None = None

        # Either feature independently enables candidate selection. Memory
        # activations remain empty when memory truth is disabled.
        if Config.MEMORY_TRUTH_ENABLED or Config.AFFECT_CONTROL_ENABLED:
            memory_activations = blackboard.get("memory_activations") or []
            behavior_decision = self._select_action_candidate(
                behavior_decision,
                goal,
                memory_activations,
                event.raw_content,
                state_snapshot=blackboard.get("state") or {},
                global_controls=blackboard.get("global_controls"),
                metacognitive_directive=blackboard.get(
                    "metacognitive_directive", "PROCEED"
                ),
                privacy_filter=blackboard.get("privacy_filter"),
            )
            selected = behavior_decision.selected_candidate
            selected_kind = selected.get("kind") if selected else None
            if selected and selected_kind == "ASK":
                # Fix round (Codex review B2): carry the selected kind into
                # the *executable* plan, not only the ActionIntent trace --
                # otherwise Stage 8 dispatches on action_type alone and an
                # ASK selection silently realizes as ordinary chat.
                action_type = "CLARIFY"
                clarification_subject = _clarification_subject_from_candidate(selected)
            elif selected_kind == "WAIT":
                action_type = "WAIT"
            elif selected_kind in ("REAPPRAISE", "REDIRECT_ATTENTION"):
                # Phase 03 Package B: same reasoning as the ASK/CLARIFY
                # mapping above -- a selected regulation candidate must
                # realize as its own action, not fall through to ordinary
                # chat generation.
                action_type = selected_kind

        payload = {
            "message": event.raw_content,
            "emotion_state": blackboard["state"]["emotion"],
            "model": event.metadata.get("preferred_model"),
            "surfaced_memories": event.metadata.get("surfaced_memories", []),
        }
        if clarification_subject is not None:
            payload["clarification_subject"] = clarification_subject

        blackboard["plan"] = ActionPlan(
            action_type=action_type,
            goal=goal,
            payload=payload,
            priority=1,
            behavior_decision=behavior_decision,
        )
        return True

    def _build_regulation_candidates(self, goal: str) -> list[ActionCandidate]:
        """Phase 03 Package B: REAPPRAISE / REDIRECT_ATTENTION / WAIT
        candidates offered under acute distress (Architecture Sections 9,
        10, 21, 38) -- additive to the ordinary social candidates
        _build_candidates already generates, never a replacement for them.
        Each carries a real constraint_claims entry so filter_constraints
        has something to evaluate, same reasoning as the SPEAK/ASK claims
        below (Codex review B3). Low risk/cost reflects that these are
        short, low-commitment utterances relative to open-ended chat.
        """
        return [
            ActionCandidate(
                candidate_id="cand-reappraise-distress",
                kind="REAPPRAISE",
                source="regulation",
                target_goal_ids=[goal],
                constraint_claims=[_REGULATION_CONSTRAINT_CLAIM],
                predicted_outcomes=["grounding_reflection"],
                risk=0.1,
                cost=0.2,
                score=_REAPPRAISE_SCORE,
            ),
            ActionCandidate(
                candidate_id="cand-redirect-distress",
                kind="REDIRECT_ATTENTION",
                source="regulation",
                target_goal_ids=[goal],
                constraint_claims=[_REGULATION_CONSTRAINT_CLAIM],
                predicted_outcomes=["topic_pivot"],
                risk=0.15,
                cost=0.25,
                score=_REDIRECT_ATTENTION_SCORE,
            ),
            ActionCandidate(
                candidate_id="cand-wait-distress",
                kind="WAIT",
                source="regulation",
                target_goal_ids=[goal],
                constraint_claims=[_REGULATION_CONSTRAINT_CLAIM],
                risk=0.0,
                cost=0.0,
                score=_DISTRESS_WAIT_SCORE,
            ),
        ]

    def _build_candidates(
        self,
        goal: str,
        memory_activations: list[MemoryActivation],
        raw_content: str,
        state_snapshot: dict[str, Any] | None = None,
    ) -> list[ActionCandidate]:
        """Phase 02 Package B: the candidate set decide() evaluates for a
        social-response turn. Always includes a SPEAK baseline and a WAIT
        fallback (constraint_claims empty, so filter_constraints can never
        empty the set entirely); adds an ASK candidate when active memory
        disputes what the turn would otherwise assert.

        Fix round (Codex review B3): SPEAK and ASK now carry real
        constraint_claims -- SPEAK from `_extract_topic_claims(raw_content)`
        (see that function's docstring for what this heuristic is and is
        not), ASK from the fixed `_ASK_CONSTRAINT_CLAIM` label -- so
        `filter_constraints` has something to evaluate instead of an empty
        list on every generated candidate.

        Phase 03 Package B: `state_snapshot` is optional and additive --
        `None` (every pre-Phase-03 caller) skips distress detection
        entirely, matching exact prior behavior. When supplied and
        `Config.AFFECT_CONTROL_ENABLED` is True, acute distress
        (`_is_acute_distress`) adds `_build_regulation_candidates`'s output
        to the set below.
        """
        candidates = [
            ActionCandidate(
                candidate_id="cand-speak-default",
                kind="SPEAK",
                source="policy",
                target_goal_ids=[goal],
                constraint_claims=_extract_topic_claims(raw_content),
                score=_SPEAK_BASELINE_SCORE,
            ),
            ActionCandidate(
                candidate_id="cand-wait-fallback",
                kind="WAIT",
                source="reflex",
                score=_WAIT_FALLBACK_SCORE,
            ),
        ]

        disputed = [
            activation
            for activation in memory_activations
            if activation.validity
            and activation.contradiction_state != "NONE"
            and activation.relevance_score >= _HIGH_RELEVANCE_THRESHOLD
        ]
        if disputed:
            most_relevant = max(disputed, key=lambda a: a.relevance_score)
            candidates.append(
                ActionCandidate(
                    candidate_id=f"cand-ask-{most_relevant.record_id}",
                    kind="ASK",
                    source="memory_activation",
                    target_goal_ids=[goal],
                    evidence_ids=[most_relevant.record_id],
                    constraint_claims=[_ASK_CONSTRAINT_CLAIM],
                    predicted_outcomes=[
                        f"clarify:{_clarification_subject(most_relevant)}"
                    ],
                    score=_ASK_BASE_SCORE
                    + _ASK_RELEVANCE_BONUS * most_relevant.relevance_score,
                )
            )

        if (
            Config.AFFECT_CONTROL_ENABLED
            and state_snapshot is not None
            and _is_acute_distress(state_snapshot)
        ):
            candidates.extend(self._build_regulation_candidates(goal))

        return candidates

    def _select_action_candidate(
        self,
        behavior_decision: BehaviorDecision,
        goal: str,
        memory_activations: list[MemoryActivation],
        raw_content: str,
        state_snapshot: dict[str, Any] | None = None,
        global_controls: Any | None = None,
        metacognitive_directive: str = "PROCEED",
        privacy_filter: Callable[[ActionCandidate], bool] | None = None,
    ) -> BehaviorDecision:
        """Constraint-first candidate generation and selection for one
        social-response turn. Returns a copy of
        `behavior_decision` carrying the winning candidate, the rejected
        alternatives (constraint-violating and lower-scoring alike), and
        whether any considered memory activation reported a retrieval
        outage.

        `state_snapshot` feeds acute-distress detection in `_build_candidates`;
        `global_controls` is forwarded to `score_and_select` only when
        `Config.AFFECT_CONTROL_ENABLED` is True, so global-control modulation
        can never affect ranking while the flag is off, matching this file's
        `MEMORY_TRUTH_ENABLED` gating pattern above. Both are optional and
        additive -- omitting them reproduces exact prior behavior.

        `metacognitive_directive` and `privacy_filter` pass straight through to
        `score_and_select` unconditionally with safe default fallbacks ("PROCEED", None).
        """
        forbidden_claims = list(self._immutable_core().get("boundaries", []))
        candidates = self._build_candidates(
            goal, memory_activations, raw_content, state_snapshot
        )

        survivors = self._candidate_selector.filter_constraints(
            candidates, forbidden_claims
        )
        survivor_ids = {candidate.candidate_id for candidate in survivors}
        constraint_rejected = [
            {
                "candidate_id": candidate.candidate_id,
                "kind": candidate.kind,
                "source": candidate.source,
                "reason": "constraint_violation",
                "constraint_claims": candidate.constraint_claims,
            }
            for candidate in candidates
            if candidate.candidate_id not in survivor_ids
        ]

        if not survivors:
            # filter_constraints should never empty the set given the WAIT
            # fallback always carries no constraint_claims -- this is a
            # defensive floor, not an expected path.
            survivors = [c for c in candidates if c.kind == "WAIT"] or candidates

        # Fix round (Codex review B1 - blocker): `survivors` was already
        # filtered above, so passing `forbidden_claims` here too is
        # normally a no-op re-filter -- but it makes this call defend
        # itself rather than relying solely on the pre-filtering above,
        # which is exactly the gap the review found in the public selector
        # API. It also converts the pathological "no WAIT candidate
        # exists at all" fallback three lines up (which would otherwise
        # silently re-admit a forbidden candidate) into a raised
        # ValueError instead of a silent constraint violation.
        winner, score_rejected = self._candidate_selector.score_and_select(
            survivors,
            active_goals=[goal],
            global_controls=(
                global_controls if Config.AFFECT_CONTROL_ENABLED else None
            ),
            forbidden_claims=forbidden_claims,
            metacognitive_directive=metacognitive_directive,
            privacy_filter=privacy_filter,
        )

        retrieval_degraded = any(
            activation.outage_flag for activation in memory_activations
        )

        return behavior_decision.model_copy(
            update={
                "selected_candidate": winner.model_dump(),
                "rejected_alternatives": constraint_rejected + score_rejected,
                "retrieval_degraded": retrieval_degraded,
            }
        )

    async def _plan_reflection(self, blackboard: dict[str, Any]) -> bool:
        event = blackboard["event"]
        intent = _build_communicative_intent(event, blackboard)
        blackboard["plan"] = ActionPlan(
            "BACKGROUND_CONSOLIDATION",
            {},
            "REFLECT",
            0,
            behavior_decision=BehaviorDecision(intent=intent),
        )
        return True

    async def _plan_storage(self, blackboard: dict[str, Any]) -> bool:
        event = blackboard["event"]
        intent = _build_communicative_intent(event, blackboard)
        blackboard["plan"] = ActionPlan(
            "STORE_MEMORY",
            {"content": event.raw_content},
            "RECALL",
            2,
            behavior_decision=BehaviorDecision(intent=intent),
        )
        return True
