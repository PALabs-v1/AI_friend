import asyncio
import inspect
import logging
import math
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Protocol, get_args, runtime_checkable

from ..config import Config
from ..contracts import StateUpdate
from ..persona.policy import PersonaPolicy
from ..state.session_state import SessionState, persist_session_state
from .action_intent import ActionIntent, ActionKind, build_action_intent
from .behavior_contracts import BehaviorDecision
from .memory_activation import MemoryActivation, memories_to_activations
from .percept import PerceptEnvelope

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .background_scheduler import BackgroundScheduler


@runtime_checkable
class WorkspaceSnapshotLike(Protocol):
    """Structural stand-in for Codex's `CognitiveWorkspaceSnapshot`
    (`app/state/workspace.py`, a parallel Phase 1 work package this pipeline
    must not import directly -- see `CLAUDE_TASK.md`'s file ownership split).
    Any object exposing `epoch`/`revision` satisfies this, including the real
    frozen dataclass once integrated, so `execute()` can commit a causally
    accurate `ActionIntent` today without a hard dependency on a module this
    branch does not own."""

    epoch: int
    revision: int


# --- Endocrine channels ---------------------------------------------------
# Until now both hormones had a release API and almost nothing calling it: one
# channel (somatic comfort recognition) for dopamine and none at all for
# cortisol. A hormone with no channel is only a formula, so these are the
# events that actually move them.
#
# The reward channel is *prediction error*, not outcome. Firing a burst on any
# good turn would double-count what tonic dopamine already tracks -- the tonic
# term is valence x arousal, and a good turn raises valence by itself. Phasic
# dopamine is supposed to signal "better than I expected", per the Schultz
# reward-prediction-error work the `dopamine_phasic` docstring cites. The
# reappraisal module was already computing exactly that quantity and throwing
# it away after using it to tune weights.
#
# Scaled well below 1.0: a single surprising turn should colour the next few
# minutes, not saturate the hormone. Deliberately asymmetric -- the stress
# response to a turn going badly is stronger than the reward for one going
# well, which is the standard negativity bias and also the safer failure mode
# for a system whose cortisol narrows its own sampling temperature.
REWARD_PREDICTION_GAIN = 0.35
STRESS_PREDICTION_GAIN = 0.45
# Below this, a prediction error is noise rather than surprise. Reappraisal
# already ignores |delta| < 0.1 for weight updates; matching that keeps one
# definition of "significant" instead of two that can drift apart.
PREDICTION_ERROR_DEADBAND = 0.1
# A self-correction means the agent caught itself about to violate its own
# identity constraints mid-sentence. Fixed rather than scaled: the severity of
# a violation is not something the metacognitive check reports, and inventing a
# magnitude for it would be false precision.
SELF_CORRECTION_STRESS = 0.3


class CognitivePipeline:
    """
    Pure Logic Pipeline for the Cognitive Loop.
    Transport-agnostic (Zero NATS/HTTP dependencies).

    Pipeline (psychological_layer.md System Principle):
        Signal -> Perception -> Appraisal -> State Update -> Decision -> Action -> Learning
    """

    def __init__(
        self,
        perception,
        appraisal,
        state,
        decision,
        action,
        learning,
        identity,
        llm_service=None,
        reappraisal=None,
        session_store=None,
        scheduler: "BackgroundScheduler | None" = None,
        workspace_store=None,
        temporal_memory_store=None,
        plan_verifier=None,
        plan_executor=None,
        episodic_simulator=None,
        learning_governor=None,
        offline_adapter_gate=None,
        provider_capability_negotiator=None,
        external_action_dispatcher=None,
    ):
        self.perception = perception
        self.appraisal = appraisal
        self.state = state
        self.decision = decision
        self.action = action
        self.learning = learning
        self.identity = identity
        self.llm = llm_service
        self.reappraisal = reappraisal
        self._system2_task = None
        # Phase 2B: a `WorkingMemoryStore` for per-turn `SessionState`.
        # Optional, like `reappraisal` above -- a pipeline built without one
        # (most unit tests) just skips session persistence, same reasoning as
        # `if self.reappraisal:` elsewhere in this class.
        self.session_store = session_store
        # Phase 04 Package B: the background scheduler this turn must
        # preempt. Optional and additive, same reasoning as every other
        # service on this class -- a pipeline built without one (every
        # pre-Phase-04 caller) just never calls preempt/resume.
        self.scheduler = scheduler
        self.workspace_store = workspace_store
        self.temporal_memory_store = temporal_memory_store
        self.plan_verifier = plan_verifier
        self.plan_executor = plan_executor
        self.episodic_simulator = episodic_simulator
        self.learning_governor = learning_governor
        self.offline_adapter_gate = offline_adapter_gate
        self.provider_capability_negotiator = provider_capability_negotiator
        self.external_action_dispatcher = external_action_dispatcher

    async def _persist_turn_session(
        self,
        raw_event: dict[str, Any],
        event_metadata: dict[str, Any],
        session_state: "SessionState",
        workspace: WorkspaceSnapshotLike | None,
    ) -> WorkspaceSnapshotLike | None:
        """Persist session state and refresh the authoritative workspace view."""
        workspace_session_id = getattr(workspace, "session_id", None) or raw_event.get(
            "user_id"
        ) or event_metadata.get("user_id")
        await persist_session_state(
            self.session_store,
            session_state,
            workspace_store=self.workspace_store,
            workspace_session_id=workspace_session_id,
        )
        if self.workspace_store is not None and bool(
            getattr(Config, "WORKSPACE_AUTHORITATIVE", False)
        ):
            return await self.workspace_store.get_snapshot(
                workspace_session_id or "default"
            )
        return workspace

    def _maybe_preempt_background(self) -> None:
        """Immediately abort any in-flight background job -- a foreground
        turn always wins (Architecture Section 19). Called once at the top
        of every `execute()`, before any other stage."""
        if self.scheduler is not None:
            self.scheduler.preempt()

    def _maybe_resume_background(self) -> None:
        """Allow background work to run again once this turn is done.

        Called from a single `finally` block wrapping the whole body of
        `execute()`, so this always runs exactly once per `execute()` call
        regardless of how it exits -- a normal return, an early return, an
        exception from any stage, or the caller closing the generator
        early. `BackgroundScheduler.resume_foreground_idle` is itself a
        reference-count decrement, so an unmatched extra call here would
        under-count a still-active nested preemption; the 1:1 pairing with
        the single `_maybe_preempt_background()` call at the top of
        `execute()` keeps the count exact.
        """
        if self.scheduler is not None:
            self.scheduler.resume_foreground_idle()

    async def _resolve_turn_conflict(
        self,
        raw_event: dict[str, Any],
        raw_event_type,
        event_metadata: dict[str, Any],
        stage_times: dict[str, Any],
        result: dict[str, Any],
        session_state: "SessionState | None" = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stage 2: turn-taking conflict resolution against a pending
        speculative intent. Yields the resume/stop mesh_signal, if any.

        `result["stop"]` is set True when a confirmed interruption means the
        whole pipeline must stop here -- an async generator can't both yield
        chunks and return a value (see `_stream_action_pass`), so the
        stop signal goes through this mutable dict instead.

        Phase 2B: `session_state.active_interruption` is set to "stop" on a
        confirmed interrupt -- `SessionState`'s first real downstream
        consumer, not just a value carried through unread. Left at its
        default "none" on rejection (playback resumes; nothing was actually
        interrupted). `session_state=None` (a caller that predates this
        parameter) skips the write, same optional-service pattern as
        `self.session_store`.
        """
        import time

        result["stop"] = False
        t_start = time.perf_counter()
        if raw_event_type == "USER_MESSAGE" and not raw_event.get("is_partial"):
            final_text = raw_event.get("content", "")
            speculative_intent = self.state.last_speculative_intent

            if speculative_intent:
                confirmed = self.decision.is_speculative_stop_confirmed(
                    final_text,
                    speculative_intent.get("keywords"),
                )
                self.state.last_speculative_intent = None
                # `session_state.turn_id` is always populated (`SessionState.
                # start_turn` generates one when nothing upstream supplies
                # it); `event_metadata["turn_id"]` usually isn't set at all,
                # so preferring it here would silently publish `None` on the
                # common path. Fall back to the raw metadata only for the
                # pre-2B caller shape (`session_state=None`).
                turn_id = (
                    session_state.turn_id
                    if session_state is not None
                    else event_metadata.get("turn_id")
                )
                # The stop/resume must address the reply that is *playing*,
                # not this new utterance: the voice agent and transport only
                # honour a turn-scoped signal whose turn_id matches the turn
                # they are currently speaking (`mesh_signal_applies_to_active_turn`).
                # This utterance's own id never matches, so a confirmed "stop"
                # used to leave the old reply playing ducked at 30% and a
                # rejected interruption's resume never restored its volume.
                # The brain records the turn it superseded as
                # `interrupted_turn_id`; the utterance's own id stays the
                # fallback for callers that do not supply it.
                turn_id = event_metadata.get("interrupted_turn_id") or turn_id

                if not confirmed:
                    logger.info(
                        "[Pipeline] Interruption REJECTED. Resuming playback..."
                    )
                    stage_times["stage_2_conflict_resolution_ms"] = (
                        time.perf_counter() - t_start
                    ) * 1000.0
                    yield {
                        "type": "mesh_signal",
                        "subject": "audio.resume",
                        "data": {
                            "reason": "conflict_rejected",
                            "perception_text": speculative_intent.get("text", ""),
                            "utterance_id": speculative_intent.get("utterance_id"),
                            # Phase 2D (Section 15 item 8): mirrors audio.stop's
                            # existing turn_id stamp below -- makes resume
                            # turn-scoped symmetrically with stop.
                            "turn_id": turn_id,
                        },
                    }
                    return
                else:
                    logger.info("[Pipeline] Interruption CONFIRMED. Stopping playback.")
                    if session_state is not None:
                        session_state.active_interruption = "stop"
                    stage_times["stage_2_conflict_resolution_ms"] = (
                        time.perf_counter() - t_start
                    ) * 1000.0
                    yield {
                        "type": "mesh_signal",
                        "subject": "audio.stop",
                        "data": {
                            "interrupt": True,
                            "speculative": False,
                            "reason": "confirmed_command",
                            "command_text": final_text,
                            "keywords": speculative_intent.get("keywords", []),
                            "utterance_id": speculative_intent.get("utterance_id"),
                            "turn_id": turn_id,
                        },
                    }
                    result["stop"] = True
                    return
        stage_times["stage_2_conflict_resolution_ms"] = (
            time.perf_counter() - t_start
        ) * 1000.0

    async def _update_state_from_appraisal(
        self,
        event,
        event_metadata: dict[str, Any],
        raw_event: dict[str, Any],
        appraisal_vector,
        state_snapshot,
        stage_times: dict[str, Any],
        result: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stage 5: reappraisal outcome eval, ToM update, apply appraisal to
        state, kick off System 2. Yields the state.update mesh_signal for a
        USER_MESSAGE turn. `result["state_snapshot"]` carries the (possibly
        refreshed) snapshot back out -- an async generator can't both yield
        chunks and return a value (see `_stream_action_pass`)."""
        import time

        t_start = time.perf_counter()
        if event.event_type == "USER_MESSAGE":
            # Reappraisal outcome evaluation
            if self.reappraisal:
                acoustic_delta = self._resolve_acoustic_delta(event_metadata, raw_event)
                actual_text_valence = self._resolve_actual_text_valence(
                    appraisal_vector, event
                )

                prediction_error = await self.reappraisal.evaluate_outcome(
                    actual_text_valence=actual_text_valence,
                    acoustic_delta=acoustic_delta,
                    behavioral_signal=0.5,
                )
                await self._apply_reward_prediction_error(prediction_error)

            # Pre-Decision Vocabulary Update (zero LLM overhead concepts indexing)
            await self.state.update_theory_of_mind(event.raw_content)

            weights = self.reappraisal.get_weights() if self.reappraisal else None
            await self.state.update_from_appraisal(appraisal_vector, weights=weights)

            # Trigger System 2 deep appraisal in background (non-blocking).
            # A2: cancel any still-running prior appraisal so overlapping
            # tasks cannot clobber each other's writes to short-term affect.
            if self.llm and getattr(Config, "SYSTEM2_APPRAISAL_ENABLED", True):
                if self._system2_task and not self._system2_task.done():
                    self._system2_task.cancel()
                self._system2_task = asyncio.create_task(
                    self._async_system2_appraisal(event.raw_content)
                )

            state_snapshot = self.state.get_context_snapshot()
            stage_times["stage_5_state_update_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0
            yield {
                "type": "mesh_signal",
                "subject": "state.update",
                "data": StateUpdate.from_snapshot(state_snapshot).model_dump(),
            }
        else:
            stage_times["stage_5_state_update_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0
        result["state_snapshot"] = state_snapshot

    @staticmethod
    def _resolve_acoustic_delta(
        event_metadata: dict[str, Any], raw_event: dict[str, Any]
    ) -> float:
        if isinstance(event_metadata, dict) and "acoustic_metadata" in event_metadata:
            return event_metadata["acoustic_metadata"].get("emotion_bias", 0.0)
        if "acoustic_metadata" in raw_event:
            return raw_event["acoustic_metadata"].get("emotion_bias", 0.0)
        return 0.0

    @staticmethod
    def _resolve_actual_text_valence(appraisal_vector, event) -> float:
        actual_text_valence = appraisal_vector.goal_congruence
        tom = event.metadata.get("tom_inferences", {}) if event.metadata else {}
        if isinstance(tom, dict) and "inferred_valence" in tom:
            return tom["inferred_valence"]
        return actual_text_valence

    async def _apply_post_decision_tom(
        self, event, state_snapshot, result: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stage 6's ToM tail: a USER_MESSAGE turn with fresh tom_inferences
        re-applies them to state and yields the refreshed state.update.
        `result["state_snapshot"]` carries the (possibly refreshed) snapshot
        back out, same reasoning as `_update_state_from_appraisal`."""
        if event.event_type == "USER_MESSAGE":
            tom_inferences = event.metadata.get("tom_inferences")
            if tom_inferences:
                await self.state.update_theory_of_mind(
                    event.raw_content, tom_inferences
                )
                state_snapshot = self.state.get_context_snapshot()
                yield {
                    "type": "mesh_signal",
                    "subject": "state.update",
                    "data": StateUpdate.from_snapshot(state_snapshot).model_dump(),
                }
        result["state_snapshot"] = state_snapshot

    async def _validate_and_self_correct(
        self,
        plan,
        is_spec: bool,
        full_response: str,
        done_chunk: dict[str, Any] | None,
        result: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stage 9: identity-boundary validation, with one self-correction
        retry pass on failure. `result["full_response"]`/`result["done_chunk"]`
        carry the (possibly corrected) output back out; `done_chunk` passes
        stage 8's value through unchanged when validation doesn't retry."""
        if full_response:
            is_valid, reason = await self.identity.validate_response(
                full_response, plan.goal
            )
            if not is_valid:
                logger.warning(
                    f"[Identity] Validation failed: {reason}. SELF-CORRECTION."
                )
                plan.payload["identity_prompt"] += (
                    f"\n\nCRITICAL FIX: Your previous response was rejected for: {reason}. Correct this immediately."
                )
                retry_result: dict[str, Any] = {"response": "", "done": None}
                async for chunk in self._stream_action_pass(
                    plan, is_spec, retry_result
                ):
                    yield chunk
                full_response = retry_result["response"]
                done_chunk = retry_result["done"]
        result["full_response"] = full_response
        result["done_chunk"] = done_chunk

    def _prepare_action_payload(
        self, plan, state_snapshot, event, state_directive
    ) -> None:
        """Stage 7: populate `plan.payload` with everything action.py's
        endocrine-to-sampling mapping and prompt assembly need. Pure -- no
        yields, so it's a plain call from `execute` rather than a
        sub-generator."""
        plan.payload["identity_prompt"] = self.identity.get_persona_prompt()
        plan.payload["state_directive"] = state_directive
        plan.payload["cortisol"] = state_snapshot.get("cortisol", 0.5)
        plan.payload["dopamine"] = state_snapshot.get("dopamine", 0.0)
        plan.payload["fatigue"] = state_snapshot.get("fatigue", 0.0)
        plan.payload["user_mental_model"] = state_snapshot.get("user_mental_model")
        plan.payload["valence"] = state_snapshot.get("mood", 0.0)
        plan.payload["arousal"] = state_snapshot.get("energy", 0.5)
        plan.payload["dominance"] = state_snapshot.get("dominance", 0.5)
        plan.payload["speculative"] = (
            event.metadata.get("speculative", False) if event.metadata else False
        )
        plan.payload["visual_context"] = (
            event.metadata.get("visuals") if event.metadata else None
        )
        plan.payload["visual_evidence"] = (
            event.metadata.get("visual_evidence") if event.metadata else None
        )
        if plan.behavior_decision is not None:
            plan.behavior_decision = PersonaPolicy.precheck(
                plan.behavior_decision, self.identity.immutable_core
            )

    @staticmethod
    def _compute_pre_llm_telemetry(stage_times: dict[str, Any], event) -> None:
        """Fold stages 1-7's timings into a pre-LLM total, plus the
        intent/ToM fields the telemetry payload reports."""
        pre_llm_total = sum(
            [
                stage_times["stage_1_extraction_ms"],
                stage_times["stage_2_conflict_resolution_ms"],
                stage_times["stage_3_perception_ms"],
                stage_times["stage_4_appraisal_ms"],
                stage_times["stage_5_state_update_ms"],
                stage_times["stage_6_decision_ms"],
                stage_times["stage_7_action_prep_ms"],
            ]
        )
        stage_times["pre_llm_total_ms"] = pre_llm_total
        stage_times["heuristic_intent"] = (
            event.metadata.get("heuristic_intent") or event.intent
        )
        stage_times["llm_intent"] = event.intent
        tom_inferences = event.metadata.get("tom_inferences") or {}
        stage_times["inferred_valence"] = tom_inferences.get("inferred_valence")
        stage_times["inferred_arousal"] = tom_inferences.get("inferred_arousal")

    @staticmethod
    def _build_consolidation_episode(
        event,
        raw_event,
        state_directive,
        appraisal_vector,
        state,
        state_snapshot,
        full_response,
    ) -> dict[str, Any]:
        """Stage 10: shape one turn into the schema `learning._consolidate`
        expects."""
        return {
            "id": event.event_id,
            "event": event.raw_content,
            "speaker": raw_event.get("user_id") or "User",
            "context": state_directive,
            "emotion_vector": {
                "V": state.current_state.valence,
                "Ar": state.current_state.arousal,
                "D": state.current_state.dominance,
            },
            "appraisal": appraisal_vector.to_dict(),
            "relationship_delta": appraisal_vector.relationship_impact,
            "intent": event.intent,
            "content": event.raw_content,
            "state": state_snapshot,
            "response": full_response,
        }

    def _check_vap_pregeneration(
        self,
        raw_event: dict[str, Any],
        raw_event_type: str | None,
        event_metadata: dict[str, Any],
        session_state: Any,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Evaluate VAP / partial input and return (should_abort, pregen_signal)."""
        is_partial = raw_event.get("is_partial", False)
        vap_prob = raw_event.get(
            "vap_probability", event_metadata.get("vap_probability", 0.0)
        )
        if not (is_partial or raw_event_type == "VAP_SIGNAL"):
            return (False, None)

        if vap_prob < 0.7:
            logger.debug(
                f"[Pipeline] Partial input/VAP but probability {vap_prob:.2f} < 0.7. Skipping speculative pre-generation."
            )
            return (True, None)

        logger.info(
            f"[Pipeline] VAP threshold met ({vap_prob:.2f} >= 0.7). Triggering speculative pre-generation."
        )
        event_metadata["speculative"] = True
        session_state.speculative = True
        signal = {
            "type": "mesh_signal",
            "subject": "audio.pre_generate",
            "data": {
                "speculative": True,
                "partial_content": raw_event.get("content", ""),
                "vap_probability": vap_prob,
            },
        }
        return (False, signal)

    # Stage 6 action-type -> ActionIntent.kind. "RESPOND_CHAT"/"STORE_MEMORY"
    # both still end in `action.py` streaming spoken content (see
    # `_execute_store_memory`'s "Got it, I've committed that to memory."), so
    # both map to SPEAK; only the reflection path produces no speech at all.
    _ACTION_KIND_BY_TYPE: dict[str, ActionKind] = {
        "BACKGROUND_CONSOLIDATION": "REFLECT",
        # Reachable only as a defensive fallback: decision.py never sets
        # action_type="CLARIFY" without also setting a selected_candidate
        # of kind "ASK", which _derive_action_kind reads first below.
        "CLARIFY": "ASK",
        "WAIT": "WAIT",
        # Phase 03 Package B: same defensive-fallback reasoning as CLARIFY/
        # ASK above -- decision.py never sets these action_types without
        # also selecting a candidate of the matching kind.
        "REAPPRAISE": "REAPPRAISE",
        "REDIRECT_ATTENTION": "REDIRECT_ATTENTION",
    }
    _VALID_ACTION_KINDS: frozenset[str] = frozenset(get_args(ActionKind))

    def _decision_accepts_memory_activations(self) -> bool:
        """Fix round (Codex review B8): detect whether the injected decision
        service can accept the memory_activations keyword before passing it,
        so an existing DecisionService-compatible double (or a hand-written
        test stub) with the pre-Phase-02 two-argument decide(event,
        state_snapshot) signature is not broken by an unconditional third
        argument. PLAN.md section 5's compatibility requirement applies to
        every dependency-injection seam, not only the concrete
        DecisionService this pipeline ships with.

        A MagicMock/AsyncMock double -- no fixed signature, accepts
        **kwargs by construction -- is treated as compatible, matching how
        this codebase's own test suite already injects decision doubles.
        """
        try:
            parameters = inspect.signature(self.decision.decide).parameters
        except (TypeError, ValueError):
            # No introspectable signature (e.g. some C-implemented or
            # exotic callables) -- assume compatible rather than silently
            # downgrading every such caller to the legacy call shape.
            return True
        if "memory_activations" in parameters:
            return True
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    def _decision_accepts_global_controls(self) -> bool:
        """Phase 03 Package B: same reasoning and shape as
        `_decision_accepts_memory_activations`, for the `global_controls`
        keyword this phase adds to `decide()` -- protects an injected
        decision double or hand-written test stub whose `decide()` predates
        this parameter from an unconditional keyword breaking it.
        """
        try:
            parameters = inspect.signature(self.decision.decide).parameters
        except (TypeError, ValueError):
            return True
        if "global_controls" in parameters:
            return True
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    def _derive_action_kind(self, plan) -> ActionKind:
        """Phase 02 Package B: when CandidateSelector picked a winner
        (`Config.MEMORY_TRUTH_ENABLED` True), Stage 6 commits *that*
        candidate's kind instead of the old action_type heuristic below --
        the whole point of generalized action selection is that the
        committed ActionIntent reflects what was actually chosen, not a
        fixed mapping from action_type. Falls back to the Phase 1 mapping
        whenever no candidate was selected (flag off, or a non-social plan
        such as BACKGROUND_CONSOLIDATION that never reaches candidate
        selection)."""
        behavior_decision = plan.behavior_decision
        if isinstance(behavior_decision, BehaviorDecision):
            selected = behavior_decision.selected_candidate
            if selected:
                candidate_kind = selected.get("kind")
                if candidate_kind in self._VALID_ACTION_KINDS:
                    return candidate_kind
        return self._ACTION_KIND_BY_TYPE.get(plan.action_type, "SPEAK")

    def _commit_action_intent(
        self,
        plan,
        session_state: "SessionState | None",
        workspace: WorkspaceSnapshotLike | None,
        percept: PerceptEnvelope | None,
    ) -> ActionIntent:
        """Stage 6: the typed commitment made before Stage 8 generates
        anything (Sections 22, 38) -- always produced, so every turn cites exactly
        one `(epoch, revision)` tuple (AC-01), not only turns whose BT branch
        happened to attach a `BehaviorDecision`.

        `isinstance` rather than `is not None`: `ActionPlan.behavior_decision`
        is untyped `Any` on several existing test doubles (e.g. a bare
        `MagicMock()` plan), where the attribute auto-vivifies as a truthy
        mock rather than `None`. Falling back to the synthesized payload for
        anything that is not a real `BehaviorDecision` keeps this additive
        rather than newly required of every existing caller.
        """
        behavior_decision_payload: dict[str, Any] = (
            plan.behavior_decision.model_dump()
            if isinstance(plan.behavior_decision, BehaviorDecision)
            else {"goal": plan.goal, "action_type": plan.action_type}
        )
        behavior_decision_payload["percept_id"] = (
            percept.percept_id if percept is not None else None
        )
        turn_id = (
            session_state.turn_id
            if session_state is not None
            else behavior_decision_payload.get("goal", "unknown-turn")
        )
        return build_action_intent(
            turn_id=turn_id,
            workspace_epoch=workspace.epoch if workspace is not None else 0,
            workspace_revision=workspace.revision if workspace is not None else 0,
            kind=self._derive_action_kind(plan),
            behavior_decision=behavior_decision_payload,
        )

    def _extract_event_and_session(
        self, raw_event: dict[str, Any]
    ) -> tuple[Any, dict[str, Any], SessionState]:
        raw_event_type = raw_event.get("event_type") or raw_event.get("type")
        event_metadata = raw_event.get("metadata", {})
        if not isinstance(event_metadata, dict):
            event_metadata = {}
        session_state = SessionState.start_turn(
            turn_id=event_metadata.get("turn_id"),
            utterance_id=raw_event.get("utterance_id")
            or event_metadata.get("utterance_id"),
        )
        event_metadata["session_state"] = session_state
        return raw_event_type, event_metadata, session_state

    def _setup_memory_activations(
        self,
        surfaced_memories: list[dict[str, Any]] | None,
        memory_activations: list[MemoryActivation] | None,
        event: Any,
    ) -> list[MemoryActivation] | None:
        activations = memory_activations
        if Config.MEMORY_TRUTH_ENABLED and activations is None:
            activations = memories_to_activations(surfaced_memories)
        if Config.MEMORY_TRUTH_ENABLED and activations:
            event.metadata["retrieval_degraded"] = any(
                activation.outage_flag for activation in activations
            )
        return activations

    def _build_decide_kwargs(
        self,
        memory_activations: list[MemoryActivation] | None,
        state_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        decide_kwargs: dict[str, Any] = {}
        if Config.MEMORY_TRUTH_ENABLED and self._decision_accepts_memory_activations():
            decide_kwargs["memory_activations"] = memory_activations
        if Config.AFFECT_CONTROL_ENABLED and self._decision_accepts_global_controls():
            decide_kwargs["global_controls"] = state_snapshot.get("global_controls")
        return decide_kwargs

    def _record_reappraisal_pre_response(
        self, plan: Any, state_snapshot: dict[str, Any]
    ) -> None:
        if self.reappraisal:
            self.reappraisal.record_pre_response_state(state_snapshot)
            self.reappraisal.record_expected_outcome(
                plan.goal, state_snapshot.get("mood", 0.0)
            )

    def _check_reflection_needed(
        self,
        event: Any,
        raw_event: dict[str, Any],
        state_directive: str,
        appraisal_vector: Any,
        state_snapshot: dict[str, Any],
        full_response: str,
    ) -> dict[str, Any] | None:
        if event.intent in ["CHAT", "REMEMBER"] and full_response:
            episode = self._build_consolidation_episode(
                event,
                raw_event,
                state_directive,
                appraisal_vector,
                self.state,
                state_snapshot,
                full_response,
            )
            return {"type": "reflection_needed", "data": [episode]}
        return None

    def _emit_terminal_done_chunk(
        self, done_chunk: dict[str, Any] | None, is_spec: bool
    ) -> dict[str, Any]:
        if done_chunk:
            if is_spec:
                done_chunk["speculative"] = True
            return done_chunk
        return {"type": "done", "data": "finished", "speculative": is_spec}

    async def execute(
        self,
        raw_event: dict[str, Any],
        surfaced_memories: list[dict[str, Any]] | None = None,
        percept: PerceptEnvelope | None = None,
        workspace: WorkspaceSnapshotLike | None = None,
        memory_activations: list[MemoryActivation] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Executes the master cognitive loop.
        Yields events/chunks for the agent wrapper to handle.

        `percept` is the Phase 1 normalized `PerceptEnvelope` for this turn
        (Section 7) and `workspace` is the current `CognitiveWorkspaceSnapshot` (Section 38,
        Codex's `app/state/workspace.py`) -- both optional and additive: a
        caller that predates them (this pipeline's existing production
        caller, `CognitiveService.process_event`) keeps working unchanged,
        and Stage 6 still commits an `ActionIntent` against a `(0, 0)`
        fallback tuple rather than skipping the causal trace entirely.

        `memory_activations` (Phase 02 Package B, Sections 8/11/22/39) is
        likewise optional and additive. When `Config.MEMORY_TRUTH_ENABLED`
        is False (the default) it is accepted but not acted on, preserving
        exact Phase 1 behavior for every existing caller. When True and any
        activation carries `outage_flag=True`, this marks the turn's
        decision metadata `retrieval_degraded=True` rather than letting a
        retrieval failure look identical to a real absence of memories, and
        threads the activations into `DecisionService.decide` so active
        memory can shift which `ActionCandidate` Stage 6 commits.

        Phase 03 Package B (Sections 9, 10, 21, 38): when
        `Config.AFFECT_CONTROL_ENABLED` is True, this also reads a
        `global_controls` value off `state_snapshot` (key "global_controls",
        left `None` until Package A's global_controls.py populates it) and
        threads it into `DecisionService.decide` so global controls can
        modulate candidate scoring and acute distress can raise emotion-
        regulation candidates. False (the default) preserves exact prior
        behavior -- `global_controls` is never read or passed.
        """
        import time

        # Phase 04 Package B: a foreground turn always preempts any
        # in-flight background job (Architecture Section 19) -- called
        # before any other stage so nothing here can race a background
        # write. The `try/finally` below guarantees `_maybe_resume_background`
        # runs on every exit from this generator -- a normal return, an early
        # return, an exception raised by any stage, or the caller closing the
        # generator early (`aclose()`, which resumes this frame with
        # `GeneratorExit` at whichever `yield` is currently suspended) all
        # unwind through the `finally`. A bare call at the end of the method
        # body (the pre-fix-round approach) only ran on the single normal
        # exit path and left the scheduler permanently preempted on any of
        # the others.
        self._maybe_preempt_background()
        try:
            stage_times: dict[str, Any] = {}

            # 1. Extraction & Metadata
            t_start = time.perf_counter()
            raw_event_type, event_metadata, session_state = (
                self._extract_event_and_session(raw_event)
            )
            stage_times["stage_1_extraction_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0

            # VAP Turn Planning / Speculative Pre-Generation
            should_abort, vap_signal = self._check_vap_pregeneration(
                raw_event, raw_event_type, event_metadata, session_state
            )
            if should_abort:
                return
            if vap_signal is not None:
                yield vap_signal

            # 2. Conflict Resolution (Turn-Taking Stability)
            conflict_result: dict[str, Any] = {}
            async for chunk in self._resolve_turn_conflict(
                raw_event,
                raw_event_type,
                event_metadata,
                stage_times,
                conflict_result,
                session_state,
            ):
                yield chunk
            if conflict_result["stop"]:
                workspace = await self._persist_turn_session(
                    raw_event, event_metadata, session_state, workspace
                )
                yield {"type": "pipeline_telemetry", "data": stage_times}
                return
            workspace = await self._persist_turn_session(
                raw_event, event_metadata, session_state, workspace
            )

            # 3. Sequential Perception
            t_start = time.perf_counter()
            event = await self.perception.perceive(raw_event)
            stage_times["stage_3_perception_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0
            if event.metadata is None:
                event.metadata = {}
            event.metadata["session_state"] = session_state
            if event_metadata.get("speculative"):
                event.metadata["speculative"] = True
            memory_activations = self._setup_memory_activations(
                surfaced_memories, memory_activations, event
            )

            # 4. Appraisal (Section 1 -- OCC/Lazarus/EMA)
            t_start = time.perf_counter()
            state_snapshot = self.state.get_context_snapshot()
            emotional_bias = state_snapshot.get("mood", 0.0)
            user_voice_properties = raw_event.get("user_voice_properties")
            appraisal_vector = self.appraisal.appraise(
                event_content=event.raw_content,
                event_type=event.event_type,
                emotional_bias=emotional_bias,
                state_snapshot=state_snapshot,
                identity_boundaries=self.identity.immutable_core["boundaries"],
                user_voice_properties=user_voice_properties,
            )
            stage_times["stage_4_appraisal_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0
            yield {"type": "appraisal", "data": appraisal_vector}

            # 5. State Update via Appraisal (Section 2.3 -- ALMA mood-pull)
            state_update_result: dict[str, Any] = {}
            async for chunk in self._update_state_from_appraisal(
                event,
                event_metadata,
                raw_event,
                appraisal_vector,
                state_snapshot,
                stage_times,
                state_update_result,
            ):
                yield chunk
            state_snapshot = state_update_result["state_snapshot"]

            # 6. Decision (BT + MAUT)
            t_start = time.perf_counter()
            state_directive = self.state.get_behavioral_directive()
            if surfaced_memories:
                event.metadata["surfaced_memories"] = surfaced_memories
            event.metadata["appraisal"] = appraisal_vector.to_dict()

            decide_kwargs = self._build_decide_kwargs(
                memory_activations, state_snapshot
            )
            plan = await self.decision.decide(event, state_snapshot, **decide_kwargs)

            self._record_reappraisal_pre_response(plan, state_snapshot)

            # Post-Decision ToM Application: ingest LLM-inferred parameters to state
            tom_result: dict[str, Any] = {}
            async for chunk in self._apply_post_decision_tom(
                event, state_snapshot, tom_result
            ):
                yield chunk
            state_snapshot = tom_result["state_snapshot"]

            # 6b. Explicit ActionIntent commitment (Sections 22, 38)
            action_intent = self._commit_action_intent(
                plan, session_state, workspace, percept
            )
            yield {"type": "action_intent", "data": action_intent.model_dump()}

            stage_times["stage_6_decision_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0

            # 7. Action Preparation
            t_start = time.perf_counter()
            self._prepare_action_payload(plan, state_snapshot, event, state_directive)
            stage_times["stage_7_action_prep_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0

            self._compute_pre_llm_telemetry(stage_times, event)

            # 8. Action Execution
            t_start = time.perf_counter()
            full_response: str = ""
            done_chunk: dict[str, Any] | None = None
            is_spec = plan.payload.get("speculative", False)
            pass_result: dict[str, Any] = {"response": "", "done": None}
            async for chunk in self._stream_action_pass(plan, is_spec, pass_result):
                yield chunk
            full_response = pass_result["response"]
            done_chunk = pass_result["done"]
            stage_times["stage_8_action_execution_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0

            # 9. Validation & Self-Correction
            t_start = time.perf_counter()
            validation_result: dict[str, Any] = {}
            async for chunk in self._validate_and_self_correct(
                plan, is_spec, full_response, done_chunk, validation_result
            ):
                yield chunk
            full_response = validation_result["full_response"]
            done_chunk = validation_result["done_chunk"]
            stage_times["stage_9_validation_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0

            # 10. Learning + Episodic Memory (Section 6.1)
            t_start = time.perf_counter()
            reflection_event = self._check_reflection_needed(
                event,
                raw_event,
                state_directive,
                appraisal_vector,
                state_snapshot,
                full_response,
            )
            if reflection_event is not None:
                yield reflection_event
            stage_times["stage_10_learning_ms"] = (
                time.perf_counter() - t_start
            ) * 1000.0

            # Yield gathered stage telemetry
            yield {"type": "pipeline_telemetry", "data": stage_times}

            # Yield the saved done chunk at the very end
            yield self._emit_terminal_done_chunk(done_chunk, is_spec)
        finally:
            self._maybe_resume_background()

    async def _stream_action_pass(self, plan, is_spec: bool, result: dict):
        """Run one generation pass, yielding what the transport should see.

        The first pass and the self-correction retry were two copies of this
        loop, and they had already drifted: the retry never tagged its chunks
        with `speculative`, so a speculative turn that got self-corrected
        emitted a stream whose chunks disagreed with the plan that produced
        them. Nothing downstream reported it, because a missing key just reads
        as "not speculative".

        Accumulated output goes into `result` rather than a return value --
        this is an async generator, so it cannot both yield chunks and hand
        back the assembled response.
        """
        async for chunk in self.action.execute(plan):
            if is_spec:
                chunk["speculative"] = True
            if chunk["type"] == "content":
                result["response"] += chunk["data"]
            if chunk["type"] == "done":
                result["done"] = chunk
                continue
            # The retry is the likeliest place to trip a second metacognitive
            # violation, since it runs with a hardened prompt after one
            # rejection. Skipping the filter would leak `self_correction` to
            # the transport *and* swallow the cortisol release on the one path
            # that most deserves it.
            if await self._consume_internal_chunk(chunk):
                continue
            yield chunk

    async def _consume_internal_chunk(self, chunk) -> bool:
        """Handle chunks meant for the pipeline itself. True = do not forward.

        Downstream consumers switch on a small set of chunk types, and an
        unrecognised one reaches the transport as a malformed message. These
        are internal signals from the action layer, not output.
        """
        if chunk.get("type") == "self_correction":
            await self._apply_self_correction_stress(chunk.get("data", ""))
            return True
        return False

    async def _apply_reward_prediction_error(self, prediction_error):
        """Turn a reward prediction error into a hormone burst.

        `None` means no comparison was made (reappraisal disabled, no recorded
        expectation, rate limited, or within tolerance) and is *not* the same as
        `0.0`. Treating them alike would fire a burst of zero on every turn the
        module declined to evaluate, which is harmless today only because the
        release methods reject non-positive amounts -- a coincidence, not a
        guarantee, so the distinction is made explicitly here.
        """
        if prediction_error is None:
            return
        try:
            prediction_error = float(prediction_error)
        except (TypeError, ValueError):
            return
        if not math.isfinite(prediction_error):
            return
        if abs(prediction_error) < PREDICTION_ERROR_DEADBAND:
            return

        try:
            if prediction_error > 0:
                await self.state.release_dopamine(
                    min(1.0, prediction_error * REWARD_PREDICTION_GAIN),
                    reason=f"turn exceeded expectation by {prediction_error:.2f}",
                )
            else:
                await self.state.release_cortisol(
                    min(1.0, -prediction_error * STRESS_PREDICTION_GAIN),
                    reason=f"turn fell short of expectation by {-prediction_error:.2f}",
                )
        except Exception as e:
            # An endocrine failure must never take down the turn: the hormone
            # modulates how the agent speaks, it does not decide whether it can.
            logger.warning("[Endocrine] Prediction-error release failed: %s", e)

    async def _apply_self_correction_stress(self, reason: str):
        """Catching yourself mid-violation is a stressor.

        Deliberately fired from the pipeline rather than from `ActionService`,
        which has no `StateService` handle. Action reports what happened; state
        decides the physiological response. Plumbing a mutable state service
        into the action layer to save one event type would invert that.
        """
        try:
            await self.state.release_cortisol(
                SELF_CORRECTION_STRESS, reason=f"self-correction: {reason}"
            )
        except Exception as e:
            logger.warning("[Endocrine] Self-correction release failed: %s", e)

    async def _async_system2_appraisal(self, user_utterance: str):
        try:
            current_pad = {
                "valence": self.state.current_state.valence,
                "arousal": self.state.current_state.arousal,
                "dominance": self.state.current_state.dominance,
            }
            new_pad = await self.appraisal.appraise_semantic_drift(
                user_utterance, self.llm, current_pad
            )
            # Update state with drifted mood values under the state lock (A2)
            # so this background write cannot race the synchronous appraisal path.
            await self.state.apply_semantic_appraisal(new_pad)
            logger.info(
                "[System 2 Appraisal] Mood drifted: V=%.2f, Ar=%.2f, D=%.2f",
                self.state.current_state.valence,
                self.state.current_state.arousal,
                self.state.current_state.dominance,
            )
        except Exception as e:
            logger.error(
                f"[System 2 Appraisal] Background semantic appraisal failed: {e}"
            )
