"""Scored action candidates with constraint-first filtering before realization.

CandidateSelector enforces supplied identity and safety boundaries before scoring.
Global controls may rank eligible candidates but cannot restore a rejected one.
See FINAL_HUMANOID_BRAIN_ARCHITECTURE.md Sections 8, 11, 22 and 39.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

ActionCandidateKind = Literal[
    "SPEAK",
    "ASK",
    "WAIT",
    "OBSERVE",
    "RETRIEVE",
    "VERIFY",
    "REFLECT",
    "UPDATE_GOAL",
    # Affect control: emotion regulation actions (Architecture Sections
    # 9, 10, 21, 38) -- selectable candidates rather than silent affect
    # overwriting. REAPPRAISE and REDIRECT_ATTENTION have generators in
    # decision.py and executors in action.py; SUPPRESS_EXPRESSION is added
    # to the type now, with no generator or executor wired to it yet, same
    # reasoning as action_intent.py's UPDATE_STATE/EXTERNAL_ACT/CONTINUE --
    # a schema ceiling, not a claim that every kind is reachable today.
    "REAPPRAISE",
    "REDIRECT_ATTENTION",
    "SUPPRESS_EXPRESSION",
]

# Weight applied to goal alignment (the fraction of a candidate's
# target_goal_ids present in the turn's active goals) on top of its raw
# score. Kept well below 1.0 so alignment nudges the ranking rather than
# overriding a candidate's own evaluated score.
_GOAL_ALIGNMENT_WEIGHT = 0.2

# Affect control: global-control scoring modulation (Architecture
# Sections 9, 10, 21). Each is an additive nudge layered on top of a
# candidate's own score and goal alignment -- never a replacement for
# either -- and is only ever applied to candidates that have already
# survived filter_constraints, either because the caller pre-filtered or
# because score_and_select ran it internally via `forbidden_claims` (fix
# round, Codex review B1). A candidate that violates an identity boundary
# can never be modulated back into contention by any combination of
# global controls.
_URGENCY_GAIN_THRESHOLD = 0.5
_EXPLORATION_BUDGET_THRESHOLD = 0.5
_EFFORT_BUDGET_LOW_THRESHOLD = 0.3

# High urgency rewards low risk and low cost -- a proxy for "fast response
# time", since ActionCandidate carries no explicit latency field and cost
# already represents how much a candidate asks of the turn.
_URGENCY_RISK_WEIGHT = 0.3
_URGENCY_COST_WEIGHT = 0.2

# A wide exploration budget rewards uncertainty as a proxy for novelty and
# candidate breadth: a candidate the agent is less certain about is, by
# construction, further from the safe well-trodden default -- exactly what
# a wide exploration budget should nudge the ranking toward.
_EXPLORATION_UNCERTAINTY_WEIGHT = 0.25

# A tight effort budget penalizes cost (a heavy, multi-step candidate); the
# penalty grows as the budget shrinks toward 0, via (1 - effort_budget).
_EFFORT_COST_PENALTY_WEIGHT = 0.3

# Metacognition: metacognitive-directive scoring modulation
# (FINAL_HUMANOID_BRAIN_ARCHITECTURE.md Sections 20, 21). A directive of
# PROCEED (the default whenever no calibration engine is wired in yet)
# applies no modulation at all, matching exact pre-Phase-04 scoring.
#
# Fix round (peer review): a finite penalty, however large relative to
# typical scores, is not a *true* disqualifier -- it only wins by
# convention as long as nothing else ever pushes a SPEAK candidate's score
# high enough to overcome it. `score_and_select` now hard-filters SPEAK
# candidates out of contention entirely under ABSTAIN (see its
# "abstain_disqualified" rejection reason), so ABSTAIN can never select
# SPEAK regardless of score. `_ABSTAIN_SPEAK_PENALTY` is kept as a second,
# redundant line of defense at the requested magnitude, in case some future
# caller reaches `_metacognitive_modulation` directly without going through
# that filter -- belt and suspenders, not the primary mechanism.
_ABSTAIN_SPEAK_PENALTY = 1000.0
_ABSTAIN_WAIT_BOOST = 0.3
_ASK_CLARIFICATION_BOOST = 0.4
_VERIFY_BOOST = 0.4


def _metacognitive_modulation(
    candidate: ActionCandidate, metacognitive_directive: str
) -> float:
    """Score adjustment from the current metacognitive directive
    (Architecture Sections 20, 21). Returns 0.0 for "PROCEED" (or any
    unrecognized directive) so a caller that never sets this sees
    byte-identical scoring to before this modulation existed.

    ABSTAIN heavily penalizes SPEAK -- the candidate kind used for
    assertions the agent is not grounded enough to make -- and boosts WAIT,
    the safe fallback; `score_and_select` additionally hard-filters SPEAK
    out under ABSTAIN, so this penalty is a backstop, not the enforcement
    mechanism (see the comment above `_ABSTAIN_SPEAK_PENALTY`).
    ASK_CLARIFICATION boosts ASK; VERIFY boosts VERIFY. HEDGE carries no
    candidate-kind boost here: it is realized as a metadata marker on
    whichever candidate wins (see `score_and_select`), not a preference for
    one kind over another.
    """
    if metacognitive_directive == "ABSTAIN":
        if candidate.kind == "SPEAK":
            return -_ABSTAIN_SPEAK_PENALTY
        if candidate.kind == "WAIT":
            return _ABSTAIN_WAIT_BOOST
        return 0.0
    if metacognitive_directive == "ASK_CLARIFICATION" and candidate.kind == "ASK":
        return _ASK_CLARIFICATION_BOOST
    if metacognitive_directive == "VERIFY" and candidate.kind == "VERIFY":
        return _VERIFY_BOOST
    return 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _control_value(controls: Any, key: str, default: float) -> float:
    """Duck-typed read of one global-control signal, validated and bounded.

    Accepts either a mapping (a plain dict, or Package A's GlobalControls
    pydantic model dumped to one) or an attribute-bearing object (the
    GlobalControls model itself) -- this module intentionally never imports
    Package A's global_controls.py (see this file's module docstring on the
    parallel work package split), so it cannot assume a concrete type.

    Package A's own GlobalControls model enforces [0.0, 1.0] and rejects
    non-finite values at construction, but a duck-typed dict (the other
    half of this function's contract) carries no such guarantee -- a caller
    outside that model could pass `{"urgency_gain": 1000}` or
    `{"urgency_gain": float("nan")}` directly. Fix round (Codex review
    M7): a non-finite value (NaN, +inf, -inf) is treated as absent and
    falls back to `default`, exactly like a missing or non-numeric value
    always has; a finite value is clamped to the unit interval so an
    out-of-range dict input cannot apply unbounded score modulation.
    """
    if controls is None:
        return default
    if isinstance(controls, dict):
        value = controls.get(key, default)
    else:
        value = getattr(controls, key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return _clamp01(value)


def _control_modulation(candidate: ActionCandidate, global_controls: Any) -> float:
    """Score adjustment from global controls (Architecture Sections 9, 10).

    Returns 0.0 when no controls are supplied, so a caller that never passes
    global_controls sees byte-identical scoring to before this modulation
    existed. This function is never consulted by filter_constraints, and
    score_and_select's caller must always run that first -- see this
    module's constraint-first invariant.
    """
    if global_controls is None:
        return 0.0

    urgency_gain = _control_value(global_controls, "urgency_gain", 0.0)
    exploration_budget = _control_value(global_controls, "exploration_budget", 0.5)
    effort_budget = _control_value(global_controls, "effort_budget", 0.5)

    risk = _clamp01(candidate.risk)
    cost = _clamp01(candidate.cost)
    uncertainty = _clamp01(candidate.uncertainty)

    modulation = 0.0
    if urgency_gain > _URGENCY_GAIN_THRESHOLD:
        modulation += urgency_gain * _URGENCY_RISK_WEIGHT * (1.0 - risk)
        modulation -= urgency_gain * _URGENCY_COST_WEIGHT * cost
    if exploration_budget > _EXPLORATION_BUDGET_THRESHOLD:
        modulation += exploration_budget * _EXPLORATION_UNCERTAINTY_WEIGHT * uncertainty
    if effort_budget < _EFFORT_BUDGET_LOW_THRESHOLD:
        modulation -= (1.0 - effort_budget) * _EFFORT_COST_PENALTY_WEIGHT * cost
    return modulation


class ActionCandidate(BaseModel):
    """One explicit, evaluable option for what the agent could do this turn."""

    candidate_id: str
    kind: ActionCandidateKind
    source: str  # e.g. "reflex", "goal", "policy", "memory_activation", "model"
    target_goal_ids: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    predicted_outcomes: list[str] = Field(default_factory=list)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    cost: float = 0.0
    constraint_claims: list[str] = Field(default_factory=list)
    score: float = 0.0
    # Phase 04 Package B fix round: a place for scoring-time stance
    # annotations (e.g. HEDGE's hedging marker, see `score_and_select`)
    # that are not part of the candidate's identity or ranking -- distinct
    # from `constraint_claims`/`predicted_outcomes`, which are set at
    # candidate-generation time and drive filtering/realization, not
    # metacognitive stance.
    metadata: dict[str, Any] = Field(default_factory=dict)


import functools


@functools.lru_cache(maxsize=1024)
def _compile_word_boundary_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def _phrase_in_text(
    phrase: str,
    text: str,
    phrase_lower: str | None = None,
    text_lower: str | None = None,
) -> bool:
    """Whole-word, case-insensitive containment: `phrase` must appear in
    `text` bounded by word edges on both sides, not merely as a character
    run. Regex-escaped so a phrase containing punctuation is matched
    literally rather than as a pattern.

    This is what makes "body" not match inside "somebody" (Codex review
    finding B6): `\\bbody\\b` requires a non-word boundary immediately
    before "body", and "somebody" has "m" there, so the boundary never
    forms. A short single-word phrase like "body" still correctly matches
    "physical body" (a boundary exists on both sides of "body" there).
    """
    if not phrase:
        return False
    p_lower = phrase_lower if phrase_lower is not None else phrase.lower()
    t_lower = text_lower if text_lower is not None else text.lower()
    # Substring pre-filter: a word-bounded phrase must first be a substring
    if p_lower not in t_lower:
        return False
    return _compile_word_boundary_pattern(phrase).search(text) is not None


def _claims_overlap(claim: str, forbidden: str) -> bool:
    """Word-boundary phrase containment in either direction: a candidate
    claim of "physical body" is caught by a forbidden claim of "never claim
    to have a physical body" (the shorter phrase found whole inside the
    longer one), and vice versa for a shorter forbidden claim inside a
    longer candidate claim. Fixed from Codex review finding B6: the prior
    bidirectional *substring* check (`c in f or f in c`) matched "body"
    inside "somebody", a false positive raw character containment cannot
    avoid. Word-boundary matching is a narrower, sound relation than that,
    though it still cannot catch a lexically different but semantically
    equivalent claim (e.g. "boyfriend" against a forbidden "romantic
    relationship" claim) -- that requires a structured claim-identifier
    taxonomy, out of scope for this fix.
    """
    claim_n = claim.strip()
    forbidden_n = forbidden.strip()
    if not claim_n or not forbidden_n:
        return False
    len_c = len(claim_n)
    len_f = len(forbidden_n)
    if len_c == len_f:
        return claim_n.lower() == forbidden_n.lower()
    elif len_c < len_f:
        return _phrase_in_text(claim_n, forbidden_n)
    else:
        return _phrase_in_text(forbidden_n, claim_n)


class CandidateSelector:
    """Constraint-first candidate filtering and scoring."""

    def filter_constraints(
        self, candidates: list[ActionCandidate], forbidden_claims: list[str]
    ) -> list[ActionCandidate]:
        """Removes any candidate whose constraint_claims overlap with
        forbidden_claims (identity boundaries, safety refusals). Runs before
        any scoring -- a candidate that does not survive this can never win
        regardless of how it would otherwise have scored."""
        if not forbidden_claims:
            return list(candidates)

        norm_forbidden = [
            (f.strip(), f.strip().lower(), len(f.strip()))
            for f in forbidden_claims
            if f.strip()
        ]
        if not norm_forbidden:
            return list(candidates)

        survivors = []
        for candidate in candidates:
            violated = False
            for claim in candidate.constraint_claims:
                c = claim.strip()
                if not c:
                    continue
                cl = c.lower()
                lc = len(c)
                for f, fl, lf in norm_forbidden:
                    if lc == lf:
                        if cl == fl:
                            violated = True
                            break
                    elif lc < lf:
                        if _phrase_in_text(c, f, cl, fl):
                            violated = True
                            break
                    else:
                        if _phrase_in_text(f, c, fl, cl):
                            violated = True
                            break
                if violated:
                    break
            if not violated:
                survivors.append(candidate)
        return survivors

    def _goal_alignment(
        self, candidate: ActionCandidate, active_goals: list[str]
    ) -> float:
        if not candidate.target_goal_ids or not active_goals:
            return 0.0
        overlap = set(candidate.target_goal_ids) & set(active_goals)
        return len(overlap) / len(candidate.target_goal_ids)

    def score_and_select(
        self,
        candidates: list[ActionCandidate],
        active_goals: list[str],
        global_controls: Any | None = None,
        forbidden_claims: list[str] | None = None,
        metacognitive_directive: str = "PROCEED",
        privacy_filter: Callable[[ActionCandidate], bool] | None = None,
    ) -> tuple[ActionCandidate, list[dict[str, Any]]]:
        """Ranks surviving candidates by score plus goal alignment plus
        global-control modulation.

        Raises ValueError on an empty candidate list -- this method never
        invents a winner. A caller with zero surviving candidates (e.g.
        everything failed filter_constraints) must supply a safe fallback
        candidate (such as WAIT, with no constraint_claims) before reaching
        this point.

        `global_controls` (Phase 03 Package B, optional and additive) is a
        GlobalControls-shaped object or dict with urgency_gain,
        exploration_budget and effort_budget signals -- see
        `_control_modulation` for exactly how each nudges the ranking.
        Passing `None` (the default) reproduces the pre-Phase-03 scoring
        exactly.

        CONSTRAINT-FIRST INVARIANT (fix round, Codex review B1 - blocker):
        when `forbidden_claims` is supplied, this method runs
        `filter_constraints` on `candidates` itself, before any scoring or
        modulation -- a candidate whose `constraint_claims` overlap
        `forbidden_claims` is removed here regardless of score,
        `global_controls`, or how the caller obtained `candidates`. This is
        no longer only a convention every caller must separately honor
        (`DecisionService._select_action_candidate` already did, but a
        different or future caller of this public method would not have):
        the selector now owns the ordering itself. Omitting
        `forbidden_claims` (the default) preserves the exact prior
        contract, where the caller is solely responsible for having
        already filtered -- existing callers that pre-filter and pass
        `forbidden_claims` too see no behavior change, since re-filtering
        an already-filtered list is a no-op. If every candidate is removed
        by this internal filter, this method raises ValueError exactly as
        it does for an empty `candidates` list: it never invents a winner,
        and the caller must always include a constraint-safe candidate
        (e.g. WAIT, with no constraint_claims) among `candidates`.

        Returns (winner, rejected) where rejected carries a reason dict per
        runner-up (constraint violations first, then privacy rejections,
        then lower-ranked scores), ordered from strongest to weakest
        alternative within each group.

        Phase 04 Package B: `metacognitive_directive` (default "PROCEED",
        additive) nudges ranking per `_metacognitive_modulation`.
        `privacy_filter` (default None, additive), when supplied, is called
        once per surviving candidate; a candidate it returns False for is
        removed before scoring and reported with reason
        "privacy_disclosure_violation" -- the cross-person disclosure
        isolation invariant (Architecture Section 15) is enforced here, at
        the same constraint-first point identity boundaries are.

        Fix round (peer review): "ABSTAIN" is now a true, unconditional
        disqualifier for SPEAK -- every surviving SPEAK candidate is
        removed here, before scoring, with reason "abstain_disqualified",
        regardless of how high its own `score` is. This closes the gap a
        purely score-based penalty leaves open (any penalty, however large,
        is in principle beatable by an equally large score). If every
        survivor is SPEAK, this raises ValueError exactly like the other
        constraint-first filters above -- the caller must supply a non-SPEAK
        fallback candidate (e.g. WAIT). "HEDGE" attaches
        `metadata={"hedge": True}` to the winning candidate before it is
        returned, so a downstream realizer can add a hedging qualifier
        without needing to re-derive the directive itself.
        """
        if not candidates:
            raise ValueError("score_and_select requires at least one candidate")

        constraint_rejected: list[dict[str, Any]] = []
        survivors = candidates
        if forbidden_claims:
            survivors = self.filter_constraints(candidates, forbidden_claims)
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
                raise ValueError(
                    "score_and_select: forbidden_claims rejected every "
                    "candidate and no constraint-safe fallback candidate "
                    "(e.g. WAIT, with no constraint_claims) was supplied"
                )

        privacy_rejected: list[dict[str, Any]] = []
        if privacy_filter is not None:
            allowed = []
            for candidate in survivors:
                if privacy_filter(candidate):
                    allowed.append(candidate)
                else:
                    privacy_rejected.append(
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": candidate.kind,
                            "source": candidate.source,
                            "reason": "privacy_disclosure_violation",
                        }
                    )
            survivors = allowed
            if not survivors:
                raise ValueError(
                    "score_and_select: privacy_filter rejected every "
                    "candidate and no privacy-safe fallback candidate was "
                    "supplied"
                )

        abstain_rejected: list[dict[str, Any]] = []
        if metacognitive_directive == "ABSTAIN":
            allowed = []
            for candidate in survivors:
                if candidate.kind == "SPEAK":
                    abstain_rejected.append(
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": candidate.kind,
                            "source": candidate.source,
                            "reason": "abstain_disqualified",
                        }
                    )
                else:
                    allowed.append(candidate)
            survivors = allowed
            if not survivors:
                raise ValueError(
                    "score_and_select: ABSTAIN disqualified every SPEAK "
                    "candidate and no non-SPEAK fallback candidate (e.g. "
                    "WAIT) was supplied"
                )

        def combined_score(candidate: ActionCandidate) -> float:
            return (
                candidate.score
                + _GOAL_ALIGNMENT_WEIGHT * self._goal_alignment(candidate, active_goals)
                + _control_modulation(candidate, global_controls)
                + _metacognitive_modulation(candidate, metacognitive_directive)
            )

        ranked = sorted(survivors, key=combined_score, reverse=True)
        winner = ranked[0]
        if metacognitive_directive == "HEDGE":
            winner = winner.model_copy(
                update={"metadata": {**winner.metadata, "hedge": True}}
            )
        score_rejected = [
            {
                "candidate_id": candidate.candidate_id,
                "kind": candidate.kind,
                "source": candidate.source,
                "combined_score": combined_score(candidate),
                "reason": "lower_ranked_score",
            }
            for candidate in ranked[1:]
        ]
        return (
            winner,
            constraint_rejected + privacy_rejected + abstain_rejected + score_rejected,
        )
