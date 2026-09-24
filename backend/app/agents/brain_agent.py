import asyncio
import inspect
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from ..cognitive import CognitiveService, percept
from ..cognitive.action_intent import (
    ActionIntent,
    OutcomeRecord,
    OutcomeStatus,
    build_outcome_record,
)
from ..cognitive.evidence import Evidence
from ..cognitive.percept import PerceptEnvelope
from ..cognitive.somatic import SomaticAppraiser
from ..config import Config
from ..contracts import (
    AudioPlaybackBacklog,
    AudioPlaybackProgress,
    AudioStop,
    ChatInput,
    SpeechExpressionWire,
    Topics,
    UserVoiceProperties,
)
from ..llm import build_llm_client
from ..logging_config import setup_logging
from ..runtime_bootstrap import bootstrap_runtime
from ..state import ConversationHistoryStore, GraphDB, MemoryStore
from ..utils.conversational_runtime import ConversationalRuntime
from ..utils.segmentation import HybridSegmenter
from ..utils.speech import SpeechCoordinator
from .base import BaseAgent, install_shutdown_signal_handlers

logger = logging.getLogger(__name__)

# Bucket 11 (voice remediation Phase 3, item 2): fired from `_on_audio_stop`
# on a genuinely confirmed interruption -- see that method's docstring for
# why this is the one place in the codebase that distinguishes a real
# interruption from a speculative duck or a same-turn silence. Matches
# `cognitive/pipeline.py`'s `SELF_CORRECTION_STRESS` (0.3) in scale: both are
# a single, moderate, discrete-event burst rather than something tunable
# per-turn.
INTERRUPTION_ADRENALINE_SPIKE = 0.3


@dataclass
class _SupersededReply:
    """The reply a new utterance took over from (ADR-003).

    `_process_chat_input_flow` resets `last_assistant_response` and
    `last_audio_progress` for the new turn long before Stage 2 publishes the
    confirmed "stop" addressed to the reply that was playing. Without this
    snapshot, accepting that stop truncated nothing. Playback progress for
    the superseded turn keeps landing here (`_on_audio_playback_progress`),
    so the cut point is what was heard when the stop arrived.
    """

    turn_id: str
    text: str | None  # None unless this text is that turn's own, unresolved reply
    progress: AudioPlaybackProgress | None
    intent: Any
    log_task: asyncio.Task | None
    message_id: uuid.UUID | None


# How long a cut waits for the reply's own history insert before giving up
# (it runs under `_turn_state_lock`; the store's pool has no command timeout).
REPLY_INSERT_WAIT_S = 2.0


def _char_offset_after_word(text: str, word_count: int) -> int:
    """P4-2: the exact character offset in `text` right after its
    `word_count`-th whitespace-delimited token.

    Used to stamp each published speech chunk with where it ends in the
    *true* response text, so `_truncate_interrupted_reply` can later slice
    `last_assistant_response` at a real boundary instead of the reconstructed
    (`" ".join(words)`) text actually sent to TTS, which does not
    byte-for-byte match `text` wherever the source stream's whitespace was
    collapsed by `.split()`/`.join()`. `re.finditer` walks `text` itself, so
    the offset it returns is always a true index into `text`, independent of
    that mismatch.
    """
    if word_count <= 0:
        return 0
    matches = list(re.finditer(r"\S+", text))
    if not matches:
        return 0
    return matches[min(word_count, len(matches)) - 1].end()


class BrainAgent(BaseAgent):
    """
    The Brain Agent (AI Friend Edition).
    Orchestrator of Identity and Temporal Cognitive Flow.
    """

    def __init__(
        self,
        ollama_url: str = Config.OLLAMA_URL,
        graph_db: GraphDB | None = None,
        memory_store: MemoryStore | None = None,
        conversation_store: ConversationHistoryStore | None = None,
    ):
        super().__init__(name="brain_agent")
        self.ollama = build_llm_client(base_url=ollama_url, model=Config.LLM_CHAT_MODEL)
        # HUMANOID_ARCHITECTURE_RESEARCH.md Phase 0: state the resolved model
        # and the file it came from in the log every time, rather than
        # requiring someone to SSH in and re-derive it -- see the ledger's
        # 2026-09-02 entries on stale-.env config reads.
        provenance = Config.LLM_PROVENANCE
        logger.info(
            "[Brain] LLM config resolved from %s (exists=%s): chat=%s fast=%s "
            "reflection=%s url=%s",
            provenance["env_file"],
            provenance["env_file_exists"],
            provenance["llm_chat_model"],
            provenance["llm_fast_model"],
            provenance["llm_reflection_model"],
            provenance["ollama_url"],
        )
        self.graph_db = graph_db
        self.memory_store = memory_store
        self.conversation_store = conversation_store

        # Initialize the Functional Core
        self.cognitive_core = CognitiveService(
            llm_service=self.ollama,
            memory_store=memory_store,
            graph_db=graph_db,
            identity_store=conversation_store,
            publish_cb=self.publish,
        )

        # Visual Somatic Homeostasis: recognising a learned comfort object in
        # what the agent is looking at lifts valence/arousal (and therefore the
        # dopamine, tonic and phasic). Lives here rather than in the vision agent
        # because it needs the graph and the state service, keeping the vision
        # agent a pure sensor with no database credentials.
        self.somatic_appraiser = SomaticAppraiser(graph_store=graph_db)

        self.last_interaction_time = datetime.now()
        self.last_visual_context = "No visual data available."
        # 1A: typed sibling of last_visual_context, built from the same
        # VisionDescription payload. Additive -- last_visual_context stays
        # the source every existing reader uses; this carries the
        # confidence/timestamp/provenance those readers don't have access to.
        self.last_visual_evidence: Evidence | None = None
        self.last_user_distance = 1.0
        self.last_user_voice_properties: UserVoiceProperties | None = None

        # AI Friend Segmentation Config
        self.coordinator = SpeechCoordinator(
            segmenter=HybridSegmenter(target_size=7), formation_buffer_s=0.300
        )
        self.conversational_runtime = ConversationalRuntime(publish_cb=self.publish)
        self._active_generation_task: asyncio.Task[Any] | None = None
        self._generation_lock = asyncio.Lock()
        self.last_audio_progress: AudioPlaybackProgress | None = None
        self.last_assistant_response: str | None = None
        self._active_response_turn_id: str | None = None
        # The turn a new utterance superseded -- usually the reply still
        # playing when the user barged in. Stage 2 addresses its confirmed
        # stop / rejected-interruption resume to this id, so `_on_audio_stop`
        # must accept it alongside the active turn.
        self._superseded_turn_id: str | None = None
        self._superseded_reply: _SupersededReply | None = None
        # Which turn `last_assistant_response` belongs to, whether it has
        # already been truncated, and the task writing it to history.
        # `last_assistant_response` is only reset by user turns, so while a
        # subconscious turn speaks it still holds the previous user turn's
        # reply; truncating it against the subconscious turn's playback
        # offset rewrote the wrong row. A truncated reply is resolved: a
        # second stop (a facial startle fires one for the active turn even
        # while idle) must not cut or record it again.
        self._reply_turn_id: str | None = None
        self._reply_resolved = False
        self._reply_log_task: asyncio.Task | None = None
        # The history row id the reply is stored under (generated here, so a
        # cut can address that row and no other), and whether its generation
        # is still running (None: unknown, e.g. state seeded outside a turn).
        self._reply_message_id: uuid.UUID | None = None
        self._reply_generating: bool | None = None
        # Phase 1 causal slice (§22, §38): the ActionIntent Stage 6 committed
        # for the turn currently generating/playing, and the last terminal
        # OutcomeRecord emitted for one. Both are read-mostly diagnostics
        # today -- nothing durably persists them yet (that is
        # `WorkspaceStore`'s job, a parallel work package) -- but every
        # `_emit_outcome_record` call binds back to `_active_action_intent`,
        # closing the percept -> decision -> outcome trace within one process.
        self.last_percept: PerceptEnvelope | None = None
        self._active_action_intent: ActionIntent | None = None
        self._last_outcome_record: OutcomeRecord | None = None
        # FIX-CLD-03: `_last_outcome_record` above is overwritten by the next
        # turn's outcome, so nothing durable let a caller look back at what
        # happened on an earlier turn once a new one started -- this is that
        # durable (in-process, not yet persisted -- WorkspaceStore's job)
        # ledger, queryable via `get_outcome_history`.
        self._outcome_history: list[OutcomeRecord] = []
        # Bucket 1 (VOICE_REMEDIATION_PLAN.md): stamped on the first playback
        # progress frame of each turn (see _on_audio_playback_progress) and
        # read by _on_chat_input's barge-in grace period below.
        self._last_audio_onset_at: float | None = None
        # Bucket 3 (VOICE_REMEDIATION_PLAN.md): last depth transport_agent
        # reported for its outbound PCM queue (see AudioPlaybackBacklog),
        # read by ConversationalRuntime to suppress a new filler while a
        # previous turn's audio is still draining. Starts at 0 (no known
        # backlog) rather than None so a cold start doesn't need a null check
        # on the hot filler-decision path.
        self.last_playback_backlog: int = 0
        # P2-14/M1-A14: `last_audio_progress` and `last_assistant_response`
        # are written from three independent NATS subscription tasks --
        # chat.input's turn flow, audio.playback.progress's tracker, and
        # audio.stop's truncation handler -- and read-then-written together
        # by audio.stop's truncation. `_generation_lock` guards only which
        # task owns `_active_generation_task`; it says nothing about this
        # data, and `_cancel_active_generation` yields back to the event
        # loop when its awaited task finishes, which is exactly the window a
        # concurrent chat.input reset can land in. See _truncate_interrupted_reply.
        self._turn_state_lock = asyncio.Lock()

    async def start(self):
        await self.connect()

        if self.conversation_store:
            pool = getattr(self.conversation_store, "pool", None)
            from unittest.mock import Mock

            if pool is None or isinstance(pool, Mock):
                await self.conversation_store.initialize()

        await self.cognitive_core.initialize(agent=self)

        if self.conversation_store:
            await self.conversation_store.start_session(
                trust_benevolence=self.cognitive_core.state.current_state.trust_benevolence,
                trust_competence=self.cognitive_core.state.current_state.trust_competence,
                trust_integrity=self.cognitive_core.state.current_state.trust_integrity,
            )

        # Subscribe to I/O streams
        await self.subscribe(
            Topics.CHAT_INPUT,
            self._on_chat_input,
            durable=f"{self.name}_chat_input_live",
            deliver_policy="new",
        )
        await self.subscribe(
            Topics.VISION_FRAMES, self._on_vision_frame, deliver_policy="last"
        )
        await self.subscribe(
            Topics.VISION_DESCRIPTION,
            self._on_vision_description,
            deliver_policy="last",
        )
        await self.subscribe(
            Topics.VISION_FACIAL_REFLEX,
            self._on_facial_reflex,
            deliver_policy="last",
            ack_wait=Config.MESH_REFLEX_ACK_WAIT_S,
            max_deliver=Config.MESH_REFLEX_MAX_DELIVER,
        )
        await self.subscribe(
            Topics.VOICE_SEGMENTATION_FEEDBACK,
            self._on_voice_feedback,
            durable=f"{self.name}_voice_segmentation_feedback_live",
            deliver_policy="new",
        )
        await self.subscribe(
            Topics.USER_VOICE_PROPERTIES,
            self._on_user_voice_properties,
            durable=f"{self.name}_user_voice_properties_live",
            deliver_policy="new",
        )
        await self.subscribe(
            Topics.AUDIO_PLAYBACK_PROGRESS,
            self._on_audio_playback_progress,
            durable=f"{self.name}_audio_playback_progress_live",
            deliver_policy="new",
        )
        await self.subscribe(
            Topics.AUDIO_STOP,
            self._on_audio_stop,
            durable=f"{self.name}_audio_stop_live",
            deliver_policy="new",
            ack_wait=Config.MESH_REFLEX_ACK_WAIT_S,
            max_deliver=Config.MESH_REFLEX_MAX_DELIVER,
        )
        await self.subscribe(
            Topics.AUDIO_PLAYBACK_BACKLOG,
            self._on_playback_backlog,
            durable=f"{self.name}_audio_playback_backlog_live",
            deliver_policy="last",
        )
        # Note: system.tick proactive engagement is now handled by SubconsciousAgent

        logger.info(f"🧠 {self.name} Online | AI Friend Cognitive Mesh Active.")

    async def _on_voice_feedback(self, data: dict[str, Any]):
        """Adaptive Tuning Loop (AI Friend alpha-damped loop)."""
        target = data.get("target_chunk_size", 8)
        alpha = getattr(Config, "FEEDBACK_ALPHA", 0.7)

        # Alpha-damped damping to prevent jittery speech fragmentation
        smoothed_size = (alpha * self.coordinator.segmenter.target_size) + (
            (1 - alpha) * target
        )
        new_size = round(smoothed_size)

        if new_size != self.coordinator.segmenter.target_size:
            logger.info(
                f"📈 Tuning Segmentation | Target: {target} -> Smoothed: {new_size}"
            )
            self.coordinator.segmenter.target_size = new_size

    async def _on_vision_frame(self, data: dict[str, Any]):
        """Fallback: basic source awareness from raw frames."""
        source = data.get("source", "unknown")
        # Only update if we don't have a richer VLM description yet
        if (
            not self.last_visual_context
            or self.last_visual_context == "No visual data available."
        ):
            self.last_visual_context = f"I am seeing the user's {source}."

    async def _on_vision_description(self, data: dict[str, Any]):
        """VLM: Rich semantic visual context from the Visual Appraisal pipeline."""
        self.last_percept = percept.from_vision_description(data)
        description = data.get("description", "")
        source = data.get("source", "unknown")
        if description:
            self.last_visual_context = f"[Visual Context from {source}]: {description}"
            logger.debug("[Brain] Visual context updated: %s", description[:60])
        else:
            self.last_visual_context = f"I am seeing the user's {source}."
        distance = data.get("user_distance")
        self.last_user_distance = float(distance) if distance is not None else 1.0

        # is_novel mirrors VisionDescription's own field: a frame that cleared
        # habituation is a fresh observation (confidence 1.0); a cached
        # repeat is worth less (0.5) without discarding it entirely.
        self.last_visual_evidence = Evidence(
            content=description or f"I am seeing the user's {source}.",
            source=source,
            modality="vision",
            timestamp=float(data.get("timestamp", time.time())),
            confidence=1.0 if data.get("is_novel", True) else 0.5,
            provenance="vision_agent",
        )

        await self._appraise_somatic(description)

    async def _on_facial_reflex(self, data: dict[str, Any]):
        """Bucket 13 (voice remediation Phase 3): a `FacialReflexEvent` from
        the CPU-only reflex channel -- see `app/vision/reflex.py` for how a
        raw blendshape frame becomes one of these. Applied directly to
        affect, unlike `_on_vision_description`'s path through
        `SomaticAppraiser`: there is no scene content to match against
        learned vocabulary here, only an already-scored expression onset.
        Failures are contained the same way `_appraise_somatic` contains
        them -- a dropped reflex signal should never take down the mesh
        subscriber.

        Bucket 17 (voice remediation Phase 4): a `startle` arriving while a
        turn is actively generating now competes for the workspace instead
        of only nudging background affect for whatever turn happens next --
        see `DecisionService.is_facial_reflex_interruption_worthy`. It
        publishes a confirmed `AudioStop` (`intent_type=VISION_INTERRUPTION`,
        a schema value `contracts.py` already declared but nothing ever
        published) onto the exact subject a real barge-in uses, so it flows
        through `_on_audio_stop`'s existing, tested cancellation/truncation/
        adrenaline path -- no duplicated logic, and voice-agent/transport_agent
        genuinely flush/stop playback rather than only flipping an internal flag.
        """
        try:
            self.last_percept = percept.from_facial_reflex(data)
            await self.cognitive_core.state.apply_facial_reflex(data)

            reflex_name = data.get("name")
            async with self._turn_state_lock:
                active_turn_id = self._active_response_turn_id
            if (
                active_turn_id
                and self.cognitive_core.decision.is_facial_reflex_interruption_worthy(
                    reflex_name
                )
            ):
                stop_msg = AudioStop(
                    interrupt=True,
                    speculative=False,
                    reason="facial_reflex_startle",
                    intent_type="VISION_INTERRUPTION",
                    turn_id=active_turn_id,
                )
                await self.publish(Topics.AUDIO_STOP, stop_msg.model_dump())
        except Exception:
            logger.exception("[Brain] Facial reflex appraisal failed; ignored.")

    async def _appraise_somatic(self, description: str):
        """Turn a recognised comfort object into an endocrine response.

        This is the step that makes vision a sense rather than a captioner: the
        description above only ever became prompt text, so the agent could
        describe something it loves and feel nothing. Failures are contained --
        the visual context is still worth having even if the somatic layer is
        unavailable.
        """
        if not description:
            return
        try:
            await self.somatic_appraiser.refresh()
            somatic = self.somatic_appraiser.appraise(description)
            if not somatic:
                return
            await self.cognitive_core.state.apply_somatic_perception(somatic)
        except Exception:
            logger.exception("[Brain] Somatic appraisal failed; visual context kept.")

    async def _emit_outcome_record(
        self,
        intent: ActionIntent | None,
        *,
        status: OutcomeStatus,
        actual_delivered_text: str | None = None,
        character_offset: int = 0,
        error: str | None = None,
    ) -> OutcomeRecord | None:
        """Phase 1 causal slice (§22, §38): the terminal record binding what
        actually happened back to the `ActionIntent` Stage 6 committed.
        `intent=None` means this turn never reached Stage 6 (e.g. a
        subconscious/proactive turn, which bypasses `CognitivePipeline`
        entirely) -- there is nothing to attribute an outcome to, so this is a
        deliberate no-op rather than fabricating an orphaned record.
        """
        if intent is None:
            logger.debug(
                "[Brain] Outcome status=%s with no active ActionIntent; skipping record.",
                status,
            )
            return None
        record = build_outcome_record(
            intent,
            status=status,
            actual_delivered_text=actual_delivered_text,
            character_offset=character_offset,
            error=error,
        )
        self._last_outcome_record = record
        # FIX-CLD-03: `getattr(..., None)` rather than a direct read -- some
        # test doubles build a `BrainAgent` via `object.__new__`, bypassing
        # `__init__` (see test_barge_in_truncation.py), so this attribute may
        # not exist yet on `self`. Matches this file's existing pattern for
        # `_active_action_intent`/`_active_response_turn_id`.
        history = getattr(self, "_outcome_history", None)
        if history is None:
            history = []
            self._outcome_history = history
        history.append(record)
        logger.info(
            "[Brain] OutcomeRecord id=%s intent=%s status=%s offset=%d elapsed_ms=%.1f",
            record.outcome_id,
            record.intent_id,
            record.status,
            record.character_offset,
            record.elapsed_ms,
        )
        return record

    def get_outcome_history(self, turn_id: str) -> list[OutcomeRecord]:
        """FIX-CLD-03: every terminal `OutcomeRecord` emitted for `turn_id`,
        in emission order. A turn normally has exactly one (COMPLETED,
        CANCELLED, or a single TRUNCATED), but this does not assume that --
        it filters the full ledger rather than tracking a per-turn slot, so
        it stays correct even if that ever changes.
        """
        history = getattr(self, "_outcome_history", None) or []
        return [record for record in history if record.turn_id == turn_id]

    async def _cancel_active_generation(self, reason: str):
        """Cancel the in-flight generation task and wait for it to fully unwind.

        A4: a fire-and-forget .cancel() only *requests* cancellation - the task
        keeps running until its next await point, so it can still write stale
        last_assistant_response/last_audio_progress after a new turn has already
        reset that state, or after _on_audio_stop has already read it for
        truncation. Awaiting the task here closes that race by guaranteeing the
        previous turn has stopped touching shared state before the caller
        (a new chat.input turn, or a confirmed audio.stop) proceeds.
        """
        async with self._generation_lock:
            task = self._active_generation_task
            if not task or task.done():
                return
            logger.info("Cancelling active generation task: %s", reason)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Previous generation task raised while being cancelled"
                )
            finally:
                if self._active_generation_task is task:
                    self._active_generation_task = None

        # Phase 1 causal slice: a cancellation that landed before any content
        # was ever produced has no truncation offset for
        # _truncate_interrupted_reply to compute (its own branches both
        # require last_assistant_response) -- record the outcome here instead,
        # while the intent that was cancelled is still known. A cancellation
        # that landed after some content streamed defers to
        # _truncate_interrupted_reply, called immediately after this by the
        # only production caller (_on_audio_stop), so the turn gets exactly
        # one terminal record rather than two disagreeing ones.
        async with self._turn_state_lock:
            produced_nothing = not self.last_assistant_response
            intent = getattr(self, "_active_action_intent", None)
            already_resolved = getattr(self, "_reply_resolved", False)
            # Whatever was running has stopped; a later replacement must not
            # record this reply CANCELLED again.
            self._reply_generating = False
            if produced_nothing:
                self._reply_resolved = True
        if produced_nothing and not already_resolved:
            await self._emit_outcome_record(intent, status="CANCELLED", error=reason)

    async def _store_heard_reply(
        self,
        heard: str,
        log_task: asyncio.Task | None,
        message_id: uuid.UUID | None,
    ) -> None:
        """Rewrite this reply's own history row to `heard`.

        The row is addressed by the id the brain generated when it stored
        the reply. Addressing "the newest assistant row" rewrote the previous
        reply when this one was never stored, and addressing by content
        rewrote an older reply with the same text. A reply with no id was
        never stored (cancelled mid-generation) and gets no write. Waits,
        bounded, for the reply's own spawned insert so the rewrite cannot
        run ahead of it.
        """
        if not self.conversation_store:
            return
        if message_id is None:
            logger.info("Interrupted reply was never stored; no history row to cut.")
            return
        if log_task is not None and not log_task.done():
            await asyncio.wait({log_task}, timeout=REPLY_INSERT_WAIT_S)
            if not log_task.done():
                logger.warning(
                    "Reply insert still pending after %.1f s; leaving it uncut.",
                    REPLY_INSERT_WAIT_S,
                )
                return
        await self.conversation_store.rewrite_assistant_message(
            heard, message_id=message_id
        )

    async def _truncate_interrupted_reply(self, reply: _SupersededReply | None = None):
        """Rewrite the stored assistant reply down to what was actually
        heard, using `last_audio_progress`, then clear both fields.

        With `reply`, truncates that superseded reply's snapshot instead and
        leaves the current turn's fields alone (ADR-003).

        P2-14/M1-A14: this used to read `last_audio_progress` and
        `last_assistant_response`, compute a truncation offset, and (on one
        branch) `await` a conversation-store write, all without a lock --
        while `_process_chat_input_flow` (a different NATS subscription,
        therefore a different task) can reset both fields to start a new
        turn, and `_on_audio_playback_progress` (a third subscription) can
        overwrite `last_audio_progress` mid-computation. `_cancel_active_generation`
        guarantees the turn that WROTE this reply has stopped -- it does not
        guarantee nothing else reads or resets it while this method runs.
        Holding `_turn_state_lock` for the whole read-compute-write section
        (including the DB write) makes this atomic with respect to those
        other writers rather than merely reducing the window.
        """
        async with self._turn_state_lock:
            if reply is None:
                text = self.last_assistant_response
                progress = self.last_audio_progress
                intent = getattr(self, "_active_action_intent", None)
                log_task = getattr(self, "_reply_log_task", None)
                message_id = getattr(self, "_reply_message_id", None)
                owner = getattr(self, "_reply_turn_id", None)
                active = getattr(self, "_active_response_turn_id", None)
                if getattr(self, "_reply_resolved", False) or (
                    owner is not None and active is not None and owner != active
                ):
                    # Already truncated, or the active turn is not the one
                    # that produced this text (a subconscious turn speaking).
                    text = None
                else:
                    self._reply_resolved = True
            else:
                text, progress, intent = reply.text, reply.progress, reply.intent
                log_task, message_id = reply.log_task, reply.message_id
            if progress and not progress.completed and text:
                offset = progress.character_offset
                if 0 < offset < len(text):
                    truncated_text = text[:offset].strip()
                    original_length = len(text)
                    truncated_length = len(truncated_text)
                    logger.info(
                        f"Truncating history (via progress): original_length={original_length}, truncated_length={truncated_length}, offset={offset}"
                    )
                    await self._store_heard_reply(truncated_text, log_task, message_id)
                    await self._emit_outcome_record(
                        intent,
                        status="TRUNCATED",
                        actual_delivered_text=truncated_text,
                        character_offset=offset,
                    )
            elif not progress and text:
                # No real playback progress, so we do not know how much of
                # the reply was actually heard -- and we no longer guess.
                #
                # This used to estimate `int(elapsed * 15)`, a hardcoded
                # 15 characters per second, and rewrite the stored reply at
                # that offset. Two things were wrong with it. The rate was
                # invented and unbounded in error: real speech rate varies
                # with prosody, pauses and the synthesiser, so the cut
                # landed wherever the arithmetic said. And
                # `assistant_response_start_time` is only set on one of the
                # two streaming paths, so `elapsed` could be measured from a
                # *previous* turn entirely.
                #
                # The transcript is not a log; it is what memory and the
                # persona prompt read back later. A wrong cut point puts
                # words in the agent's mouth that it never said, or deletes
                # ones it did, and nothing downstream can tell that the
                # sentence was reconstructed. Keeping the full text is also
                # wrong -- the agent may believe it said more than was heard
                # -- but it is wrong in a way that is honest and visible in
                # the log, rather than silently fabricated.
                logger.info(
                    "Interrupted with no playback progress; keeping the full "
                    "reply (%d chars) rather than guessing a cut point.",
                    len(text),
                )
                # Still a terminal record for this intent -- best-known
                # offset (the full text) rather than no record at all, same
                # honesty tradeoff as the log line above: this is what we
                # believe was delivered, not a claim that it was verified.
                await self._emit_outcome_record(
                    intent,
                    status="TRUNCATED",
                    actual_delivered_text=text,
                    character_offset=len(text),
                )

            if reply is not None:
                return  # the current turn's fields belong to the new turn
            # Cleared however this turn resolved. It was previously reset
            # only on the branch that actually truncated, so a stop that
            # matched none of the guards left the progress marker in place
            # for the *next* interrupt to truncate against -- a stale offset
            # from a reply that had already ended.
            self.last_audio_progress = None

    async def _replace_active_generation(self, coro, reason: str):
        """Atomically replace the active generation task with a new one.

        Holds the lock through the entire critical section: cancel the prior task,
        await its completion, create the new task, and assign it to
        _active_generation_task. This prevents concurrent callers from both
        creating tasks and losing ownership when one overwrites the other's
        assignment (TOCTOU race).

        Args:
            coro: Coroutine to wrap in the new generation task
            reason: Reason for cancelling the prior task (if any)

        Returns:
            The newly created task
        """
        cancelled_a_running_turn = False
        async with self._generation_lock:
            # Cancel and await prior task if it exists
            prior_task = self._active_generation_task
            if prior_task and not prior_task.done():
                logger.info("Cancelling active generation task: %s", reason)
                prior_task.cancel()
                try:
                    await prior_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "Previous generation task raised while being cancelled"
                    )
                cancelled_a_running_turn = True

            # Create and assign new task while still holding the lock
            new_task = asyncio.create_task(coro)
            self._active_generation_task = new_task

        # The intent belongs to the last user turn's reply. Record it
        # CANCELLED only if that reply's generation was what got cut: the
        # task replaced may be a proactive turn, or a later turn still in
        # its pacing sleep, after that reply had finished (and been
        # recorded COMPLETED or TRUNCATED on its own).
        if cancelled_a_running_turn and (
            getattr(self, "_reply_generating", None) is False
        ):
            cancelled_a_running_turn = False
        if cancelled_a_running_turn:
            # FIX-CLD-05: a new incoming turn preempting one still in flight
            # never called `_cancel_active_generation`/`_truncate_interrupted_reply`
            # (those only run on `_on_audio_stop`'s path), so the replaced
            # turn's ActionIntent previously got no terminal record at all --
            # its partial `last_assistant_response` is simply overwritten by
            # `_process_chat_input_flow`'s reset for the new turn. Read
            # before returning: `new_task` (`coro`) has only been scheduled
            # above, not yet run, so `_active_action_intent` here is still
            # the interrupted turn's own, never the new one's.
            async with self._turn_state_lock:
                intent = getattr(self, "_active_action_intent", None)
                # Its terminal record is written here; a later truncation of
                # this reply (a superseded "stop", ADR-003) must not add a
                # second, disagreeing one.
                self._active_action_intent = None
                self._reply_resolved = True
                self._reply_generating = False
            await self._emit_outcome_record(intent, status="CANCELLED", error=reason)

        return new_task

    async def _on_chat_input(self, message: dict[str, Any]):
        now = datetime.now()
        self.last_interaction_time = now

        try:
            msg = ChatInput.model_validate(message)
            is_subconscious = msg.metadata.source == "subconscious"
        except ValidationError as e:
            logger.warning(f"Dropping invalid chat.input message: {e}")
            return
        except Exception:
            logger.exception("Unexpected error processing chat.input")
            return

        self.last_percept = percept.from_chat_input(msg.model_dump())

        # If it is not a subconscious pulse, publish a confirmed stop to silence any playing voice agent audio.
        #
        # Bucket 1 (VOICE_REMEDIATION_PLAN.md): this used to fire unconditionally, on every
        # chat.input, with no gate at all -- bypassing decision.py's
        # `is_speculative_stop_confirmed`, the arbiter `_resolve_turn_conflict`
        # (pipeline.py) already calls moments later on this same final text. Measured
        # directly in a live session (2026-09-01): on "Let's stop it.", the arbiter
        # correctly REJECTED the interruption ("contradicts early perception"), but this
        # unconditional publish had already cut playback half a second earlier regardless.
        #
        # The fix is not to duplicate the arbiter's keyword logic here -- that logic is
        # specifically for confirming an explicit command (stop/wait/hold/...), not for
        # deciding whether a new, keyword-free user turn should mute old audio, which is
        # this publish's actual job and must keep working unconditionally. The real defect
        # is only the double-fire: when STT's speculative duck already flagged this
        # utterance as command-like (`last_speculative_intent` is set), the pipeline's own
        # Stage 2 conflict resolution is about to run the arbiter on this exact text and
        # will itself publish audio.stop (confirmed) or audio.resume (rejected) --
        # skipping the immediate stop here defers entirely to that already-correct,
        # already-gated decision instead of racing ahead of it. Audio is already ducked
        # (not silenced) from the speculative signal, so nothing is lost by waiting the
        # extra tens of milliseconds for the real verdict.
        speculative_intent_pending = bool(
            self.cognitive_core.state.last_speculative_intent
        )
        # Bucket 1: a barge-in grace period right after the agent's own audio
        # actually starts playing. STT's own pipeline (250ms min_speech_ms +
        # 700ms endpoint silence, per config.py) means a genuine human
        # utterance cannot produce a *final* chat.input this soon after
        # onset -- the audit's own human-baseline research (Appendix A.1)
        # puts real reaction time at >200ms, and that clock does not even
        # start until the listener has heard enough to react to. What
        # arrives this fast is far more likely to be onset noise: a click,
        # pop, or brief echo tail before echoCancellation has settled.
        within_onset_grace = (
            self._last_audio_onset_at is not None
            and (time.time() - self._last_audio_onset_at)
            < Config.BARGE_IN_ONSET_GRACE_S
        )
        if (
            not is_subconscious
            and not speculative_intent_pending
            and not within_onset_grace
        ):
            stop_msg = AudioStop(
                interrupt=True,
                speculative=False,
                reason="confirmed_user_speech",
                perception_text=msg.text,
                intent="CONFIRMED_STOP",
                utterance_id=msg.utterance_id,
            )
            await self.publish(Topics.AUDIO_STOP, stop_msg.model_dump())
        elif (
            not is_subconscious
            and within_onset_grace
            and not speculative_intent_pending
        ):
            # Reviewer finding: this transcript is exactly the case the onset
            # grace comment above describes as most likely onset noise (a
            # click, pop, or echo tail), not real speech -- STT's own
            # speculative pipeline did not flag it as command-like either.
            # Suppressing only the audio.stop above still let this fall
            # through to _replace_active_generation, cancelling whatever the
            # agent was already doing and generating a brand-new reply to
            # noise while its prior audio kept playing. Treat it the same as
            # the stop we just skipped: drop it entirely rather than starting
            # a new turn.
            logger.debug(
                "Dropping chat.input within barge-in onset grace with no "
                "speculative intent (likely onset noise): %r",
                msg.text,
            )
            return

        # Atomically replace the active generation task to prevent concurrent
        # chat inputs from both creating tasks and losing ownership.
        task = await self._replace_active_generation(
            self._process_chat_input_flow(msg, is_subconscious, message),
            "new incoming speech turn",
        )
        try:
            await task
        except asyncio.CancelledError:
            logger.info("Active generation flow task cancelled.")
        finally:
            # Clean up only if we still own this task
            async with self._generation_lock:
                if self._active_generation_task == task:
                    self._active_generation_task = None

    @staticmethod
    def _resolve_chat_input_metadata(
        chat_input: ChatInput, message: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Normalize the loosely-typed metadata/latency_metadata the message
        may or may not carry into plain dicts, falling back to the
        ChatInput's own metadata."""
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            metadata = chat_input.metadata.model_dump() if chat_input.metadata else {}
        latency_metadata = message.get("latency_metadata")
        if not isinstance(latency_metadata, dict):
            latency_metadata = {}
        return metadata, latency_metadata

    async def _apply_conversational_pacing(
        self, metadata: dict[str, Any], state_snap: dict[str, Any]
    ) -> None:
        """Sleep for the calculated pre-response silence, skipped entirely
        for a latency benchmark pulse."""
        pacing = self.conversational_runtime.calculate_pacing_parameters(state_snap)
        is_benchmark = metadata.get("benchmark_id") == "bench_pulse"
        if is_benchmark:
            silence_s = 0.0
            logger.info(
                "⚡ [Brain] Benchmark pulse detected. Pacing sleep bypassed for raw latency measurement."
            )
        else:
            silence_s = pacing["silence_duration_ms"] / 1000.0
            logger.info(
                f"Pacing conversational turn: sleeping {pacing['silence_duration_ms']:.1f}ms before starting response."
            )
        await asyncio.sleep(silence_s)

    @staticmethod
    def _resolve_chat_user_id(
        message: dict[str, Any], chat_input: ChatInput, metadata: dict[str, Any]
    ) -> str:
        return (
            message.get("user_id")
            or metadata.get("user_id")
            or getattr(chat_input, "user_id", None)
            or (
                chat_input.metadata.model_dump().get("user_id")
                if chat_input.metadata
                else None
            )
            or "User"
        )

    async def _begin_turn(self, turn_id: str) -> str | None:
        """Make `turn_id` the active turn; returns the turn it superseded.

        The superseded reply's text, playback progress, intent and history
        state are snapshotted here (ADR-003): Stage 2 addresses the confirmed
        "stop" to that reply, and by the time it arrives this turn has reset
        the live fields.
        """
        async with self._turn_state_lock:
            interrupted_turn_id = getattr(self, "_active_response_turn_id", None)
            self._superseded_turn_id = interrupted_turn_id
            # The previous turn's generation has already been cancelled (or
            # had finished) by `_replace_active_generation`, so this is its
            # final state; its audio may still be playing. The reply text is
            # carried only if it is that turn's own and not yet truncated.
            owns_reply = (
                interrupted_turn_id is not None
                and getattr(self, "_reply_turn_id", None) == interrupted_turn_id
                and not getattr(self, "_reply_resolved", False)
            )
            self._superseded_reply = (
                _SupersededReply(
                    turn_id=interrupted_turn_id,
                    text=self.last_assistant_response if owns_reply else None,
                    progress=getattr(self, "last_audio_progress", None),
                    intent=(
                        getattr(self, "_active_action_intent", None)
                        if owns_reply
                        else None
                    ),
                    log_task=(
                        getattr(self, "_reply_log_task", None) if owns_reply else None
                    ),
                    message_id=(
                        getattr(self, "_reply_message_id", None) if owns_reply else None
                    ),
                )
                if interrupted_turn_id
                else None
            )
            self._active_response_turn_id = turn_id
        return interrupted_turn_id

    async def _process_chat_input_flow(
        self, chat_input: ChatInput, is_subconscious: bool, message: dict[str, Any]
    ):
        user_text = chat_input.text
        turn_id = chat_input.turn_id or chat_input.utterance_id or str(uuid.uuid4())
        metadata, latency_metadata = self._resolve_chat_input_metadata(
            chat_input, message
        )
        utterance_id = chat_input.utterance_id

        if not user_text:
            return

        interrupted_turn_id = await self._begin_turn(turn_id)

        # Pacing Conversational Turn: calculate silence duration and pause
        state_snap = self.cognitive_core.state.get_context_snapshot()
        await self._apply_conversational_pacing(metadata, state_snap)

        # Bucket 3 (VOICE_REMEDIATION_PLAN.md): stamped *after* the pacing
        # sleep, not before. A live capture (2026-09-01) showed the filler's
        # elapsed-time check used to start its clock before this deliberate
        # 300-900ms silence (`calculate_pacing_parameters`), so the filler's
        # budget was already exhausted before generation even began -- e.g. a
        # 496ms pacing sleep alone blew a 250ms threshold, and the fired-filler
        # log line always landed within ~15ms of the pacing sleep ending, never
        # any later. Measuring from here instead means the threshold reflects
        # actual generation latency, the thing it was meant to mask.
        generation_start_time = time.time()

        # Only update human interaction tracking if it's an actual user message
        if not is_subconscious:
            self.cognitive_core.state.record_user_interaction()

        # Ingest the latest user voice properties (System 1 feature stream) into the raw_event
        user_voice_properties = None
        if self.last_user_voice_properties:
            user_voice_properties = self.last_user_voice_properties.model_dump()
            self.last_user_voice_properties = None

        user_id = self._resolve_chat_user_id(message, chat_input, metadata)

        raw_event = {
            "id": str(uuid.uuid4()),
            "type": "USER_MESSAGE",
            "user_id": user_id,
            "content": user_text,
            "user_voice_properties": user_voice_properties,
            "metadata": {
                **metadata,
                "visuals": self.last_visual_context,
                "visual_evidence": self.last_visual_evidence,
                "turn_id": turn_id,
                "utterance_id": utterance_id,
                "interrupted_turn_id": interrupted_turn_id,
            },
        }

        if self.conversation_store and not is_subconscious:
            self.spawn(self.conversation_store.log_message(user_id, user_text))

        workspace_snapshot = None
        if not is_subconscious:
            workspace_snapshot = await self.cognitive_core.workspace_store.get_snapshot(
                user_id
            )

        if is_subconscious:
            logger.info("💭 [Brain] Processing subconscious thought: %s", user_text)
            generator = self.cognitive_core.generate_proactive_response(
                thought_prompt=user_text
            )
        else:
            # P2-14/M1-A14: locked so this reset cannot land mid-computation
            # inside _truncate_interrupted_reply, running concurrently on the
            # audio.stop subscription's own task for a still-unwinding turn.
            async with self._turn_state_lock:
                self.last_assistant_response = None
                self.last_audio_progress = None
                self._active_action_intent = None
                self._reply_log_task = None
                self._reply_message_id = None
                self._reply_turn_id = turn_id
                self._reply_resolved = False
                self._reply_generating = True
            process_kwargs = {
                "percept": self.last_percept,
                "workspace": workspace_snapshot,
            }
            try:
                process_parameters = inspect.signature(
                    self.cognitive_core.process_event
                ).parameters
            except (TypeError, ValueError):
                process_parameters = {}
            if process_parameters and "workspace" not in process_parameters:
                accepts_kwargs = any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in process_parameters.values()
                )
                if not accepts_kwargs:
                    process_kwargs.pop("workspace")
            generator = self.cognitive_core.process_event(raw_event, **process_kwargs)

        # Wrap generator to monitor TTFT and inject fillers
        wrapped_generator = self.conversational_runtime.monitor_stream_and_fill(
            generator=generator,
            turn_id=turn_id,
            state_snap=state_snap,
            user_distance=self.last_user_distance,
            is_proactive=is_subconscious,
            incoming_metadata=metadata,
            incoming_latency_metadata=latency_metadata,
            generation_start_time=generation_start_time,
            # A live provider, not a snapshot: `last_playback_backlog` can
            # change during send_filler's own wait (see the reviewer finding
            # on ConversationalRuntime.monitor_stream_and_fill), and this
            # attribute is exactly the kind of thing that changes underneath
            # a long sleep here.
            playback_backlog=lambda: self.last_playback_backlog,
        )

        if not is_subconscious:
            async with self._turn_state_lock:
                self.last_assistant_response = ""
            # `assistant_response_start_time` used to be stamped here, read only
            # by the character-rate truncation guess in `_on_audio_stop`. That
            # guess is gone, and it was set on this path only -- the other
            # streaming path assigns `last_assistant_response` without it, which
            # is how elapsed time could be measured from an earlier turn.

        full_response = await self._stream_to_speech(
            wrapped_generator,
            turn_id=turn_id,
            is_proactive=is_subconscious,
            incoming_metadata=metadata,
            incoming_latency_metadata=latency_metadata,
        )

        store_reply = bool(self.conversation_store and full_response)
        message_id = uuid.uuid4() if store_reply else None
        if not is_subconscious:
            # One critical section: a stop must never see this reply as
            # finished but without the row id its insert is about to use
            # (it would cut nothing while the full text lands in history).
            # Spawning does not yield, so the insert task is recorded here too.
            async with self._turn_state_lock:
                self.last_assistant_response = full_response
                log_task = (
                    self.spawn(
                        self.conversation_store.log_message(
                            "assistant", full_response, message_id=message_id
                        )
                    )
                    if store_reply
                    else None
                )
                if self._reply_turn_id == turn_id:
                    self._reply_generating = False
                    self._reply_log_task = log_task
                    self._reply_message_id = message_id
        elif store_reply:
            self.spawn(
                self.conversation_store.log_message(
                    "assistant", full_response, message_id=message_id
                )
            )

    async def _on_audio_playback_progress(self, data: dict[str, Any]):
        """Tracks the current word/character progress of the audio playback."""
        try:
            self.last_percept = percept.from_playback_progress(data)
            progress = AudioPlaybackProgress.model_validate(data)
            async with self._turn_state_lock:
                active_turn_id = getattr(self, "_active_response_turn_id", None)
                superseded = getattr(self, "_superseded_reply", None)
                if (
                    superseded is not None
                    and active_turn_id
                    and progress.utterance_id == superseded.turn_id
                    and progress.utterance_id != active_turn_id
                ):
                    # Still playing after the user took the floor: this is
                    # the cut point a confirmed "stop" for it will use.
                    superseded.progress = progress
                    if progress.completed and superseded.text:
                        # It played to the end after all: its terminal
                        # record, and nothing left for a later stop to cut.
                        await self._emit_outcome_record(
                            superseded.intent,
                            status="COMPLETED",
                            actual_delivered_text=superseded.text,
                            character_offset=min(
                                progress.character_offset, len(superseded.text)
                            ),
                        )
                        superseded.text = superseded.intent = None
                        if getattr(self, "_reply_turn_id", None) == superseded.turn_id:
                            self._reply_resolved = True  # a proactive turn took over
                    return
                if active_turn_id and progress.utterance_id != active_turn_id:
                    logger.debug(
                        "Ignoring playback progress for stale turn %s; active turn is %s.",
                        progress.utterance_id,
                        active_turn_id,
                    )
                    return
                self.last_audio_progress = progress
                if progress.word_index == 0 and progress.character_offset == 0:
                    # Bucket 1: the first frame of a new utterance actually
                    # starting to play, not just being queued -- the moment
                    # _on_chat_input's grace period below measures from.
                    self._last_audio_onset_at = time.time()
                owner = getattr(self, "_reply_turn_id", None)
                if progress.completed and (
                    owner is None
                    or (
                        owner == progress.utterance_id
                        and not getattr(self, "_reply_resolved", False)
                    )
                ):
                    # Phase 1 causal slice (§22, §38): the turn's terminal,
                    # non-interrupted outcome. `last_assistant_response` is
                    # the full text this same handler's playback reports
                    # finished delivering. Only for the turn that owns that
                    # text (a proactive turn finishing used to re-record the
                    # previous user reply), and once.
                    if owner is not None:
                        self._reply_resolved = True
                    delivered = self.last_assistant_response or ""
                    # FIX-CLD-04: trust the playback-reported offset rather
                    # than assuming every COMPLETED frame delivered the full
                    # text -- `min(...)` still clamps to `len(delivered)`
                    # when the report reaches or exceeds it (the common
                    # case), rather than indexing past the actual string.
                    offset = min(progress.character_offset, len(delivered))
                    await self._emit_outcome_record(
                        getattr(self, "_active_action_intent", None),
                        status="COMPLETED",
                        actual_delivered_text=delivered,
                        character_offset=offset,
                    )
            logger.debug(
                f"🔊 Audio Playback Progress | Word Index: {progress.word_index} | Offset: {progress.character_offset} | Completed: {progress.completed}"
            )
        except Exception as e:
            logger.error(f"Error parsing audio playback progress: {e}")

    async def _on_playback_backlog(self, data: dict[str, Any]):
        """Bucket 3 (VOICE_REMEDIATION_PLAN.md): tracks transport_agent's
        outbound PCM queue depth so a new turn's filler can be suppressed
        while a previous turn's audio is still draining."""
        try:
            backlog = AudioPlaybackBacklog.model_validate(data)
            self.last_playback_backlog = backlog.queue_depth
        except Exception as e:
            logger.error(f"Error parsing audio playback backlog: {e}")

    async def _on_audio_stop(self, data: dict[str, Any]):
        """Handles confirmed audio stops: cancels in-flight generation for the
        interrupted turn and truncates the last played utterance in history.

        audit/ROADMAP.md P1-4: this is now the single place that reacts to a
        confirmed interrupt -- previously a second, unscoped classifier here
        (`InterruptionClassifier`, regex over every partial) independently
        cancelled generation the instant a keyword matched, racing with
        decision.py's `is_speculative_stop_confirmed` (the arbiter that
        actually decides, using the full utterance and its context -- see
        `CognitivePipeline.execute`'s conflict-resolution stage). Reacting
        here instead means there is exactly one path from "confirmed" to
        "generation cancelled", however the confirmation was reached.
        """
        try:
            self.last_percept = percept.from_audio_stop(data)
            stop_msg = AudioStop.model_validate(data)

            # Truncation, and cancelling the turn that was cut off, only
            # happen on confirmed (non-speculative) interrupts -- a
            # speculative duck has not stopped anything yet.
            if not stop_msg.speculative:
                # If this stop was emitted for a new incoming user speech turn to silence
                # the voice agent, do NOT cancel the newly spawned generation task.
                if stop_msg.reason == "confirmed_user_speech":
                    return

                async with self._turn_state_lock:
                    active_turn_id = getattr(self, "_active_response_turn_id", None)
                    # Only Stage 2's confirmed voice command is addressed to
                    # the superseded reply (ADR-003). Any other stop for that
                    # turn -- e.g. a facial-startle stop published before the
                    # user's new utterance took over -- is stale, as before.
                    # Consumed on use so it cannot match twice.
                    accepts_superseded = (
                        stop_msg.reason == "confirmed_command"
                        and stop_msg.turn_id is not None
                        and stop_msg.turn_id
                        == getattr(self, "_superseded_turn_id", None)
                    )
                    superseded_reply = None
                    if accepts_superseded:
                        self._superseded_turn_id = None
                        superseded_reply = getattr(self, "_superseded_reply", None)
                        self._superseded_reply = None
                if (
                    stop_msg.turn_id
                    and active_turn_id
                    and stop_msg.turn_id != active_turn_id
                    and not accepts_superseded
                ):
                    logger.debug(
                        "Ignoring audio stop for stale turn %s; active turn is %s.",
                        stop_msg.turn_id,
                        active_turn_id,
                    )
                    return
                if accepts_superseded:
                    # The superseded reply's generation already ended when
                    # the new utterance started; the task running now is
                    # that utterance's own turn, which Stage 2 stops itself.
                    # Cancelling it would cut the "stop" turn off mid-persist.
                    if superseded_reply is not None:
                        await self._truncate_interrupted_reply(superseded_reply)
                else:
                    await self._cancel_active_generation(
                        stop_msg.reason or "confirmed audio.stop"
                    )
                    await self._truncate_interrupted_reply()
                # Bucket 11 (voice remediation Phase 3, item 2): this branch
                # is reached only for a confirmed, non-speculative interrupt
                # that actually cut off an in-progress turn -- not a
                # same-turn "confirmed_user_speech" silence (returned above)
                # and not a speculative duck (filtered above that). That is
                # exactly the "genuine interruption" the adrenaline channel
                # exists to feel different from a false one.
                try:
                    await self.cognitive_core.state.release_adrenaline(
                        INTERRUPTION_ADRENALINE_SPIKE, reason="confirmed interruption"
                    )
                except Exception as e:
                    logger.warning(
                        "[Endocrine] Interruption adrenaline release failed: %s", e
                    )
        except Exception as e:
            logger.error(f"Error handling audio stop truncation: {e}")

    async def _on_user_voice_properties(self, data: dict[str, Any]):
        """Ingest real-time user voice properties (System 1 feature stream)."""
        try:
            props = UserVoiceProperties.model_validate(data)
            self.last_user_voice_properties = props
            # tempo_wpm is None until the first utterance completes (see
            # contracts.py's own comment) -- formatting it unconditionally
            # would raise on every chunk before that and get silently
            # swallowed below as a "parsing" error, which it is not.
            tempo = f"{props.tempo_wpm:.1f}WPM" if props.tempo_wpm is not None else "—"
            logger.debug(
                f"🎙️ Ingested User Voice | Pitch: {props.pitch_f0:.1f}Hz | Energy: {props.energy_rms:.3f} | Tempo: {tempo}"
            )
        except Exception as e:
            logger.error(f"Error parsing user voice properties: {e}")

    def _derive_expression_wire(
        self, state_snap: dict[str, Any] | None
    ) -> SpeechExpressionWire | None:
        """Phase 3B: attach the Phase 3A `SpeechExpression` alongside
        `affect` on `chat.output`.

        Stage 3: `cognitive.expression` (Codex's Phase 3A, `b1096f5`) now
        exists, so this calls the real `derive_speech_expression` rather
        than degrading to `None` -- the `ImportError` guard stays as a
        defensive fallback for a stale checkout, not the expected path
        anymore. No affect->parameter formula lives here: the single
        computation stays in `derive_speech_expression` itself, the same
        reasoning that keeps the removed `ChatOutput.prosody` mistake (two
        disagreeing implementations of one number) from recurring here.

        `intent` is still passed as `None`: a typed `CommunicativeIntent`
        (built from `plan.behavior_decision`, several layers up in
        `cognitive/decision.py`) does not currently reach this publish
        boundary -- only `state_snap` does. `derive_speech_expression`
        accepts `None` here by design (`intent` is deliberately unused in
        this first contract slice, per its own docstring), so this is not
        a placeholder waiting on a signature change -- threading a real
        intent through is a future enhancement, not a currently-broken path.
        """
        try:
            from ..cognitive.expression import derive_speech_expression
        except ImportError:
            return None
        try:
            expression = derive_speech_expression(state_snap, None)
        except Exception as e:
            logger.debug("SpeechExpression derivation skipped this chunk: %s", e)
            return None
        return SpeechExpressionWire.model_validate(expression.model_dump())

    async def _publish_final_chunk_payload(
        self,
        *,
        full_response: str,
        turn_id: str,
        generation_errors: list[str],
        is_proactive: bool,
        incoming_metadata: dict[str, Any] | None,
        incoming_latency_metadata: dict[str, Any] | None,
    ) -> None:
        """Publish the turn's terminal chat.output chunk, if there's
        anything worth sending (a proactive turn with nothing generated
        stays silent rather than publishing an empty message)."""
        if not (full_response.strip() or not is_proactive):
            return
        state_snap = self.cognitive_core.state.get_context_snapshot()
        output_msg = self.coordinator.create_chunk_payload(
            state_snap=state_snap,
            turn_id=turn_id,
            done=True,
            full_response=full_response,
            generation_error=generation_errors[-1] if generation_errors else None,
            proactive=is_proactive,
            user_distance=self.last_user_distance,
        )
        output_msg.expression = self._derive_expression_wire(state_snap)
        output_msg.metadata = incoming_metadata
        output_msg.latency_metadata = incoming_latency_metadata
        await self.publish(Topics.CHAT_OUTPUT, output_msg.model_dump())

    async def _publish_stream_error_fallback(
        self,
        error: Exception,
        *,
        turn_id: str,
        incoming_metadata: dict[str, Any] | None,
        incoming_latency_metadata: dict[str, Any] | None,
    ) -> None:
        """Recovery path for `_stream_to_speech`'s outer exception handler,
        for a non-proactive turn only (the caller checks `is_proactive`).

        P4-2: deliberately untracked (no character_offset/word_index) --
        unlike the empty-generation fallback, the caller's `full_response`
        is NOT reassigned to `fallback_text` here, and it will set
        `last_assistant_response` to whatever partial `full_response`
        `_stream_to_speech` returns, not to `fallback_text`. Stamping this
        chunk's audio against `fallback_text` would produce an offset that
        indexes into a string `last_assistant_response` never actually holds
        -- a pre-existing gap (that mismatch exists whether or not this
        chunk carries progress metadata), not one to paper over with a
        fabricated-looking offset.
        """
        fallback_text = "I'm having trouble thinking right now..."
        await self._publish_speech_chunk(
            fallback_text.split(),
            turn_id,
            incoming_metadata=incoming_metadata,
            incoming_latency_metadata=incoming_latency_metadata,
        )
        output_msg = self.coordinator.create_chunk_payload(
            done=True,
            full_response=fallback_text,
            turn_id=turn_id,
            generation_error=str(error),
            user_distance=self.last_user_distance,
        )
        output_msg.metadata = incoming_metadata
        output_msg.latency_metadata = incoming_latency_metadata
        await self.publish(Topics.CHAT_OUTPUT, output_msg.model_dump())

    async def _stream_to_speech(
        self,
        generator,
        turn_id: str,
        is_proactive: bool = False,
        incoming_metadata: dict[str, Any] | None = None,
        incoming_latency_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Helper method to process text generation streams and segment them into speech chunks."""
        full_response = ""
        current_chunk_words: list[str] = []
        segment_started_at = None
        generation_errors: list[str] = []
        fallback_text = "I'm having trouble thinking right now..."
        # P4-2: cumulative word count across every chunk published so far
        # this turn, used to derive each chunk's (character_offset,
        # word_index) into `source_text`. Correct regardless of exactly when
        # `full_response` was last extended relative to a given flush,
        # because every word that ever enters `current_chunk_words` came
        # from a `chunk_text` already appended to `full_response` -- the
        # published word sequence is always a prefix of `full_response`'s
        # own word sequence.
        published_word_count = 0

        async def _publish_tracked(words: list[str], source_text: str) -> None:
            nonlocal published_word_count
            new_word_count = published_word_count + len(words)
            offset = _char_offset_after_word(source_text, new_word_count)
            await self._publish_speech_chunk(
                words,
                turn_id,
                incoming_metadata=incoming_metadata,
                incoming_latency_metadata=incoming_latency_metadata,
                character_offset=offset,
                word_index=new_word_count,
            )
            published_word_count = new_word_count

        async def _handle_content_output(chunk_text: str) -> None:
            nonlocal full_response, current_chunk_words, segment_started_at
            await self.set_state("speaking")
            # Bucket 5 follow-up (VOICE_REMEDIATION_PLAN.md): a live capture
            # showed "Aniket" arriving as three separate stream fragments
            # ("An", "ik", "et") with no space between them -- Ollama's raw
            # token stream can emit a continuation sub-word with no leading
            # space of its own, and chunk_text.split() below has no way to
            # know that unless checked here first. Computed before the
            # append, since this is exactly the boundary a missing space
            # would sit at: neither this chunk's first character nor the
            # text-so-far's last character is whitespace.
            boundary_missing_space = bool(
                chunk_text
                and not chunk_text[0].isspace()
                and full_response
                and not full_response[-1].isspace()
            )
            full_response += chunk_text
            if not is_proactive:
                # P2-14/M1-A14: this fires once per streamed chunk, so
                # contention is rare, but an uncontended asyncio.Lock
                # acquire/release is cheap and correctness here matters
                # more than the microscopic saving from skipping it.
                async with self._turn_state_lock:
                    self.last_assistant_response = full_response

            # Reviewer finding: this glue step used to run *after* the
            # timer-flush check below. If formation_buffer_s had already
            # elapsed and the buffer held >= 3 words, the timer published
            # and cleared current_chunk_words first -- leaving nothing here
            # to glue this continuation onto, so e.g. a delayed "iket"
            # arriving after buffered "Hi I am An" got synthesized as its
            # own separate word instead of completing "Aniket". Running the
            # merge before the timer check means the buffer the timer later
            # sees already has the complete word in it.
            words = chunk_text.split()
            if boundary_missing_space and current_chunk_words and words:
                # The buffer still holds the word this fragment continues --
                # glue rather than let .split() invent a word-break that was
                # never in the original text.
                current_chunk_words[-1] += words[0]
                words = words[1:]
                # The merge may have added punctuation this fragment
                # carried (e.g. "Anik" + "et," -> "Aniket,") that changes
                # whether this word is now a split point.
                score = self.coordinator.segmenter.score_split_point(
                    current_chunk_words[-1], len(current_chunk_words)
                )
                if score >= 0.7 or len(current_chunk_words) > 12:
                    await _publish_tracked(current_chunk_words, full_response)
                    current_chunk_words = []
                    segment_started_at = None

            now_monotonic = time.perf_counter()
            if (
                current_chunk_words
                and segment_started_at is not None
                and (now_monotonic - segment_started_at)
                >= self.coordinator.formation_buffer_s
                and len(current_chunk_words) >= 3
            ):
                await _publish_tracked(current_chunk_words, full_response)
                current_chunk_words = []
                segment_started_at = None

            for word in words:
                if not current_chunk_words:
                    segment_started_at = time.perf_counter()
                current_chunk_words.append(word)

                score = self.coordinator.segmenter.score_split_point(
                    word, len(current_chunk_words)
                )
                # Bucket 5 (VOICE_REMEDIATION_PLAN.md): comma/colon/semicolon
                # (0.4) plus the length-pressure term at or past target_size
                # (0.3) sums to exactly 0.7, which a strict `>` can never
                # satisfy -- the single most natural prosodic boundary
                # (Goldman-Eisler juncture) could never fire on its own.
                if score >= 0.7 or len(current_chunk_words) > 12:
                    await _publish_tracked(current_chunk_words, full_response)
                    current_chunk_words = []
                    segment_started_at = None

        await self.set_state("thinking")

        try:
            async for output in generator:
                if output["type"] == "content":
                    await _handle_content_output(output["data"])

                elif output["type"] == "error":
                    error_msg = str(
                        output.get("data", "unknown cognitive stream error")
                    )
                    generation_errors.append(error_msg)
                    logger.error(
                        "[Brain] LLM stream error on turn_id=%s: %s",
                        turn_id,
                        error_msg,
                    )

                elif output["type"] == "pipeline_telemetry":
                    if incoming_latency_metadata is None:
                        incoming_latency_metadata = {}
                    incoming_latency_metadata["pipeline_telemetry"] = output["data"]

                elif output["type"] == "action_intent":
                    # Phase 1 causal slice (§22, §38): Stage 6 committed this
                    # before any of the content above was generated. Bound
                    # here, before playback/interruption events can race it,
                    # so _on_audio_playback_progress/_truncate_interrupted_reply/
                    # _cancel_active_generation always have the right intent
                    # to attribute their OutcomeRecord to.
                    try:
                        intent = ActionIntent.model_validate(output["data"])
                        async with self._turn_state_lock:
                            self._active_action_intent = intent
                    except Exception:
                        logger.warning(
                            "[Brain] Malformed action_intent chunk on turn_id=%s",
                            turn_id,
                            exc_info=True,
                        )

                elif output["type"] == "done":
                    if current_chunk_words:
                        await _publish_tracked(current_chunk_words, full_response)
                        current_chunk_words = []

                    if not full_response.strip() and not is_proactive:
                        logger.error(
                            "[Brain] Empty generation on turn_id=%s. errors=%s. Emitting fallback.",
                            turn_id,
                            generation_errors[-3:],
                        )
                        full_response = fallback_text
                        await _publish_tracked(fallback_text.split(), full_response)

                    await self._publish_final_chunk_payload(
                        full_response=full_response,
                        turn_id=turn_id,
                        generation_errors=generation_errors,
                        is_proactive=is_proactive,
                        incoming_metadata=incoming_metadata,
                        incoming_latency_metadata=incoming_latency_metadata,
                    )

        except Exception as e:
            logger.error("Cognitive Loop error on turn_id=%s: %s", turn_id, e)
            if not is_proactive:
                await self._publish_stream_error_fallback(
                    e,
                    turn_id=turn_id,
                    incoming_metadata=incoming_metadata,
                    incoming_latency_metadata=incoming_latency_metadata,
                )

        await self.set_state("idle")
        return full_response

    async def _publish_speech_chunk(
        self,
        words: list[str],
        turn_id: str | None = None,
        incoming_metadata: dict[str, Any] | None = None,
        incoming_latency_metadata: dict[str, Any] | None = None,
        character_offset: int | None = None,
        word_index: int | None = None,
    ):
        """
        Publishes a semantically coherent chunk with full PAD affect metadata.
        Implements the Brain→Voice contract from psychological_layer.md §5.3.
        """
        text = " ".join(words).strip()
        if not text:
            return

        # Built by the coordinator, which is where every other chunk on this
        # subject is built. This method used to re-derive the affect vector
        # inline — the same eight `state_snap.get(...)` lines with the same
        # defaults — so the wire contract had two implementations and a change
        # to one silently produced streams whose chunks disagreed with their own
        # `done` message. Exactly the drift that put prosody in this state to
        # begin with, one layer up.
        state_snap = self.cognitive_core.state.get_context_snapshot()
        payload = self.coordinator.create_chunk_payload(
            words=words,
            state_snap=state_snap,
            turn_id=turn_id,
            user_distance=self.last_user_distance,
        )
        payload.expression = self._derive_expression_wire(state_snap)
        # P4-2: non-destructive merge -- `incoming_metadata` originates from
        # the user's own chat.input and must reach voice/transport unchanged;
        # this only adds two keys alongside it. voice-agent passes them
        # through unchanged on every PCM chunk it publishes for this text, and
        # transport_agent relays them as `audio.playback.progress` once that
        # PCM has actually reached the LiveKit audio source -- the closest
        # observable "reached the speaker" point in this architecture.
        # Deliberately omitted (None) for the one caller that cannot make
        # them meaningful (see _stream_to_speech's exception-handler
        # fallback) -- absent metadata is honest; a fabricated offset is not.
        if character_offset is not None and word_index is not None:
            metadata = dict(incoming_metadata) if incoming_metadata else {}
            metadata["character_offset"] = character_offset
            metadata["word_index"] = word_index
            payload.metadata = metadata
        else:
            payload.metadata = incoming_metadata
        payload.latency_metadata = incoming_latency_metadata
        await self.publish(Topics.CHAT_OUTPUT, payload.model_dump())

    async def stop(self):
        """P3-4: brain_agent owns the most resources of any agent in the
        mesh (the LLM client, the graph driver, two DB pools, the whole
        cognitive core) and used to close none of them -- the exact "owns
        the most, cleans up least" asymmetry this item names. Cancel first,
        close second: an in-flight generation still holding `memory_store`/
        `graph_db` must stop before those are torn out from under it.
        """
        await self._prepare_stop()
        async with self._generation_lock:
            task = self._active_generation_task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "Active generation task raised while being cancelled"
                    )

        self.cognitive_core.close()

        for resource, label in (
            (self.ollama, "LLMClient"),
            (self.graph_db, "GraphDB"),
            (self.memory_store, "MemoryStore"),
            (self.conversation_store, "ConversationHistoryStore"),
        ):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception as e:
                logger.warning(f"[Brain] {label} close warning: {e}")

        await super().stop()
        logger.info(f"🧠 {self.name} Offline.")


async def main():
    if Config.RUNTIME_AUTO_BOOTSTRAP:
        logger.info("[Brain] Running runtime bootstrap checks...")
        await bootstrap_runtime()

    # 1. Initialize AI Friend Foundation (Pool-based logic)
    conversation_store = ConversationHistoryStore()
    await conversation_store.initialize()  # Creates the database pool

    graph_db = GraphDB()
    await graph_db.initialize()
    # Inject the established pool and graph_db into MemoryStore
    memory_store = MemoryStore(pool=conversation_store.pool, graph_db=graph_db)

    # 2. Instantiate Brain Agent with injected dependencies
    agent = BrainAgent(
        ollama_url=Config.OLLAMA_URL,
        graph_db=graph_db,
        memory_store=memory_store,
        conversation_store=conversation_store,
    )

    await agent.start()

    shutdown_trigger = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_trigger)
    await shutdown_trigger.wait()
    await agent.stop()


if __name__ == "__main__":
    setup_logging(level=logging.INFO, json_format=getattr(Config, "LOG_JSON", False))
    asyncio.run(main())
