"""
NATS Mesh Message Contracts — AI Friend

Typed Pydantic models for every inter-agent message on the NATS bus.
Using these at publish/subscribe boundaries catches key-rename and
type-mismatch bugs at runtime instead of silently dropping data.

Usage:
    # Publishing
    msg = ChatInput(text="hello", utterance_id="utt-1")
    await agent.publish("chat.input", msg.model_dump())

    # Receiving
    msg = ChatInput.model_validate(data)
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ProactiveCategory = Literal["useful_to_user", "self_directed"]


class Topics(str, Enum):
    CHAT_INPUT = "chat.input"
    CHAT_OUTPUT = "chat.output"
    VISION_CONTROL = "vision.control"
    VISION_FRAMES = "vision.frames"
    VISION_DESCRIPTION = "vision.description"
    VISION_FACIAL_REFLEX = "vision.facial_reflex"
    AUDIO_PERCEPTION = "audio.perception"
    AUDIO_STOP = "audio.stop"
    AUDIO_RESUME = "audio.resume"
    AUDIO_INBOUND = "audio.inbound"
    AUDIO_STREAM = "audio.stream"
    VOICE_SEGMENTATION_FEEDBACK = "voice.segmentation_feedback"
    SYSTEM_TICK = "system.tick"
    MEMORY_SURFACED = "memory.surfaced"
    STATE_UPDATE = "state.update"
    STATE_SUBCONSCIOUS = "state.subconscious"
    USER_VOICE_PROPERTIES = "user.voice.properties"
    AGENT_VOICE_MODULATION = "agent.voice.modulation"
    AUDIO_PLAYBACK_VISEMES = "audio.playback.visemes"
    AUDIO_PLAYBACK_PROGRESS = "audio.playback.progress"
    AUDIO_PLAYBACK_BACKLOG = "audio.playback.backlog"
    AUDIO_PLAYBACK_LIFECYCLE = "audio.playback.lifecycle"
    AMBIENT_NOISE_TELEMETRY = "ambient.noise.telemetry"
    # "state.presence", not "session.presence": AI_MESSAGES' JetStream
    # subject pattern is state.>, and a subject outside every declared
    # pattern needs a check_subject_wiring.py allowlist entry to justify why
    # (see ambient.noise.telemetry's) -- fitting the existing wildcard needs
    # no such justification, and "who is connected" is state as much as
    # state.broadcast's affect snapshot is.
    SESSION_PRESENCE = "state.presence"


# 1E (§14 MODIFY / §2 Infrastructure): explicit delivery semantics per
# subject, so "audio is lossy, cognition is durable" is a declared fact
# rather than something inferred from which stream tier a subject's pattern
# happens to fall into, or which client library a process uses to consume
# it -- HUMANOID_ARCHITECTURE_RESEARCH.md §2 names both of those as exactly
# the wrong place to infer this from.
#
# Default derivation: every subject under the AI_AUDIO stream's `audio.>`
# pattern (nats_streams.CORE_STREAMS) is memory-backed with a minutes-scale
# retention (nats_streams.STREAM_POLICIES) -- an intentional, documented
# trade-off -- so it defaults to "best_effort". Every other declared subject
# sits on the file-backed, week-scale AI_MESSAGES stream, so it defaults to
# "durable". Two subjects override that default, both for reasons already
# established in check_subject_wiring.py's ALLOWLIST:
#
#   - AGENT_VOICE_MODULATION technically matches AI_MESSAGES' `agent.>`
#     pattern, but its only real consumer is the frontend voice UI over a
#     lossy WebRTC data channel, not durable cognition -- expressive
#     control data, not something a restart should replay.
#   - AMBIENT_NOISE_TELEMETRY matches no declared stream pattern at all (a
#     known gap tracked there); its only consumer (voice-agent) reads it
#     over core NATS as live telemetry, so it is classified by that actual
#     behavior rather than left unclassified.
#
# `scripts/diagnostics/check_delivery_semantics.py` re-derives the
# stream-tier default from `nats_streams.py` and fails if a subject here
# disagrees with it without being one of the two named overrides above --
# catching drift (a subject moved between streams, or a new one added here
# without its tier being reasoned about) going forward.
TOPIC_DELIVERY: dict[Topics, Literal["durable", "best_effort"]] = {
    Topics.CHAT_INPUT: "durable",
    Topics.CHAT_OUTPUT: "durable",
    Topics.VISION_CONTROL: "durable",
    Topics.VISION_FRAMES: "durable",
    Topics.VISION_DESCRIPTION: "durable",
    Topics.VISION_FACIAL_REFLEX: "durable",
    Topics.AUDIO_PERCEPTION: "best_effort",
    Topics.AUDIO_STOP: "best_effort",
    Topics.AUDIO_RESUME: "best_effort",
    Topics.AUDIO_INBOUND: "best_effort",
    Topics.AUDIO_STREAM: "best_effort",
    Topics.VOICE_SEGMENTATION_FEEDBACK: "durable",
    Topics.SYSTEM_TICK: "durable",
    Topics.MEMORY_SURFACED: "durable",
    Topics.STATE_UPDATE: "durable",
    Topics.STATE_SUBCONSCIOUS: "durable",
    Topics.USER_VOICE_PROPERTIES: "durable",
    Topics.AGENT_VOICE_MODULATION: "best_effort",  # override: frontend-consumed, see above
    Topics.AUDIO_PLAYBACK_VISEMES: "best_effort",
    Topics.AUDIO_PLAYBACK_PROGRESS: "best_effort",
    Topics.AUDIO_PLAYBACK_BACKLOG: "best_effort",
    Topics.AUDIO_PLAYBACK_LIFECYCLE: "best_effort",
    Topics.AMBIENT_NOISE_TELEMETRY: "best_effort",  # override: no declared stream, see above
    Topics.SESSION_PRESENCE: "durable",
}


# ─── chat.input ──────────────────────────────────────────────
class ChatInputMetadata(BaseModel):
    model_config = {"extra": "allow"}

    source: str = "whisper"
    confidence: float = 0.9
    utterance_id: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    category: ProactiveCategory | None = None
    goal_id: str | None = None


class ChatInput(BaseModel):
    """Published by STTAgent on `chat.input` after final transcription."""

    model_config = {"extra": "allow"}

    text: str
    utterance_id: str | None = None
    turn_id: str | None = None
    metadata: ChatInputMetadata = Field(default_factory=ChatInputMetadata)
    latency_metadata: dict[str, Any] | None = None


# ─── chat.output ─────────────────────────────────────────────
class ChatOutputAffect(BaseModel):
    """PAD and Relational affect vector attached to each speech chunk."""

    valence: float = 0.0
    arousal: float = 0.5
    dominance: float = 0.5
    trust: float = 0.5
    attachment: float = 0.1
    emotion: str = "neutral"
    fatigue: float = 0.0
    user_distance: float | None = None


class SpeechExpressionWire(BaseModel):
    """Wire mirror of the in-process cognitive SpeechExpression.

    BrainAgent derives this from state for speech output. It is optional for
    producers using the legacy inline-tag path. Extra fields allow producers
    and consumers to upgrade independently.
    """

    model_config = {"extra": "allow"}

    affect_label: str | None = None
    breath: float = 0.0
    hesitation: float = 0.0
    style: str = "neutral"
    # Preserves generate_apra_trajectory's native (time_offset_ms, rate, pitch, volume)
    # frame tuples verbatim for prosody realization.
    trajectory: list[tuple[int, float, float, float]] = Field(default_factory=list)


class ChatOutput(BaseModel):
    """Published by BrainAgent on `chat.output` for each speech chunk or done signal."""

    model_config = {"extra": "allow"}

    content: str | None = None
    done: bool = False
    turn_id: str | None = None
    affect: ChatOutputAffect | None = None
    expression: SpeechExpressionWire | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    category: ProactiveCategory | None = None

    # The deprecated prosody block (confidence, intensity, speaking_rate,
    # pause_bias, paralinguistic_tags) was removed here. Prosody has a single
    # source: the voice agent derives it from `affect` above via
    # `contracts::vad_to_prosody` (Rust). Python used to populate these with a
    # formula that disagreed with the Rust one, and nothing ever read them.
    #
    # Safe to remove in both rollout directions, which is why they went rather
    # than staying deprecated forever: this model is `extra: "allow"`, so a
    # message from an older producer still carrying them validates; and the
    # Rust struct sets `#[serde(default)]` on every field with no
    # `deny_unknown_fields`, so a new message missing them deserializes there
    # too. No deploy ordering is required.

    # Metadata
    timestamp: float = Field(default_factory=time.time)
    full_response: str | None = None
    generation_error: str | None = None
    proactive: bool = False
    metadata: dict[str, Any] | None = None
    latency_metadata: dict[str, Any] | None = None


# ─── audio.perception ────────────────────────────────────────
class SpeculativeIntent(BaseModel):
    name: str = "SPECULATIVE_STOP"
    keywords: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    text: str = ""
    timestamp: float = 0.0
    utterance_id: str | None = None


class AudioPerception(BaseModel):
    """Published by STTAgent on `audio.perception` from the SenseVoice fast path."""

    text: str = ""
    intent: str | None = None
    intent_type: str = "CONVERSATIONAL"  # COMMAND, PERCEPTION, CONVERSATIONAL
    keywords: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    snr: float = 0.0  # Signal-to-Noise Ratio
    paralinguistic_events: list[str] = Field(
        default_factory=list
    )  # [laughter], [cough], etc.
    speculative_intent: SpeculativeIntent | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0
    utterance_id: str | None = None


# ─── audio.stop / audio.resume ───────────────────────────────
class AudioStop(BaseModel):
    """Published on `audio.stop` to halt voice playback."""

    interrupt: bool = True
    # Flush already-rendered audio without cancelling the generation that is
    # producing its replacement (self-correction, DR-029).
    flush: bool = False
    speculative: bool = False
    reason: str | None = None
    command_text: str | None = None
    intent: str | None = None
    intent_type: str = (
        "VOICE_INTERRUPTION"  # VOICE_INTERRUPTION, VISION_INTERRUPTION, SYSTEM_HALT
    )
    keywords: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    perception_text: str | None = None
    utterance_id: str | None = None
    turn_id: str | None = None


class AudioResume(BaseModel):
    """Published on `audio.resume` to resume voice playback after rejected stop."""

    reason: str = "conflict_rejected"
    perception_text: str | None = None
    utterance_id: str | None = None
    # Turn-scoped symmetrically with AudioStop.turn_id to ensure resumes target
    # the exact active utterance context.
    turn_id: str | None = None


# ─── memory.surfaced ─────────────────────────────────────────
class MemoryScope(BaseModel):
    """Hierarchical scope for memory items (Wings -> Rooms -> Drawers)."""

    wing: str = "personal"
    room: str | None = None
    drawer_id: str | None = None


class SurfacedMemory(BaseModel):
    content: str
    raw_content: str  # Verbatim Truth
    scope: MemoryScope = Field(default_factory=MemoryScope)
    score: float = 0.0
    valence: float = 0.0
    created_at: str | None = None
    recall_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySurfaced(BaseModel):
    """Published by SurfacingAgent on `memory.surfaced`."""

    memories: list[SurfacedMemory] = Field(default_factory=list)
    source: str = "episodic"
    provenance: str = "pgvector_actr"  # cognitive source of the memory
    context: str | None = None


# ─── vision.description ──────────────────────────────────────
class VisionDescription(BaseModel):
    """Published by VisionAgent on `vision.description` after VLM appraisal."""

    description: str
    source: str = "screen"
    timestamp: float = Field(default_factory=time.time)
    # Estimated distance in metres via a calibrated pinhole-camera formula
    # (see Config.VISION_FOCAL_PX / _calculate_user_distance in
    # vision/agent.py); falls back to 1.0 when no face is detected or
    # calibration inputs are unavailable.
    user_distance: float | None = None
    # P3-1: whether this frame cleared VisualAppraisalService's own
    # habituation delta (reused, not recomputed) rather than being a cached
    # repeat. Downstream salience gating for visual episodic memory
    # (SubconsciousAgent) uses this so a static scene doesn't mint a new
    # memory every VLM_APPRAISAL_INTERVAL. Defaults True so a producer that
    # predates this field (or genuinely can't tell) does not silently
    # suppress storage.
    is_novel: bool = True


# ─── vision.facial_reflex ─────────────────────────────────────
class FacialReflexEvent(BaseModel):
    """Published on `vision.facial_reflex` by the CPU-only reflex channel
    (Bucket 13, voice remediation Phase 3) -- the continuous counterpart to
    `VisionDescription` above. That is a slow (VLM_APPRAISAL_INTERVAL, 5s),
    suspended-during-the-turn semantic poll; this samples facial expression
    continuously, including while the agent is speaking, and exists
    specifically to catch what the slow poll architecturally cannot: a smile
    or a flinch mid-sentence. See `app/vision/reflex.py::score_blendshapes`
    for how a raw MediaPipe blendshape frame becomes one of these.

    Deltas are signed and small by design -- this can fire many times in a
    single conversation (once per refractory-gated expression onset), unlike
    `VisionDescription`'s comfort-object recognition which is comparatively
    rare, so each event's effect on affect must be a nudge, not a spike.
    """

    name: str  # e.g. "smile", "brow_furrow", "startle" -- see reflex.py
    valence_delta: float = 0.0
    arousal_delta: float = 0.0
    dopamine_spike: float = 0.0
    evidence: str = ""  # human-readable, e.g. "smile=0.87" -- for logs, not policy
    timestamp: float = Field(default_factory=time.time)
    source: str = "camera"


# ─── state.update ────────────────────────────────────────────
class StateUpdate(BaseModel):
    """The affect broadcast published on `state.update` when agent state changes.

    This is the single definition of that payload. `CognitivePipeline` builds it
    with `from_snapshot` at its two publish sites, and `SurfacingAgent` reads the
    same fields off the wire to drive mood-congruent recall and APRA vocal
    modulation. The subject also carries a separate lifecycle message from
    `BaseAgent.set_state` (`{agent, state, timestamp}` — "thinking"/"idle"); that
    one is deliberately *not* modelled here, and the consumer tolerates it because
    it reads affect fields with defaults rather than validating a fixed shape.

    Before this was wired up the payload lived as an 11-field dict literal
    duplicated across both pipeline sites, and this model shadowed it unused —
    two definitions of one wire contract, free to drift apart silently.
    """

    model_config = {"extra": "allow"}

    mood: float = 0.0
    energy: float = 0.5
    dominance: float = 0.5
    trust: float = 0.5
    attachment: float = 0.1
    emotion: str = "neutral"
    interaction_count: int = 0
    cortisol: float = 0.0
    dopamine: float = 0.0
    adrenaline: float = 0.0
    fatigue: float = 0.0
    user_mental_model: dict[str, Any] | None = None
    # Informational revision and writer identifier carried over state.update for
    # end-to-end tracing across background agents.
    revision: int = 0
    writer_id: str = ""

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> StateUpdate:
        """Build the broadcast from a `StateService.get_context_snapshot()` dict.

        Only the modelled fields are pulled; a key the snapshot omits falls back
        to this model's default, so the defaults live here and nowhere else.
        Snapshot keys outside the schema (`valence`, `arousal`, …) are dropped,
        exactly as the previous hand-written literal dropped them.
        """
        return cls(**{k: snapshot[k] for k in cls.model_fields if k in snapshot})


# ─── user.voice.properties ───────────────────────────────────
class UserVoiceProperties(BaseModel):
    model_config = {"extra": "allow"}

    pitch_f0: float
    energy_rms: float
    # docs/FUTURE_WORK.md §1.2: None until the first utterance completes and
    # a real words-over-duration rate exists -- see the Rust struct's own
    # comment (crates/contracts/src/lib.rs) for why this changed on both
    # sides of the wire together rather than just here.
    tempo_wpm: float | None = None
    timestamp: float = Field(default_factory=time.time)


# ─── agent.voice.modulation ───────────────────────────────────
class ProsodyFrame(BaseModel):
    model_config = {"extra": "allow"}

    time_offset_ms: int = Field(..., ge=0)
    rate: float
    pitch: float
    volume: float


class AgentVoiceModulation(BaseModel):
    model_config = {"extra": "allow"}

    # A7: the reference generator (generate_apra_trajectory, cognitive-rust)
    # steps in exactly 50ms increments, but the contract only needs frames close
    # enough together for smooth playback - not that exact grid. A hard "==50"
    # check made any jitter or resampling (e.g. a future non-Rust producer, or a
    # frame dropped/merged in transit) reject the *entire* trajectory.
    MAX_FRAME_GAP_MS: ClassVar[int] = 250

    trajectory: list[ProsodyFrame] = Field(..., min_length=1)
    timestamp: float = Field(default_factory=time.time)

    @field_validator("trajectory")
    @classmethod
    def validate_trajectory(cls, v: list[ProsodyFrame]) -> list[ProsodyFrame]:
        if not v:
            raise ValueError("Trajectory must not be empty.")
        if v[0].time_offset_ms < 0:
            raise ValueError("First offset must be >= 0.")
        for i in range(1, len(v)):
            gap = v[i].time_offset_ms - v[i - 1].time_offset_ms
            if gap <= 0:
                raise ValueError(
                    "Trajectory must be strictly ordered by time_offset_ms ascending."
                )
            if gap > AgentVoiceModulation.MAX_FRAME_GAP_MS:
                raise ValueError(
                    "Consecutive frames must be no more than "
                    f"{AgentVoiceModulation.MAX_FRAME_GAP_MS} ms apart "
                    f"(got {gap} ms)."
                )
        return v


# ─── audio.playback.visemes ───────────────────────────────────
class PlaybackVisemes(BaseModel):
    model_config = {"extra": "allow"}

    target_level: float
    viseme_id: str
    timestamp: float = Field(default_factory=time.time)


# ─── audio.playback.progress ───────────────────────────────────
class AudioPlaybackProgress(BaseModel):
    model_config = {"extra": "allow"}

    utterance_id: str
    character_offset: int = Field(..., ge=0)
    word_index: int = Field(..., ge=0)
    completed: bool
    timestamp: float = Field(default_factory=time.time)


class AudioPlaybackLifecycle(BaseModel):
    """Transport-owned, ordered state for one spoken reply."""

    model_config = {"extra": "allow"}

    utterance_id: str
    turn_id: str
    seq: int = Field(..., ge=0)
    state: Literal["STARTED", "PLAYING", "COMPLETED", "INTERRUPTED", "FAILED"]
    words_played: int = Field(..., ge=0)
    words_streamed: int = Field(..., ge=0)
    heard_offset: int = Field(..., ge=0)
    streamed_offset: int = Field(..., ge=0)
    # INTERRUPTED by a self-correction flush (DR-029): the take was rejected
    # and a retry of the same turn follows, so this is not the reply's end.
    flushed: bool = False
    timestamp: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def heard_position_is_streamed(self):
        if self.words_played > self.words_streamed:
            raise ValueError("words_played cannot exceed words_streamed")
        if self.heard_offset > self.streamed_offset:
            raise ValueError("heard_offset cannot exceed streamed_offset")
        return self


class AudioStreamTrailer(BaseModel):
    """Typed, non-PCM end marker for one voice-agent output stream."""

    kind: Literal["END_OF_STREAM"]
    utterance_id: str
    turn_id: str
    failed: bool = False


class LifecycleApplyResult(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    PROTOCOL_ERROR = "protocol_error"


class PlaybackLifecycleTracker:
    """Small deterministic reducer at the lifecycle consumer.

    The topic is best effort, so any event can be the first one seen: a lost
    STARTED must not turn the reply's COMPLETED away. `seq` orders what does
    arrive. One entry per utterance, oldest dropped past `max_entries`, so a
    long-running brain does not keep every reply it ever spoke.
    """

    _TERMINAL = {"COMPLETED", "INTERRUPTED", "FAILED"}

    def __init__(self, max_entries: int = 1024) -> None:
        self._events: dict[tuple[str, str], AudioPlaybackLifecycle] = {}
        self._max_entries = max_entries
        self.protocol_errors = 0

    def apply(self, event: AudioPlaybackLifecycle) -> LifecycleApplyResult:
        key = (event.utterance_id, event.turn_id)
        previous = self._events.get(key)
        if previous is None:
            self._events[key] = event
            while len(self._events) > self._max_entries:
                self._events.pop(next(iter(self._events)))
            return LifecycleApplyResult.APPLIED

        if previous.state in self._TERMINAL:
            if event.state == previous.state:
                return LifecycleApplyResult.DUPLICATE
            if event.state in self._TERMINAL:
                self.protocol_errors += 1
                return LifecycleApplyResult.PROTOCOL_ERROR
            return LifecycleApplyResult.OUT_OF_ORDER

        if event.seq < previous.seq:
            return LifecycleApplyResult.OUT_OF_ORDER
        if event.seq == previous.seq:
            if event.state == previous.state:
                return LifecycleApplyResult.DUPLICATE
            # One seq, two states: the producer broke its own ordering.
            self.protocol_errors += 1
            return LifecycleApplyResult.PROTOCOL_ERROR
        if event.state == "STARTED":
            return LifecycleApplyResult.OUT_OF_ORDER
        self._events[key] = event
        return LifecycleApplyResult.APPLIED

    def get(self, utterance_id: str, turn_id: str) -> AudioPlaybackLifecycle | None:
        return self._events.get((utterance_id, turn_id))


# ─── audio.playback.backlog ───────────────────────────────────
class AudioPlaybackBacklog(BaseModel):
    """Bucket 3 (VOICE_REMEDIATION_PLAN.md): the outbound PCM queue depth
    `transport_agent` already tracks internally (`self.audio_queue.qsize()`,
    the same number `MEASURE_TRACE`'s buffer2_to_3 event logs) -- surfaced
    over the mesh so `ConversationalRuntime` can suppress a new filler while
    a previous turn's audio is still backed up, instead of talking over it."""

    model_config = {"extra": "allow"}

    queue_depth: int = Field(..., ge=0)
    capacity: int = Field(..., ge=0)
    timestamp: float = Field(default_factory=time.time)


# ─── ambient.noise.telemetry ───────────────────────────────────
class AmbientNoiseTelemetry(BaseModel):
    model_config = {"extra": "allow"}

    rms_energy: float
    noise_floor_db: float
    timestamp: float = Field(default_factory=time.time)


# ─── session.presence ───────────────────────────────────────
class SessionPresence(BaseModel):
    """Published by `transport_agent` on every LiveKit room join/leave edge
    (Phase 3.1) -- the only component with direct visibility into who is
    actually connected. `subconscious_agent` runs as a separate process and
    has no other way to know: without this, a proactive thought generated
    while nobody was listening had nowhere to go but a synthesized-and-
    discarded utterance (see `app/state/proactive_queue.py`)."""

    model_config = {"extra": "allow"}

    connected: bool
    participant_count: int = 0
    timestamp: float = Field(default_factory=time.time)
