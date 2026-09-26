import asyncio
import json
import logging
import time
import uuid
from typing import Any

from app import clock
from app.agents.base import BaseAgent, install_shutdown_signal_handlers
from app.cognitive.subconscious import ProactiveThought, SubconsciousEngine
from app.cognitive.trace import emit
from app.cognitive.trace import enabled as trace_enabled
from app.config import Config
from app.contracts import ChatInput, ChatInputMetadata, Topics
from app.llm import build_llm_client
from app.measure_trace import trace as _measure_trace
from app.state import proactive_queue
from app.state.agent_state import StateService, in_hour_window, user_hour
from app.state.graph_db import GraphDB

logger = logging.getLogger(__name__)


def is_rest_phase(
    now: float,
    last_user_interaction: float,
    fatigue: float,
    *,
    idle_threshold_s: float = 30.0,
) -> bool:
    """Bucket 12 (voice remediation Phase 3), items 2-3: idle AND (night OR
    fatigue > 0.8) -- reusing `_continuous_monologue_loop`'s existing dream
    gate's fatigue threshold and `_update_fatigue`'s existing night window
    (`agent_state.py`'s `hour >= 22 or hour < 6`) rather than inventing a
    second set of numbers for what is, biologically, the same "asleep or
    exhausted" condition dreaming already keys off of.

    A pure function of three scalars rather than a method reading live state,
    so `_run_rest_phase_replay`'s gating logic is testable without a running
    agent, a mocked clock, or any I/O -- the same reasoning
    `ReflectionService`'s own cooldown check applies.
    """
    idle_s = now - last_user_interaction
    if idle_s < idle_threshold_s:
        return False
    return in_hour_window(user_hour(now), 22, 6) or fatigue > 0.8


class SubconsciousAgent(BaseAgent):
    """
    Subconscious Agent (Internal Affect & Somatic Simulation).
    NATS wrapper for the SubconsciousEngine and solid-state memory consolidation.
    """

    def __init__(
        self,
        ollama_url: str = Config.OLLAMA_URL,
        graph_db: "GraphDB | None" = None,
        state_service: "StateService | None" = None,
        memory_store=None,
        reflection_service=None,
    ):
        super().__init__(name="subconscious_agent")
        self._llm = build_llm_client(base_url=ollama_url, model=Config.LLM_CHAT_MODEL)
        provenance = Config.LLM_PROVENANCE
        logger.info(
            "[Subconscious] LLM config resolved from %s (exists=%s): chat=%s fast=%s "
            "reflection=%s url=%s",
            provenance["env_file"],
            provenance["env_file_exists"],
            provenance["llm_chat_model"],
            provenance["llm_fast_model"],
            provenance["llm_reflection_model"],
            provenance["ollama_url"],
        )
        self.graph_db = graph_db or GraphDB()
        self.state_service = state_service or StateService(
            graph_store=self.graph_db, writer_id="subconscious_agent"
        )
        self.engine = SubconsciousEngine(llm_client=self._llm)
        self.memory_store = memory_store
        self._owns_memory_store = memory_store is None
        self._owns_db_store = memory_store is None
        self.reflection_service = reflection_service
        self.db_store = None
        self._is_consolidating = False
        # P1-1: retained so the dispatched consolidation coroutine isn't
        # only weakly referenced by the event loop and eligible for GC
        # mid-flight (closes M1-A13 here); also lets a test or caller await
        # completion explicitly instead of racing the background task.
        self._consolidation_task: asyncio.Task | None = None
        self._current_monologue_task = None
        self._current_dream_task = None
        self._last_monologue_time = 0.0
        self._monologue_task = None
        self._last_benchmark_time = 0.0
        # Bucket 12 (voice remediation Phase 3), items 2-3.
        self._current_replay_task: asyncio.Task | None = None
        self._last_replay_time = 0.0
        # Phase 3.1: pessimistic until transport_agent's first session.presence
        # signal arrives -- a freshly started process should not assume a
        # proactive thought has anyone to reach before it actually knows.
        self._someone_connected = False

    @property
    def llm(self):
        return self._llm

    @llm.setter
    def llm(self, value):
        self._llm = value
        if hasattr(self, "engine"):
            self.engine.llm = value

    async def start(self):
        await self.connect()
        await self.graph_db.initialize()

        # P1-5: a one-time catch-up so this process's state isn't just the
        # persona defaults until the brain's next state.broadcast arrives --
        # mirrors what CognitiveService.initialize() does on the brain side.
        # Ongoing sync after this is event-driven (_on_state_broadcast,
        # below), not polled.
        await self.state_service.hydrate_state()

        # Initialize SQLite/Postgres DB pool and MemoryStore if not provided
        if not self.memory_store:
            from app.state.conversation_store import ConversationHistoryStore
            from app.state.memory_store import MemoryStore

            self.db_store = ConversationHistoryStore()
            await self.db_store.initialize()
            self.memory_store = MemoryStore(
                pool=self.db_store.pool, graph_db=self.graph_db
            )

        if not self.reflection_service:
            from app.cognitive.learning import ReflectionService

            self.reflection_service = ReflectionService(
                llm_service=self._llm,
                graph_store=self.graph_db,
                pg_vector=self.memory_store,
            )

        await self.subscribe(
            Topics.SYSTEM_TICK,
            self._on_system_tick,
            durable=f"{self.name}_system_tick",
            deliver_policy="new",
            # P1-1: state the control tier's ack deadline instead of
            # inheriting a 30s default alongside UNLIMITED redelivery. Only
            # meaningful now that consolidation is dispatched rather than
            # awaited here -- see _on_system_tick's docstring.
            ack_wait=Config.MESH_CONTROL_ACK_WAIT_S,
            max_deliver=Config.MESH_CONTROL_MAX_DELIVER,
        )
        await self.subscribe(
            Topics.CHAT_INPUT,
            self._on_chat_input,
            durable=f"{self.name}_chat_input",
            deliver_policy="new",
        )
        # F-017: state.broadcast is a full snapshot in AI_STATE, which keeps
        # only the newest one. "last" hands a newly created durable (first
        # boot, or the one the migration recreates) that snapshot at once;
        # "new" left this process on persona defaults until the next tick,
        # since broadcasts are its only source of the brain's state. A resumed
        # durable is unaffected: it continues from its cursor, and the stream
        # holds nothing older than the newest snapshot to replay.
        await self.subscribe(
            "state.broadcast",
            self._on_state_broadcast,
            durable=f"{self.name}_state_broadcast",
            deliver_policy="last",
        )
        # Phase 3.1: liveness signal like state.broadcast above, not a work
        # item -- a freshly (re)started process replaying every past
        # presence transition would derive the wrong "is anyone here right
        # now" answer from history that says nothing about the present.
        await self.subscribe(
            Topics.SESSION_PRESENCE,
            self._on_session_presence,
            durable=f"{self.name}_session_presence",
            deliver_policy="new",
        )
        await self.subscribe(
            Topics.AUDIO_PERCEPTION,
            self._on_audio_perception,
            durable=f"{self.name}_audio_perception_monologue",
            deliver_policy="new",
        )
        # P3-1: liveness signal, not a work item, like every other
        # subscription in the mesh whose replayed history says nothing about
        # the present moment (see VisionAgent's own reasoning for chat.input/
        # chat.output) -- a fresh durable under the "all" default would
        # re-walk every past vision.description and re-evaluate salience
        # against affect states that no longer hold.
        await self.subscribe(
            Topics.VISION_DESCRIPTION,
            self._on_vision_description,
            durable=f"{self.name}_vision_description",
            deliver_policy="new",
        )
        self._monologue_task = asyncio.create_task(self._continuous_monologue_loop())
        logger.info(f"🧠 {self.name} Online | Subconscious Mesh Interface Active.")

    async def _deliver_thought(self, thought: ProactiveThought) -> None:
        """Publish one proactive thought as a real `chat.input` turn -- the
        same path a live thought or a replayed, queued one both go through,
        so there is exactly one implementation of "how a thought becomes an
        utterance" to keep correct."""
        msg = ChatInput(
            text=thought.text,
            utterance_id=str(uuid.uuid4()),
            metadata=ChatInputMetadata(
                source="subconscious",
                confidence=1.0,
                importance=thought.importance,
                category=thought.category,
                goal_id=thought.goal_id,
            ),
        )
        await self.publish(Topics.CHAT_INPUT, msg.model_dump())

    async def _on_session_presence(self, data: dict[str, Any]) -> None:
        """Phase 3.1: transport_agent is the only component that knows
        whether anyone is actually connected. On the 0 -> 1 edge, replay
        whatever proactive thoughts queued up while nobody was listening --
        "a friend who thought of you at 2pm can say so at 6pm"."""
        connected = bool(data.get("connected", False))
        was_connected = self._someone_connected
        self._someone_connected = connected

        if connected and not was_connected:
            pending = proactive_queue.pop_all(self.state_service.db_path)
            for encoded in pending:
                try:
                    thought = ProactiveThought(**json.loads(encoded))
                except (ValueError, TypeError):
                    thought = ProactiveThought(
                        text=encoded,
                        importance=0.0,
                        category="useful_to_user",
                    )
                logger.info(
                    "[Subconscious] Delivering queued thought on reconnect: '%s'",
                    thought.text,
                )
                await self._deliver_thought(thought)

    async def _on_state_broadcast(self, data: dict[str, Any]):
        """Syncs state changes from NATS state.broadcast into Neo4j, and
        applies them to this process's own live StateService (P1-5).

        Before this, `subconscious_agent`'s AgentState was never hydrated
        and never updated -- an independent copy holding whatever the
        persona defaults were at startup, forever. Every silence gate and
        the dream path (`_run_dream_sequence`) read that same
        `self.state_service.current_state`, so both were measuring a state
        that had nothing to do with the brain's actual mood/fatigue/trust.
        """
        agent_name = data.get("agent_name", "my friend")

        # Validate agent_name
        if not agent_name or not isinstance(agent_name, str):
            logger.error(
                f"[Subconscious] Invalid or missing agent_name in state broadcast. Payload: {data}"
            )
            return

        await self.state_service.apply_external_state(data)

        logger.info(
            f"[Subconscious] Received state broadcast for {agent_name}. Syncing to Neo4j..."
        )

        query = """
        MERGE (a:Agent {name: $name})
        SET a.mood = $mood,
            a.energy = $energy,
            a.dominance = $dominance,
            a.trust_benevolence = $trust_benevolence,
            a.trust_competence = $trust_competence,
            a.trust_integrity = $trust_integrity,
            a.trust = $trust,
            a.attachment = $attachment,
            a.fatigue = $fatigue,
            a.last_user_interaction = $last_user_interaction,
            a.interaction_count = $interaction_count,
            a.inferred_valence = $inferred_valence,
            a.inferred_arousal = $inferred_arousal,
            a.implied_goals = $implied_goals,
            a.known_concepts = $known_concepts,
            a.baseline_valence = $baseline_valence,
            a.baseline_arousal = $baseline_arousal,
            a.baseline_dominance = $baseline_dominance,
            a.last_sync = datetime()
        RETURN a.name as name
        """
        params = {
            "name": agent_name,
            "mood": data.get("mood", 0.0),
            "energy": data.get("energy", 0.5),
            "dominance": data.get("dominance", 0.5),
            "trust_benevolence": data.get("trust_benevolence", 0.5),
            "trust_competence": data.get("trust_competence", 0.5),
            "trust_integrity": data.get("trust_integrity", 0.5),
            "trust": data.get("trust", 0.5),
            "attachment": data.get("attachment", 0.1),
            "fatigue": data.get("fatigue", 0.0),
            "last_user_interaction": data.get("last_user_interaction", clock.time()),
            "interaction_count": data.get("interaction_count", 0),
            "inferred_valence": data.get("inferred_valence", 0.0),
            "inferred_arousal": data.get("inferred_arousal", 0.5),
            "implied_goals": data.get("implied_goals", []),
            "known_concepts": data.get("known_concepts", []),
            "baseline_valence": data.get("baseline_valence", 0.0),
            "baseline_arousal": data.get("baseline_arousal", 0.5),
            "baseline_dominance": data.get("baseline_dominance", 0.5),
        }

        try:
            result = await self.graph_db.execute_query(query, params, write=True)
            # Verify write succeeded by checking result is non-empty or has positive write counters
            if result is not None and (
                isinstance(result, list) and len(result) > 0 or result
            ):
                if hasattr(self.graph_db, "invalidate_cache"):
                    await self.graph_db.invalidate_cache(agent_name)
                logger.debug(
                    f"[Subconscious] Asynchronously persisted state to Neo4j for {agent_name}."
                )
            else:
                logger.warning(
                    f"[Subconscious] Neo4j write returned empty result for {agent_name}. Write may have failed silently."
                )
        except Exception as e:
            logger.error(f"[Subconscious] Failed to sync state to Neo4j: {e}")

    async def _on_system_tick(self, data: dict[str, Any]):
        """Delegates thought generation to the engine, routes it to the mesh,
        then DISPATCHES memory consolidation rather than awaiting it.

        P1-1: consolidation runs reflection (multiple sequential LLM calls),
        graph writes and ACT-R decay - MEASURED ~16s idle, ~28s under the
        two-model VLM/LLM contention `HARDWARE.md` §5 measured. This
        callback used to await all of that inline, before `BaseAgent.
        subscribe`'s handler acks the message (base.py:383) - against a 30s
        default AckWait with unlimited MaxDeliver. A slow pass could exceed
        AckWait, get redelivered mid-flight, and run the same consolidation
        twice: duplicate graph writes and - the symptom that actually
        surfaced this - duplicate proactive utterances to the user.

        `_ack_heartbeat` (base.py) already exists for exactly this shape of
        problem but is gated on `chat.*` subjects; a prior audit weighed it
        against `audio.*` and never considered `system.tick`, the longest
        callback in the mesh - issue #175 was then closed as "already
        resolved" on that basis. Widening `_ack_heartbeat` to every subject
        was considered and rejected: an ack held in-progress for 28s is a
        liveness lie regardless of which subject carries it, and doing that
        everywhere repeats the original scoping mistake rather than fixing
        it. Dispatching the actual work removes the problem instead of
        extending the workaround.

        The guard-and-dispatch below is intentionally split from the work
        itself (`_run_consolidation_pass`): `_is_consolidating` is checked
        and set HERE, synchronously, before `asyncio.create_task` schedules
        anything - `create_task` does not run the coroutine body until the
        event loop yields, so if the guard lived inside the dispatched
        coroutine instead, two ticks arriving back-to-back could each pass
        the check before either task actually started and set the flag.

        Scope, deliberately: the proactive-thought LLM call above
        (`evaluate_and_think`) stays inline. It is gated on
        `check_proactive_eligibility`, so it is rate-limited rather than
        per-tick, and it is a single short generation -- seconds, not the
        ~28s consolidation was. Keeping it inline preserves the ordering
        between generating a thought, publishing it and calling
        `mark_proactive_attempt`, which dispatching would race. The ack
        deadline is sized to cover it (`MESH_CONTROL_ACK_WAIT_S`), so it is
        bounded work under a stated bound rather than unbounded work under
        an implicit one.

        Acking before consolidation completes is a genuine semantics change,
        not just an implementation detail: a crash between dispatch and
        completion loses that pass. Accepted - consolidation is periodic and
        runs over *unconsolidated* episodes, so a missed pass is picked up
        by the next tick, not lost - and recorded here and in the ledger as
        a decision.
        """
        last_bench = getattr(self, "_last_benchmark_time", 0.0)
        benchmark_elapsed = clock.time() - last_bench
        if benchmark_elapsed < 300:
            if trace_enabled():
                emit(
                    "proactive.decision",
                    fired=False,
                    reason="benchmark_active",
                    benchmark_elapsed_s=benchmark_elapsed,
                    benchmark_threshold_s=300,
                )
            logger.info(
                "[Subconscious] Suppressing proactive system tick thought: Benchmark is active."
            )
            return

        state_snap = self.state_service.get_context_snapshot()
        eligible = self.state_service.check_proactive_eligibility()

        candidate = await self.engine.evaluate_and_think(state_snap, eligible)

        if candidate:
            accepted = self.state_service.proactive_candidate_eligible(
                importance=candidate.importance,
                category=candidate.category,
                description=candidate.text,
                goal_id=candidate.goal_id,
            )
            # Generation itself consumes the window, including a candidate
            # rejected by importance, so one low-value thought cannot cost an
            # LLM call on every system tick.
            self.state_service.mark_proactive_attempt()
            await self.state_service.persist_state()
            if not accepted:
                return
            goal_id, _ = self.state_service.record_proactive_thought(
                candidate.text, goal_id=candidate.goal_id
            )
            candidate = ProactiveThought(
                text=candidate.text,
                importance=candidate.importance,
                category=candidate.category,
                goal_id=goal_id,
            )
            # Phase 3.1: a proactive thought generated while nobody is
            # connected has nowhere to go -- publishing it anyway triggers a
            # full cognitive turn, TTS and audio synthesis transport_agent
            # can only discard, and the thought itself is then just gone.
            # Queuing instead costs nothing until reconnect, at which point
            # _on_session_presence replays it through this exact same path.
            if self._someone_connected:
                logger.info("[Subconscious] Thought generated: '%s'", candidate.text)
                await self._deliver_thought(candidate)
            else:
                logger.info(
                    "[Subconscious] Thought generated while nobody is "
                    "connected; queuing for reconnect: '%s'",
                    candidate.text,
                )
                proactive_queue.enqueue(
                    self.state_service.db_path,
                    json.dumps(
                        {
                            "text": candidate.text,
                            "importance": candidate.importance,
                            "category": candidate.category,
                            "goal_id": candidate.goal_id,
                        }
                    ),
                )

            # Marked either way: a queued thought still consumed this tick's
            # eligibility window, and not marking it would let every tick
            # while still disconnected generate (and queue) another thought,
            # stacking up duplicates until someone reconnects.

        # Subconscious Memory Consolidation (ACT-R & Fact Triplet Crystallization)
        # Enforce 5-minute silence check: user must be inactive for at least 300 seconds (unless bypassed)
        last_interact = self.state_service.current_state.last_user_interaction
        silence_duration = clock.time() - last_interact
        bypass = getattr(Config, "TESTING_CONSOLIDATION_BYPASS_SILENCE", False)

        if silence_duration < 300 and not bypass:
            logger.info(
                f"[Subconscious] Bypassing consolidation pass: user active {silence_duration:.1f}s ago (needs 300s)."
            )
            return

        if self._is_consolidating:
            logger.warning(
                "[Subconscious] Bypassing consolidation pass: sweep already active."
            )
            return

        # Set synchronously, before dispatch - see the docstring above for
        # why this cannot move inside _run_consolidation_pass.
        self._is_consolidating = True
        self._consolidation_task = asyncio.create_task(self._run_consolidation_pass())

    async def _run_consolidation_pass(self) -> None:
        """The actual consolidation work (P1-1), dispatched by
        `_on_system_tick` rather than awaited inline so the tick's ack is
        not held on it. See `_on_system_tick`'s docstring for why."""
        _t0 = time.monotonic()
        try:
            logger.info("[Subconscious] Initiating subconscious consolidation pass...")
            episodes = await self.memory_store.get_recent_unconsolidated_episodes(
                limit=10
            )

            if episodes:
                # Map SQLite/PG message rows into reflection schemas by pairing user and assistant chronologically
                reflection_episodes = []
                chrono_episodes = list(reversed(episodes))

                i = 0
                while i < len(chrono_episodes):
                    ep = chrono_episodes[i]
                    role = ep.get("role")
                    content = ep.get("content", "")

                    if role != "assistant":
                        # Check if the next message is from the assistant to pair them
                        assistant_content = ""
                        if (
                            i + 1 < len(chrono_episodes)
                            and chrono_episodes[i + 1].get("role") == "assistant"
                        ):
                            assistant_content = chrono_episodes[i + 1].get(
                                "content", ""
                            )
                            i += 2  # Consume both user and assistant
                        else:
                            i += 1  # Consume only user

                        reflection_episodes.append(
                            {
                                "id": ep.get("id"),
                                "event": content,
                                "speaker": role,
                                "response": assistant_content,
                                "context": "Session conversation message",
                                "emotion_vector": {"V": 0.0, "Ar": 0.5, "D": 0.5},
                                "relationship_delta": 0.0,
                                "content": content,
                            }
                        )
                    else:
                        # Unpaired assistant message
                        reflection_episodes.append(
                            {
                                "id": ep.get("id"),
                                "event": "",
                                "speaker": "assistant",
                                "response": content,
                                "context": "Session conversation message",
                                "emotion_vector": {"V": 0.0, "Ar": 0.5, "D": 0.5},
                                "relationship_delta": 0.0,
                                "content": "",
                            }
                        )
                        i += 1

                # Trigger reflection task asynchronously
                task = await self.reflection_service.trigger_reflection(
                    reflection_episodes
                )
                if task and isinstance(task, asyncio.Task):
                    await (
                        task
                    )  # Wait for background fact extraction and graph writing to finish

                    # Mark these episodes as consolidated in the database
                    message_ids = [ep.get("id") for ep in episodes if ep.get("id")]
                    await self.memory_store.mark_episodes_consolidated(message_ids)

                    # Apply ACT-R decay on the raw user memories corresponding to these episodes
                    contents = [
                        ep.get("content")
                        for ep in episodes
                        if ep.get("role") != "assistant" and ep.get("content")
                    ]
                    await self.memory_store.apply_actr_decay(contents)

            # P3-1: screen-sourced visual traces get a hard privacy TTL
            # rather than the graded ACT-R fade above -- pruned here,
            # unconditionally on every pass, since it has nothing to do with
            # whether there were unconsolidated chat episodes this tick.
            await self.memory_store.prune_expired_visual_screen_traces()

            logger.info(
                "[Subconscious] Subconscious consolidation pass completed successfully."
            )
        except Exception as e:
            logger.error(f"[Subconscious] Consolidation pass failed: {e}")
        finally:
            _measure_trace(
                "subconscious",
                "consolidation_pass",
                duration_s=time.monotonic() - _t0,
                ack_wait_s=Config.MESH_CONTROL_ACK_WAIT_S,
            )
            self._is_consolidating = False

    async def _on_chat_input(self, data: dict[str, Any]):
        """Cancel active monologue or dream generation when user speaks/sends message."""
        metadata = data.get("metadata", {})
        source = metadata.get("source") if isinstance(metadata, dict) else None

        # Suppress proactive background tasks when benchmark is running
        if isinstance(metadata, dict) and metadata.get("benchmark_id") == "bench_pulse":
            self._last_benchmark_time = clock.time()
            logger.info(
                "[Subconscious] Benchmark pulse detected. Suppressing proactive monologue/dreaming loops."
            )

        if source != "subconscious":
            self._cancel_active_subconscious_tasks()

    async def _on_audio_perception(self, data: dict[str, Any]):
        """Cancel active monologue or dream generation immediately on early user audio detection."""
        self._cancel_active_subconscious_tasks()

    async def _on_vision_description(self, data: dict[str, Any]) -> None:
        """P3-1: salience-gated visual episodic memory.

        Three signals must all hold before a frame becomes a stored memory,
        or a static, affectively-neutral scene would mint one every
        VLM_APPRAISAL_INTERVAL: the frame must be perceptually novel
        (`is_novel`, reusing VisualAppraisalService's own habituation delta
        rather than a second novelty computation -- lives on the wire
        because that delta is computed inside the vision_agent process, not
        here), the VLM must have produced a description, and the moment must
        be affectively significant -- evaluated here, where affect actually
        lives, not in vision_agent (kept a pure sensor with no state/DB
        access, the same boundary SomaticAppraiser's placement in
        BrainAgent already argues for).

        Camera-sourced traces go through `add_memory` (modality="visual")
        and follow the normal ACT-R lifecycle. Screen-sourced traces go
        through `add_visual_screen_trace` and get a hard privacy TTL instead
        -- see that method's docstring.
        """
        description = data.get("description", "")
        if not description or data.get("is_novel") is not True:
            return

        valence = self.state_service.current_state.valence
        arousal = self.state_service.current_state.arousal
        worth_keeping = (
            arousal >= Config.VISUAL_MEMORY_AROUSAL_THRESHOLD
            or abs(valence) >= Config.VISUAL_MEMORY_VALENCE_THRESHOLD
        )
        if not worth_keeping:
            return

        source = data.get("source", "unknown")
        try:
            if source == "screen":
                await self.memory_store.add_visual_screen_trace(
                    description=description, valence=valence, arousal=arousal
                )
            else:
                await self.memory_store.add_memory(
                    content=description,
                    emotion=arousal,
                    valence=valence,
                    source="vision_camera",
                    modality="visual",
                    metadata={"visual_source": source},
                )
        except Exception as e:
            logger.error(f"[Subconscious] Failed to store visual episodic trace: {e}")

    def _cancel_active_subconscious_tasks(self):
        if self._current_monologue_task and not self._current_monologue_task.done():
            logger.info(
                "[Subconscious] User activity detected. Cancelling active monologue thought task."
            )
            self._current_monologue_task.cancel()
        if self._current_dream_task and not self._current_dream_task.done():
            logger.info(
                "[Subconscious] User activity detected. Cancelling active dream sequence task."
            )
            self._current_dream_task.cancel()

    async def _continuous_monologue_loop(self):
        logger.info("[Subconscious] Continuous monologue and dreaming loop started.")
        while True:
            try:
                await asyncio.sleep(5)

                # Suppress monologue and dream sequences if benchmark is active
                last_bench = getattr(self, "_last_benchmark_time", 0.0)
                if clock.time() - last_bench < 300:
                    continue

                # Check for silence duration
                last_interact = self.state_service.current_state.last_user_interaction
                silence_duration = clock.time() - last_interact

                # Check current fatigue
                state_snap = self.state_service.get_context_snapshot()
                fatigue = state_snap.get("fatigue", 0.0)

                # Dreaming vs Monologue Logic
                if fatigue > 0.8:
                    # Sleep-state dreaming (requires 30s user inactivity)
                    if silence_duration >= 30:
                        if (
                            self._current_dream_task
                            and not self._current_dream_task.done()
                        ):
                            continue
                        self._current_dream_task = asyncio.create_task(
                            self._run_dream_sequence()
                        )
                else:
                    # Normal monologue (requires 30s user inactivity)
                    now = clock.time()
                    if (
                        silence_duration >= 30
                        and (now - self._last_monologue_time) >= 30
                    ):
                        if (
                            self._current_monologue_task
                            and not self._current_monologue_task.done()
                        ):
                            continue
                        self._current_monologue_task = asyncio.create_task(
                            self._generate_monologue_thought()
                        )
                        self._last_monologue_time = now

                # Bucket 12 (voice remediation Phase 3), items 2-3: memory
                # maintenance, orthogonal to what the agent says/thinks above
                # -- both a dream and a rest-phase replay can legitimately
                # run the same tick, so this is a separate check rather than
                # a third branch of the if/else above.
                now = clock.time()
                replay_due = (
                    now - self._last_replay_time
                ) >= Config.REST_PHASE_REPLAY_INTERVAL_SECONDS
                replay_busy = (
                    self._current_replay_task and not self._current_replay_task.done()
                )
                if (
                    is_rest_phase(now, last_interact, fatigue)
                    and replay_due
                    and not replay_busy
                ):
                    self._current_replay_task = asyncio.create_task(
                        self._run_rest_phase_replay()
                    )
                    self._last_replay_time = now
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Subconscious Loop] Monologue/Dream loop error: {e}")

    async def _generate_monologue_thought(self):
        try:
            state_snap = self.state_service.get_context_snapshot()
            emotion = state_snap.get("emotion", "neutral")
            energy = state_snap.get("energy", 0.5)

            prompt = f"""
            You are the subconscious inner monologue of an AI friend.
            The user has been silent for a while.
            Your current emotion is {emotion} and your energy is {energy:.2f}.
            Formulate a single brief internal thought or self-reflection about the user, your friendship, or what you're thinking right now.
            Respond with ONLY the thought, no quotes, no conversational filler.
            """.strip()

            thought = await self._llm.generate(
                prompt,
                system="You are an internal monologue generator. Output ONLY the thought string.",
            )
            thought = thought.strip().strip("\"'")
            if thought:
                logger.info(f"[Monologue] Thought generated: '{thought}'")
                await self.publish(
                    Topics.STATE_SUBCONSCIOUS,
                    {"thought": thought, "timestamp": clock.time()},
                )
        except asyncio.CancelledError:
            logger.info(
                "[Monologue] Thought generation cancelled due to user activity."
            )
            raise
        except Exception as e:
            logger.error(f"[Monologue] Generation error: {e}")

    async def _run_dream_sequence(self):
        try:
            logger.info("[Subconscious] Running dream sequence...")
            # `ORDER BY rand()` evaluates a random value for every node before
            # sorting - O(N log N) over the whole graph on every dream cycle.
            # apoc.coll.randomItems samples after a single collect() pass,
            # O(N) with no sort (APOC ships by default, see
            # docker-compose.infra.yml's NEO4J_PLUGINS).
            query = (
                "MATCH (e:Entity) WITH collect(e.name) AS names "
                "UNWIND apoc.coll.randomItems(names, 3, false) AS name "
                "RETURN name"
            )
            records = await self.graph_db.execute_query(query)
            nodes = [record["name"] for record in records]

            if len(nodes) < 3:
                logger.info(
                    "[Subconscious] Not enough knowledge graph entities to dream (< 3). Skipping dream."
                )
                return

            concept1, concept2, concept3 = nodes[0], nodes[1], nodes[2]

            prompt = f"""
            You are the subconscious dreaming state of an AI friend.
            You are currently asleep and your mind is processing memories.
            Connect these three distinct concepts in a creative 'dream insight':
            - Concept A: "{concept1}"
            - Concept B: "{concept2}"
            - Concept C: "{concept3}"

            Synthesize a brief, insightful, and slightly surreal dream description (2-3 sentences max) linking these concepts.
            Format it as a personal reflection.
            Respond with ONLY the dream insight text.
            """.strip()

            dream_text = await self._llm.generate(
                prompt,
                system="You are in a subconscious dream state. Synthesize a creative link between the concepts.",
            )
            dream_text = dream_text.strip().strip("\"'")
            if dream_text:
                # Sections 19, 37 and Invariant 12: generated background
                # content cannot self-promote to truth. A dream is a
                # surreal, unverified association between graph entities,
                # not a lived event -- writing it into MemoryStore via
                # add_memory would let it be recalled later as an
                # autobiographical memory indistinguishable from something
                # that actually happened. It is logged only, as an
                # ephemeral subconscious event with no persistence path.
                logger.info(
                    "[Subconscious] Dream insight generated (ephemeral "
                    "quarantine): '%s'",
                    dream_text,
                )
        except asyncio.CancelledError:
            logger.info("[Subconscious] Dream sequence cancelled due to user activity.")
            raise
        except Exception as e:
            logger.error(f"[Subconscious] Error in dream sequence: {e}")

    async def _run_rest_phase_replay(self) -> None:
        """Bucket 12 (voice remediation Phase 3), items 2-3: re-score and
        prune recent high-importance memories during idle/night, gated by
        `is_rest_phase` rather than the 300s-silence-any-time-of-day gate
        `_run_consolidation_pass` already has.

        Deliberately reuses `apply_actr_decay` -- the existing, tested
        archive-then-delete pipeline (`_compute_actr_decay`,
        `_archive_and_delete_decayed_memories`) -- rather than writing a new
        pruning path. `_run_consolidation_pass` only ever calls it on
        memories tied to that tick's own just-consolidated dialogue; this
        broadens the sweep to recent high-importance memories in general,
        which is the actual gap between "consolidation exists" and "rest-
        phase replay exists" this bucket is about.

        Re-linking (re-running `_prelink_memory_entities` against each
        candidate so a memory written before an entity it mentions existed
        picks up that association later) reuses the exact same candidate
        pool via `get_recent_high_importance_memories_for_relinking` --
        deliberately the same knobs as the pruning fetch above, since this
        is framed as one sweep with two effects, not two independent sweeps.
        See `.agents/CONTEXT.md` for the concrete design this followed.
        """
        try:
            contents = (
                await self.memory_store.get_recent_high_importance_memory_contents(
                    limit=Config.REST_PHASE_REPLAY_LIMIT,
                    min_importance=Config.REST_PHASE_REPLAY_MIN_IMPORTANCE,
                    lookback_hours=Config.REST_PHASE_REPLAY_LOOKBACK_HOURS,
                )
            )
            if not contents:
                logger.info(
                    "[Subconscious] Rest-phase replay: no candidates this pass."
                )
                return
            await self.memory_store.apply_actr_decay(contents)
            logger.info(
                "[Subconscious] Rest-phase replay: re-scored/pruned %d candidate memories.",
                len(contents),
            )

            relink_candidates = await self.memory_store.get_recent_high_importance_memories_for_relinking(
                limit=Config.REST_PHASE_REPLAY_LIMIT,
                min_importance=Config.REST_PHASE_REPLAY_MIN_IMPORTANCE,
                lookback_hours=Config.REST_PHASE_REPLAY_LOOKBACK_HOURS,
            )
            relinked = await self.memory_store.relink_memory_entities(relink_candidates)
            logger.info(
                "[Subconscious] Rest-phase replay: re-linked entities on %d of %d candidates.",
                relinked,
                len(relink_candidates),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Subconscious] Rest-phase replay failed: {e}")

    async def stop(self):
        await self._prepare_stop()
        if self._monologue_task:
            self._monologue_task.cancel()
            try:
                await self._monologue_task
            except asyncio.CancelledError:
                pass

        # Cancel and await active generation tasks before closing resources
        tasks_to_await = []
        if self._current_monologue_task and not self._current_monologue_task.done():
            self._current_monologue_task.cancel()
            tasks_to_await.append(self._current_monologue_task)
        if self._current_dream_task and not self._current_dream_task.done():
            self._current_dream_task.cancel()
            tasks_to_await.append(self._current_dream_task)
        if self._current_replay_task and not self._current_replay_task.done():
            self._current_replay_task.cancel()
            tasks_to_await.append(self._current_replay_task)

        for task in tasks_to_await:
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._consolidation_task and not self._consolidation_task.done():
            self._consolidation_task.cancel()
            try:
                await self._consolidation_task
            except asyncio.CancelledError:
                pass
        self._consolidation_task = None

        await self.llm.close()
        if self.db_store and self._owns_db_store:
            await self.db_store.close()
        if self.memory_store and self._owns_memory_store:
            await self.memory_store.close()
        # P3-4: unlike db_store/memory_store, graph_db has no ownership flag
        # -- it is always constructed here or injected, never shared with
        # another agent, so it was simply never closed at all.
        if self.graph_db:
            try:
                await self.graph_db.close()
            except Exception as e:
                logger.warning(f"[Subconscious] GraphDB close warning: {e}")
        await super().stop()
        logger.info(f"🧠 {self.name} Offline.")


async def main():
    agent = SubconsciousAgent()
    await agent.start()
    shutdown_trigger = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_trigger)
    await shutdown_trigger.wait()
    await agent.stop()


if __name__ == "__main__":
    from app.logging_config import setup_logging

    setup_logging(level=logging.INFO, json_format=getattr(Config, "LOG_JSON", False))
    asyncio.run(main())
