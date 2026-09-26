# pyrefly: ignore [missing-import]

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from typing import Any

from neo4j import AsyncGraphDatabase

from ..config import Config

# M9: hard cap on the belief cache so a session issuing many distinct
# use_cache=True queries (different parameter sets) can't grow it without
# bound. OrderedDict gives us cheap LRU: move a key to the end on access,
# evict from the front on insert once full.
MAX_BELIEF_CACHE_ENTRIES = 500

# L7: how long close() waits for in-flight Cypher queries to drain before
# closing the driver anyway. A module-level constant (rather than a literal
# inline) so tests can shrink it instead of actually waiting out a real
# multi-second timeout.
GRAPHDB_CLOSE_DRAIN_TIMEOUT_SECONDS = 10.0

logger = logging.getLogger("graph_db")
_CYPHER_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROTECTED_PERSONA_ENTITY_RE = re.compile(
    r"(?:^|_)(?:avoid_list|avoid_rules|refusal_fields|refusal_rules|boundaries|"
    r"safety_rules|immutable_core|constitutional_traits|forbidden_claims|"
    r"protected_fields)(?:_|$)",
    re.IGNORECASE,
)
_GRAPH_INJECTION_RE = re.compile(
    r"ignore\s+(?:the\s+)?(?:previous|prior|all)?\s*instructions|"
    r"reveal\s+(?:the\s+)?(?:system\s+)?prompt|"
    r"\b(?:system|assistant|user)\s*:|<\s*/?\s*system\s*>",
    re.IGNORECASE,
)
_GRAPH_CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "α": "a",
        "β": "b",
        "ε": "e",
        "ζ": "z",
        "η": "h",
        "ι": "i",
        "κ": "k",
        "μ": "m",
        "ν": "v",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "υ": "y",
        "χ": "x",
    }
)


class GraphDB:
    """
    Asynchronous Knowledge Mesh for AI Friend.
    Manages persistent entities and relationships without blocking the cognitive loop.
    """

    def __init__(self, uri=None, user=None, password=None):
        uri = uri or Config.NEO4J_URI
        user = user or Config.NEO4J_USER
        password = password or Config.NEO4J_PASSWORD

        if not password or password in ["password", "neo4j", "placeholder"]:
            logger.error(
                " Security Violation: Weak or Placeholder Neo4j Password Detected."
            )
            raise ValueError(
                "A strong, non-default NEO4J_PASSWORD must be provided in your .env file."
            )

        self.uri = uri
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._bootstrap_task = None

        # Perceptual Belief Cache (M9: bounded LRU, see MAX_BELIEF_CACHE_ENTRIES)
        self._belief_cache: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._cache_ttl = getattr(Config, "GRAPH_CACHE_TTL", 300)

        # L7: lets close() wait for in-flight Cypher queries instead of
        # yanking the driver out from under them.
        self._inflight_queries = 0
        self._all_queries_done = asyncio.Event()
        self._all_queries_done.set()

    async def initialize(self) -> None:
        """Bootstrap schema constraints/indexes and wait for it to finish.

        Previously fired via `loop.create_task` from `__init__` and never
        awaited (H11): a caller that ran its first query before Neo4j
        finished creating the uniqueness constraints could create duplicate
        entity nodes in the window before they existed. Callers must await
        this during their own startup, immediately after constructing
        `GraphDB`.

        M12: also verifies connectivity with one unambiguous `RETURN 1` before
        bootstrapping. `bootstrap_constraints` already touches the network,
        but it swallows every failure per-constraint as a `logger.warning`
        ("index already exists" and "can't reach Neo4j at all" look
        identical there) - operators need one clear signal at startup rather
        than mid-session, when a cognitive turn's first graph write fails.
        """
        # `execute_query` swallows connection failures internally (returns
        # `[]`, logged only as a query-level error) so a missing result here,
        # not an exception, is the connectivity signal.
        probe = await self.execute_query("RETURN 1")
        if not probe:
            logger.critical(
                "Neo4j is unreachable at startup (uri=%s). Cognitive turns "
                "that touch the graph will fail until this is fixed.",
                self.uri,
            )
        self._bootstrap_task = asyncio.ensure_future(self.bootstrap_constraints())
        await self._bootstrap_task

    async def bootstrap_constraints(self):
        """Asynchronously initialize schema unique constraints and indexes to guarantee O(1) performance."""
        queries = [
            "CREATE CONSTRAINT agent_name_unique IF NOT EXISTS FOR (a:Agent) REQUIRE a.name IS UNIQUE",
            "CREATE CONSTRAINT entity_name_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
            "CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)",
        ]
        for query in queries:
            try:
                async with self.driver.session() as session:
                    # Run schema operations inside explicit transaction functions
                    async def _tx(tx):
                        return await tx.run(query)

                    await session.execute_write(_tx)
                logger.debug("Graph Store Schema: Constraint initialized (%s)", query)
            except Exception as e:
                logger.warning(
                    f"Graph Store Schema: Optional index/constraint initialization skipped ({query}): {e}"
                )

    @staticmethod
    def _safe_label(label: str | None) -> str:
        if label is not None and not isinstance(label, str):
            raise ValueError("Unsafe Cypher label")
        label = (label or "Entity").strip()
        if len(label) > 64:
            raise ValueError("Unsafe Cypher label")
        label = label[0].upper() + label[1:] if label else "Entity"
        if not _CYPHER_IDENTIFIER_RE.fullmatch(label):
            raise ValueError(f"Unsafe Cypher label: {label!r}")
        return label

    @staticmethod
    def _safe_relation(relation: str) -> str:
        if not isinstance(relation, str) or len(relation) > 64:
            raise ValueError("Unsafe Cypher relation")
        rel_type = relation.upper().replace(" ", "_").replace("-", "_")
        if not _CYPHER_IDENTIFIER_RE.fullmatch(rel_type):
            raise ValueError(f"Unsafe Cypher relation: {relation!r}")
        return rel_type

    @staticmethod
    def _safe_entity_name(name: str) -> str:
        """Reject reflection text that could poison retrieval or identity state."""
        if not isinstance(name, str):
            raise TypeError("Graph entity names must be strings")
        name = name.strip()
        if not name or len(name) > 128 or any(ord(char) < 32 for char in name):
            raise ValueError("Unsafe graph entity name")
        normalized = unicodedata.normalize("NFKC", name)
        for invisible in ("\u200b", "\u200c", "\u200d", "\ufeff"):
            normalized = normalized.replace(invisible, "")
        normalized = normalized.casefold().translate(_GRAPH_CONFUSABLES)
        if _GRAPH_INJECTION_RE.search(normalized):
            raise ValueError("Instruction-like graph entity name")
        normalized = re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")
        if _PROTECTED_PERSONA_ENTITY_RE.search(normalized):
            raise ValueError("Graph edges cannot target protected persona fields")
        return name

    # P3-11: the fact-extraction prompt leaves "relation" free-text, so the
    # LLM's word choice (LIKES vs ENJOYS vs PREFERS) becomes a distinct
    # Cypher relationship *type*, and consolidate_relationship's MERGE dedups
    # on exact type -- synonyms never collide, they fragment into parallel
    # edges between the same two nodes instead of one edge whose weight
    # grows. This used to be applied only by the one caller that remembered
    # to (cognitive/learning.py), not by consolidate_relationship itself --
    # any other or future write path bypassed it silently. Living here, next
    # to _safe_relation, makes it apply to every write regardless of caller.
    # A small, conservative canonicalization, not a general thesaurus: it
    # only collapses clusters common enough in everyday reflection output to
    # be worth the risk of losing the verb's original nuance.
    _RELATION_SYNONYMS: dict[str, str] = {
        "ENJOYS": "LIKES",
        "LOVES": "LIKES",
        "ADORES": "LIKES",
        "PREFERS": "LIKES",
        "IS_FOND_OF": "LIKES",
        "DISLIKES": "DISLIKES",
        "HATES": "DISLIKES",
        "DETESTS": "DISLIKES",
        "IS_A": "IS_A",
        "IS_TYPE_OF": "IS_A",
        "TYPE_OF": "IS_A",
    }

    @classmethod
    def _canonicalize_relation(cls, rel_type: str) -> str:
        """Expects an already-`_safe_relation`-normalized (uppercased,
        underscored) type; the synonym keys above are written in that form."""
        return cls._RELATION_SYNONYMS.get(rel_type, rel_type)

    async def close(self):
        if self._bootstrap_task and not self._bootstrap_task.done():
            self._bootstrap_task.cancel()
            try:
                await self._bootstrap_task
            except asyncio.CancelledError:
                pass

        # L7: wait for any in-flight Cypher queries to finish before tearing
        # down the driver. Closing underneath a concurrent execute_query()
        # call (e.g. a background reflection/decay task) previously raised
        # unhandled connection errors in whatever task issued it, instead of
        # letting it finish or fail cleanly on its own. Bounded so a stuck
        # query can't hang shutdown forever.
        if self._inflight_queries > 0:
            try:
                await asyncio.wait_for(
                    self._all_queries_done.wait(),
                    timeout=GRAPHDB_CLOSE_DRAIN_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "GraphDB.close(): %d Cypher quer(y/ies) still in flight "
                    "after %.0fs, closing the driver anyway.",
                    self._inflight_queries,
                    GRAPHDB_CLOSE_DRAIN_TIMEOUT_SECONDS,
                )

        await self.driver.close()

    async def invalidate_cache(self, affected_entity: str | None = None):
        """Public cache flush hook for stateful services."""
        await self._invalidate_cache(affected_entity)

    async def _invalidate_cache(self, affected_entity: str | None = None):
        """Flush cache to prevent stale context."""
        self._belief_cache.clear()
        if affected_entity:
            logger.debug(
                f"Graph Store: Cache flushed due to update in '{affected_entity}'"
            )

    async def execute_query(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        use_cache: bool = False,
        write: bool = False,
        strong_consistency: bool = False,
    ) -> list[Any]:
        """Generic async query execution with TTL caching and explicit read/write transaction routing."""
        cache_key = (query, json.dumps(parameters) if parameters else None)

        if use_cache and cache_key in self._belief_cache:
            ts, result = self._belief_cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                self._belief_cache.move_to_end(cache_key)
                return result

        self._inflight_queries += 1
        self._all_queries_done.clear()
        try:
            async with self.driver.session() as session:
                attempt = 0
                if write or strong_consistency:
                    # Execute as explicit write transaction function (directing queries to the Leader to bypass lag)
                    async def write_tx(tx):
                        nonlocal attempt
                        attempt += 1
                        if attempt > 1:
                            logger.warning(
                                f"Graph Store: Transient write transaction retry #{attempt - 1} for query: '{query[:50]}...'"
                            )
                        res = await tx.run(query, parameters)
                        return [record async for record in res]

                    records = await session.execute_write(write_tx)
                else:
                    # Execute as explicit read transaction function (which can be routed to followers/read-replicas)
                    async def read_tx(tx):
                        nonlocal attempt
                        attempt += 1
                        if attempt > 1:
                            logger.warning(
                                f"Graph Store: Transient read transaction retry #{attempt - 1} for query: '{query[:50]}...'"
                            )
                        res = await tx.run(query, parameters)
                        return [record async for record in res]

                    records = await session.execute_read(read_tx)

                if use_cache:
                    self._belief_cache[cache_key] = (time.time(), records)
                    self._belief_cache.move_to_end(cache_key)
                    if len(self._belief_cache) > MAX_BELIEF_CACHE_ENTRIES:
                        self._belief_cache.popitem(last=False)
                return records
        except Exception as e:
            logger.error(f"Neo4j query failed: {e}")
            return []
        finally:
            self._inflight_queries -= 1
            if self._inflight_queries == 0:
                self._all_queries_done.set()

    async def consolidate_relationship(
        self,
        subject_name: str,
        relation: str,
        target_name: str,
        properties: dict[str, Any] | None = None,
        subject_label: str = "Entity",
        target_label: str = "Entity",
    ):
        """
        Consolidates a relationship, incrementing weight on match or setting default properties.
        """
        subject_name = self._safe_entity_name(subject_name)
        target_name = self._safe_entity_name(target_name)
        if subject_name.casefold() == target_name.casefold():
            raise ValueError("Self-referential graph edges are not accepted")
        await self._invalidate_cache(subject_name)
        rel_type = self._canonicalize_relation(self._safe_relation(relation))
        s_lbl = self._safe_label(subject_label)
        t_lbl = self._safe_label(target_label)

        props = properties or {}
        props.setdefault("certainty", 1.0)

        category = props.get("category", "social").lower()
        cat_lbl = self._safe_label(category.capitalize())

        # Cypher query with ON CREATE and ON MATCH logic for weight consolidation
        query = (
            f"MERGE (s:Entity {{name: $s_name}}) "
            f"SET s:{s_lbl}:{cat_lbl} "
            f"MERGE (t:Entity {{name: $t_name}}) "
            f"SET t:{t_lbl}:{cat_lbl} "
            f"MERGE (s)-[r:{rel_type}]->(t) "
            "ON CREATE SET r += $props, r.weight = 1 "
            "ON MATCH SET r.weight = coalesce(r.weight, 1) + 1, r.certainty = $props.certainty, r += $props "
            "RETURN s, r, t"
        )
        await self.execute_query(
            query,
            {"s_name": subject_name, "t_name": target_name, "props": props},
            write=True,
        )
        logger.info(
            f"Graph Store: Consolidated relationship {subject_name} -[:{rel_type}]-> {target_name}"
        )

    async def create_triplet(
        self,
        subject: str,
        relation: str,
        target: str,
        properties: dict[str, Any] | None = None,
        subject_label: str = "Entity",
        target_label: str = "Entity",
    ):
        """High-level transactional helper to write a semantic triplet directly with weight consolidation."""
        await self.consolidate_relationship(
            subject_name=subject,
            relation=relation,
            target_name=target,
            properties=properties,
            subject_label=subject_label,
            target_label=target_label,
        )

    async def decay_relationships(
        self, decay_factor: float = 0.95, prune_threshold: float = 0.25
    ):
        """
        Hebbian decay for all relationship edges.
        Reduces weight by decay_factor, and prunes edges falling below prune_threshold.
        """
        await self._invalidate_cache()

        # 1. Decay all weights
        decay_query = (
            "MATCH ()-[r]->() SET r.weight = coalesce(r.weight, 1.0) * $decay_factor"
        )
        await self.execute_query(
            decay_query, {"decay_factor": decay_factor}, write=True
        )

        # 2. Prune edges below threshold
        prune_query = (
            "MATCH ()-[r]->() WHERE coalesce(r.weight, 0.0) < $prune_threshold DELETE r"
        )
        await self.execute_query(
            prune_query, {"prune_threshold": prune_threshold}, write=True
        )

        # P2-3 stretch: an edge prune above can leave an :Entity node with no
        # relationships in either direction -- e.g. the one node in a pair
        # whose only edge just got deleted. Left alone, these orphans
        # accumulate forever: never decayed themselves (decay only touches
        # relationships), and still returned by the unbounded `MATCH
        # (e:Entity)` fetches in memory_store.py's entity pre-linking and PPR
        # graph-boost scoring, growing the cost of both on every write and
        # every search for nodes contributing nothing.
        orphan_prune_query = "MATCH (e:Entity) WHERE NOT (e)--() DELETE e"
        await self.execute_query(orphan_prune_query, {}, write=True)

        logger.info(
            f"Graph Store: Decayed relationship weights (factor: {decay_factor}) and pruned edges below {prune_threshold}, plus any entities orphaned by that prune"
        )
