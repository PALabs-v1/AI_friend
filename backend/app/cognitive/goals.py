"""Phase 04 Package B: time-watermarked goal tracking
(FINAL_HUMANOID_BRAIN_ARCHITECTURE.md Section 19).

A `GoalRecord` is a durable commitment (a user task, a self-directed
follow-up) with an optional deadline. `review_due_goals` is the
`DUE_GOAL_REVIEW` background job's core logic: it never runs on a clock of
its own, it only compares each active goal's deadline against whatever
watermark the caller (the background scheduler) supplies.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field


class GoalRecord(BaseModel):
    """One tracked goal, active until completed, expired, or failed."""

    goal_id: str
    type: str = "user_task"
    source: str = "user"
    description: str
    owner: str = "user"
    created_at: float = Field(default_factory=time.time)
    priority_class: int = 50
    deadline: float | None = None
    success_test: str | None = None
    status: str = "ACTIVE"
    last_progress: float = 0.0
    uncertainty: float = 0.0
    # Fix round (Architecture Section 11 alignment): a goal's priority is not
    # just `priority_class` -- Section 11 scores goals from named utility
    # terms (e.g. urgency, relationship_value) rather than one bare integer,
    # and a goal can be blocked by explicit constraints, descend from a
    # parent goal (a sub-goal spawned to satisfy a larger one), cite the
    # evidence that justified creating it, and expire or self-satisfy on a
    # condition distinct from `deadline` (a deadline is a hard time cutoff;
    # `satiation_or_expiry` is a separate watermark for a goal that becomes
    # moot or self-satisfies, e.g. "check back in an hour" with no failure
    # mode if skipped). All default to empty/None so every existing
    # `GoalRecord(...)` construction keeps working unchanged.
    utility_terms: dict[str, float] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    parent: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    satiation_or_expiry: float | None = None
    proactive_raise_count: int = 0
    proactive_ignored_count: int = 0
    proactive_outcomes: list[Literal["acted_on", "dismissed", "ignored"]] = Field(
        default_factory=list
    )
    last_proactive_raise: float | None = None
    proactive_review_after: float | None = None

    def record_proactive_raise(self, now: float) -> None:
        self.proactive_raise_count += 1
        self.last_proactive_raise = now
        self.proactive_review_after = None

    def record_proactive_outcome(
        self, outcome: Literal["acted_on", "dismissed", "ignored"]
    ) -> None:
        self.proactive_outcomes.append(outcome)
        if outcome == "ignored":
            self.proactive_ignored_count += 1

    def re_raise_probability(self, ignore_limit: int) -> float:
        if self.proactive_raise_count > len(self.proactive_outcomes):
            return 0.0
        if self.proactive_outcomes and self.proactive_outcomes[-1] in {
            "acted_on",
            "dismissed",
        }:
            return 0.0
        if ignore_limit <= 0 or self.proactive_ignored_count >= ignore_limit:
            return 0.0
        return 1.0 - self.proactive_ignored_count / ignore_limit


def review_due_goals(
    goals: list[GoalRecord],
    current_watermark: float,
    *,
    ignore_after_s: float | None = None,
) -> tuple[list[GoalRecord], list[str]]:
    """Expire any ACTIVE goal whose deadline has passed the watermark.

    Mutates and returns the same `GoalRecord` instances (status flips
    in place) alongside a human-readable note per expiry -- goals with no
    deadline, or a deadline still ahead of `current_watermark`, are left
    untouched.
    """
    notes: list[str] = []
    for goal in goals:
        if (
            ignore_after_s is not None
            and goal.proactive_raise_count > len(goal.proactive_outcomes)
            and goal.last_proactive_raise is not None
            and current_watermark - goal.last_proactive_raise >= ignore_after_s
        ):
            goal.record_proactive_outcome("ignored")
            notes.append(f"Proactive thought '{goal.goal_id}' was ignored.")
        if goal.status != "ACTIVE":
            continue
        if goal.deadline is not None and current_watermark >= goal.deadline:
            goal.status = "EXPIRED"
            notes.append(
                f"Goal '{goal.goal_id}' ({goal.description}) expired: "
                f"deadline {goal.deadline:.2f} reached at watermark "
                f"{current_watermark:.2f}."
            )
    return goals, notes
