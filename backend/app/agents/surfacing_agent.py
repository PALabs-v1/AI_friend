"""
Surfacing Agent — Episodic + Semantic Memory (psychological_layer.md §6-7).

Two memory channels:
  1. Episodic (pgvector): ACT-R scored, mood-congruent recall (Bower, 1981)
  2. Semantic (Neo4j):   Structured facts/relationships from the knowledge graph

The agent alternates between channels to provide the cognitive core with both
"I remember when..." (episodic) and "I know that..." (semantic) context.
"""

import asyncio
import logging
import time
from datetime import UTC
from typing import Any

import cognitive_rust

from ..contracts import (
    AgentVoiceModulation,
    MemoryScope,
    MemorySurfaced,
    ProsodyFrame,
    SurfacedMemory,
    Topics,
)
from ..metrics import SubjectMetrics
from ..state import ConversationHistoryStore, GraphDB, MemoryStore
from .base import BaseAgent, install_shutdown_signal_handlers

logger = logging.getLogger("surfacing_agent")


class SurfacingAgent(BaseAgent):
    """
    Active Memory Influence Agent.
    Asynchronously evaluates long-term memory and 'surfaces' relevant context
    as mesh events for current cognition.

    Dual-channel retrieval:
      - Episodic: pgvector similarity + ACT-R activation + emotional alignment
      - Semantic: Neo4j entity/relationship lookup for structured knowledge
    """

    def __init__(self, memory_store=None, graph_db=None, conversation_store=None):
        super().__init__(name="surfacing_agent")
        self.memory = memory_store
        self.graph = graph_db
        self.conversation_store = conversation_store
        self.last_context = ""
        self.surfacing_cooldown = 30  # Seconds between surfacing events
        self.min_sweep_interval = 5
        self.surface_novelty_window = 300
        self.last_surfaced_time = 0
        self.last_sweep_attempt = 0
        self.recently_surfaced = {}
        self._sweep_task: asyncio.Task | None = None

        # Dual-channel state
        self._last_channel = "episodic"  # Alternate between channels
        self._current_valence = 0.0  # For mood-congruent retrieval
        self._current_arousal = 0.5
        self._current_cortisol = 0.0

        # P3-2: shared implementation instead of a hand-rolled dict -- see
        # app/metrics.py. A distinct attribute from BaseAgent's own
        # `self._metrics` (set by super().__init__() above): that one tracks
        # generic mesh-level publish/rx counts, this one tracks this class's
        # own business-logic subjects -- reusing the name would silently
        # overwrite the base tracker instead of adding a second one.
        self._surfacing_metrics = SubjectMetrics(
            tracked_subjects={"system.tick", "memory.surfaced"},
            log_every=20,
            tag="SurfacingMetrics",
        )

    async def start(self):
        await self.connect()
        await self.subscribe(
            "chat.input",
            self._on_chat_input,
            durable=f"{self.name}_chat_input_live",
            deliver_policy="new",
        )
        await self.subscribe(
            "system.tick",
            self._on_system_tick,
            durable=f"{self.name}_system_tick_live",
            deliver_policy="new",
        )
        # Subscribe to state broadcasts to track current valence for mood-congruent recall
        await self.subscribe(
            "state.update",
            self._on_agent_state,
            durable=f"{self.name}_state_update_live",
            deliver_policy="new",
        )
        # Brain V2 (ADR-001): warm the SQLite retrieval index off the
        # critical path so the first surfacing sweep is not the one to pay.
        warm = getattr(self.memory, "warm_retrieval_index", None)
        if asyncio.iscoroutinefunction(warm):
            self._retrieval_warmup_task = asyncio.create_task(warm())
        logger.info(f"🧠 {self.name} Online | Dual-Channel Memory Surfacing Active.")

    async def _on_chat_input(self, data: dict[str, Any], metadata: dict | None = None):
        """Update recent context tracking."""
        self.last_context = data.get("text", "")
        msg_metadata = data.get("metadata") or {}
        is_benchmark = msg_metadata.get("benchmark_id") == "bench_pulse"
        if is_benchmark or (time.time() - self.last_surfaced_time > 10):
            source_meta = metadata or data.get("latency_metadata") or msg_metadata
            self._schedule_sweep(source_metadata=source_meta, force=is_benchmark)

    async def _on_system_tick(self, data: dict[str, Any]):
        """Periodic background sweep for memory relevance."""
        self._record_surfacing_metric(
            "system.tick", metadata=data.get("latency_metadata")
        )
        if time.time() - self.last_surfaced_time > self.surfacing_cooldown:
            await self._run_sweep_now(source_metadata=data.get("latency_metadata"))

    async def _on_agent_state(self, data: dict[str, Any]):
        """Track current valence, arousal, and cortisol, and calculate APRA vocal modulations."""
        if isinstance(data, dict):
            self._current_valence = data.get(
                "valence", data.get("mood", self._current_valence)
            )
            self._current_arousal = data.get(
                "arousal", data.get("energy", self._current_arousal)
            )
            self._current_cortisol = data.get("cortisol", self._current_cortisol)

            # Map PAD scores to voice parameters (APRA Formulas)
            valence = self._current_valence
            arousal = self._current_arousal
            dominance = data.get("dominance", 0.5)
            fatigue = data.get("fatigue", 0.0)
            # Bucket 10 (revised scope): the endocrine channels widen this
            # from a PAD-only trajectory to one that also carries *why* the
            # agent is in that state -- see generate_apra_trajectory's own
            # comment in cognitive-rust for the per-hormone acoustic design.
            cortisol = self._current_cortisol
            dopamine = data.get("dopamine", 0.0)
            adrenaline = data.get("adrenaline", 0.0)

            # Generate continuous trajectory using Rust PyO3
            trajectory_tuples = cognitive_rust.generate_apra_trajectory(
                valence, arousal, dominance, fatigue, cortisol, dopamine, adrenaline
            )

            trajectory = [
                ProsodyFrame(time_offset_ms=t_ms, rate=r, pitch=p, volume=v)
                for t_ms, r, p, v in trajectory_tuples
            ]

            modulation = AgentVoiceModulation(
                trajectory=trajectory,
                timestamp=time.time(),
            )
            self.spawn(
                self.publish(
                    Topics.AGENT_VOICE_MODULATION.value, modulation.model_dump()
                )
            )

    async def _run_sweep_now(
        self, source_metadata: dict[str, Any] | None = None, force: bool = False
    ):
        """Run a sweep inline (used by low-frequency control channels like system.tick)."""
        now = time.time()
        if self._sweep_task is not None and not self._sweep_task.done():
            return
        if not force and (now - self.last_sweep_attempt) < self.min_sweep_interval:
            return

        self.last_sweep_attempt = now
        self._sweep_task = asyncio.create_task(
            self._surface_relevant_memories(source_metadata=source_metadata)
        )
        await self._sweep_task

    def _schedule_sweep(
        self, source_metadata: dict[str, Any] | None = None, force: bool = False
    ):
        """Run at most one surfacing sweep at a time and throttle retry storms."""
        now = time.time()
        if self._sweep_task is not None and not self._sweep_task.done():
            return
        if not force and (now - self.last_sweep_attempt) < self.min_sweep_interval:
            return

        self.last_sweep_attempt = now
        self._sweep_task = asyncio.create_task(
            self._surface_relevant_memories(source_metadata=source_metadata)
        )

    async def _surface_relevant_memories(
        self, source_metadata: dict[str, Any] | None = None
    ):
        """
        Dual-Channel Surfacing Logic (§6-7):
        1. Episodic channel: pgvector ACT-R + mood-congruent retrieval
        2. Semantic channel: Neo4j entity/relationship lookup
        3. Publish 'memory.surfaced' events with source tagging
        """
        if not self.last_context:
            return

        try:
            sweep_started = time.perf_counter()
            now = time.time()
            self._prune_recently_surfaced(now)

            # Alternate channels to balance episodic and semantic recall
            if self._last_channel == "episodic":
                surfaced = await self._surface_semantic(now, source_metadata)
                if surfaced:
                    self._last_channel = "semantic"
                else:
                    # Fallback to episodic if semantic yields nothing
                    surfaced = await self._surface_episodic(now, source_metadata)
                    if surfaced:
                        self._last_channel = "episodic"
            else:
                surfaced = await self._surface_episodic(now, source_metadata)
                if surfaced:
                    self._last_channel = "episodic"
                else:
                    surfaced = await self._surface_semantic(now, source_metadata)
                    if surfaced:
                        self._last_channel = "semantic"

            if surfaced:
                total_ms = (time.perf_counter() - sweep_started) * 1000
                logger.info(
                    "[SurfacingMetrics] channel=%s total_ms=%.2f",
                    self._last_channel,
                    total_ms,
                )

        except Exception as e:
            logger.error(f"[Surfacing] Error in background sweep: {e}")

    # ── Episodic Channel (pgvector + ACT-R) ──

    async def _surface_episodic(
        self, now: float, source_metadata: dict[str, Any] | None = None
    ) -> bool:
        """
        Episodic retrieval via pgvector with ACT-R scoring and
        mood-congruent recall (Bower, 1981 — §6.4).

        Surfaces memories as narrative episodes (Tulving, 1972):
        not just "User likes coding" but "Late night debugging session,
        felt stressed but bonded over solving the routing issue."
        """
        if not self.memory:
            return False

        search_started = time.perf_counter()
        memories = await self.memory.search_memories(
            self.last_context,
            wing="personal",  # Default wing for conversation history
            limit=3,
            refresh_on_recall=False,
            exclude_contents=list(self.recently_surfaced.keys()),
            current_valence=self._current_valence,
            current_arousal=self._current_arousal,
            current_cortisol=self._current_cortisol,
        )
        search_ms = (time.perf_counter() - search_started) * 1000

        for mem in memories:
            content = mem.get("content")
            if content and not self._was_recently_surfaced(content, now):
                # Build a narrative episode from the enriched metadata
                episode = self._build_episode_narrative(mem, now)

                # Validate and publish via AI Friend Contracts
                surfaced_msg = MemorySurfaced(
                    memories=[
                        SurfacedMemory(
                            content=episode["narrative"],
                            raw_content=episode["raw_content"],
                            scope=MemoryScope(
                                wing=mem.get("wing", "personal"),
                                room=mem.get("room"),
                            ),
                            # Search results carry `relevance` (clipped
                            # cosine, absolute); `score` is pool-relative
                            # (hybrid) or an unbounded activation sum (V1),
                            # and the brain reads this field as relevance
                            # (decision thresholds).
                            score=mem.get("relevance", mem.get("score", 0.0)),
                            valence=episode["valence"],
                            created_at=episode["created_at"],
                            recall_count=episode["recall_count"],
                            metadata=episode["metadata"],
                        )
                    ],
                    source="episodic",
                    provenance="pgvector_actr",
                    context=self.last_context,
                )

                publish_started = time.perf_counter()
                await self.publish(
                    "memory.surfaced",
                    surfaced_msg.model_dump(),
                    metadata=source_metadata,
                )
                publish_ms = (time.perf_counter() - publish_started) * 1000

                self.last_surfaced_time = now
                self.recently_surfaced[content] = now
                self._record_surfacing_metric(
                    "memory.surfaced", metadata=source_metadata
                )
                logger.info(
                    "[Surfacing] Episodic recall: '%s...' (search=%.1fms pub=%.1fms)",
                    episode["narrative"][:60],
                    search_ms,
                    publish_ms,
                )
                return True

        return False

    def _build_episode_narrative(
        self, mem: dict[str, Any], now: float
    ) -> dict[str, Any]:
        """
        Transforms a raw memory row into a Tulving-style episode with
        temporal context, emotional color, and narrative framing.

        This is what allows the LLM to say:
          "Remember last week when we were up until 3 AM debugging?"
        instead of:
          "You mentioned debugging."
        """
        content = mem.get("content", "")
        created_at = mem.get("created_at")
        valence = mem.get("valence", 0.0)
        recall_count = mem.get("recall_count", 1)
        meta = mem.get("metadata", {})

        # Temporal context: "earlier today", "a few days ago", "last week"
        time_label = self._temporal_label(created_at, now)

        # Emotional color: what the mood was during this memory
        if valence > 0.3:
            mood_label = "a good moment"
        elif valence < -0.3:
            mood_label = "a tough time"
        else:
            mood_label = "a conversation"

        # Familiarity: how often this has come up
        if recall_count > 10:
            familiarity = "something we keep coming back to"
        elif recall_count > 3:
            familiarity = "something that's come up before"
        else:
            familiarity = None

        # Build the narrative string for the LLM
        parts = []
        if time_label:
            parts.append(f"[{time_label}]")
        parts.append(content)
        if familiarity:
            parts.append(f"({familiarity})")

        narrative = " ".join(parts)

        return {
            "narrative": narrative,
            "raw_content": content,
            "time_label": time_label,
            "mood_label": mood_label,
            "valence": valence,
            "recall_count": recall_count,
            "created_at": created_at,
            "metadata": meta,
        }

    @staticmethod
    def _temporal_label(created_at_iso: str | None, now: float) -> str:
        """Converts an ISO timestamp into a human-readable relative label."""
        if not created_at_iso:
            return ""
        try:
            from datetime import datetime

            created = datetime.fromisoformat(created_at_iso)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            now_dt = datetime.fromtimestamp(now, tz=UTC)
            delta = now_dt - created
            hours = delta.total_seconds() / 3600

            if hours < 1:
                return "just now"
            if hours < 6:
                return "earlier today"
            if hours < 24:
                return "today"
            if hours < 48:
                return "yesterday"
            if hours < 168:
                days = int(hours / 24)
                return f"{days} days ago"
            if hours < 720:
                weeks = int(hours / 168)
                return f"{'last week' if weeks == 1 else f'{weeks} weeks ago'}"
            months = int(hours / 720)
            return f"{'last month' if months == 1 else f'{months} months ago'}"
        except (ValueError, TypeError):
            return ""

    # ── Semantic Channel (Neo4j Knowledge Graph) ──

    async def _surface_semantic(
        self, now: float, source_metadata: dict[str, Any] | None = None
    ) -> bool:
        """
        Semantic retrieval from the Neo4j knowledge graph.
        Extracts structured facts about entities mentioned in the current context.

        Tulving (1972): Semantic memory = general knowledge, facts, concepts
        (vs episodic = specific events with temporal context).
        """
        if not self.graph:
            return False

        # Extract candidate entity names from the context (simple word-based extraction)
        entities = self._extract_entity_candidates(self.last_context)
        if not entities:
            return False

        try:
            search_started = time.perf_counter()
            facts = await self._query_related_facts(entities)
            search_ms = (time.perf_counter() - search_started) * 1000

            for fact_text in facts:
                if not self._was_recently_surfaced(fact_text, now):
                    surfaced_msg = MemorySurfaced(
                        memories=[
                            SurfacedMemory(
                                content=fact_text,
                                raw_content=fact_text,
                                scope=MemoryScope(wing="knowledge"),
                                score=0.8,
                            )
                        ],
                        source="semantic",
                        provenance="neo4j_graph",
                        context=self.last_context,
                    )

                    publish_started = time.perf_counter()
                    await self.publish(
                        "memory.surfaced",
                        surfaced_msg.model_dump(),
                        metadata=source_metadata,
                    )
                    publish_ms = (time.perf_counter() - publish_started) * 1000

                    self.last_surfaced_time = now
                    self.recently_surfaced[fact_text] = now
                    self._record_surfacing_metric(
                        "memory.surfaced", metadata=source_metadata
                    )
                    logger.info(
                        "[Surfacing] Semantic recall: '%s...' (search=%.1fms pub=%.1fms)",
                        fact_text[:40],
                        search_ms,
                        publish_ms,
                    )
                    return True

        except Exception as e:
            logger.error(f"[Surfacing] Semantic query failed: {e}")

        return False

    def _extract_entity_candidates(self, text: str) -> list[str]:
        """
        Lightweight entity extraction from context text.
        Uses capitalized words as candidate proper nouns.
        Filters out common stop words and short tokens.
        """
        stop_words = {
            "i",
            "me",
            "my",
            "you",
            "your",
            "we",
            "our",
            "they",
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "am",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "can",
            "may",
            "might",
            "shall",
            "not",
            "no",
            "but",
            "and",
            "or",
            "if",
            "then",
            "so",
            "what",
            "when",
            "where",
            "how",
            "why",
            "who",
            "which",
            "that",
            "this",
            "it",
            "its",
            "just",
            "also",
            "very",
            "really",
            "about",
            "with",
            "from",
            "into",
            "for",
            "of",
            "on",
            "in",
            "at",
            "to",
            "by",
            "up",
            "out",
            "hey",
            "hello",
            "hi",
            "ok",
            "yeah",
            "yes",
            "oh",
            "ah",
        }

        words = text.split()
        candidates = []
        for word in words:
            clean = word.strip(".,!?;:'\"()[]{}").strip()
            if len(clean) < 2:
                continue
            # Capitalized words (potential proper nouns)
            if clean[0].isupper() and clean.lower() not in stop_words:
                candidates.append(clean)

        return list(dict.fromkeys(candidates))[:5]  # Deduplicate, limit to 5

    async def _query_related_facts(self, entities: list[str]) -> list[str]:
        """
        Query Neo4j for all relationships involving the given entity names.
        Returns human-readable fact strings.
        """
        if not entities:
            return []

        query = """
        MATCH (s)-[r]->(t)
        WHERE s.name IN $names OR t.name IN $names
        RETURN s.name AS subject, type(r) AS relation, t.name AS object,
               coalesce(r.confidence, 1.0) AS confidence
        ORDER BY coalesce(r.confidence, 1.0) DESC
        LIMIT 5
        """
        results = await self.graph.execute_query(
            query, {"names": entities}, use_cache=True
        )

        facts = []
        for record in results:
            subj = record.get("subject", record.get("s.name", "?"))
            rel = record.get("relation", record.get("type(r)", "?"))
            obj = record.get("object", record.get("t.name", "?"))

            # Convert UPPER_SNAKE_CASE relation to readable form
            readable_rel = rel.replace("_", " ").lower()
            fact_text = f"{subj} {readable_rel} {obj}"
            facts.append(fact_text)

        return facts

    # ── Shared infrastructure ──

    def _prune_recently_surfaced(self, now: float):
        stale = [
            content
            for content, surfaced_at in self.recently_surfaced.items()
            if (now - surfaced_at) >= self.surface_novelty_window
        ]
        for content in stale:
            self.recently_surfaced.pop(content, None)

    def _was_recently_surfaced(self, content: str, now: float) -> bool:
        surfaced_at = self.recently_surfaced.get(content)
        if surfaced_at is None:
            return False
        return (now - surfaced_at) < self.surface_novelty_window

    def _record_surfacing_metric(
        self, subject: str, metadata: dict[str, Any] | None = None
    ):
        start_time = metadata.get("start_time") if isinstance(metadata, dict) else None
        self._surfacing_metrics.record(
            subject,
            direction="surfacing",
            latency_ms=SubjectMetrics.compute_latency(start_time),
        )

    async def stop(self):
        await self._prepare_stop()
        if self._sweep_task and not self._sweep_task.done():
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            finally:
                self._sweep_task = None

        if self.graph:
            try:
                await self.graph.close()
            except Exception as e:
                logger.warning(f"[Surfacing] GraphDB close warning: {e}")

        if self.conversation_store:
            try:
                await self.conversation_store.close()
            except Exception as e:
                logger.warning(f"[Surfacing] Conversation store close warning: {e}")

        # P3-4: self.memory (MemoryStore) held its own HTTP client
        # (get_embedding's network calls) and was never closed here at all.
        if self.memory:
            try:
                await self.memory.close()
            except Exception as e:
                logger.warning(f"[Surfacing] MemoryStore close warning: {e}")

        # P3-4: a second SubjectMetrics instance, deliberately named
        # `_surfacing_metrics` rather than `_metrics` (P3-2) so it would not
        # silently clobber BaseAgent's own -- but that also means
        # BaseAgent.stop()'s `self._metrics.shutdown()` never reaches this
        # one; it needs its own call.
        self._surfacing_metrics.shutdown()

        await super().stop()
        logger.info(f"🧠 {self.name} Offline.")


async def main():
    conversation_store = ConversationHistoryStore()
    await conversation_store.initialize()

    graph_db = GraphDB()
    await graph_db.initialize()
    memory_store = MemoryStore(pool=conversation_store.pool, graph_db=graph_db)

    agent = SurfacingAgent(
        memory_store=memory_store,
        graph_db=graph_db,
        conversation_store=conversation_store,
    )

    await agent.start()
    shutdown_trigger = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_trigger)
    await shutdown_trigger.wait()
    await agent.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
