"""Gate tests for `experiments/gpu/` -- the real-embedding and ToM packages.

These don't need a model or a GPU: they protect the plumbing that real runs
depend on, the same way test_cognitive_bench.py protects the synthetic
benchmark's validity rather than any one result.
"""

import pytest

from evals.cognitive.memory_experiments import HELDOUT_SEEDS, TUNE_SEEDS
from evals.cognitive.scenarios import build_history
from experiments.gpu.real_embedding_retrieval import build_embedder


class _Args:
    def __init__(self, backend="synthetic", prefix=None):
        self.backend = backend
        self.prefix = prefix
        self.model = "unused"


@pytest.mark.parametrize("regime", ["verbatim", "unique", "summary"])
@pytest.mark.parametrize("seed", [*TUNE_SEEDS, *HELDOUT_SEEDS])
def test_no_doc_query_text_collision(seed, regime):
    """`PrecomputedEmbedder` is keyed by raw text (real_embedding_retrieval.py's
    `build_embedder`). If a document's text and a query's text were ever
    identical, the query's (possibly differently-prefixed) vector would
    silently overwrite the document's in that dict -- a real risk the audit
    (docs/brain-research-v3/07-gpu-experiment-audit.md, item 2) flagged as
    currently untriggered but unguarded. This pins it so a future corpus edit
    can't reintroduce it silently."""
    scenario = build_history(seed, regime=regime)
    docs = {e.text for e in scenario.events}
    queries = {p.query for p in scenario.probes}
    assert not (docs & queries), (
        f"seed={seed} regime={regime}: doc/query exact-text collision "
        f"{docs & queries} would corrupt PrecomputedEmbedder's dict keying"
    )


def test_build_embedder_raises_loudly_on_collision():
    """Defense in depth: even if the corpus invariant above ever regresses,
    a real run must fail fast with a clear message, not silently keep the
    wrong (query-prefixed) vector under a document's key."""
    scenario = build_history(1, regime="verbatim")
    # Force a collision: alias one probe's query to an existing document text.
    doc_text = next(iter(scenario.events)).text
    scenario.probes[0].query = doc_text
    with pytest.raises(ValueError, match="collide"):
        build_embedder(scenario, _Args(backend="synthetic"))
