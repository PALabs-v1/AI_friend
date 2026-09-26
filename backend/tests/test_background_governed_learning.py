"""Phase 04 Package B: due-goal review, governed
learning proposals with rollback, and metacognitive/privacy-aware candidate
selection (FINAL_HUMANOID_BRAIN_ARCHITECTURE.md Sections 11, 19, 20, 21, 38).
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cognitive.action_candidate import ActionCandidate, CandidateSelector
from app.cognitive.goals import GoalRecord, review_due_goals
from app.cognitive.learning_review import (
    LearningProposal,
    LearningProposalStatus,
    LearningReviewQueue,
    validate_proposal_safety,
)

pytestmark = pytest.mark.asyncio


# --- Due-goal review --------------------------------------------------------


def test_due_goal_review_expiry():
    """Only ACTIVE goals whose deadline has passed the watermark flip to
    EXPIRED; goals with no deadline, a future deadline, or a non-ACTIVE
    status must be left untouched."""
    goals = [
        GoalRecord(goal_id="g1", description="finish report", deadline=100.0),
        GoalRecord(goal_id="g2", description="ongoing chat", deadline=None),
        GoalRecord(goal_id="g3", description="long term goal", deadline=200.0),
        GoalRecord(
            goal_id="g4",
            description="already finished",
            status="COMPLETED",
            deadline=50.0,
        ),
    ]

    updated, notes = review_due_goals(goals, current_watermark=150.0)

    assert updated[0].status == "EXPIRED"
    assert updated[1].status == "ACTIVE"
    assert updated[2].status == "ACTIVE"
    assert updated[3].status == "COMPLETED"
    assert len(notes) == 1
    assert "g1" in notes[0]


def test_goal_record_section_11_fields_default_and_settable():
    """Fix round: GoalRecord must carry Architecture Section 11's named
    utility terms, blocking constraints, parent/sub-goal linkage, citing
    evidence, and a satiation/expiry watermark distinct from `deadline` --
    all optional so every pre-fix-round GoalRecord(...) construction keeps
    working unchanged."""
    bare_goal = GoalRecord(goal_id="g1", description="write the report")
    assert bare_goal.utility_terms == {}
    assert bare_goal.constraints == []
    assert bare_goal.parent is None
    assert bare_goal.evidence_ids == []
    assert bare_goal.satiation_or_expiry is None

    child_goal = GoalRecord(
        goal_id="g2",
        description="draft the intro",
        parent="g1",
        utility_terms={"urgency": 0.8, "relationship_value": 0.2},
        constraints=["must not contradict prior commitment"],
        evidence_ids=["mem-42"],
        satiation_or_expiry=12345.0,
    )
    assert child_goal.parent == "g1"
    assert child_goal.utility_terms["urgency"] == 0.8
    assert child_goal.constraints == ["must not contradict prior commitment"]
    assert child_goal.evidence_ids == ["mem-42"]
    assert child_goal.satiation_or_expiry == 12345.0


# --- Governed learning proposals --------------------------------------------


async def test_learning_proposal_approval_and_durable_rollback():
    """An APPROVED proposal must roll back to exactly its rollback_value,
    the queue must durably remember the ROLLED_BACK status (not just hand
    back a transient copy), and neither approve nor rollback may be
    replayed on a proposal that already left the PENDING/APPROVED state."""
    queue = LearningReviewQueue()
    proposal = LearningProposal(
        target_domain="conversation.tone",
        proposed_value="warmer",
        expected_effect="increase warmth in casual chat",
        rollback_value="neutral",
    )

    submitted = queue.submit(proposal=proposal)
    assert submitted.status == LearningProposalStatus.PENDING

    approved = await queue.approve(proposal.proposal_id)
    assert approved is not None
    assert approved.status == LearningProposalStatus.APPROVED

    rolled_back, restored_value = queue.rollback(proposal.proposal_id)
    assert rolled_back is not None
    assert rolled_back.status == LearningProposalStatus.ROLLED_BACK
    assert restored_value == "neutral"

    # Durable: re-fetching from the queue shows the same terminal status.
    assert queue.get(proposal.proposal_id).status == LearningProposalStatus.ROLLED_BACK

    # Cannot roll back twice, or approve after rollback.
    assert queue.rollback(proposal.proposal_id) == (None, None)
    assert await queue.approve(proposal.proposal_id) is None


async def test_learning_proposal_rejection_cannot_be_rolled_back():
    queue = LearningReviewQueue()
    proposal = LearningProposal(
        target_domain="reply_length",
        proposed_value="shorter",
        expected_effect="reduce verbosity",
    )
    queue.submit(proposal=proposal)

    rejected = queue.reject(proposal.proposal_id, reason="not enough evidence")

    assert rejected.status == LearningProposalStatus.REJECTED
    assert rejected.rejection_reason == "not enough evidence"
    assert queue.rollback(proposal.proposal_id) == (None, None)


def test_learning_proposal_immutable_core_safety_invariant():
    """No proposal may ever target the immutable persona core or a safety
    boundary, at submission time, regardless of risk_class or source --
    and a rejected submission must never be registered in the queue."""
    queue = LearningReviewQueue()
    forbidden_domains = [
        "name",
        "core_values",
        "safety_boundaries",
        "immutable",
        "constitutional",
        "persona.name",
        "identity.safety_boundaries.refusal",
    ]
    for domain in forbidden_domains:
        proposal = LearningProposal(
            target_domain=domain, proposed_value="x", expected_effect="y"
        )
        with pytest.raises(ValueError):
            validate_proposal_safety(proposal)
        with pytest.raises(ValueError):
            queue.submit(proposal=proposal)
        assert queue.get(proposal.proposal_id) is None

    # A domain that merely contains "name" as a substring, not as a whole
    # token, must not be caught by the same check.
    safe_proposal = LearningProposal(
        target_domain="conversation.nickname_style",
        proposed_value="playful",
        expected_effect="warmer nicknames",
    )
    assert queue.submit(proposal=safe_proposal).status == LearningProposalStatus.PENDING


def test_learning_proposal_immutable_core_bracket_and_case_bypass_rejected():
    """Fix round: the initial check split target_domain on `.`, `:`, `/`
    only and required an exact segment match, which brackets and casing
    could bypass (`persona[name]` is one segment that never equals "name";
    "PERSONA.CORE_VALUES" was lowercased but "core_values" as one token
    never appears when the marker itself is two words). Canonicalizing `[`,
    `]`, and `_` as additional word boundaries and matching contiguous
    token phrases closes both bypasses."""
    bypass_domains = [
        "persona[name]",
        "PERSONA.CORE_VALUES",
        "identity:core_values",
        "identity/immutable/rule",
        "rules[constitutional]",
        "identity[SAFETY_BOUNDARIES]",
    ]
    for domain in bypass_domains:
        proposal = LearningProposal(
            target_domain=domain, proposed_value="x", expected_effect="y"
        )
        with pytest.raises(ValueError):
            validate_proposal_safety(proposal)


async def test_learning_proposal_approve_revalidates_after_mutation():
    """Fix round: a proposal that was safe at submission time but mutated
    (pydantic models are not frozen) to target the immutable core before
    review must be rejected at approval time, not silently applied --
    submission-time validation alone is not enough once the object can be
    changed out from under the queue."""
    queue = LearningReviewQueue()
    proposal = LearningProposal(
        target_domain="conversation.tone",
        proposed_value="warmer",
        expected_effect="increase warmth",
    )
    queue.submit(proposal=proposal)

    proposal.target_domain = "persona.safety_boundaries"

    with pytest.raises(ValueError):
        await queue.approve(proposal.proposal_id)

    assert queue.get(proposal.proposal_id).status == LearningProposalStatus.PENDING


async def test_learning_review_queue_legacy_api_compat():
    """Fix round: the pre-Phase-04 suggestions-dict workflow
    (`ReflectionService` in learning.py, and the separately-owned
    tests/test_learning_review.py) must keep working unchanged alongside
    the new governed-proposal workflow -- this is Package B's own
    regression guard for that promise."""
    direct = LearningProposal(suggestions={"relationship": "Friend"})
    assert direct.id == direct.proposal_id
    assert direct.suggestions == {"relationship": "Friend"}
    assert direct.is_contradiction is False

    queue = LearningReviewQueue()
    legacy = queue.submit({"relationship": "Trusted Friend"})
    assert legacy.suggestions == {"relationship": "Trusted Friend"}
    assert queue.pending() == [legacy]

    contradicting = queue.submit({"relationship": "Stranger"}, contradicts_id="mem-1")
    assert contradicting.is_contradiction is True
    assert queue.contradictions() == [contradicting]

    identity = MagicMock()
    identity.history = {}
    identity.evolve_persona = AsyncMock()

    applied = await queue.approve(legacy.id, identity)
    identity.evolve_persona.assert_called_once_with({"relationship": "Trusted Friend"})
    assert "evolved_learnings" in identity.history
    assert applied.id == legacy.id
    assert legacy not in queue.pending()


# --- Metacognitive directive and privacy filtering in CandidateSelector ----


def test_candidate_selector_metacognitive_directive_modulation():
    """ABSTAIN must flip the winner from SPEAK to WAIT, ASK_CLARIFICATION
    must favor an ASK candidate over a higher-scoring SPEAK candidate, and
    VERIFY must favor a VERIFY candidate the same way -- while the default
    PROCEED directive reproduces plain score ranking."""
    selector = CandidateSelector()

    speak_or_wait = [
        ActionCandidate(candidate_id="speak", kind="SPEAK", source="policy", score=0.9),
        ActionCandidate(candidate_id="wait", kind="WAIT", source="reflex", score=0.1),
    ]
    winner_default, _ = selector.score_and_select(speak_or_wait, active_goals=[])
    assert winner_default.candidate_id == "speak"

    winner_abstain, _ = selector.score_and_select(
        speak_or_wait, active_goals=[], metacognitive_directive="ABSTAIN"
    )
    assert winner_abstain.candidate_id == "wait"

    speak_or_ask = [
        ActionCandidate(
            candidate_id="speak2", kind="SPEAK", source="policy", score=0.5
        ),
        ActionCandidate(
            candidate_id="ask2", kind="ASK", source="memory_activation", score=0.3
        ),
    ]
    winner_ask, _ = selector.score_and_select(
        speak_or_ask, active_goals=[], metacognitive_directive="ASK_CLARIFICATION"
    )
    assert winner_ask.candidate_id == "ask2"

    speak_or_verify = [
        ActionCandidate(
            candidate_id="speak3", kind="SPEAK", source="policy", score=0.5
        ),
        ActionCandidate(
            candidate_id="verify3", kind="VERIFY", source="policy", score=0.3
        ),
    ]
    winner_verify, _ = selector.score_and_select(
        speak_or_verify, active_goals=[], metacognitive_directive="VERIFY"
    )
    assert winner_verify.candidate_id == "verify3"


def test_candidate_selector_abstain_is_a_true_disqualifier():
    """Fix round: ABSTAIN must disqualify SPEAK outright, not merely
    out-score it with a large-but-finite penalty -- even a SPEAK candidate
    with an enormous score must lose to WAIT, and ABSTAIN must refuse to
    invent a winner when every survivor is SPEAK."""
    selector = CandidateSelector()
    candidates = [
        ActionCandidate(
            candidate_id="speak", kind="SPEAK", source="policy", score=1_000_000.0
        ),
        ActionCandidate(candidate_id="wait", kind="WAIT", source="reflex", score=0.0),
    ]

    winner, rejected = selector.score_and_select(
        candidates, active_goals=[], metacognitive_directive="ABSTAIN"
    )

    assert winner.candidate_id == "wait"
    assert any(
        r["candidate_id"] == "speak" and r["reason"] == "abstain_disqualified"
        for r in rejected
    )

    with pytest.raises(ValueError):
        selector.score_and_select(
            [
                ActionCandidate(
                    candidate_id="only-speak", kind="SPEAK", source="policy", score=0.5
                )
            ],
            active_goals=[],
            metacognitive_directive="ABSTAIN",
        )


def test_candidate_selector_hedge_attaches_marker():
    """Fix round: HEDGE must attach a concrete, machine-readable marker to
    the winning candidate so a downstream realizer can add a hedging
    qualifier without re-deriving the directive; PROCEED must never attach
    it."""
    selector = CandidateSelector()
    candidates = [
        ActionCandidate(candidate_id="speak", kind="SPEAK", source="policy", score=0.9),
        ActionCandidate(candidate_id="wait", kind="WAIT", source="reflex", score=0.1),
    ]

    winner, _ = selector.score_and_select(
        candidates, active_goals=[], metacognitive_directive="HEDGE"
    )
    assert winner.candidate_id == "speak"
    assert winner.metadata.get("hedge") is True

    winner_default, _ = selector.score_and_select(candidates, active_goals=[])
    assert winner_default.metadata.get("hedge") is None


def test_candidate_selector_cross_person_privacy_rejection():
    """A candidate whose predicted_outcomes would disclose one person's
    private knowledge to another must always be rejected before scoring,
    with reason privacy_disclosure_violation, and can never win even if it
    scores far higher than the safe alternative."""
    selector = CandidateSelector()
    candidates = [
        ActionCandidate(
            candidate_id="disclose",
            kind="SPEAK",
            source="policy",
            score=0.9,
            evidence_ids=["person-a-private-fact"],
        ),
        ActionCandidate(candidate_id="safe", kind="SPEAK", source="policy", score=0.1),
    ]

    def no_cross_person_disclosure(candidate: ActionCandidate) -> bool:
        return "person-a-private-fact" not in candidate.evidence_ids

    winner, rejected = selector.score_and_select(
        candidates, active_goals=[], privacy_filter=no_cross_person_disclosure
    )

    assert winner.candidate_id == "safe"
    assert any(
        r["candidate_id"] == "disclose"
        and r["reason"] == "privacy_disclosure_violation"
        for r in rejected
    )

    with pytest.raises(ValueError):
        selector.score_and_select(
            candidates, active_goals=[], privacy_filter=lambda c: False
        )


# --- ASCII hygiene -----------------------------------------------------------


def test_phase04_claude_files_are_ascii_only():
    """Phase 04 Package B sources must remain portable 7-bit ASCII artifacts."""
    repository_root = Path(__file__).resolve().parents[2]
    owned_files = [
        repository_root / "backend/app/cognitive/goals.py",
        repository_root / "backend/app/cognitive/learning_review.py",
        repository_root / "backend/app/cognitive/decision.py",
        repository_root / "backend/app/cognitive/pipeline.py",
        repository_root / "backend/app/cognitive/action_candidate.py",
        repository_root / "backend/tests/test_background_governed_learning.py",
    ]
    orchestration_file = repository_root / "orchestration/PHASE_04/CLAUDE_RESULT.md"
    if orchestration_file.exists():
        owned_files.append(orchestration_file)

    for path in owned_files:
        assert path.exists(), f"Missing owned file: {path}"
        assert all(byte < 128 for byte in path.read_bytes()), path
