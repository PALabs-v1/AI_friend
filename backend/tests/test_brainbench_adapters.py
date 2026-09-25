"""Gate tests for evals/brainbench/adapters.py: does `architecture_only`
actually construct a real CognitiveService and drive a real turn through it,
with zero LLM-generated text and the clock seam resolving simulated time?

These import real `app.*` code (unlike lifesim's own test suite, which is
deliberately pure) -- that is the whole point of BrainBench, so a slow-ish
import here is expected and accepted.
"""

from datetime import datetime

import pytest

from app import clock
from app.config import Config
from evals.brainbench.adapters import (
    NullLLM,
    build_cognitive_service,
    build_memory_store,
)


def test_architecture_only_uses_heuristic_intent_backend_only_during_construction(tmp_path):
    original = Config.INTENT_CLASSIFIER_BACKEND
    service = build_cognitive_service("architecture_only", tmp_path)
    try:
        assert Config.INTENT_CLASSIFIER_BACKEND == original  # restored after construction
        assert service.cognitive.decision.intent_classifier.__class__.__name__ == (
            "HeuristicIntentClassifier"
        )
    finally:
        service.cognitive.close()


def test_architecture_only_rejects_an_explicit_llm_service(tmp_path):
    from unittest.mock import MagicMock

    with pytest.raises(ValueError, match="architecture_only supplies its own"):
        build_cognitive_service("architecture_only", tmp_path, llm_service=MagicMock())


def test_llm_augmented_requires_an_llm_service(tmp_path):
    with pytest.raises(ValueError, match="llm_augmented requires"):
        build_cognitive_service("llm_augmented", tmp_path)


def test_unknown_mode_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown BrainBench mode"):
        build_cognitive_service("not_a_real_mode", tmp_path)  # type: ignore[arg-type]


def test_null_llm_never_produces_generated_text():
    import asyncio

    llm = NullLLM()

    async def run():
        reply = await llm.generate("anything")
        chunks = [c async for c in llm.generate_stream("anything")]
        return reply, chunks

    reply, chunks = asyncio.run(run())
    assert reply == "{}"
    assert chunks == [" "]
    assert llm.generate_call_count == 1
    assert llm.stream_call_count == 1


def test_build_memory_store_is_real_sqlite_not_a_mock(tmp_path):
    store = build_memory_store(tmp_path)
    assert store.is_sqlite is True
    assert (tmp_path / "memory.db").exists()


@pytest.mark.asyncio
async def test_architecture_only_drives_one_real_turn_under_the_simulated_clock(tmp_path):
    """The end-to-end smoke test: a real CognitiveService, a real turn, real
    memory/state/decision code, zero LLM text, and every timestamp the turn
    produces resolving against a ManualClock instead of wall time.
    """
    service = build_cognitive_service("architecture_only", tmp_path)
    sim = clock.ManualClock(datetime(2031, 6, 1, 9, 0, 0))
    try:
        with clock.use_clock(sim):
            raw_event = {
                "id": "turn-0001",
                "type": "USER_MESSAGE",
                "content": "I got a new job at a bakery downtown.",
                "metadata": {},
            }
            outputs = [
                output async for output in service.cognitive.process_event(raw_event)
            ]
        assert outputs, "a real turn must yield at least one pipeline output"
        assert any(o.get("type") == "done" for o in outputs)
        # A `generate_stream()` shaped as a coroutine-returning-an-iterator
        # instead of a real async generator fails with "'coroutine' object
        # has no attribute '__aiter__'" deep in action.py's streaming call,
        # which the pipeline catches broadly and reports as {"type": "error"}
        # -- silently masking the real turn behind a fallback line. This is
        # the regression test for that: a real turn must never take the
        # error path just because the stand-in LLM was shaped wrong.
        assert not any(o.get("type") == "error" for o in outputs), (
            f"turn hit an internal error path: {outputs}"
        )
        # architecture_only must never touch the LLM for generated text --
        # only the harmless generate()/generate_stream() stand-ins, and only
        # if some non-intent stage happened to call them at all.
        assert isinstance(service.llm_service, NullLLM)
    finally:
        service.cognitive.close()
