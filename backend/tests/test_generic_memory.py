import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Config
from app.state.memory_store import MemoryStore


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.acquire = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, conn


def _make_row(content, similarity=0.8):
    return {
        "content": content,
        "raw_content": content,
        "wing": "personal",
        "room": "social",
        "importance_score": 0.9,
        "emotional_weight": 0.5,
        "valence": 0.5,
        "recall_count": 1,
        "last_recalled_at": datetime.now(UTC),
        "created_at": datetime.now(UTC),
        "metadata": "{}",
        "similarity": similarity,
    }


@pytest.mark.usefixtures("actr_v1_ranking")
def test_context_aware_pronoun_mapping_user_speaking(mock_pool):
    """
    Verify that when user_id = 'Raj' and is_self_reflection = False,
    first-person pronouns resolve to 'Raj' and second-person to the agent name.
    """
    pool, conn = mock_pool
    # A candidate memory referring to Raj and Aniket
    rows = [
        _make_row("I chatted with Raj about his work in the workspace."),
        _make_row("Aniket played cricket yesterday."),
    ]
    conn.fetch.return_value = rows

    mock_graph = MagicMock()
    seed_params = {}

    # Mock Neo4j graph nodes and relations
    async def mock_execute_query(query, *args, **kwargs):
        if "MATCH (seed:Entity)" in query:
            params = args[0] if args else kwargs.get("parameters", {})
            seed_params.update(params)
            identity_names = {
                str(name).lower() for name in params.get("identity_names", [])
            }
            identities = [
                {
                    "name": Config.AI_NAME,
                    "description": "The central cognitive system.",
                },
                {"name": "Raj", "description": "User / Companion"},
            ]
            return [row for row in identities if row["name"].lower() in identity_names]
        elif "MATCH (s:Entity)-[r]-(t:Entity)" in query:
            return [{"source": Config.AI_NAME, "target": "Raj"}]
        return []

    mock_graph.execute_query = mock_execute_query

    store = MemoryStore(pool, mock_graph)
    store.qdrant_store.client = None

    with patch.object(store, "get_embedding", return_value=[0.1] * 768):
        # User is speaking: "What did I do yesterday?" -> "I" resolves to "Raj"
        results = asyncio.run(
            store.search_memories(
                query_text="What is my favorite drink?",
                user_id="Raj",
                is_self_reflection=False,
                threshold=-10.0,
                limit=2,
            )
        )
        assert len(results) > 0

        # Also verify second-person maps to agent: "Where were you?" -> "you" maps to the configured identity.
        results_you = asyncio.run(
            store.search_memories(
                query_text="Where were you?",
                user_id="Raj",
                is_self_reflection=False,
                threshold=-10.0,
                limit=2,
            )
        )
        assert len(results_you) > 0

    assert seed_params["has_pronoun"] is True
    assert {"raj", Config.AI_NAME.lower()} <= {
        str(name).lower() for name in seed_params["identity_names"]
    }


def test_context_aware_pronoun_mapping_self_reflection(mock_pool):
    """
    Verify that when is_self_reflection = True,
    first-person pronouns resolve to the agent itself.
    """
    pool, conn = mock_pool
    rows = [_make_row("Aniket walked in the garden alone.")]
    conn.fetch.return_value = rows

    mock_graph = MagicMock()

    async def mock_execute_query(query, *args, **kwargs):
        if "MATCH (seed:Entity)" in query:
            params = args[0] if args else kwargs.get("parameters", {})
            identity_names = {
                str(name).lower() for name in params.get("identity_names", [])
            }
            identities = [
                {
                    "name": Config.AI_NAME,
                    "description": "The central cognitive system.",
                },
                {"name": "Raj", "description": "User / Companion"},
            ]
            return [row for row in identities if row["name"].lower() in identity_names]
        elif "MATCH (s:Entity)-[r]-(t:Entity)" in query:
            return []
        return []

    mock_graph.execute_query = mock_execute_query

    store = MemoryStore(pool, mock_graph)
    store.qdrant_store.client = None

    with patch.object(store, "get_embedding", return_value=[0.1] * 768):
        # Agent is self-reflecting: "Where did I walk?" -> "I" resolves to "Aniket"
        results = asyncio.run(
            store.search_memories(
                query_text="Where did I walk?",
                user_id="Raj",
                is_self_reflection=True,
                threshold=-10.0,
                limit=1,
            )
        )
        assert len(results) > 0


@pytest.mark.usefixtures("actr_v1_ranking")
def test_entity_and_relation_fetches_are_query_scoped(mock_pool):
    """Graph retrieval expands only from entities relevant to the query."""
    pool, conn = mock_pool
    conn.fetch.return_value = []

    queries_run = []

    async def mock_execute_query(query, *args, **kwargs):
        params = args[0] if args else kwargs.get("parameters")
        queries_run.append((query, params))
        if query.startswith("MATCH (seed:Entity)"):
            return [{"name": "anything", "description": "query entity"}]
        return []

    mock_graph = MagicMock()
    mock_graph.execute_query = mock_execute_query

    store = MemoryStore(pool, mock_graph)
    store.qdrant_store.client = None

    with patch.object(store, "get_embedding", return_value=[0.1] * 768):
        asyncio.run(store.search_memories(query_text="anything", threshold=-10.0))

    entity_queries = [
        (q, p) for q, p in queries_run if q.startswith("MATCH (seed:Entity)")
    ]
    relation_queries = [
        (q, p)
        for q, p in queries_run
        if q.startswith("MATCH (s:Entity)-[r]-(t:Entity)")
    ]
    assert entity_queries, "entity fetch query never ran"
    assert relation_queries, "relation fetch query never ran"
    entity_query, entity_params = entity_queries[0]
    relation_query, relation_params = relation_queries[0]
    assert "LIMIT" not in entity_query.upper()
    assert "LIMIT" not in relation_query.upper()
    assert entity_params["query_text"] == "anything"
    assert "query_terms" in entity_params
    assert "entity_names" in relation_params
