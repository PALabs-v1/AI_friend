"""Adapters that construct a real `CognitiveService` for BrainBench to drive.

Two modes (`06-benchmark-plan.md`), never merged in results:

- `architecture_only`: `Config.INTENT_CLASSIFIER_BACKEND = "heuristic"` --
  production's own zero-LLM intent path (`app/cognitive/intent_classifier.py`),
  not a BrainBench-specific bypass -- plus a text-free stand-in `llm_service`
  for every other call the pipeline makes, since this mode scores retrieved
  memory ids and state, never text quality. Appraisal, decision, memory,
  affect and trust all run as the real, unmodified production code: this is
  V2 exactly as it ships, which is what Phase 6's "V2 lifesim baseline" needs
  to measure. It deliberately does *not* attempt to feed oracle appraisal
  signals into `AppraisalEngine` -- inventing that pathway is W2's job
  (Phase 7), against this baseline, not something to bake into the baseline
  itself. It also cannot form episodic memory: production's consolidation
  (`learning.py`'s `_consolidate_episodic_memory`) asks the model to write
  the summary that gets stored, so with no model there is nothing real to
  store. Per DR-037 that is not papered over with a deterministic
  passthrough -- suites that depend on memory content run `llm_augmented`.
- `llm_augmented`: a real `llm_service` (`app.llm.build_llm_client()`,
  pointed at the Ollama models on home-gpu), for a small reference run.
  Stored and reported separately from `architecture_only`, always labeled.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock, patch

from app.cognitive.core import CognitiveService
from app.config import Config
from app.state.memory_store import MemoryStore
from app.state.sqlite_fallback import SQLitePool

AdapterMode = Literal["architecture_only", "llm_augmented"]


class NullLLM:
    """`architecture_only`'s `llm_service` stand-in: never generates real text.

    Duck-type-compatible with `OllamaClient`/`build_llm_client()` -- the same
    interface `tests/integration/harness/mock_cognitive_engines.py`'s
    `MockDeterministicLLM` implements -- but returns a fixed, content-free
    reply. `architecture_only` scores memory ids and state, never text, so a
    fluent-sounding canned reply would just be wasted design surface.

    Intent classification never reaches `generate()`: with
    `INTENT_CLASSIFIER_BACKEND = "heuristic"`, `HeuristicIntentClassifier`
    answers it deterministically before any LLM call is made.

    Only valid for suites whose scored signal needs no generated text.
    Reflection still runs and will store `"{}"` as a memory summary; that is
    why the memory and personality suites never use this mode (DR-037).
    """

    is_null_llm = True

    def __init__(self) -> None:
        self.generate_call_count = 0
        self.stream_call_count = 0

    async def generate(
        self, prompt: str, system: str | None = None, **kwargs: Any
    ) -> str:
        self.generate_call_count += 1
        # Whatever non-intent caller reaches here (reflection summarization,
        # a JSON-expecting prompt) gets a harmless empty object rather than
        # failing a downstream json.loads on empty string.
        return "{}"

    async def generate_stream(
        self, prompt: str, system: str | None = None, **kwargs: Any
    ) -> AsyncIterator[str]:
        # Must be an async *generator* (a `yield` directly in this body), not
        # a coroutine that returns an iterator: the real `OllamaClient.
        # generate_stream` is one (it `yield`s directly, see
        # app/llm/ollama_client.py), and app/cognitive/action.py calls it as
        # `async for chunk in self.llm.generate_stream(...)` with no `await`
        # on the call itself. A `return some_iterator` shape here -- which
        # `tests/integration/harness/mock_cognitive_engines.py`'s
        # `MockDeterministicLLM` uses -- hands that caller a bare coroutine
        # object instead, which has no `__aiter__` and fails with exactly
        # that `AttributeError` the moment a real turn streams through it
        # (found by driving one real turn end to end, not by a type check).
        self.stream_call_count += 1
        yield " "


@contextmanager
def _architecture_only_intent_backend():
    """`DecisionService.__init__` reads `INTENT_CLASSIFIER_BACKEND` exactly
    once, at construction, to pick `HeuristicIntentClassifier` vs.
    `LLMIntentClassifier` and store the chosen instance -- so the override
    only needs to be active for the `CognitiveService(...)` call itself, not
    for the run's lifetime afterward.
    """
    with patch.object(Config, "INTENT_CLASSIFIER_BACKEND", "heuristic"):
        yield


@contextmanager
def isolated_runtime():
    """No Redis for a BrainBench service: every cell keeps its state in its own
    `run_dir` SQLite files.

    With Redis reachable (home-gpu runs the infra stack), every cell in every
    worker shared one Redis and, before F-016's fix, one `state_cache.db`. That
    was write-only on a BrainBench turn, so no number leaked between cells,
    but cells serialised on SQLite locks and ran 30-60x slower. Both Redis
    clients connect at construction (`runtime_paths.redis_endpoint`), so the
    override only needs to cover construction.
    """
    with patch.object(Config, "REDIS_URL", ""):
        yield


def build_memory_store(base_path: Path) -> MemoryStore:
    """A real `MemoryStore` against a per-run SQLite database -- the same
    fallback machinery production uses when Postgres is unavailable (see
    `tests/test_memory_store_is_sqlite.py`), not a mock of retrieval logic.
    Qdrant is disabled the same way that test does. A live-infra run passes
    a real Postgres pool + Qdrant-backed store to `build_cognitive_service`
    instead of calling this.
    """
    pool = SQLitePool(str(base_path / "memory.db"))
    store = MemoryStore(pool, MagicMock())
    store.qdrant_store.client = None
    return store


@dataclass
class BrainBenchService:
    mode: AdapterMode
    cognitive: CognitiveService
    llm_service: Any
    memory_store: MemoryStore


def build_cognitive_service(
    mode: AdapterMode,
    run_dir: Path,
    *,
    llm_service: Any = None,
    memory_store: MemoryStore | None = None,
    graph_db: Any = None,
) -> BrainBenchService:
    """Compose a real `CognitiveService` the way `BrainAgent` does in
    production (see `tests/test_runtime_composition.py`'s fixture for the
    proven-working bare-construction pattern this mirrors), for the given
    mode. Every `app.clock` call this service's turns make must be wrapped
    in `app.clock.use_clock(sim_clock)` by the caller driving the turns --
    this function only builds the service, it doesn't drive it.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    store = memory_store if memory_store is not None else build_memory_store(run_dir)
    graph = graph_db if graph_db is not None else MagicMock()

    if mode == "architecture_only":
        if llm_service is not None:
            raise ValueError(
                "architecture_only supplies its own llm_service (NullLLM); "
                "got one explicitly"
            )
        service = NullLLM()
        with _architecture_only_intent_backend(), isolated_runtime():
            cognitive = CognitiveService(
                llm_service=service,
                memory_store=store,
                graph_db=graph,
                base_path=str(run_dir),
            )
    elif mode == "llm_augmented":
        if llm_service is None:
            raise ValueError(
                "llm_augmented requires a real llm_service (app.llm.build_llm_client())"
            )
        service = llm_service
        with isolated_runtime():
            cognitive = CognitiveService(
                llm_service=service,
                memory_store=store,
                graph_db=graph,
                base_path=str(run_dir),
            )
    else:
        raise ValueError(f"unknown BrainBench mode: {mode!r}")

    return BrainBenchService(
        mode=mode, cognitive=cognitive, llm_service=service, memory_store=store
    )
