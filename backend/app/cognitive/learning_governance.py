"""Trusted learning governance and learning-progress curiosity.

Formalizes FINAL_HUMANOID_BRAIN_ARCHITECTURE.md Section 21 ("Learning") and
Section 38 Phase 6 ("Optional advanced learning and planning"): every
persistent behavior change is a `LearningProposal` that must be validated,
approved under a risk-tiered policy, activated as one versioned step, and
remain reversible by a single atomic rollback. Generated inferences cannot
promote themselves, and identity core / safety boundaries are never learned.

Naming note (orchestration/PHASE_06): this module intentionally does not
reuse the `app/cognitive/learning.py` path named in CLAUDE_TASK.md/PLAN.md.
That path is already occupied by `ReflectionService` (Phase "AI Friend Solid
State Learning Layer"), and `app/cognitive/learning_review.py` (Phase 04)
already defines a `LearningProposal` / `LearningProposalStatus` pair with a
different, incompatible schema (PENDING/APPROVED/REJECTED/ROLLED_BACK, no
`activation_revision`, string `risk_class`). Reusing either name here would
either overwrite production code used across 5+ call sites and tests, or
create two same-named-but-incompatible `LearningProposal` classes inside one
package. This module is additive and does not modify either existing file;
see orchestration/PHASE_06/CLAUDE_RESULT.md for the full rationale.

Trusted learning follows the pipeline Section 21 describes: observe outcome
-> attribute credit cautiously -> propose -> validate schema/safety -> test
on held-out and retention suites -> approve by policy/risk -> activate
versioned change -> monitor -> rollback on regression. `LearningGovernor`
owns exactly this state machine; `LearningApprovalGate` owns only the
risk-tiered approval decision, kept separate so an approval policy can be
swapped or unit-tested without touching lifecycle bookkeeping.

Fix round (orchestration/PHASE_06/FIX_PLAN.md, Package B P0-1/P1-2/P1-3):
peer review (`CODEX_REVIEW_OF_CLAUDE.md`) demonstrated the original hard
invariant only ever inspected `target_domain`, so a proposal could name an
innocuous domain while smuggling a protected field into `proposed_value`;
that its delimiter set missed braces/parens/commas/backslashes and any
joined or camelCase spelling; and that nothing re-checked a proposal
between `approve()` and `activate()`/`rollback()`, so a mutated proposal
(pydantic models are not frozen) could bypass every earlier check. All
three are fixed below. Separately, `activate()`/`rollback()` now raise a
dedicated `LearningStateApplyError` rather than leaking whatever exception
`state_applier` happens to raise, and never transition proposal status when
it does; and `LearningProgressCuriosity` rejects non-finite observations
and requires a delta to be large relative to each window's own spread, not
just above a fixed threshold, so a high-variance oscillation can no longer
outrank genuine steady progress.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from collections.abc import Iterator
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ..persona.profile import IMMUTABLE_CORE, PersonaProfile, Tier


class LearningRiskClass(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class LearningProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVATED = "ACTIVATED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class LearningStateApplyError(RuntimeError):
    """Raised when a `state_applier` callback fails inside `activate()` or
    `rollback()`. The proposal's status is never transitioned when this is
    raised -- the caller can trust that a status still reading APPROVED or
    ACTIVATED means the corresponding state write did not go through."""


class LearningProposal(BaseModel):
    """Section 21's proposal record. Every field the architecture requires
    ("source records, proposed target/value, expected effect, risk class,
    training/eval provenance if relevant, counterfactual baseline, approval
    policy, activation revision, rollback value, and post-activation
    measurement") is a real field here, not a dict key a caller has to
    remember to set."""

    proposal_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source_records: list[str] = Field(default_factory=list)
    target_domain: str
    proposed_value: dict[str, Any]
    expected_effect: str
    risk_class: LearningRiskClass
    counterfactual_baseline: float | None = None
    approval_policy: str = "risk_tiered"
    activation_revision: int | None = None
    rollback_value: dict[str, Any] | None = None
    status: LearningProposalStatus = LearningProposalStatus.PROPOSED
    # Populated at construction time rather than a literal 0.0 default: a
    # proposal's audit trail is only useful if "when was this proposed"
    # survives without every caller remembering to stamp it.
    created_at: float = Field(default_factory=time.time)
    evaluated_at: float | None = None
    rejection_reason: str | None = None
    # Fix round (P1-1): Section 21 names "training/eval provenance if
    # relevant" and "post-activation measurement" as part of a proposal's
    # required record, alongside the fields above. Both are optional --
    # most proposals in this codebase are not offline-adapter-training
    # proposals, so "if relevant" is load-bearing -- but when set, they are
    # real, typed fields rather than something bolted onto `proposed_value`.
    training_provenance: dict[str, Any] | None = None
    post_activation_measurement: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Hard invariant: identity core, constitutional bounds, and safety
# boundaries can never be a learning target, at any risk class, from any
# source, whether named directly in `target_domain` or smuggled into a key
# anywhere inside `proposed_value`/`rollback_value`. Every string checked
# below is tokenized on any common delimiter (and camelCase boundary) so a
# proposal cannot dodge the block by choice of separator, bracket, case, or
# joined spelling.
# ---------------------------------------------------------------------------

_STATIC_PROTECTED_PHRASES: tuple[tuple[str, ...], ...] = (
    ("immutable",),
    ("constitutional",),
    ("safety", "invariant"),
    ("safety", "invariants"),
    ("safety", "boundary"),
    ("safety", "boundaries"),
)

# Fix round (P0-1): the original pattern recognized only `.:/[]_-` and
# whitespace. Peer review demonstrated `persona{mood_decay_rate}` (braces)
# slipping through untouched; parentheses, commas, and backslashes are the
# same class of gap. All are now treated as the same word boundary.
_DOMAIN_DELIMITER_PATTERN = re.compile(r"[.:/\[\]{}(),\\_\-\s]+")

# Splits a camelCase boundary (a lowercase-or-digit character immediately
# followed by an uppercase one) into a delimiter, so "moodDecayRate" and
# "userName" tokenize the same way "mood_decay_rate" and "user_name" do.
_CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Strips everything except letters and digits, for the joined-phrase
# substring check below.
_ALNUM_ONLY_PATTERN = re.compile(r"[^a-z0-9]")


def _normalize_domain_tokens(text: str) -> list[str]:
    """Lowercase, delimiter- and camelCase-boundary-separated tokens for
    `text`. Applying the camelCase split before lowercasing is what lets
    "moodDecayRate" tokenize to ["mood", "decay", "rate"] exactly like
    "mood_decay_rate" does."""
    spaced = _CAMEL_BOUNDARY_PATTERN.sub(" ", text or "")
    return [t for t in _DOMAIN_DELIMITER_PATTERN.split(spaced.lower()) if t]


def _tokens_contain_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    """True if `phrase` appears as a contiguous run inside `tokens`."""
    phrase_len = len(phrase)
    if phrase_len == 0 or phrase_len > len(tokens):
        return False
    return any(
        tuple(tokens[start : start + phrase_len]) == phrase
        for start in range(len(tokens) - phrase_len + 1)
    )


def _protected_phrases() -> tuple[tuple[str, ...], ...]:
    """Static safety/immutable markers, plus every IMMUTABLE_CORE key and
    every CONSTITUTIONAL-tier PersonaProfile field name, split on `_` so a
    multi-word field like `mood_decay_rate` matches as a phrase rather than
    requiring an exact underscored token."""
    phrases = list(_STATIC_PROTECTED_PHRASES)
    phrases.extend(tuple(key.split("_")) for key in IMMUTABLE_CORE)
    phrases.extend(
        tuple(name.split("_")) for name in PersonaProfile.fields_in(Tier.CONSTITUTIONAL)
    )
    return tuple(phrases)


_PROTECTED_PHRASES: tuple[tuple[str, ...], ...] = _protected_phrases()
_SINGLE_WORD_PROTECTED: frozenset[str] = frozenset(p[0] for p in _PROTECTED_PHRASES if len(p) == 1)
_MULTI_WORD_PROTECTED: tuple[tuple[str, ...], ...] = tuple(p for p in _PROTECTED_PHRASES if len(p) >= 2)
_JOINED_PROTECTED: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    ("".join(p), p) for p in _PROTECTED_PHRASES if len(p) >= 2
)

# Fix round (orchestration/PHASE_07/FIX_PLAN.md P7-FIX-05): ADAPTIVE-tier
# operation keys explicitly recognized as safe despite colliding, at the
# single-word-token level, with an unrelated CONSTITUTIONAL field name.
# `new_traits` -- the key `IdentityManager.evolve_persona` reads to call
# `PersonaProfile.learn_traits`, an ADAPTIVE-tier, capped trait-*addition*
# operation -- tokenizes to `("new", "traits")`, and "traits" alone is a
# protected single-word marker here because `PersonaProfile.traits` (an
# unrelated, fixed-at-creation CONSTITUTIONAL core temperament list)
# happens to be named exactly that. Rejecting every reflection suggestion
# carrying `new_traits` was a false positive on the one suggestion shape
# `ReflectionService._consolidate_persona` actually produces, not a gap in
# the scan -- see that module's own history for the workaround (renaming
# the key before handing it to the governor) this allowlist replaces.
#
# Matched as an exact, case-sensitive, un-normalized string, deliberately
# not run through `_normalize_domain_tokens`: an obfuscated spelling
# ("New_Traits", "new.traits") gains an attacker nothing, since
# `evolve_persona`'s own lookup (`"new_traits" in suggestions`) would not
# recognize it as the operation either, so normalizing this allowlist
# entry would only create a bypass with no matching legitimate use. A bare
# `"traits"` key, or `new_traits` spelled any other way, is still caught by
# the checks below exactly as before -- this exempts one specific,
# verified-safe key, not the word "traits" in general.
_ADAPTIVE_ALLOWED_FIELD_NAMES: frozenset[str] = frozenset({"new_traits"})


def _string_names_protected_region(text: str) -> tuple[str, ...] | None:
    """The matched phrase if `text` names a protected region under any
    delimiter, camelCase, joined, or casing variation; `None` otherwise.

    Checks `_ADAPTIVE_ALLOWED_FIELD_NAMES` first, against the raw
    (un-normalized) text -- see that constant's docstring for why an exact,
    case-sensitive match is the correct scope for that exemption.
    """
    if not text:
        return None
    if text in _ADAPTIVE_ALLOWED_FIELD_NAMES:
        return None
    tokens = _normalize_domain_tokens(text)
    for t in tokens:
        if t in _SINGLE_WORD_PROTECTED:
            return (t,)
    for phrase in _MULTI_WORD_PROTECTED:
        if _tokens_contain_phrase(tokens, phrase):
            return phrase
    stripped = _ALNUM_ONLY_PATTERN.sub("", text.lower())
    for joined_str, phrase in _JOINED_PROTECTED:
        if joined_str in stripped:
            return phrase
    return None


def _iter_nested_string_keys(value: Any) -> Iterator[str]:
    """Yield every dict key found anywhere inside `value`, at any nesting
    depth, including keys on dicts nested inside list/tuple/set elements.
    This is what lets `check_targets_protected_domain` catch a protected
    field name smuggled into `proposed_value`/`rollback_value` rather than
    written into `target_domain` where the earlier version only looked."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_nested_string_keys(nested)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _iter_nested_string_keys(item)


def _protected_reason(source: str, text: str, phrase: tuple[str, ...]) -> str:
    return (
        f"{source} {text!r} names a protected region "
        f"('{' '.join(phrase)}') -- immutable core, safety invariant, "
        "or constitutional bound -- and can never be a learning target"
    )


def check_targets_protected_domain(
    target_domain: str,
    proposed_value: Any = None,
    rollback_value: Any = None,
) -> tuple[bool, str]:
    """(True, reason) if `target_domain`, or any key anywhere inside
    `proposed_value`/`rollback_value` (at any nesting depth), names the
    immutable persona core, a safety invariant, or a CONSTITUTIONAL-tier
    field -- under any delimiter, camelCase, joined, or casing variation.
    (False, "") otherwise. This is the one check every lifecycle entry
    point below re-runs -- Section 21's "Identity core and safety
    boundaries are never learned" admits no exception by risk class,
    source, or which field of the proposal actually names the target."""
    phrase = _string_names_protected_region(target_domain)
    if phrase is not None:
        return True, _protected_reason("target_domain", target_domain, phrase)

    for label, value in (("proposed_value", proposed_value), ("rollback_value", rollback_value)):
        for key in _iter_nested_string_keys(value):
            phrase = _string_names_protected_region(key)
            if phrase is not None:
                return True, _protected_reason(label, key, phrase)
    return False, ""


def _proposal_targets_protected_region(proposal: LearningProposal) -> tuple[bool, str]:
    """`check_targets_protected_domain` against a live proposal's current
    field values -- deliberately re-reading `proposal.target_domain`/
    `.proposed_value`/`.rollback_value` fresh every call rather than a
    cached copy, since `LearningProposal` is a mutable model and this is
    the re-check that catches a proposal mutated after an earlier check
    already passed."""
    return check_targets_protected_domain(
        proposal.target_domain, proposal.proposed_value, proposal.rollback_value
    )


class LearningApprovalGate:
    """The risk-tiered approval policy alone, stateless apart from the
    optional gatekeeper callback. Kept separate from `LearningGovernor` so
    the policy itself -- what LOW/MEDIUM/HIGH/CRITICAL actually mean -- can
    be tested and swapped without touching proposal bookkeeping.

    Policy: LOW risk auto-approves (a threshold gate, not a bypass -- the
    hard immutable/constitutional check below still applies first).
    MEDIUM and HIGH require an explicit gatekeeper decision. CRITICAL is
    permanently blocked; there is no configuration of this gate that can
    approve one, matching Section 38's "permanently reject online
    uncontrolled self-modification".
    """

    def __init__(self, gatekeeper: Any = None) -> None:
        # gatekeeper: Callable[[LearningProposal], bool] | None. Represents
        # the human or governance-board decision Section 21 requires for
        # MEDIUM/HIGH risk ("approve by policy/human according to risk").
        self._gatekeeper = gatekeeper

    def evaluate(self, proposal: LearningProposal) -> tuple[bool, str]:
        """(approved, reason). Never mutates `proposal`."""
        protected, reason = _proposal_targets_protected_region(proposal)
        if protected:
            return False, reason

        if proposal.risk_class is LearningRiskClass.CRITICAL:
            return False, "CRITICAL risk class is permanently blocked from approval"

        if proposal.risk_class is LearningRiskClass.LOW:
            return True, "LOW risk auto-approved under the risk-tiered policy"

        # MEDIUM or HIGH.
        if self._gatekeeper is None:
            return False, (
                f"{proposal.risk_class.value} risk requires an explicit gatekeeper "
                "decision and none is configured"
            )
        approved = bool(self._gatekeeper(proposal))
        return approved, ("gatekeeper approved" if approved else "gatekeeper rejected")


class LearningGovernor:
    """Owns the `LearningProposal` lifecycle end to end: submit -> validate
    -> approve -> activate -> rollback. One instance per running agent
    process. `_proposals` is the durable, append-only audit trail Section 21
    requires -- a proposal is never deleted, only transitioned, so its full
    history stays addressable by `proposal_id`.
    """

    def __init__(self, gate: LearningApprovalGate | None = None, state_applier: Any = None) -> None:
        self._gate = gate or LearningApprovalGate()
        self._proposals: dict[str, LearningProposal] = {}
        self._revision = 0
        # state_applier(target_domain, value) -> None. How an activated
        # proposal's `proposed_value` is actually written, and how
        # `rollback_value` gets restored. None means the governor only
        # tracks status transitions and leaves applying state to the caller.
        self._state_applier = state_applier

    def submit(self, proposal: LearningProposal) -> LearningProposal:
        """Register a new PROPOSED proposal. Raises ValueError -- and
        registers nothing -- for a duplicate id, a non-PROPOSED status, a
        missing `rollback_value` (a proposal with nothing to roll back to
        can never be safely activated), or a protected `target_domain`/
        `proposed_value`/`rollback_value`. Rejecting here, before the
        proposal is even stored, is the strictest reading of "strictly
        reject" the hard invariant asks for.
        """
        if proposal.proposal_id in self._proposals:
            raise ValueError(f"duplicate proposal_id {proposal.proposal_id!r}")
        if proposal.status is not LearningProposalStatus.PROPOSED:
            raise ValueError(
                f"a newly submitted proposal must be PROPOSED, got {proposal.status.value}"
            )
        if proposal.rollback_value is None:
            raise ValueError(
                "proposal has no rollback_value; a change that cannot be "
                "undone in one step cannot be submitted"
            )
        protected, reason = _proposal_targets_protected_region(proposal)
        if protected:
            raise ValueError(reason)
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    def validate(self, proposal_id: str) -> LearningProposal:
        """PROPOSED -> VALIDATED, or REJECTED if a defense-in-depth
        immutable/constitutional re-check now fails (the model is mutable,
        so any of its fields could in principle have changed since
        submit)."""
        proposal = self._transition(proposal_id, LearningProposalStatus.PROPOSED)
        protected, reason = _proposal_targets_protected_region(proposal)
        if protected:
            return self._reject(proposal, reason)
        proposal.status = LearningProposalStatus.VALIDATED
        return proposal

    def approve(self, proposal_id: str) -> LearningProposal:
        """VALIDATED -> APPROVED via the risk-tiered gate, or REJECTED with
        the gate's stated reason."""
        proposal = self._transition(proposal_id, LearningProposalStatus.VALIDATED)
        approved, reason = self._gate.evaluate(proposal)
        proposal.evaluated_at = time.time()
        if not approved:
            return self._reject(proposal, reason)
        proposal.status = LearningProposalStatus.APPROVED
        return proposal

    def activate(self, proposal_id: str) -> LearningProposal:
        """APPROVED -> ACTIVATED. Re-checks the hard invariant one last
        time against the proposal's current (possibly mutated) fields --
        catching a proposal that passed submit/validate/approve honestly
        and then had `target_domain`/`proposed_value`/`rollback_value`
        mutated afterward -- and, only if that still passes, applies
        `proposed_value` via the configured `state_applier` and stamps a
        new, monotonically increasing `activation_revision`. If the
        invariant now fails, the proposal is rejected the same way
        `validate()` rejects one, since nothing has been written yet. If
        `state_applier` raises, `LearningStateApplyError` propagates and
        the proposal is left APPROVED (not ACTIVATED) -- see
        `LearningStateApplyError`."""
        proposal = self._transition(proposal_id, LearningProposalStatus.APPROVED)
        protected, reason = _proposal_targets_protected_region(proposal)
        if protected:
            return self._reject(proposal, reason)
        if self._state_applier is not None:
            try:
                self._state_applier(proposal.target_domain, proposal.proposed_value)
            except Exception as error:
                raise LearningStateApplyError(
                    f"activation of proposal {proposal_id!r} failed while "
                    f"applying state; proposal remains APPROVED, not "
                    f"ACTIVATED: {error}"
                ) from error
        self._revision += 1
        proposal.activation_revision = self._revision
        proposal.status = LearningProposalStatus.ACTIVATED
        return proposal

    def rollback(self, proposal_id: str) -> LearningProposal:
        """1-step atomic rollback: ACTIVATED -> ROLLED_BACK, restoring
        `rollback_value` via `state_applier` in the same transition that
        flips status. There is no intermediate, partially-rolled-back state
        another reader of this governor can observe -- either this call
        raises and nothing changes, or it fully restores and marks the
        proposal ROLLED_BACK.

        Re-checks the hard invariant first, against the proposal's current
        fields. Unlike `activate()`, a failure here does not reject the
        proposal -- real state may already reflect an ACTIVATED change, so
        silently relabeling it REJECTED would misrepresent that history.
        Instead this raises ValueError and leaves the proposal ACTIVATED,
        refusing to perform the now-untrusted restore. If `state_applier`
        itself raises, `LearningStateApplyError` propagates and the
        proposal is left ACTIVATED (not ROLLED_BACK)."""
        proposal = self._transition(proposal_id, LearningProposalStatus.ACTIVATED)
        protected, reason = _proposal_targets_protected_region(proposal)
        if protected:
            raise ValueError(
                f"proposal {proposal_id!r} now targets a protected region; "
                f"rollback refused and the proposal remains ACTIVATED: {reason}"
            )
        if self._state_applier is not None:
            try:
                self._state_applier(proposal.target_domain, proposal.rollback_value)
            except Exception as error:
                raise LearningStateApplyError(
                    f"rollback of proposal {proposal_id!r} failed while "
                    f"applying state; proposal remains ACTIVATED, not "
                    f"ROLLED_BACK: {error}"
                ) from error
        proposal.status = LearningProposalStatus.ROLLED_BACK
        return proposal

    def get(self, proposal_id: str) -> LearningProposal | None:
        return self._proposals.get(proposal_id)

    def list_proposals(self) -> list[LearningProposal]:
        return list(self._proposals.values())

    def _reject(self, proposal: LearningProposal, reason: str) -> LearningProposal:
        proposal.status = LearningProposalStatus.REJECTED
        proposal.rejection_reason = reason
        return proposal

    def _transition(
        self, proposal_id: str, required_status: LearningProposalStatus
    ) -> LearningProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"unknown proposal_id {proposal_id!r}")
        if proposal.status is not required_status:
            raise ValueError(
                f"proposal {proposal_id!r} is {proposal.status.value}, "
                f"expected {required_status.value}"
            )
        return proposal


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: list[float]) -> float:
    """Population standard deviation of `values` (non-empty by every
    caller's construction -- both windows are always exactly
    `window_size` long)."""
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return variance**0.5


class LearningProgressCuriosity:
    """Learning-progress curiosity (Section 38 Phase 6): ranks domains by
    how fast an empirical error/loss signal is improving, not by how good
    or bad it currently is. This is what keeps exploration off both dead
    ends a naive novelty signal falls into: a domain already mastered (flat,
    near-zero error) is not interesting because there is nothing left to
    learn, and a domain that is pure noise (no consistent trend) is not
    interesting because nothing is actually being learned there either --
    only a domain with a real, sustained error reduction ranks.

    Fix round (P1-3): the original noise filter compared a two-window mean
    delta against one fixed absolute threshold, which peer review showed a
    high-variance oscillation can clear by chance (`[1, 0, 1, 0.2, 0, 0.2]`
    swings between 0 and 1 within each window, yet its raw window-mean
    delta is large). `rank()` now also requires the delta to be large
    relative to each window's own spread (`stability_factor` times the
    larger of the two windows' standard deviations) -- a reliable-trend
    check, not just a magnitude check.
    """

    def __init__(
        self,
        window_size: int = 5,
        noise_threshold: float = 0.02,
        mastery_threshold: float = 0.05,
        stability_factor: float = 2.0,
    ) -> None:
        if window_size < 2:
            raise ValueError("window_size must be at least 2 to form two windows")
        if noise_threshold < 0.0:
            raise ValueError("noise_threshold must be non-negative")
        if mastery_threshold < 0.0:
            raise ValueError("mastery_threshold must be non-negative")
        if stability_factor < 0.0:
            raise ValueError("stability_factor must be non-negative")
        self.window_size = window_size
        self.noise_threshold = noise_threshold
        self.mastery_threshold = mastery_threshold
        self.stability_factor = stability_factor
        self._history: dict[str, list[float]] = {}

    def record(self, domain: str, error: float) -> None:
        """Append one empirical error/loss observation for `domain` (lower
        is better competence). Keeps only the samples needed for two
        adjacent windows, so this stays bounded regardless of how long a
        domain has been tracked. Raises ValueError for a non-finite `error`
        (NaN or +/-infinity) -- neither is an empirical measurement, and a
        NaN in particular would silently poison every downstream mean and
        comparison rather than raising anywhere near its source."""
        if math.isnan(error) or math.isinf(error):
            raise ValueError(f"error must be a finite number, got {error!r}")
        history = self._history.setdefault(domain, [])
        history.append(error)
        max_len = self.window_size * 2
        if len(history) > max_len:
            del history[: len(history) - max_len]

    def progress_delta(self, domain: str) -> float | None:
        """Older-window mean error minus recent-window mean error: positive
        means the domain is improving, negative means it is getting worse.
        `None` until two full adjacent windows exist."""
        history = self._history.get(domain, [])
        if len(history) < self.window_size * 2:
            return None
        recent = history[-self.window_size :]
        older = history[-self.window_size * 2 : -self.window_size]
        return _mean(older) - _mean(recent)

    def is_mastered(self, domain: str) -> bool:
        """True if the recent window's mean error is already at or below
        `mastery_threshold` -- a flat, already-solved routine."""
        history = self._history.get(domain, [])
        if len(history) < self.window_size:
            return False
        recent = history[-self.window_size :]
        return _mean(recent) <= self.mastery_threshold

    def _is_reliable_progress(self, domain: str, delta: float) -> bool:
        """True if `delta` is large relative to the noisier of the two
        windows' own spread, not just numerically above
        `noise_threshold`. A window with zero internal spread (every
        sample identical) makes any positive delta trivially reliable,
        since there is no noise to distinguish it from."""
        history = self._history[domain]
        recent = history[-self.window_size :]
        older = history[-self.window_size * 2 : -self.window_size]
        combined_spread = max(_stdev(recent), _stdev(older))
        if combined_spread <= 0.0:
            return True
        return delta > self.stability_factor * combined_spread

    def rank(self) -> list[tuple[str, float]]:
        """Domains with a real (above-`noise_threshold`, and reliable
        relative to their own within-window spread) positive progress
        delta, excluding mastered ones, ranked highest-progress first."""
        ranked: list[tuple[str, float]] = []
        for domain in self._history:
            if self.is_mastered(domain):
                continue
            delta = self.progress_delta(domain)
            if delta is None or delta <= self.noise_threshold:
                continue
            if not self._is_reliable_progress(domain, delta):
                continue
            ranked.append((domain, delta))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked
