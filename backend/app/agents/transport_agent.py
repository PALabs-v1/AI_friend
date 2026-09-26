import asyncio
import base64
import json
import logging
import time
from typing import Any

from livekit import rtc
from livekit.api import AccessToken, VideoGrants

from ..config import Config
from ..contracts import (
    AudioPlaybackBacklog,
    AudioPlaybackLifecycle,
    AudioPlaybackProgress,
    AudioStreamTrailer,
    PlaybackVisemes,
    SessionPresence,
    Topics,
)
from ..measure_trace import trace as _measure_trace
from .base import BaseAgent, install_shutdown_signal_handlers

logger = logging.getLogger("transport_agent")

# Bucket 3 (VOICE_REMEDIATION_PLAN.md): cadence for AudioPlaybackBacklog
# telemetry. `VOICE_FILLER_MIN_INTERVAL_SECONDS` (1.5s) is the coarsest
# consumer of this signal, so anything well under that is fresh enough.
BACKLOG_TELEMETRY_INTERVAL_S = 0.2

# Same bound as the brain's PlaybackLifecycleTracker (contracts.py).
LIFECYCLE_MAX_ENTRIES = 1024


def _cap(mapping: dict) -> None:
    """Evict insertion-order-oldest entries until `mapping` fits the cap."""
    while len(mapping) > LIFECYCLE_MAX_ENTRIES:
        del mapping[next(iter(mapping))]


class TransportAgent(BaseAgent):
    """
    Bridges NATS Events to WebRTC Tracks (LiveKit).
    Inherits from BaseAgent for consistent mesh connectivity.
    """

    def __init__(
        self,
        nats_url: str = Config.NATS_URL,
        lk_url: str = Config.LIVEKIT_URL,
        lk_api_key: str = Config.LIVEKIT_API_KEY,
        lk_api_secret: str = Config.LIVEKIT_API_SECRET,
    ):
        super().__init__(name="transport_agent", nats_url=nats_url)
        self.lk_url = lk_url
        self.lk_api_key = lk_api_key
        self.lk_api_secret = lk_api_secret
        self.output_sample_rate = Config.SAMPLE_RATE
        self.output_channels = 1

        self.room = rtc.Room()

        # Match the voice agent's PCM output contract.
        self.audio_source = rtc.AudioSource(
            self.output_sample_rate,
            self.output_channels,
        )
        self.audio_track = rtc.LocalAudioTrack.create_audio_track(
            "ai-voice", self.audio_source
        )
        self.audio_queue: asyncio.Queue[
            tuple[
                bytes,
                int,
                int,
                str | None,
                str | None,
                int | None,
                int | None,
                str | None,
            ]
        ] = asyncio.Queue(
            maxsize=max(32, int(getattr(Config, "TRANSPORT_AUDIO_QUEUE_SIZE", 256)))
        )
        self.audio_worker_task = None
        self.dropped_audio_frames = 0
        # Bucket 3 (VOICE_REMEDIATION_PLAN.md): throttle state for publishing
        # this queue's depth so `ConversationalRuntime` can see it (see
        # `AudioPlaybackBacklog`) without flooding NATS -- `_on_nats_audio`
        # fires once per PCM frame, far more often than a filler decision
        # needs fresh data for.
        self._last_backlog_publish_at: float = 0.0
        # Set for real once `start()` publishes the initial track; needed to
        # unpublish it by sid when P1-3's flush rotates to a fresh one.
        self.audio_publication: rtc.LocalTrackPublication | None = None
        # P1-3: which turn's PCM is currently flowing through audio.stream,
        # read off the X-Latency-Meta header voice-agent now stamps with
        # `event.turn_id` (see `build_latency_metadata` in voice-agent). Mirrors
        # voice-agent's own `stop_applies_to_active_turn`: an unscoped stop
        # (turn_id=None) always applies; a named one must match, so a stop
        # delayed in the mesh for a turn that has already finished cannot flush
        # audio for the turn speaking now.
        self._active_turn_id = None
        # P4-2: this process is the closest observable "reached the speaker"
        # point in this architecture -- there is no frontend PCM player to
        # publish playback progress from (the browser side plays a LiveKit
        # WebRTC audio track via `track.attach()`, opaque to application
        # code; there is nothing there to instrument). `character_offset`/
        # `word_index` arrive pass-through in the same X-Latency-Meta header
        # `turn_id` already does (brain_agent stamps them, voice-agent
        # forwards them unchanged). Tracks the last value actually published
        # so a turn's PCM chunks -- several per text chunk, all carrying the
        # same offset -- don't republish identical progress repeatedly, and
        # resets whenever `turn_id` changes so a new turn cannot inherit a
        # stale offset from the previous one.
        self._last_progress_turn_id = None
        self._last_progress_offset = -1
        self._active_utterance_id: str | None = None
        self._active_utterance_turn_id: str | None = None
        # Every lifecycle map is keyed per turn or per (utterance, turn) and
        # capped at LIFECYCLE_MAX_ENTRIES, oldest first: a reply that never
        # reaches a terminal (lost trailer, dropped stop) must not grow this
        # process forever. Per-key progress state is dropped at the terminal;
        # the terminal itself is kept (bounded) so late events stay rejected.
        self._lifecycle_active_by_turn: dict[str, str] = {}
        self._lifecycle_attempts: dict[str, int] = {}
        self._lifecycle_seq: dict[tuple[str, str], int] = {}
        self._lifecycle_terminal: dict[tuple[str, str], str] = {}
        self._lifecycle_started: dict[tuple[str, str], None] = {}
        self._lifecycle_position: dict[tuple[str, str], tuple[int, int]] = {}
        self._lifecycle_dropped: dict[tuple[str, str], None] = {}
        self._lifecycle_publish_lock = asyncio.Lock()
        self.lifecycle_protocol_errors = 0

        # #173: mirrors the outbound queue above, for the opposite direction.
        # `_process_remote_audio` used to `await self.publish(...)` directly
        # inside LiveKit's `AudioStream` iteration loop - if NATS publishing
        # stalls (network delay, JetStream backpressure), that await stalls
        # right there, delaying every subsequent frame the WebRTC stack hands
        # over. Decoupling capture from publish with a bounded queue and a
        # dedicated worker means a slow NATS publish drops frames instead of
        # stalling audio capture.
        self.inbound_audio_queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = (
            asyncio.Queue(
                maxsize=max(32, int(getattr(Config, "TRANSPORT_AUDIO_QUEUE_SIZE", 256)))
            )
        )
        self.inbound_audio_worker_task = None
        self.dropped_inbound_audio_frames = 0

    def _trace(self, event: str, **fields) -> None:
        """Stage 3 (audit/ROADMAP.md measurement 1.1, M3-R1): one structured
        log line per buffer-seam crossing along the outbound audio path,
        parsed out of container logs by backend/tools/measure/m11_bargein.py.
        See measure_trace.trace for why this is a log line, not a subject.
        """
        _measure_trace(self.name, event, **fields)

    async def _connect_livekit_with_retry(self, token: str):
        """Connect to LiveKit with bounded retries for transient SFU startup gaps."""
        max_attempts = 8
        for attempt in range(1, max_attempts + 1):
            try:
                await self.room.connect(self.lk_url, token)
                logger.info("Connected to LiveKit Room: ai-friend-room")
                return
            except Exception as e:
                if attempt == max_attempts:
                    raise

                delay = min(10.0, 1.5 * attempt)
                logger.warning(
                    "LiveKit connection failed (attempt %s/%s): %s. Retrying in %.1fs",
                    attempt,
                    max_attempts,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)

    async def start(self):
        """Initialize NATS and connect to LiveKit"""
        await self.connect()

        # 1. Connect to LiveKit Room
        token = (
            AccessToken(self.lk_api_key, self.lk_api_secret)
            .with_identity("transport-agent")
            .with_name("AI Bridge")
            .with_grants(VideoGrants(room_join=True, room="ai-friend-room"))
            .to_jwt()
        )

        await self._connect_livekit_with_retry(token)

        # 2. Publish Audio Track
        publication = await self.room.local_participant.publish_track(self.audio_track)
        self.audio_publication = publication
        logger.info(f"Published Audio Track: {publication.sid}")

        # Capture frames on a dedicated worker so NATS callback can return fast.
        self.audio_worker_task = asyncio.create_task(self._audio_playback_worker())
        # #173: drains inbound WebRTC frames independently of NATS publish speed.
        self.inbound_audio_worker_task = asyncio.create_task(
            self._inbound_audio_worker()
        )

        # 3. Subscribe to NATS Audio Stream (Outbound - AI Speech)
        await self.subscribe(
            "audio.stream",
            callback=self._on_nats_audio,
            durable=f"{self.name}_audio_stream_live",
            deliver_policy="new",
            pending_msgs_limit=200000,
            pending_bytes_limit=268435456,
        )
        logger.info("Subscribed to NATS audio.stream. Outbound Bridge Active.")

        # P1-3 (audit/ROADMAP.md): flush buffers 2->4 on a confirmed barge-in.
        await self.subscribe(
            "audio.stop",
            callback=self._on_audio_stop,
            durable=f"{self.name}_audio_stop_live",
            deliver_policy="new",
        )
        logger.info("Subscribed to NATS audio.stop. Barge-in flush active.")

        # Phase 5.3: bridge voice-agent's viseme stream onto the room's data
        # channel. `check_subject_wiring.py`'s whitelist has documented
        # `audio.playback.visemes` as "consumed by the frontend voice UI"
        # since the wiring audit, but nothing ever actually delivered it
        # there -- this is that delivery, not new data.
        await self.subscribe(
            Topics.AUDIO_PLAYBACK_VISEMES,
            callback=self._on_viseme,
            durable=f"{self.name}_playback_visemes_live",
            deliver_policy="new",
        )
        logger.info("Subscribed to NATS audio.playback.visemes. Viseme bridge active.")

        # 4. Listen for Remote Tracks (Inbound - User Speech)
        self.room.on("track_subscribed", self._on_track_subscribed)
        logger.info("Listening for remote tracks (Inbound Bridge enabled).")

        # 5. Phase 3.1: this process is the only one with direct visibility
        # into who is actually in the room -- subconscious_agent (a separate
        # process) needs that to know whether a proactive thought has anyone
        # to reach. Edge-triggered (0<->1+ participants), not a signal on
        # every join/leave of an Nth participant, since only "is anyone here
        # at all" matters for that decision.
        self.room.on("participant_connected", self._on_participant_connected)
        self.room.on("participant_disconnected", self._on_participant_disconnected)

    def _on_participant_connected(self, participant: rtc.RemoteParticipant) -> None:
        # LiveKit has already added `participant` to `remote_participants` by
        # the time this callback fires, so "was the room empty before this
        # one joined" is exactly count == 1, not 0 -- checking against 0 here
        # would always be false and this edge would never fire.
        if len(self.room.remote_participants) == 1:
            self._publish_presence(connected=True)

    def _on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        # Mirror of the above: LiveKit has already removed the leaving
        # participant, so "is the room now empty" is exactly count == 0.
        if len(self.room.remote_participants) == 0:
            self._publish_presence(connected=False)

    def _publish_presence(self, *, connected: bool) -> None:
        presence = SessionPresence(
            connected=connected,
            participant_count=len(self.room.remote_participants),
        )
        self.spawn(self.publish(Topics.SESSION_PRESENCE, presence.model_dump()))
        logger.info(
            "[Transport] Room presence changed: connected=%s participants=%d",
            connected,
            presence.participant_count,
        )

    def _on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        if track.kind == rtc.TrackKind.KIND_AUDIO and isinstance(
            track, rtc.RemoteAudioTrack
        ):
            logger.info(
                f"Subscribed to remote audio track: {track.sid} from {participant.identity}"
            )
            self.spawn(self._process_remote_audio(track))

    async def _process_remote_audio(self, track: rtc.RemoteAudioTrack):
        """Convert WebRTC audio frames to NATS events for STT.

        #173: enqueues rather than publishing inline, so a slow NATS publish
        never stalls draining `audio_stream` - only the queue backs up, and
        overflow drops the oldest frame (matching `_on_nats_audio`'s policy)
        rather than blocking capture.
        """
        audio_stream = rtc.AudioStream(track)
        async for event in audio_stream:
            frame = event.frame
            audio_data = bytes(frame.data)
            metadata = {
                "sample_rate": frame.sample_rate,
                "channels": frame.num_channels,
                "participant": track.sid,
                "captured_at": time.time(),
            }
            try:
                self.inbound_audio_queue.put_nowait((audio_data, metadata))
            except asyncio.QueueFull:
                try:
                    _ = self.inbound_audio_queue.get_nowait()
                    self.inbound_audio_queue.task_done()
                except asyncio.QueueEmpty:
                    pass

                try:
                    self.inbound_audio_queue.put_nowait((audio_data, metadata))
                except asyncio.QueueFull:
                    pass

                self.dropped_inbound_audio_frames += 1
                if self.dropped_inbound_audio_frames % 50 == 1:
                    logger.warning(
                        "Inbound transport audio queue overloaded; dropped %s frames.",
                        self.dropped_inbound_audio_frames,
                    )

    async def _inbound_audio_worker(self):
        """Drain queued inbound frames and publish to NATS at publish pace."""
        while True:
            try:
                audio_data, metadata = await self.inbound_audio_queue.get()
                try:
                    # Publish raw PCM to the binary mesh path. STT still
                    # accepts the legacy JSON/base64 shape for compatibility.
                    await self.publish("audio.inbound", audio_data, metadata=metadata)
                finally:
                    self.inbound_audio_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Inbound transport audio worker error: {e}")
                await asyncio.sleep(0.01)

    async def _on_nats_audio(self, data, metadata: dict | None = None):
        """Convert NATS audio events to WebRTC frames for User."""
        try:
            # P1-3: track which turn is currently flowing so a later
            # audio.stop can tell whether it names this turn or a stale one.
            turn_id = metadata.get("turn_id") if metadata else None
            utterance_id = (metadata or {}).get("utterance_id") or turn_id
            if turn_id:
                self._active_turn_id = turn_id
            # P4-2: pass-through values (brain_agent computes them, voice-agent
            # forwards them) carried in the same header `turn_id` above comes
            # from. `None` when absent -- e.g. the exception-fallback chunk
            # brain_agent deliberately omits them for.
            character_offset = metadata.get("character_offset") if metadata else None
            word_index = metadata.get("word_index") if metadata else None

            if metadata and metadata.get("audio_stream_kind") == "trailer":
                await self._enqueue_stream_trailer(data, character_offset, word_index)
                return

            decoded = self._decode_audio_payload(data)
            if decoded is None:
                return
            pcm_data, sample_rate, num_channels = decoded
            if pcm_data:
                self._enqueue_pcm(
                    pcm_data,
                    sample_rate,
                    num_channels,
                    self._lifecycle_utterance_for(turn_id, utterance_id),
                    turn_id,
                    character_offset,
                    word_index,
                )
        except Exception as e:
            logger.error(f"Error bridging audio: {e}")

    async def _enqueue_stream_trailer(self, data, character_offset, word_index) -> None:
        """Queue the voice agent's end-of-stream marker behind the reply's PCM."""
        try:
            trailer = AudioStreamTrailer.model_validate(json.loads(bytes(data)))
        except Exception as exc:
            logger.error("Dropping invalid audio stream trailer: %s", exc)
            return
        turn_id = trailer.turn_id
        utterance_id = self._lifecycle_active_by_turn.get(turn_id)
        if utterance_id is None:
            return
        marker = (
            b"",
            self.output_sample_rate,
            self.output_channels,
            utterance_id,
            turn_id,
            character_offset if character_offset is not None else 0,
            word_index if word_index is not None else 0,
            "FAILED" if trailer.failed else "COMPLETED",
        )
        # Unlike media frames, losing this marker would leave a fully
        # played reply unresolved. Let the bounded queue drain first.
        await self.audio_queue.put(marker)

    def _decode_audio_payload(self, data) -> tuple[bytes, int, int] | None:
        """PCM bytes (WAV header stripped), sample rate and channel count, or
        None for a payload type this bridge does not carry."""
        audio_bytes = b""
        sample_rate = self.output_sample_rate
        num_channels = self.output_channels
        if isinstance(data, (bytes, bytearray)):
            audio_bytes = bytes(data)
        elif isinstance(data, dict):
            audio_b64 = data.get("audio", "")
            sample_rate = data.get("sample_rate", sample_rate)
            num_channels = data.get("channels", num_channels)
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
        else:
            logger.warning(
                "Unsupported audio payload type from NATS: %s",
                type(data).__name__,
            )
            return None
        # Strip WAV header if present (44 bytes)
        if audio_bytes.startswith(b"RIFF"):
            audio_bytes = audio_bytes[44:]
        return audio_bytes, sample_rate, num_channels

    def _lifecycle_utterance_for(self, turn_id, utterance_id):
        """The utterance this turn's PCM belongs to. The first take keeps the
        voice agent's id; each take after a flush gets `<id>:<attempt>`."""
        if not turn_id:
            return utterance_id
        active_utterance = self._lifecycle_active_by_turn.get(turn_id)
        if active_utterance is None:
            attempt = self._lifecycle_attempts.get(turn_id, 0)
            self._lifecycle_attempts[turn_id] = attempt + 1
            _cap(self._lifecycle_attempts)
            active_utterance = (
                utterance_id if attempt == 0 else f"{utterance_id or turn_id}:{attempt}"
            )
            self._lifecycle_active_by_turn[turn_id] = active_utterance
            _cap(self._lifecycle_active_by_turn)
        return active_utterance

    def _enqueue_pcm(
        self,
        pcm_data,
        sample_rate,
        num_channels,
        utterance_id,
        turn_id,
        character_offset,
        word_index,
    ) -> None:
        queued_frame = (
            pcm_data,
            sample_rate,
            num_channels,
            utterance_id,
            turn_id,
            character_offset,
            word_index,
            None,
        )
        try:
            self.audio_queue.put_nowait(queued_frame)
        except asyncio.QueueFull:
            # Bucket 2 (VOICE_REMEDIATION_PLAN.md): drop the NEW frame, not
            # the oldest one. The old policy evicted the oldest queued frame
            # and spliced this one in behind it -- for synthesised speech
            # (a fixed artifact that must arrive intact, unlike a live
            # stream where freshness beats completeness) that is a hard
            # discontinuity between non-adjacent PCM segments: an audible
            # click plus a hole in the speech, reported live as "grainy" /
            # "can't understand it."
            #
            # True backpressure (`await self.audio_queue.put(...)`) would
            # fix this without dropping anything, but this callback is the
            # direct NATS handler for audio.stream -- the comment on this
            # agent's `start()` ("Capture frames on a dedicated worker so
            # NATS callback can return fast") is the same ack-timing
            # constraint CLAUDE.md's finding A1 documents: `BaseAgent.subscribe`
            # acks only after the callback returns, so blocking here until
            # a real-time playback worker catches up on a 256-deep backlog
            # risks the same JetStream AckWait/redelivery failure mode, not
            # fixes it. Dropping the newest frame keeps everything already
            # queued in order and keeps this callback non-blocking; in
            # measured real sessions (2026-09-01) the queue never exceeded
            # 68 of 256 slots, so this path is a rare-overflow safety net,
            # not the common case.
            self.dropped_audio_frames += 1
            if utterance_id and turn_id:
                self._lifecycle_dropped[(utterance_id, turn_id)] = None
                _cap(self._lifecycle_dropped)
            if self.dropped_audio_frames % 50 == 1:
                logger.warning(
                    "Transport audio queue overloaded; dropped %s frames.",
                    self.dropped_audio_frames,
                )

        self._trace(
            "buffer2_to_3",
            qsize=self.audio_queue.qsize(),
            dropped=self.dropped_audio_frames,
        )

        self._maybe_publish_playback_backlog()

    async def _audio_playback_worker(self):
        """Drain queued PCM frames and push to LiveKit at sink pace."""
        while True:
            try:
                item = await self.audio_queue.get()
                try:
                    if item[-1]:
                        await self._finish_utterance(*item[3:])
                    else:
                        await self._play_frame(*item[:-1])
                finally:
                    self.audio_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Transport playback worker error: {e}")
                await asyncio.sleep(0.01)

    def _release_utterance(self, utterance_id, turn_id) -> None:
        """Forget which utterance a turn is playing once it has ended."""
        if self._lifecycle_active_by_turn.get(turn_id or "") == (utterance_id or ""):
            self._lifecycle_active_by_turn.pop(turn_id or "", None)
        if self._active_utterance_id == utterance_id:
            self._active_utterance_id = None
            self._active_utterance_turn_id = None

    async def _finish_utterance(
        self, utterance_id, turn_id, character_offset, word_index, terminal_state
    ) -> None:
        """Emit a reply's terminal once its end-of-stream marker is drained;
        COMPLETED only after LiveKit has played the last frame out."""
        lifecycle_key = (utterance_id or "", turn_id or "")
        heard_words, heard_offset = self._lifecycle_position.get(lifecycle_key, (0, 0))
        streamed_words = max(heard_words, word_index or 0)
        streamed_offset = max(heard_offset, character_offset or 0)
        if terminal_state == "COMPLETED" and lifecycle_key in self._lifecycle_dropped:
            terminal_state = "FAILED"
        self._lifecycle_dropped.pop(lifecycle_key, None)
        if terminal_state == "COMPLETED":
            source = self.audio_source
            try:
                await source.wait_for_playout()
            except Exception:
                self._emit_lifecycle(
                    utterance_id=utterance_id or "",
                    turn_id=turn_id or "",
                    state="FAILED",
                    words_played=heard_words,
                    words_streamed=streamed_words,
                    heard_offset=heard_offset,
                    streamed_offset=streamed_offset,
                )
                raise
            if self._lifecycle_terminal.get(lifecycle_key) == "INTERRUPTED":
                return
        completed = terminal_state == "COMPLETED"
        self._emit_lifecycle(
            utterance_id=utterance_id or "",
            turn_id=turn_id or "",
            state=terminal_state,
            words_played=streamed_words if completed else heard_words,
            words_streamed=streamed_words,
            heard_offset=streamed_offset if completed else heard_offset,
            streamed_offset=streamed_offset,
        )
        self._release_utterance(utterance_id, turn_id)

    async def _play_frame(
        self,
        pcm_data,
        sample_rate,
        num_channels,
        utterance_id,
        turn_id,
        character_offset,
        word_index,
    ) -> None:
        if not pcm_data or num_channels <= 0:
            return

        samples_per_channel = len(pcm_data) // (2 * num_channels)
        if samples_per_channel <= 0:
            return

        frame = rtc.AudioFrame(
            data=pcm_data,
            sample_rate=sample_rate,
            num_channels=num_channels,
            samples_per_channel=samples_per_channel,
        )
        lifecycle_key = (utterance_id or "", turn_id or "")
        try:
            await self.audio_source.capture_frame(frame)
        except Exception:
            heard_words, heard_offset = self._lifecycle_position.get(
                lifecycle_key, (0, 0)
            )
            self._emit_lifecycle(
                utterance_id=utterance_id or "",
                turn_id=turn_id or "",
                state="FAILED",
                words_played=heard_words,
                words_streamed=word_index or 0,
                heard_offset=heard_offset,
                streamed_offset=character_offset or 0,
            )
            self._release_utterance(utterance_id, turn_id)
            raise
        self._active_utterance_id = utterance_id
        self._active_utterance_turn_id = turn_id
        self._emit_playing_lifecycle(
            utterance_id, turn_id, character_offset, word_index
        )
        self._trace(
            "buffer3_to_4",
            qsize=self.audio_queue.qsize(),
        )
        self._maybe_publish_playback_progress(turn_id, character_offset, word_index)
        # Reviewer finding: backlog telemetry used to be published
        # only on enqueue (_on_nats_audio). If a report reached
        # the brain at depth >= VOICE_FILLER_MAX_PLAYBACK_BACKLOG
        # and the queue then fully drained during silence (no new
        # PCM arriving to trigger another enqueue-side publish),
        # the brain's last-known depth stayed stuck high and
        # suppressed the next turn's filler for no live reason.
        # Publishing from the drain side too means an emptied
        # queue eventually reports its own zero depth.
        self._maybe_publish_playback_backlog()

    def _maybe_publish_playback_backlog(self) -> None:
        """Throttled `AudioPlaybackBacklog` publish, called from both the
        enqueue side (`_on_nats_audio`) and the drain side
        (`_audio_playback_worker`) so the brain's view of queue depth can
        also fall back to zero once playback catches up, not just rise on
        new audio. Same `BACKLOG_TELEMETRY_INTERVAL_S` throttle either way.
        """
        now = time.time()
        if now - self._last_backlog_publish_at < BACKLOG_TELEMETRY_INTERVAL_S:
            return
        self._last_backlog_publish_at = now
        backlog = AudioPlaybackBacklog(
            queue_depth=self.audio_queue.qsize(),
            capacity=self.audio_queue.maxsize,
        )
        self.spawn(self.publish(Topics.AUDIO_PLAYBACK_BACKLOG, backlog.model_dump()))

    def _maybe_publish_playback_progress(
        self, turn_id, character_offset, word_index
    ) -> None:
        """P4-2: fires once a PCM frame carrying a *new* offset has actually
        reached the LiveKit audio source -- the closest observable "reached
        the speaker" point available in this architecture (see the
        `_last_progress_turn_id` docstring in `__init__`).

        Not awaited inline: this is a real-time audio drain loop, and a
        JetStream ack round-trip for an observability-shaped message must not
        delay the next PCM frame -- the same reasoning voice-agent's own
        `publish_pcm` documents for its own ack.

        Terminal state is carried on `audio.playback.lifecycle`; this legacy
        progress topic remains nonterminal for word-offset consumers.
        """
        if character_offset is None or word_index is None:
            return
        if turn_id != self._last_progress_turn_id:
            self._last_progress_turn_id = turn_id
            self._last_progress_offset = -1
        if character_offset <= self._last_progress_offset:
            return
        self._last_progress_offset = max(self._last_progress_offset, character_offset)

        progress = AudioPlaybackProgress(
            utterance_id=turn_id or "",
            character_offset=character_offset,
            word_index=word_index,
            completed=False,
        )
        self.spawn(self.publish(Topics.AUDIO_PLAYBACK_PROGRESS, progress.model_dump()))

    def _emit_playing_lifecycle(
        self, utterance_id, turn_id, character_offset, word_index
    ) -> None:
        if not turn_id or not utterance_id:
            return
        offset = max(0, int(character_offset or 0))
        words = max(0, int(word_index or 0))
        key = (utterance_id, turn_id)
        if key in self._lifecycle_terminal:
            # A frame of a reply that already ended (queued before its stop)
            # must not recreate the progress state the terminal dropped.
            return
        self._lifecycle_position[key] = (words, offset)
        _cap(self._lifecycle_position)
        if key not in self._lifecycle_started:
            self._lifecycle_started[key] = None
            _cap(self._lifecycle_started)
            self._emit_lifecycle(
                utterance_id=utterance_id,
                turn_id=turn_id,
                state="STARTED",
                words_played=0,
                words_streamed=words,
                heard_offset=0,
                streamed_offset=offset,
            )
        self._emit_lifecycle(
            utterance_id=utterance_id,
            turn_id=turn_id,
            state="PLAYING",
            words_played=words,
            words_streamed=words,
            heard_offset=offset,
            streamed_offset=offset,
        )

    def _emit_lifecycle(
        self,
        *,
        utterance_id: str,
        turn_id: str,
        state: str,
        words_played: int,
        words_streamed: int,
        heard_offset: int,
        streamed_offset: int,
        flushed: bool = False,
    ) -> None:
        key = (utterance_id, turn_id)
        terminal_states = {"COMPLETED", "INTERRUPTED", "FAILED"}
        terminal = self._lifecycle_terminal.get(key)
        if terminal:
            if state != terminal and state in terminal_states:
                self.lifecycle_protocol_errors += 1
                logger.error(
                    "Conflicting playback terminal %s after %s for %s/%s (count=%d)",
                    state,
                    terminal,
                    utterance_id,
                    turn_id,
                    self.lifecycle_protocol_errors,
                )
            return
        seq = self._lifecycle_seq.get(key, 0)
        if state in terminal_states:
            self._lifecycle_terminal[key] = state
            _cap(self._lifecycle_terminal)
            # Nothing is emitted for this key after its terminal, so its
            # progress state is dead weight from here on.
            self._lifecycle_seq.pop(key, None)
            self._lifecycle_started.pop(key, None)
            self._lifecycle_position.pop(key, None)
            self._lifecycle_dropped.pop(key, None)
        else:
            self._lifecycle_seq[key] = seq + 1
            _cap(self._lifecycle_seq)
        event = AudioPlaybackLifecycle(
            utterance_id=utterance_id,
            turn_id=turn_id,
            seq=seq,
            state=state,
            words_played=words_played,
            words_streamed=max(words_played, words_streamed),
            heard_offset=heard_offset,
            streamed_offset=max(heard_offset, streamed_offset),
            flushed=flushed,
        )
        self.spawn(self._publish_lifecycle(event))

    async def _publish_lifecycle(self, event: AudioPlaybackLifecycle) -> None:
        # Preserve seq order without awaiting NATS from the real-time PCM loop.
        async with self._lifecycle_publish_lock:
            await self.publish(Topics.AUDIO_PLAYBACK_LIFECYCLE, event.model_dump())

    async def _on_viseme(self, data: dict) -> None:
        """Forward one viseme frame onto the room's data channel.

        `reliable=False`: this is a live, latest-value-wins animation signal
        published at the audio chunk rate (voice-agent's
        `generate_and_publish_visemes`, four call sites in its playback
        loop), the same reasoning WebRTC media itself is UDP-based for -- a
        dropped frame a moment before the next one lands is invisible, and
        waiting on reliable delivery would only add latency an animation
        curve does not need. Not spawned like `_publish_presence`/progress:
        `publish_data` is a local, non-blocking call over an already-open
        data channel, not a NATS round trip, so there is nothing here worth
        moving off this callback.
        """
        try:
            viseme = PlaybackVisemes.model_validate(data)
        except Exception:
            logger.warning("Dropping malformed viseme payload: %r", data)
            return
        if not self.room.local_participant:
            return
        payload = viseme.model_dump_json().encode("utf-8")
        try:
            # publish_data is a coroutine in this livekit-rtc version; an
            # unawaited call here silently never sends anything.
            await self.room.local_participant.publish_data(
                payload, reliable=False, topic="visemes"
            )
        except Exception as exc:
            logger.debug("Could not publish viseme to the room (no listener?): %s", exc)

    async def _on_audio_stop(self, data: dict) -> None:
        """P1-3 (audit/ROADMAP.md): flush audio already in flight on a
        confirmed barge-in.

        A speculative stop is only a duck (see decision.py's
        `is_speculative_stop_confirmed`, the sole arbiter that ever confirms
        one) -- nothing has been cancelled yet, so there is nothing here to
        flush. `turn_id` scoping mirrors voice-agent's own
        `stop_applies_to_active_turn`: unscoped (turn_id=None) always
        applies; a named one must match what is currently flowing, so a stop
        that arrives late for a turn that has already finished cannot flush
        audio queued for the turn speaking now.
        """
        if data.get("speculative"):
            return

        stop_turn_id = data.get("turn_id")
        if (
            stop_turn_id is not None
            and self._active_turn_id is not None
            and stop_turn_id != self._active_turn_id
        ):
            logger.info(
                "Ignoring AUDIO_STOP for turn %s; transport is currently playing %s.",
                stop_turn_id,
                self._active_turn_id,
            )
            return

        if self._active_utterance_id:
            utterance_id = self._active_utterance_id
            active_turn = self._active_utterance_turn_id or ""
            words, offset = self._lifecycle_position.get(
                (utterance_id, active_turn), (0, 0)
            )
            # `flushed` marks the rejected take of the flush's own turn, which
            # the brain must not record as the reply's outcome (DR-029). A
            # flush for one turn that cuts audio still playing for another
            # turn is a real interruption of that other reply.
            self._emit_lifecycle(
                utterance_id=utterance_id,
                turn_id=active_turn,
                state="INTERRUPTED",
                words_played=words,
                words_streamed=words,
                heard_offset=offset,
                streamed_offset=offset,
                flushed=bool(data.get("flush")) and active_turn == stop_turn_id,
            )
            if self._lifecycle_active_by_turn.get(active_turn) == utterance_id:
                self._lifecycle_active_by_turn.pop(active_turn, None)
            self._active_utterance_id = None
            self._active_utterance_turn_id = None

        await self._flush_downstream_audio()

    async def _flush_downstream_audio(self) -> None:
        """Discard queued PCM (buffer 3) and rotate the published LiveKit
        track so audio already handed to its native, time-paced send buffer
        (buffer 4) stops playing rather than draining out over however much
        it holds.

        Stage 3's measurement work (audit/ROADMAP.md measurement 1.1) found
        buffer 4 has no public API to inspect or drain from here:
        `capture_frame()` only acknowledges the frame reached the client's
        buffer, and `wait_for_playout()` paces a *different*, unrelated wait
        -- neither exposes or clears what is already queued natively.
        `rtc.AudioSource`'s own default (`queue_size_ms=1000`) is the
        code-level bound on how much that buffer can hold: up to a second of
        stale audio, worst case, if nothing else is done about it.

        Recreating the published track routes around that boundary instead
        of reaching through it: the client stops receiving the *old* track
        (and whatever was queued for it) the moment it is unpublished, and
        only audio for the *new* track plays from here on. The two publishes
        overlap deliberately -- the new track goes live before the old one
        is torn down -- so this costs a brief SDP renegotiation, not a gap
        in the output track.
        """
        drained = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
                drained += 1
            except asyncio.QueueEmpty:
                break

        new_source = rtc.AudioSource(self.output_sample_rate, self.output_channels)
        new_track = rtc.LocalAudioTrack.create_audio_track("ai-voice", new_source)
        new_publication = await self.room.local_participant.publish_track(new_track)

        old_publication = self.audio_publication
        self.audio_source = new_source
        self.audio_track = new_track
        self.audio_publication = new_publication

        if old_publication is not None:
            await self.room.local_participant.unpublish_track(old_publication.sid)

        self._trace("buffer3_4_flush", drained_frames=drained)
        logger.info(
            "Flushed %d queued frame(s) and rotated the LiveKit audio track "
            "after a confirmed barge-in.",
            drained,
        )

    async def stop(self):
        await self._prepare_stop()
        if self.audio_worker_task:
            self.audio_worker_task.cancel()
            try:
                await self.audio_worker_task
            except asyncio.CancelledError:
                pass
        if self.inbound_audio_worker_task:
            self.inbound_audio_worker_task.cancel()
            try:
                await self.inbound_audio_worker_task
            except asyncio.CancelledError:
                pass
        await self.room.disconnect()
        await super().stop()
        logger.info("Transport Agent Stopped.")


async def main():
    agent = TransportAgent()
    await agent.start()
    shutdown_trigger = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_trigger)
    await shutdown_trigger.wait()
    await agent.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
