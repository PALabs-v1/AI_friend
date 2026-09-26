import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProactiveThought:
    text: str
    importance: float
    category: str
    goal_id: str | None = None


def estimate_importance(state_snapshot: dict[str, Any]) -> float:
    """Estimate importance from state signals already owned by the brain."""
    goals = state_snapshot.get("active_goals") or []
    implied = (state_snapshot.get("user_mental_model") or {}).get("implied_goals", [])
    unresolved = state_snapshot.get("unresolved_thoughts") or []
    now = float(state_snapshot.get("timestamp", 0.0))
    goal_records = state_snapshot.get("proactive_goals") or []
    due_or_upcoming = any(
        isinstance(goal, dict)
        and isinstance(goal.get("deadline"), (int, float))
        and 0.0 <= goal["deadline"] - now <= 86400.0
        for goal in goal_records
    )
    now_dt = datetime.fromtimestamp(now, UTC)
    due_or_upcoming = due_or_upcoming or any(
        _active_goal_is_upcoming(goal, now_dt) for goal in goals
    )
    score = 0.20
    if goals:
        score += 0.10
    if implied:
        score += 0.05
    if unresolved:
        score += 0.05
    if due_or_upcoming:
        score += 0.55
    return min(1.0, score)


def _active_goal_is_upcoming(goal: Any, now: datetime) -> bool:
    if isinstance(goal, dict):
        deadline = goal.get("deadline")
        return (
            isinstance(deadline, (int, float))
            and 0 <= deadline - now.timestamp() <= 86400
        )
    if isinstance(goal, str):
        match = re.search(r"; due_in_hours=([-+]?\d+(?:\.\d+)?)", goal)
        return match is not None and 0 <= float(match.group(1)) <= 24
    return False


def deterministic_thought(state_snapshot: dict[str, Any]) -> ProactiveThought:
    """Build a restrained check-in from context when no model is configured."""
    goals = state_snapshot.get("proactive_goals") or []
    unresolved = state_snapshot.get("unresolved_thoughts") or []
    now = float(state_snapshot.get("timestamp", 0.0))
    due_goal = next(
        (
            goal
            for goal in goals
            if isinstance(goal, dict)
            and isinstance(goal.get("deadline"), (int, float))
            and 0.0 <= goal["deadline"] - now <= 86400.0
        ),
        None,
    )
    due_active_goal = next(
        (
            goal
            for goal in state_snapshot.get("active_goals") or []
            if _active_goal_is_upcoming(goal, datetime.fromtimestamp(now, UTC))
        ),
        None,
    )
    if due_active_goal:
        text = f"Would you like to revisit {due_active_goal}?"
    elif due_goal:
        subject = due_goal.get("description") or "something you planned"
        text = f"Your plan about {subject} is coming up. Would a check-in help?"
    elif state_snapshot.get("active_goals"):
        text = f"Would you like to revisit {state_snapshot['active_goals'][0]}?"
    elif (state_snapshot.get("user_mental_model") or {}).get("implied_goals"):
        text = "Would you like to pick up one of the things you were working toward?"
    elif unresolved:
        thought = unresolved[0]
        subject = thought.get("description") if isinstance(thought, dict) else thought
        text = f"Would you like to revisit {subject}?"
    else:
        text = "Is there anything from our last conversation you'd like to revisit?"
    return ProactiveThought(text, estimate_importance(state_snapshot), "useful_to_user")


def _parse_candidate(raw: str, fallback: float) -> ProactiveThought | None:
    """Accept structured output; malformed scores never become high scores."""
    text = raw.strip().strip("\"'")
    importance = fallback
    category = "useful_to_user"
    goal_id = None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        candidate = payload.get("thought")
        if isinstance(candidate, str) and candidate.strip():
            text = candidate.strip()
        else:
            return None
        score = payload.get("importance")
        if (
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(score)
        ):
            importance = min(1.0, max(0.0, float(score)))
        candidate_category = payload.get("category")
        if candidate_category in {"useful_to_user", "self_directed"}:
            category = candidate_category
        goal_id = (
            payload.get("goal_id") if isinstance(payload.get("goal_id"), str) else None
        )
    if not text:
        return None
    if category == "useful_to_user" and re.search(
        r"\b(I have been thinking|I wonder|I want to share)\b", text, re.IGNORECASE
    ):
        category = "self_directed"
    return ProactiveThought(text, importance, category, goal_id)


def _prompt_context(state_snapshot: dict[str, Any]) -> str:
    """Give the existing generation call bounded, structured signals to score."""
    now = float(state_snapshot.get("timestamp", 0.0))
    goals = []
    for goal in state_snapshot.get("proactive_goals") or []:
        if not isinstance(goal, dict) or goal.get("status", "ACTIVE") != "ACTIVE":
            continue
        deadline = goal.get("deadline")
        timing = ""
        if isinstance(deadline, (int, float)):
            hours = (deadline - now) / 3600
            timing = f"; deadline in {hours:.1f} hours"
        goals.append(
            f"{goal.get('goal_id', 'unknown')}: {goal.get('description', '')}{timing}"
        )
    goals.extend(str(item) for item in (state_snapshot.get("active_goals") or []))
    implied = (state_snapshot.get("user_mental_model") or {}).get("implied_goals", [])
    unresolved = []
    for thought in state_snapshot.get("unresolved_thoughts") or []:
        if isinstance(thought, dict):
            unresolved.append(
                f"{thought.get('goal_id', 'unknown')}: {thought.get('description', '')}"
            )
        else:
            unresolved.append(str(thought))
    return (
        f"Known active goals and commitments: {goals[:8]}\n"
        f"Possible inferred goals: {[str(item) for item in implied[:8]]}\n"
        f"Unresolved prior thoughts: {unresolved[:8]}"
    )


class SubconsciousEngine:
    """
    Subconscious Cognition & Affect Engine.
    Decoupled from NATS and State Persistence.
    """

    def __init__(self, llm_client):
        self.llm = llm_client

    async def evaluate_and_think(
        self, state_snapshot: dict[str, Any], proactive_eligible: bool
    ) -> ProactiveThought | None:
        """
        Evaluates conditions and generates a thought string if eligible.
        """
        if not proactive_eligible:
            return None

        if getattr(self.llm, "is_null_llm", False) is True:
            return deterministic_thought(state_snapshot)

        emotion = state_snapshot.get("emotion", "neutral")
        energy = state_snapshot.get("energy", 0.5)
        context = _prompt_context(state_snapshot)

        prompt = f"""
        You are the subconscious inner voice of an AI companion.
        You notice the user hasn't spoken to you in a while.
        Your current emotion is {emotion} and your energy level is {energy:.2f}.
        Use only the following known context; do not invent commitments or
        claim the user disclosed something absent here:
        {context}

        Return JSON with thought (one or two sentences), importance (0..1),
        category (useful_to_user or self_directed), and goal_id (stable short
        id when this revisits a prior goal, otherwise null). Score urgency, not
        how eloquent the thought sounds. For a useful thought, refer naturally
        to the relevant disclosed plan; do not expose its internal id. Do not
        speak directly to the user.
        """

        try:
            thought = await self.llm.generate(
                prompt,
                system="You are an internal thought generator. Output only valid JSON.",
            )
            return _parse_candidate(thought, estimate_importance(state_snapshot))

        except Exception as e:
            logger.error(f"[SubconsciousEngine] Thought generation failed: {e}")
            return None
