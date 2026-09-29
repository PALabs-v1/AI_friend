import asyncio
import inspect
import logging
import math
import re
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from .. import clock
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
    AudioPerception,
    AudioPlaybackBacklog,
    AudioPlaybackLifecycle,
    AudioPlaybackProgress,
    AudioStop,
    ChatInput,
    LifecycleApplyResult,
    PlaybackLifecycleTracker,
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


# How long a cut waits for the reply's own history insert before giving up
# (it runs under `_turn_state_lock`; the store's pool has no command timeout).
REPLY_INSERT_WAIT_S = 2.0
# How long a replacement or a confirmed stop waits, holding
# `_generation_lock`, for a cancelled generation to unwind. A generator
# stalled in its cancellation cleanup would otherwise hold every new
# chat.input; past this the task is fenced (it can no longer publish) and
# left to finish on its own.
GENERATION_TEARDOWN_WAIT_S = 0.25
REPLY_LEDGER_MAX = 32
# Resolutions and declined proactive inputs are kept for observation (tests,
# BrainBench) and for ignoring a stop that names an already-resolved reply.
# Bounded: one entry per reply for the life of the process otherwise.
REPLY_RESOLUTIONS_MAX = 256


@dataclass(frozen=True)
class ReplyResolution:
    turn_id: str
    status: str
    heard_text: str
    character_offset: int
    source: str
    reason: str


@dataclass
class _ReplyLedgerEntry:
    turn_id: str
    source: str
    importance: float | None = None
    text: str = ""
    intent: ActionIntent | None = None
    progress: AudioPlaybackProgress | None = None
    message_id: uuid.UUID | None = None
    log_task: asyncio.Task | None = None
    started: bool = False
    speaking: bool = False
    cut_pending: bool = False
    cut_reason: str | None = None
    resolved: bool = False
    grace_task: asyncio.Task | None = None
    # The flow task generating this reply, and why it was cancelled if it
    # was. A reply whose flow ends before its first chunk resolves CANCELLED
    # in that flow (§4); nothing was published, so no terminal will come.
    generation: asyncio.Task | None = None
    cancel_reason: str | None = None
    interruption_felt: bool = False  # adrenaline released for a stop on it
    status: str | None = None  # the terminal status it resolved with


def _finite_score(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        return min(1.0, max(0.0, score)) if math.isfinite(score) else None
    return None


def _proactive_category(value: Any) -> str | None:
    return value if value in {"useful_to_user", "self_directed"} else None


def _char_offset_after_word(text: str, word_count: int) -> int:
    """P4-2: the exact character offset in `text` right after its
    `word_count`-th whitespace-delimited token.

    Used to stamp each published speech chunk with where it ends in the
    *true* response text, so a cut (`_resolve_reply`) can later slice the
    reply at a real boundary instead of the reconstructed
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
        self._playback_lifecycle = PlaybackLifecycleTracker()
        self.last_assistant_response: str | None = None
        self._active_response_turn_id: str | None = None
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
        # W5 (ADR-W5 §3): every reply that has not resolved yet, by turn id.
        # It replaced the single superseded-reply slot: each reply carries
        # its own text, progress, intent and history row, so a cut, a stale
        # stop and a transport terminal all address that reply and no other.
        self.reply_resolutions: deque[ReplyResolution] = deque(
            maxlen=REPLY_RESOLUTIONS_MAX
        )
        self.declined_proactive_inputs: deque[str] = deque(maxlen=REPLY_RESOLUTIONS_MAX)
        self._reply_ledger: OrderedDict[str, _ReplyLedgerEntry] = OrderedDict()
        # Resolved replies, still addressable by a stop (`_handle_ledger_audio_stop`).
        self._resolved_replies: OrderedDict[str, _ReplyLedgerEntry] = OrderedDict()
        self._accepted_utterances: OrderedDict[str, None] = OrderedDict()
        self._seen_audio_stops: OrderedDict[tuple[Any, ...], None] = OrderedDict()
        # I10 (W5 critic round 2): an unscoped stop (`turn_id` is None)
        # resolves to whatever turn is active *the first time it is seen*.
        # `_seen_audio_stops` shares its 256-key window with every scoped
        # stop too, so heavy scoped traffic can evict an unscoped stop's key
        # long before JetStream redelivers the same message -- at which
        # point it would resolve against a completely different, later
        # active turn. Kept separately, keyed on identity minus `turn_id`
        # (which is always None here), so a redelivery always finds the
        # turn it first resolved to.
        self._unscoped_stop_targets: OrderedDict[tuple[Any, ...], str] = OrderedDict()
        self._last_user_partial_at: float | None = None
        self._reply_terminal_events: set[tuple[str, str, int]] = set()
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
        # P2-14/M1-A14: the active turn, the ledger entries and the
        # `last_*` fields are written from independent NATS subscription
        # tasks -- chat.input's turn flow, the playback progress tracker and
        # the audio.stop handler. `_generation_lock` guards only which task
        # owns `_active_generation_task`; this lock keeps each of those
        # read-then-write sections whole.
        self._turn_state_lock = asyncio.Lock()

    def reply_ledger_size(self) -> int:
        return len(self._reply_ledger)

    def _is_user_mid_utterance(self, now: float | None = None) -> bool:
        last_partial = self._last_user_partial_at
        return bool(
            last_partial is not None
            and (clock.monotonic() if now is None else now) - last_partial
            <= Config.USER_MID_UTTERANCE_TIMEOUT_S
        )

    def _remember_accepted_utterance(self, utterance_id: str | None) -> bool:
        if not utterance_id:
            return True
        if utterance_id in self._accepted_utterances:
            return False
        self._accepted_utterances[utterance_id] = None
        while len(self._accepted_utterances) > 256:
            self._accepted_utterances.popitem(last=False)
        return True

    async def _resolve_reply(
        self,
        entry: _ReplyLedgerEntry,
        status: str,
        *,
        offset: int | None = None,
        reason: str,
        error: str | None = None,
    ) -> None:
        if entry.resolved:
            return
        entry.resolved = True
        entry.status = status
        # Everything up to the history write is synchronous, so a caller
        # cancelled during that write has already freed the ledger slot and
        # recorded the outcome (the overflow caller also shields the write).
        self._reply_ledger.pop(entry.turn_id, None)
        resolved = getattr(self, "_resolved_replies", None)
        if resolved is not None:
            resolved[entry.turn_id] = entry
            while len(resolved) > REPLY_RESOLUTIONS_MAX:
                resolved.popitem(last=False)
        progress = getattr(self, "last_audio_progress", None)
        if getattr(progress, "utterance_id", None) == entry.turn_id:
            self.last_audio_progress = None
        if entry.grace_task and entry.grace_task is not asyncio.current_task():
            entry.grace_task.cancel()
        text = entry.text
        heard_offset = (
            len(text)
            if status == "COMPLETED"
            else max(
                0,
                min(
                    offset
                    if offset is not None
                    else (entry.progress.character_offset if entry.progress else 0),
                    len(text),
                ),
            )
        )
        heard = text[:heard_offset].strip()
        if status == "COMPLETED":
            heard = text
            heard_offset = len(text)
        # `entry` is already out of `_reply_ledger` (popped above); the only
        # other reader of `.text` is `_finish_reply`, which checks
        # `_resolved_replies` when it finds no live entry. Narrowing it to
        # `heard` here means that lookup always sees what was actually
        # heard, never the full generation this reply was cut from -- the
        # ordering that beats `_finish_reply` to a row id has nothing to
        # rewrite (I3): the row gets created with the true text the first
        # time, and never needs cutting down.
        entry.text = heard
        await self._emit_outcome_record(
            entry.intent,
            status=status,
            actual_delivered_text=heard,
            character_offset=heard_offset,
            error=error,
        )
        self.reply_resolutions.append(
            ReplyResolution(
                turn_id=entry.turn_id,
                status=status,
                heard_text=heard,
                character_offset=heard_offset,
                source=entry.source,
                reason=reason,
            )
        )
        if heard != text:  # a reply never stored has no row (`_store_heard_reply`)
            await self._store_heard_reply(heard, entry.log_task, entry.message_id)

    async def _cut_reply(
        self, entry: _ReplyLedgerEntry, reason: str, *, publish: bool = True
    ) -> None:
        if entry.resolved or entry.cut_pending:
            return
        if not entry.started:
            await self._resolve_reply(entry, "CANCELLED", reason=reason)
            return
        entry.cut_pending = True
        entry.cut_reason = reason
        if publish:
            await self.publish(
                Topics.AUDIO_STOP,
                AudioStop(
                    interrupt=True,
                    speculative=False,
                    reason=reason,
                    turn_id=entry.turn_id,
                ).model_dump(),
            )

        async def terminal_timeout() -> None:
            await clock.sleep(Config.REPLY_TERMINAL_WAIT_S)
            if entry.resolved:
                return
            status = "TRUNCATED" if entry.speaking else "CANCELLED"
            offset = entry.progress.character_offset if entry.progress else 0
            await self._resolve_reply(entry, status, offset=offset, reason=reason)

        entry.grace_task = self.spawn(terminal_timeout())

    def _request_generation_cancel(self, turn_id: str) -> None:
        """Stop future chunks for a cut reply without awaiting its flow task."""
        if getattr(self, "_active_response_turn_id", None) != turn_id:
            return
        task = getattr(self, "_active_generation_task", None)
        if task is not None and not task.done():
            task.cancel()

    def _generation_entry(self, task: asyncio.Task | None) -> _ReplyLedgerEntry | None:
        """The unresolved reply `task` is generating, if any."""
        if task is None:
            return None
        return next(
            (
                entry
                for entry in self._reply_ledger.values()
                if entry.generation is task
            ),
            None,
        )

    async def _end_generation(
        self, turn_id: str, ended: str, generation: asyncio.Task | None = None
    ) -> None:
        """Resolve a reply whose flow ended before its first chunk.

        Nothing was published for it and nothing will be, so no transport
        terminal can come; left in the ledger it waited for an overflow to
        record it FAILED. Runs in the flow's own `finally`, so a cancelled
        flow has resolved its reply by the time its canceller's await
        returns (or, past `GENERATION_TEARDOWN_WAIT_S`, the canceller runs
        it for that flow's `generation`). Only the entry that flow created
        is touched: a flow cancelled before `_begin_turn` added one must not
        resolve another flow's reply under the same turn id.
        """
        entry = self._reply_ledger.get(turn_id)
        if generation is None:
            generation = asyncio.current_task()
        if (
            entry is None
            or entry.started
            or entry.resolved
            or entry.generation is not generation
        ):
            return
        reason = entry.cancel_reason or ended
        await self._resolve_reply(
            entry,
            "CANCELLED",
            reason=reason,
            error=None if reason == "nothing_generated" else reason,
        )

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
            Topics.AUDIO_PERCEPTION,
            self._on_user_speech_partial,
            durable=f"{self.name}_audio_perception_live",
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
            Topics.AUDIO_PLAYBACK_LIFECYCLE,
            self._on_audio_playback_lifecycle,
            durable=f"{self.name}_audio_playback_lifecycle_live",
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
        """Cancel the in-flight generation task and wait for it to unwind.

        A4: a fire-and-forget .cancel() only *requests* cancellation - the task
        keeps running until its next await point, so it could still publish
        chunks for a reply that was already cut. By the time this returns the
        task has stopped, or (past `GENERATION_TEARDOWN_WAIT_S`) is fenced
        from publishing, before the caller (a confirmed audio.stop) cuts the
        reply. A reply the task had not started resolves CANCELLED, with
        `reason`, in the task's own `finally` (`_end_generation`).
        """
        async with self._generation_lock:
            task = self._active_generation_task
            if not task or task.done():
                return
            entry = self._generation_entry(task)
            if entry is not None and entry.cancel_reason is None:
                entry.cancel_reason = reason
            logger.info("Cancelling active generation task: %s", reason)
            task.cancel()
            try:
                await self._await_generation_teardown(task, entry)
            finally:
                if self._active_generation_task is task:
                    self._active_generation_task = None

    async def _await_generation_teardown(
        self, task: asyncio.Task, entry: _ReplyLedgerEntry | None
    ) -> None:
        """Wait, bounded, for a cancelled generation task to unwind.

        Past `GENERATION_TEARDOWN_WAIT_S` the task is left running: it is
        fenced from publishing (`_generation_fenced`), and the reply it had
        not started is resolved here instead of in its own `finally`.
        """
        done, _pending = await asyncio.wait({task}, timeout=GENERATION_TEARDOWN_WAIT_S)
        if task in done:
            if not task.cancelled() and task.exception() is not None:
                logger.error(
                    "Previous generation task raised while being cancelled",
                    exc_info=task.exception(),
                )
            return
        logger.warning(
            "Cancelled generation still unwinding after %.2f s; continuing without it.",
            GENERATION_TEARDOWN_WAIT_S,
        )
        if entry is not None:
            await self._end_generation(entry.turn_id, "generation_cancelled", task)

    @staticmethod
    def _generation_fenced() -> bool:
        """True inside a generation task that has been told to cancel.

        A generator that catches the cancellation and keeps going, or is
        still unwinding when its successor starts, must not put more of a
        superseded reply on the wire.
        """
        task = asyncio.current_task()
        return task is not None and task.cancelling() > 0

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
        # I5 (W5 critic round 2): this call used to be awaited directly with
        # no deadline of its own, so a stalled rewrite blocked whichever
        # lifecycle handler called us -- and with it, every later lifecycle
        # event, since NATS delivers them to this callback one at a time.
        # The insert wait above has to be synchronous (the rewrite would
        # otherwise race the insert), but the rewrite itself does not: it is
        # this call's last statement, so nothing here depends on its result.
        # Spawned and tracked (kept alive past this call's return, unlike a
        # bare `create_task`) rather than awaited, so the handler's return
        # is never at the mercy of the store's latency.
        self.spawn(
            self.conversation_store.rewrite_assistant_message(
                heard, message_id=message_id
            )
        )

    async def _replace_active_generation(self, coro_factory, reason: str):
        """Atomically replace the active generation task with a new one.

        Holds the lock through the entire critical section: cancel the prior task,
        await its teardown (bounded, `_await_generation_teardown`), create
        the new task, and assign it to
        _active_generation_task. This prevents concurrent callers from both
        creating tasks and losing ownership when one overwrites the other's
        assignment (TOCTOU race).

        A reply the prior task had not started resolves CANCELLED with
        `reason` in that task's `finally` (`_end_generation`), before the new
        task exists. A started reply is not touched: it plays on until a stop
        cuts it or its transport terminal resolves it.

        `coro_factory` is a zero-argument callable, not a coroutine, and is
        called only after teardown (W5 critic round 2, LOW): a coroutine
        built by the caller before this call exists in memory the moment
        it's constructed, so a caller cancelled while this method still
        awaits the prior task's teardown would otherwise leave that
        already-built coroutine neither scheduled nor closed, an unawaited
        coroutine leaked past this call's own cancellation.

        Args:
            coro_factory: Zero-argument callable returning the coroutine to
                wrap in the new generation task, called once teardown of any
                prior task has finished
            reason: Reason for cancelling the prior task (if any)

        Returns:
            The newly created task
        """
        async with self._generation_lock:
            prior_task = self._active_generation_task
            if prior_task and not prior_task.done():
                prior_entry = self._generation_entry(prior_task)
                if prior_entry is not None and prior_entry.cancel_reason is None:
                    prior_entry.cancel_reason = reason
                logger.info("Cancelling active generation task: %s", reason)
                prior_task.cancel()
                await self._await_generation_teardown(prior_task, prior_entry)

            # Create and assign new task while still holding the lock. The
            # coroutine itself is built here, after teardown, not by the
            # caller before this call -- see the LOW fix note above.
            new_task = self.spawn(coro_factory())
            self._active_generation_task = new_task

        return new_task

    async def _on_chat_input(self, message: dict[str, Any]):
        try:
            msg = ChatInput.model_validate(message)
            is_subconscious = msg.metadata.source == "subconscious"
        except ValidationError as e:
            logger.warning(f"Dropping invalid chat.input message: {e}")
            return
        except Exception:
            logger.exception("Unexpected error processing chat.input")
            return

        if not self._remember_accepted_utterance(msg.utterance_id):
            return

        if is_subconscious:
            if not await self._allow_proactive_input(msg):
                return
        else:
            self._last_user_partial_at = None

        self.last_interaction_time = datetime.now()

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
        if not is_subconscious and not await self._handle_user_final(msg):
            return

        # The per-subject NATS callback accepts one message at a time and this
        # task replacement is serialized by _generation_lock. Ack now means
        # accepted: at most one generator runs, rapid inputs cancel its
        # predecessor, and no in-process input queue can grow without bound.
        task = await self._replace_active_generation(
            lambda: self._process_chat_input_flow(msg, is_subconscious, message),
            "new incoming speech turn",
        )

        def clear_generation(done_task: asyncio.Task) -> None:
            if done_task.cancelled():
                logger.info("Active generation flow task cancelled.")
            # This callback runs after the flow ends, without holding up the
            # serial NATS callback that must accept the next input.
            if self._active_generation_task is done_task:
                self._active_generation_task = None

        task.add_done_callback(clear_generation)

    async def _allow_proactive_input(self, msg: ChatInput) -> bool:
        active_id = getattr(self, "_active_response_turn_id", None)
        active_reply = self._reply_ledger.get(active_id) if active_id else None
        # An unresolved user reply is in flight from the moment its turn
        # begins: pacing, generating or playing. Keyed on a generating flag,
        # a user reply in its pacing sleep (300-900 ms) did not count, and
        # any thought landing then ceded it CANCELLED below: the user's
        # question went unanswered. A flow that ends without a chunk
        # resolves its reply (`_end_generation`), so unresolved means live.
        user_reply_in_flight = bool(
            active_reply and active_reply.source == "user" and not active_reply.resolved
        )
        importance = _finite_score(msg.metadata.importance) or 0.0
        if self._is_user_mid_utterance() or (
            user_reply_in_flight
            and importance < Config.SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE
        ):
            self.declined_proactive_inputs.append(msg.utterance_id or "")
            return False
        if user_reply_in_flight and active_reply is not None:
            if not active_reply.started:
                # `_cut_reply` publishes only for a started reply, but the
                # interrupt announces itself either way (ADR-W5 §5): a
                # filler may already be playing under this turn.
                await self.publish(
                    Topics.AUDIO_STOP,
                    AudioStop(
                        interrupt=True,
                        speculative=False,
                        reason="self_thought_interrupt",
                        turn_id=active_reply.turn_id,
                    ).model_dump(),
                )
            await self._cut_reply(active_reply, "self_thought_interrupt")
        elif active_reply is not None:
            await self._cut_reply(active_reply, "proactive_ceded")
        # I8 (W5 critic round 2): the checks above ran before the awaited
        # cut publish, and a real user partial can land during that await.
        # Recheck the state this gate exists to enforce, right before
        # granting the floor, rather than trusting a read that is now
        # stale -- the cut already under way (if any) still proceeds; only
        # the floor grant itself is race-safe.
        if self._is_user_mid_utterance():
            self.declined_proactive_inputs.append(msg.utterance_id or "")
            return False
        return True

    async def _handle_user_final(self, msg: ChatInput) -> bool:
        """Apply onset filtering and cut the reply before accepting its turn."""
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
            and (clock.time() - self._last_audio_onset_at)
            < Config.BARGE_IN_ONSET_GRACE_S
        )
        if not speculative_intent_pending and not within_onset_grace:
            stop_msg = AudioStop(
                interrupt=True,
                speculative=False,
                reason="confirmed_user_speech",
                perception_text=msg.text,
                intent="CONFIRMED_STOP",
                utterance_id=msg.utterance_id,
            )
            # Unscoped, as before W5: the user talking over the agent
            # silences whatever is playing, and that may be an older reply
            # still draining while a newer turn is active. A stop scoped to
            # the active turn left it playing over the user.
            await self.publish(Topics.AUDIO_STOP, stop_msg.model_dump())
            # Every reply the transport is flushing is cut here, so each one
            # resolves from its INTERRUPTED heard offset (DR-028, F-013).
            for reply in tuple(self._reply_ledger.values()):
                if reply.started and not reply.resolved:
                    await self._cut_reply(reply, "confirmed_user_speech", publish=False)
        elif within_onset_grace and not speculative_intent_pending:
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
            return False
        # With a speculative intent pending nothing is cut here: no stop was
        # published, and Stage 2 either confirms (a `confirmed_command` stop
        # that cuts the reply through the ledger) or rejects and resumes the
        # audio, in which case the reply plays on and must not be recorded
        # as cut. A reply still generating is cancelled by the replacement.
        return True

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

    async def _on_user_speech_partial(self, data: dict[str, Any]) -> None:
        try:
            partial = AudioPerception.model_validate(data)
        except ValidationError:
            return
        if not partial.text.strip():
            return
        onset = getattr(self, "_last_audio_onset_at", None)
        if onset is not None and clock.time() - onset < Config.BARGE_IN_ONSET_GRACE_S:
            return
        self._last_user_partial_at = clock.monotonic()
        active_id = getattr(self, "_active_response_turn_id", None)
        entry = self._reply_ledger.get(active_id) if active_id else None
        if entry is None or entry.source != "proactive" or entry.resolved:
            return
        if (
            not entry.started
            or (entry.importance or 0.0) < Config.PROACTIVE_GRACE_MIN_IMPORTANCE
        ):
            await self._cut_reply(entry, "proactive_ceded")
            self._request_generation_cancel(entry.turn_id)
            return
        if entry.grace_task is not None:
            return

        async def expire_grace() -> None:
            await clock.sleep(Config.PROACTIVE_GRACE_WINDOW_S)
            if not entry.resolved and not entry.cut_pending:
                await self._cut_reply(entry, "proactive_grace_expired")
                self._request_generation_cancel(entry.turn_id)

        entry.grace_task = self.spawn(expire_grace())

    async def _begin_turn(
        self,
        turn_id: str,
        *,
        source: str = "user",
        importance: float | None = None,
    ) -> str | None:
        """Make `turn_id` the active turn; returns the turn it superseded.

        The superseded turn's reply keeps its own ledger entry (ADR-W5 §3):
        the confirmed "stop" Stage 2 addresses to it, its progress and its
        transport terminal all find it there, whatever this turn does to the
        live fields. The returned id goes into the pipeline's metadata as
        `interrupted_turn_id`, which is how Stage 2 knows what to address.
        """
        async with self._turn_state_lock:
            interrupted_turn_id = getattr(self, "_active_response_turn_id", None)
            self._active_response_turn_id = turn_id
            self._reply_ledger[turn_id] = _ReplyLedgerEntry(
                turn_id=turn_id,
                source=source,
                importance=importance,
                generation=asyncio.current_task(),
            )
            overflow = (
                next(iter(self._reply_ledger.values()))
                if len(self._reply_ledger) > REPLY_LEDGER_MAX
                else None
            )
        if overflow is not None:
            # Shielded: this flow can be cancelled by the next input while
            # the evicted reply's row is being cut, and that write must still
            # land. (`_resolve_reply` frees the slot before it awaits.)
            await asyncio.shield(
                self.spawn(
                    self._resolve_reply(
                        overflow,
                        "FAILED",
                        offset=(
                            overflow.progress.character_offset
                            if overflow.progress
                            else 0
                        ),
                        reason="reply_ledger_overflow",
                        error="reply ledger capacity exceeded",
                    )
                )
            )
        return interrupted_turn_id

    async def _process_chat_input_flow(
        self, chat_input: ChatInput, is_subconscious: bool, message: dict[str, Any]
    ):
        user_text = chat_input.text
        turn_id = chat_input.turn_id or chat_input.utterance_id or str(uuid.uuid4())
        metadata, latency_metadata = self._resolve_chat_input_metadata(
            chat_input, message
        )

        if not user_text:
            return

        # However the flow ends, a reply it never started is resolved here
        # (`_end_generation`); a started one resolves on its terminal or cut.
        # `_begin_turn` is inside: a flow cancelled while its overflow
        # resolves has already added its own entry.
        try:
            interrupted_turn_id = await self._begin_turn(
                turn_id,
                source="proactive" if is_subconscious else "user",
                importance=_finite_score(metadata.get("importance")),
            )
            await self._run_turn(
                chat_input,
                is_subconscious,
                message,
                turn_id=turn_id,
                metadata=metadata,
                latency_metadata=latency_metadata,
                interrupted_turn_id=interrupted_turn_id,
            )
        except asyncio.CancelledError:
            await self._end_generation(turn_id, "generation_cancelled")
            raise
        except Exception:
            await self._end_generation(turn_id, "generation_failed")
            raise
        await self._end_generation(turn_id, "nothing_generated")

    async def _run_turn(
        self,
        chat_input: ChatInput,
        is_subconscious: bool,
        message: dict[str, Any],
        *,
        turn_id: str,
        metadata: dict[str, Any],
        latency_metadata: dict[str, Any],
        interrupted_turn_id: str | None,
    ) -> None:
        """Pace, run the pipeline, stream the reply and store it."""
        user_text = chat_input.text
        utterance_id = chat_input.utterance_id

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

        # Keep the brain's durable view aligned with attempts it accepts from
        # the subconscious; user idle time still advances only on user turns.
        if is_subconscious:
            self.cognitive_core.state.mark_proactive_attempt()
            goal_id = metadata.get("goal_id")
            self.cognitive_core.state.record_proactive_thought(
                user_text, goal_id=goal_id if isinstance(goal_id, str) else None
            )
            await self.cognitive_core.state.persist_state()
        else:
            self.cognitive_core.state.resolve_proactive_thoughts(user_text)
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
            # P2-14/M1-A14: locked so this reset cannot land inside another
            # subscription's read-then-write of the same fields.
            async with self._turn_state_lock:
                self.last_assistant_response = None
                self.last_audio_progress = None
                self._active_action_intent = None
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

        await self._finish_reply(turn_id, full_response, is_subconscious)

    async def _finish_reply(
        self, turn_id: str, full_response: str, is_subconscious: bool
    ) -> None:
        """Record the finished reply on its ledger entry and store it.

        The history row id and its insert task go on the entry before the
        state lock is taken: a stop can arrive while this flow waits for
        that lock, and its cut must wait for and rewrite this reply's own
        insert (`_store_heard_reply`), never cancel the only writer.

        A terminal can instead beat this call to the ledger entirely: the
        done marker was still publishing when `_on_audio_playback_lifecycle`
        resolved the reply and popped it (I3, W5 critic round 2). `entry` is
        then `None` here, but the reply is not unaccounted for -- it moved
        to `_resolved_replies`, with `.text` already narrowed to what was
        actually heard. Storing `full_response` in that case would write
        the whole generation after the fact, so the resolved entry's text
        is what gets stored, never the parameter.
        """
        entry = self._reply_ledger.get(turn_id)
        resolved_entry = self._resolved_replies.get(turn_id) if entry is None else None
        store_text = (
            resolved_entry.text if resolved_entry is not None else full_response
        )
        store = self.conversation_store if store_text else None
        target = entry if entry is not None else resolved_entry
        message_id = uuid.uuid4() if store is not None else None
        log_task = None
        if store is not None:
            if target is not None:
                message_id = target.message_id or message_id
                target.message_id = message_id
                log_task = target.log_task
            if log_task is None:
                log_task = self.spawn(
                    store.log_message("assistant", store_text, message_id=message_id)
                )
            if target is not None:
                target.log_task = log_task
        async with self._turn_state_lock:
            if not is_subconscious:
                self.last_assistant_response = full_response
            if entry is not None:
                entry.text = full_response
                if not is_subconscious:
                    entry.intent = getattr(self, "_active_action_intent", None)
                entry.log_task = log_task
                entry.message_id = message_id

    async def _on_audio_playback_progress(self, data: dict[str, Any]):
        """Tracks the current word/character progress of the audio playback."""
        try:
            self.last_percept = percept.from_playback_progress(data)
            progress = AudioPlaybackProgress.model_validate(data)
            async with self._turn_state_lock:
                entry = self._reply_ledger.get(progress.utterance_id)
                if entry is not None and not entry.resolved:
                    entry.progress = progress
                    entry.started = True
                    entry.speaking = True
                active_turn_id = getattr(self, "_active_response_turn_id", None)
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
                    self._last_audio_onset_at = clock.time()
            logger.debug(
                f"🔊 Audio Playback Progress | Word Index: {progress.word_index} | Offset: {progress.character_offset} | Completed: {progress.completed}"
            )
        except Exception as e:
            logger.error(f"Error parsing audio playback progress: {e}")

    async def _on_audio_playback_lifecycle(self, data: dict[str, Any]):
        """Consume transport's terminal authority; `progress` stays nonterminal."""
        try:
            event = AudioPlaybackLifecycle.model_validate(data)
            result = self._playback_lifecycle.apply(event)
            if result is LifecycleApplyResult.PROTOCOL_ERROR:
                logger.error(
                    "Rejected conflicting playback terminal for utterance=%s turn=%s (protocol_errors=%d)",
                    event.utterance_id,
                    event.turn_id,
                    self._playback_lifecycle.protocol_errors,
                )
                return
            if result is not LifecycleApplyResult.APPLIED:
                return
            if event.state in {"STARTED", "PLAYING"}:
                progress = AudioPlaybackProgress(
                    utterance_id=event.utterance_id,
                    character_offset=event.heard_offset,
                    word_index=event.words_played,
                    completed=False,
                )
                await self._on_audio_playback_progress(progress.model_dump())
                return
            if event.state not in {"COMPLETED", "INTERRUPTED", "FAILED"}:
                return
            entry = self._reply_ledger.get(event.turn_id)
            if entry is not None:
                if event.state == "INTERRUPTED" and event.flushed:
                    return
                entry.started = True
                entry.speaking = False
                await self._resolve_reply(
                    entry,
                    {
                        "COMPLETED": "COMPLETED",
                        "INTERRUPTED": "TRUNCATED",
                        "FAILED": "FAILED",
                    }[event.state],
                    offset=event.heard_offset,
                    reason=entry.cut_reason or event.state.lower(),
                    error=(
                        "playback transport failed" if event.state == "FAILED" else None
                    ),
                )
                return
            # No ledger entry: the reply already resolved (its one outcome is
            # recorded) or was never this brain's. Nothing to apply.
            logger.debug(
                "Ignoring playback %s for unknown or resolved turn %s.",
                event.state,
                event.turn_id,
            )
        except Exception:
            logger.exception("Error consuming audio playback lifecycle event")

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
        interrupted turn and cuts the addressed reply through the ledger
        (`_handle_ledger_audio_stop`), which rewrites its history to what
        was heard once the transport's terminal (or the wait) resolves it.

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
            if not self._remember_audio_stop(stop_msg):
                return

            # A flush stop silences the rejected take while its same-turn
            # self-correction continues generating (DR-029). The transport
            # marks that take's INTERRUPTED `flushed`, which is what the
            # lifecycle handler keys on; nothing here is cancelled or cut.
            if stop_msg.flush:
                if not stop_msg.turn_id:
                    logger.error("Ignoring self-correction flush without turn scope")
                return

            # Truncation, and cancelling the turn that was cut off, only
            # happen on confirmed (non-speculative) interrupts -- a
            # speculative duck has not stopped anything yet.
            if not stop_msg.speculative:
                if self._is_brain_stop_reason(stop_msg.reason):
                    return

                if not await self._handle_ledger_audio_stop(stop_msg):
                    # No live reply is addressed: a stop for a superseded
                    # reply that is not Stage 2's command, one for a turn
                    # that never had a reply, or one with nothing active.
                    logger.debug(
                        "Ignoring audio stop for turn %s (reason=%s); no live "
                        "reply is addressed.",
                        stop_msg.turn_id,
                        stop_msg.reason,
                    )
        except Exception as e:
            logger.error(f"Error handling audio stop truncation: {e}")

    def _remember_audio_stop(self, stop_msg: AudioStop) -> bool:
        key = (
            stop_msg.utterance_id,
            stop_msg.turn_id,
            stop_msg.reason,
            stop_msg.speculative,
            stop_msg.flush,
            stop_msg.interrupt,
        )
        seen = getattr(self, "_seen_audio_stops", None)
        if seen is None:
            return True
        if key in seen:
            return False
        seen[key] = None
        while len(seen) > 256:
            seen.popitem(last=False)
        return True

    @staticmethod
    def _is_brain_stop_reason(reason: str | None) -> bool:
        return reason in {
            "confirmed_user_speech",
            "proactive_ceded",
            "proactive_grace_expired",
            "self_thought_interrupt",
        }

    async def _handle_ledger_audio_stop(self, stop_msg: AudioStop) -> bool:
        active_id = getattr(self, "_active_response_turn_id", None)
        # A confirmed stop that names no turn is addressed to the active one
        # (ADR-003 for unscoped signals). Looked up under "" it missed the
        # ledger and cut nothing.
        target = stop_msg.turn_id or active_id
        if stop_msg.turn_id is None:
            # I10: bind an unscoped stop's target once, and hold it there
            # for any later redelivery of the exact same message -- never
            # to whichever turn happens to be active when it is re-seen.
            sig = (
                stop_msg.utterance_id,
                stop_msg.reason,
                stop_msg.speculative,
                stop_msg.flush,
                stop_msg.interrupt,
            )
            remembered = self._unscoped_stop_targets.get(sig)
            if remembered is not None:
                target = remembered
            elif target is not None:
                self._unscoped_stop_targets[sig] = target
                while len(self._unscoped_stop_targets) > REPLY_RESOLUTIONS_MAX:
                    self._unscoped_stop_targets.popitem(last=False)
        if target is None:
            return False
        reply = getattr(self, "_reply_ledger", {}).get(target)
        if reply is None:
            # A reply the user's own speech already cut stays addressable:
            # Stage 2's confirmed "stop" for it lands after that cut, whatever
            # the history write is doing, and is the interruption felt. A
            # reply that completed, or never played, was not interrupted.
            done = getattr(self, "_resolved_replies", {}).get(target)
            if done is not None and done.status == "TRUNCATED":
                reply = done
        addressed = target == active_id or stop_msg.reason == "confirmed_command"
        if reply is None or not addressed:
            return False
        if target == active_id:
            await self._cancel_active_generation(
                stop_msg.reason or "confirmed audio.stop"
            )
        # Idempotent: a reply already cut or resolved is left as it is.
        await self._cut_reply(
            reply, stop_msg.reason or "confirmed audio.stop", publish=False
        )
        # One interruption is felt once. A second stop for the same reply
        # (a startle and a voice command, or one landing after the cut
        # resolved it) is not a second interruption. The user's own speech cuts
        # through the brain's stop and never gets here, so a voice command
        # after it is the first one felt.
        if reply.interruption_felt:
            return True
        reply.interruption_felt = True
        try:
            await self.cognitive_core.state.release_adrenaline(
                INTERRUPTION_ADRENALINE_SPIKE, reason="confirmed interruption"
            )
        except Exception as exc:
            logger.warning(
                "[Endocrine] Interruption adrenaline release failed: %s", exc
            )
        return True

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
        if not (full_response.strip() or not is_proactive) or self._generation_fenced():
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
        output_msg.importance = _finite_score(
            (incoming_metadata or {}).get("importance")
        )
        output_msg.category = _proactive_category(
            (incoming_metadata or {}).get("category")
        )
        output_msg.latency_metadata = incoming_latency_metadata
        # The reply's text is on its entry before the done marker goes out,
        # so a transport terminal that beats `_finish_reply` still resolves
        # it against the whole text. Synchronous: no lock wait between the
        # end of generation and the row-id write in `_finish_reply`.
        entry = self._reply_ledger.get(turn_id)
        if entry is not None and not entry.resolved:
            entry.text = full_response
        await self.publish(Topics.CHAT_OUTPUT, output_msg.model_dump())

    async def _publish_stream_error_fallback(
        self,
        error: Exception,
        *,
        full_response: str,
        turn_id: str,
        incoming_metadata: dict[str, Any] | None,
        incoming_latency_metadata: dict[str, Any] | None,
    ) -> None:
        """The done marker for `_stream_to_speech`'s exception handler, for
        a non-proactive turn only (the caller checks `is_proactive`).

        `full_response` is what reached the voice: the chunks published
        before the error, then the fallback, which the caller has already
        published tracked against this same text. It goes on the reply's
        ledger entry before the done marker, as `_publish_final_chunk_payload`
        does, so a terminal resolves the reply against what was spoken.
        """
        if self._generation_fenced():
            return
        output_msg = self.coordinator.create_chunk_payload(
            done=True,
            full_response=full_response,
            turn_id=turn_id,
            generation_error=str(error),
            user_distance=self.last_user_distance,
        )
        output_msg.metadata = incoming_metadata
        output_msg.latency_metadata = incoming_latency_metadata
        entry = self._reply_ledger.get(turn_id)
        if entry is not None and not entry.resolved:
            entry.text = full_response
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
                    entry = self._reply_ledger.get(turn_id)
                    if entry is not None:
                        entry.text = full_response

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
                    # so the reply's ledger entry always has the right
                    # intent to attribute its OutcomeRecord to.
                    try:
                        intent = ActionIntent.model_validate(output["data"])
                        async with self._turn_state_lock:
                            self._active_action_intent = intent
                            entry = self._reply_ledger.get(turn_id)
                            if entry is not None:
                                entry.intent = intent
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
                # The reply is what reached the voice: the words already
                # published, then the fallback. Unpublished words are
                # dropped. Tracked like any chunk, so the ledger, the history
                # row and the transport's offsets all index this one text.
                spoken = full_response[
                    : _char_offset_after_word(full_response, published_word_count)
                ].rstrip()
                full_response = f"{spoken} {fallback_text}" if spoken else fallback_text
                await _publish_tracked(fallback_text.split(), full_response)
                await self._publish_stream_error_fallback(
                    e,
                    full_response=full_response,
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
        if not text or self._generation_fenced():
            return
        entry = self._reply_ledger.get(turn_id) if turn_id else None
        # Started before the publish, not after: a stop that lands while the
        # chunk is on its way must cut it, not resolve an unstarted reply.
        was_started = entry.started if entry is not None else True
        if entry is not None:
            entry.started = True

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
        payload.importance = _finite_score((incoming_metadata or {}).get("importance"))
        payload.category = _proactive_category(
            (incoming_metadata or {}).get("category")
        )
        # P4-2: non-destructive merge -- `incoming_metadata` originates from
        # the user's own chat.input and must reach voice/transport unchanged;
        # this only adds two keys alongside it. voice-agent passes them
        # through unchanged on every PCM chunk it publishes for this text, and
        # transport_agent relays them as `audio.playback.progress` once that
        # PCM has actually reached the LiveKit audio source -- the closest
        # observable "reached the speaker" point in this architecture.
        # Every chunk `_stream_to_speech` publishes is tracked, the
        # exception-handler fallback included (W5); a caller without an
        # offset gets none rather than a fabricated one.
        if character_offset is not None and word_index is not None:
            metadata = dict(incoming_metadata) if incoming_metadata else {}
            metadata["character_offset"] = character_offset
            metadata["word_index"] = word_index
            payload.metadata = metadata
        else:
            payload.metadata = incoming_metadata
        payload.latency_metadata = incoming_latency_metadata
        try:
            await self.publish(Topics.CHAT_OUTPUT, payload.model_dump())
        except Exception:
            # Nothing reached the voice. Unless an earlier chunk did, or the
            # transport has reported playing it, the reply is still
            # unstarted, so the flow's end resolves it (`_end_generation`).
            if (
                entry is not None
                and not was_started
                and entry.progress is None
                and not entry.speaking
            ):
                entry.started = False
            raise

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
