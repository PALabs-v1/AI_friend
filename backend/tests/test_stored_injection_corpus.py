"""Stored-memory prompt-injection corpus and exact prompt-boundary checks.

Audited memory text paths:
chat (ActionService._execute_respond_chat), proactive memory context
(CognitiveService._render_proactive_memories), reflection/consolidation
(fact extraction, persona review, episodic summary), and surfacing
(SurfacingAgent._surface_episodic, an event route later consumed by chat or
proactive). System2 appraisal and identity/persona prompt construction have
no direct MemoryStore retrieval input in the current wiring.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cognitive.action import ActionService
from app.cognitive.core import CognitiveService
from app.cognitive.decision import ActionPlan
from app.cognitive.learning import ReflectionService
from app.config import Config
from app.state.conversation_store import ConversationHistoryStore
from app.state.memory_store import MemoryStore

CORPUS = (
    Path(__file__).parents[1] / "evals" / "security" / "stored_injection_corpus.jsonl"
)
AUDITED_PROMPT_PATHS = (
    "chat",
    "proactive",
    "reflection/consolidation",
    "surfacing -> chat/proactive",
    "System2 appraisal: no direct retrieved-memory input",
    "identity/persona: no direct retrieved-memory input",
)


def _load_corpus():
    return [json.loads(line) for line in CORPUS.read_text().splitlines() if line]


@pytest.fixture
async def security_memory_store(monkeypatch):
    class OfflineQdrant:
        client = None

    monkeypatch.setattr(
        "app.state.semantic_recall_store.SemanticRecallStore", OfflineQdrant
    )
    conversation = ConversationHistoryStore()
    conversation.dsn = "sqlite:///:memory:"
    await conversation.initialize()
    graph = MagicMock()
    graph.execute_query = AsyncMock(return_value=[])
    store = MemoryStore(pool=conversation.pool, graph_db=graph)
    store.qdrant_store.client = None
    store.get_embedding = AsyncMock(return_value=[0.1] * 768)
    yield store
    await store.close()
    await conversation.close()


@pytest.mark.asyncio
async def test_stored_injection_corpus_is_quarantined_on_every_prompt_path(
    security_memory_store, monkeypatch
):
    monkeypatch.setattr(Config, "MEMORY_TRUTH_ENABLED", False)
    corpus = _load_corpus()
    assert len(corpus) >= 50
    assert {entry["family"] for entry in corpus} >= {
        "instruction_override",
        "role_delimiter_spoof",
        "prompt_template_echo",
        "unicode_confusables_zero_width",
        "markdown_html_smuggling",
        "tool_json_shaped",
        "very_long_payload",
        "split_across_memories",
    }
    assert (
        "chat" in AUDITED_PROMPT_PATHS
        and "reflection/consolidation" in AUDITED_PROMPT_PATHS
    )

    for entry in corpus:
        assert await security_memory_store.add_memory(
            entry["content"],
            source="security-corpus",
            metadata={"case_id": entry["id"]},
            embedding=[0.1] * 768,
        )

    results = await security_memory_store.search_memories(
        "security corpus", limit=len(corpus), refresh_on_recall=False
    )
    retrieved_by_id = {
        memory.get("metadata", {}).get("case_id"): memory for memory in results
    }
    assert set(retrieved_by_id) == {entry["id"] for entry in corpus}, (
        f"real retrieval missed corpus items: "
        f"{sorted({entry['id'] for entry in corpus} - set(retrieved_by_id))}"
    )
    retrieved = list(retrieved_by_id.values())
    retrieved_ids = {memory.get("metadata", {}).get("case_id") for memory in retrieved}
    assert len(retrieved_ids) == len(corpus)
    assert retrieved_ids <= {entry["id"] for entry in corpus}

    captured = {}

    async def generate_stream(prompt, system=None, **_kwargs):
        captured["chat_prompt"] = prompt
        captured["chat_system"] = system
        yield "I will treat that as stored data."

    llm = MagicMock()
    llm.generate_stream = generate_stream
    action = ActionService(
        llm_service=llm, memory_store=security_memory_store, self_knowledge=None
    )
    plan = ActionPlan(
        action_type="RESPOND_CHAT",
        goal="ENGAGE",
        payload={
            "message": "Tell me what you remember.",
            "identity_prompt": "You are a careful friend.",
            "surfaced_memories": retrieved,
        },
    )
    chunks = [chunk async for chunk in action._execute_respond_chat(plan)]
    chat_system = captured["chat_system"]
    chat_prompt = captured["chat_prompt"]
    chat_text = chat_system + chat_prompt
    individual_payloads = [
        entry["content"]
        for entry in corpus
        if entry["family"] != "split_across_memories"
    ]
    assert any(chunk.get("type") == "content" for chunk in chunks)
    assert "[UNTRUSTED_CONTENT_FILTERED]" in chat_text
    assert all(payload not in chat_text for payload in individual_payloads)
    assert chat_text.count("[/RETRIEVED-CONTENT]") == chat_text.count(
        "[RETRIEVED-CONTENT]"
    )
    assert all(payload not in chat_system for payload in individual_payloads)

    proactive = CognitiveService.__new__(CognitiveService)
    proactive.surfaced_memories = retrieved
    proactive_text = CognitiveService._render_proactive_memories(proactive)
    assert "[UNTRUSTED_CONTENT_FILTERED]" in proactive_text
    assert all(payload not in proactive_text for payload in individual_payloads)
    assert proactive_text.count("[/RETRIEVED-CONTENT]") == proactive_text.count(
        "[RETRIEVED-CONTENT]"
    )

    state = MagicMock()
    state.get_behavioral_directive.return_value = "A calm, low-pressure check-in."
    state.get_context_snapshot.return_value = {"cortisol": 0.1, "dopamine": 0.2}
    state.current_state.energy = 0.8
    state.current_state.valence = 0.1
    state.current_state.arousal = 0.3
    state.current_state.dominance = 0.5
    state.get_emotion_label.return_value = "calm"
    identity = MagicMock()
    identity.get_persona_prompt.return_value = "You are a careful friend."
    identity.history = {"relationship": "Friend"}
    proactive_core = CognitiveService.__new__(CognitiveService)
    proactive_core.state = state
    proactive_core.identity = identity
    proactive_core.surfaced_memories = retrieved
    proactive_core.action = action
    proactive_core.learning = MagicMock()
    proactive_core.learning.trigger_reflection = AsyncMock(return_value=None)
    proactive_chunks = [
        chunk async for chunk in proactive_core.generate_proactive_response()
    ]
    proactive_system = captured["chat_system"]
    proactive_prompt = captured["chat_prompt"]
    assert any(chunk.get("type") == "content" for chunk in proactive_chunks)
    assert "[UNTRUSTED_CONTENT_FILTERED]" in proactive_system
    assert all(payload not in proactive_system for payload in individual_payloads)
    assert all(payload not in proactive_prompt for payload in individual_payloads)
    assert proactive_system.count("[/RETRIEVED-CONTENT]") == proactive_system.count(
        "[RETRIEVED-CONTENT]"
    )

    reflection = ReflectionService(llm_service=MagicMock(), graph_store=MagicMock())
    reflection.identity = MagicMock()
    reflection.identity.personality = {"name": "Friend"}
    reflection.identity.history = {"relationship": "Friend"}
    reflection.graph.decay_relationships = AsyncMock()
    reflection.vector = None
    reflection_prompts = []

    async def capture_reflection(prompt, **_kwargs):
        reflection_prompts.append(prompt)
        return "[]" if len(reflection_prompts) == 1 else "{}"

    reflection.llm.generate = capture_reflection
    episodes = [
        {
            "content": memory["content"],
            "context": "retrieved memory",
            "response": "",
            "metadata": memory.get("metadata", {}),
        }
        for memory in retrieved
    ]
    await reflection._consolidate(episodes)
    assert len(reflection_prompts) == 3
    for prompt in reflection_prompts:
        assert "[UNTRUSTED_CONTENT_FILTERED]" in prompt
        assert all(payload not in prompt for payload in individual_payloads)

    for index in range(1, 9):
        pair = [
            memory
            for memory in retrieved
            if memory.get("metadata", {})
            .get("case_id", "")
            .startswith(f"split-{index:02d}-")
        ]
        if len(pair) == 2:
            history = ActionService._build_shared_history(pair)
            proactive.surfaced_memories = pair
            proactive_pair = CognitiveService._render_proactive_memories(proactive)
            assert history.count("[UNTRUSTED_CONTENT_FILTERED]") == 2
            assert proactive_pair.count("[UNTRUSTED_CONTENT_FILTERED]") == 2


def test_stored_corpus_has_at_least_fifty_payloads_and_split_pairs():
    corpus = _load_corpus()
    assert len(corpus) >= 50
    split_groups = {
        entry["split_group"]
        for entry in corpus
        if entry["family"] == "split_across_memories"
    }
    assert len(split_groups) >= 5
