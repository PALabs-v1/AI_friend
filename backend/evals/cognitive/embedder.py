"""Deterministic embedders for cognitive benchmarks that run without a model.

The production embedder is Ollama `nomic-embed-text` (768-d). This cloud/CI
environment has no model server, and the retrieval questions this suite asks
("does the ranking formula prefer the relevant memory?") are about how scores
are *combined*, not about embedding quality. So the suite needs vectors whose
cosine structure is realistic and controlled, not vectors that are clever.

`LatentTopicEmbedder` builds each registered text's vector from four seeded,
near-orthogonal random components in 768 dimensions:

    v = sqrt(baseline)*COMMON + sqrt(topic)*TOPIC[t] + sqrt(facet)*FACET[t,f]
        + sqrt(noise)*NOISE[text]

Two texts that share a facet therefore have expected cosine
``baseline + topic + facet``, two texts sharing only a topic
``baseline + topic``, unrelated texts ``baseline``. Random 768-d components are
not exactly orthogonal, so every pair also carries realistic jitter of about
+-0.04. The profile presets are fitted to published cosine ranges:

* ``nomic_like`` -- anisotropic model (unrelated ~0.40, same topic ~0.60, same
  fact ~0.80), the shape of nomic-embed-text and most modern retrievers.
* ``minilm_like`` -- near-isotropic (unrelated ~0.05, same topic ~0.35, same
  fact ~0.70).

Both are approximations. The GPU package under `experiments/gpu/` reruns the
same scenarios through the real model; any conclusion that flips between the
two is reported as embedding-dependent rather than as a result.

Unregistered text raises by default. A silent fallback would let a typo in a
scenario turn into an unrelated vector and read as a retrieval failure.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

DIM = 768


@dataclass(frozen=True)
class CosineProfile:
    """Squared-norm shares of each component; must sum to 1.0."""

    name: str
    baseline: float
    topic: float
    facet: float
    noise: float
    # Components are drawn in the first `effective_dim` coordinates. Random
    # unit vectors in k dimensions have dot products with s.d. ~1/sqrt(k), so
    # a smaller k gives each pair more idiosyncratic jitter -- real models
    # are much noisier pair-to-pair than 768 independent coordinates imply.
    effective_dim: int = DIM

    def __post_init__(self) -> None:
        total = self.baseline + self.topic + self.facet + self.noise
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"profile {self.name} shares sum to {total}, not 1.0")
        if min(self.baseline, self.topic, self.facet, self.noise) < 0:
            raise ValueError(f"profile {self.name} has a negative share")

    def expected_cosine(self, same_topic: bool, same_facet: bool) -> float:
        cos = self.baseline
        if same_topic:
            cos += self.topic
            if same_facet:
                cos += self.facet
        return cos


PROFILES: dict[str, CosineProfile] = {
    "nomic_like": CosineProfile("nomic_like", 0.40, 0.20, 0.20, 0.20),
    "minilm_like": CosineProfile("minilm_like", 0.05, 0.30, 0.35, 0.30),
    # Stress profile: the fact-level signal is small relative to topic and
    # pair jitter (same fact ~0.72, same topic ~0.62, unrelated ~0.40, pair
    # s.d. ~0.06), so paraphrase retrieval is genuinely ambiguous and lexical
    # evidence has to earn its weight.
    "hard": CosineProfile("hard", 0.40, 0.22, 0.10, 0.28, effective_dim=40),
}


def _unit(seed_material: str, dim: int = DIM) -> np.ndarray:
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    vec = np.zeros(DIM)
    vec[:dim] = rng.standard_normal(dim)
    return vec / np.linalg.norm(vec)


class UnregisteredTextError(KeyError):
    pass


class LatentTopicEmbedder:
    """Maps registered texts to vectors with a known cosine structure."""

    def __init__(self, profile: CosineProfile | str = "nomic_like", seed: int = 0):
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        self.seed = seed
        self._specs: dict[str, tuple[str, str]] = {}
        self._cache: dict[str, list[float]] = {}
        self.calls = 0

    def register(self, text: str, topic: str, facet: str) -> None:
        existing = self._specs.get(text)
        if existing is not None and existing != (topic, facet):
            raise ValueError(
                f"text {text!r} registered twice with different semantics: "
                f"{existing} vs {(topic, facet)}"
            )
        self._specs[text] = (topic, facet)

    def is_registered(self, text: str) -> bool:
        return text in self._specs

    def vector(self, text: str) -> np.ndarray:
        spec = self._specs.get(text)
        if spec is None:
            raise UnregisteredTextError(text)
        topic, facet = spec
        p = self.profile
        s = self.seed
        k = p.effective_dim
        # The common direction is shared by every text, so it gets no jitter.
        vec = (
            np.sqrt(p.baseline) * _unit(f"{s}|common")
            + np.sqrt(p.topic) * _unit(f"{s}|topic|{topic}", k)
            + np.sqrt(p.facet) * _unit(f"{s}|facet|{topic}|{facet}", k)
            + np.sqrt(p.noise) * _unit(f"{s}|noise|{text}", k)
        )
        return vec / np.linalg.norm(vec)

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        cached = self._cache.get(text)
        if cached is None:
            cached = self.vector(text).tolist()
            self._cache[text] = cached
        return cached

    async def aembed(self, text: str) -> list[float]:
        """Drop-in for `MemoryStore.get_embedding`."""
        return self.embed(text)

    async def aembed_many(self, texts: list[str]) -> list[list[float] | None]:
        """Drop-in for `MemoryStore.get_embeddings`."""
        return [self.embed(t) for t in texts]


def cosine(a, b) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / denom) if denom > 0 else 0.0


class PrecomputedEmbedder:
    """Serves vectors computed elsewhere (e.g. a real model on a GPU box).

    Same interface as `LatentTopicEmbedder`, so the lab and the production
    harness run unchanged on real embeddings. `register` is accepted and
    ignored; unknown text raises, exactly like the synthetic embedder.
    """

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = {k: np.asarray(v, dtype=float) for k, v in vectors.items()}
        self.calls = 0

    def register(self, text: str, topic: str, facet: str) -> None:
        return None

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        vec = self._vectors.get(text)
        if vec is None:
            raise UnregisteredTextError(text)
        return vec.tolist()

    async def aembed(self, text: str) -> list[float]:
        return self.embed(text)

    async def aembed_many(self, texts: list[str]) -> list[list[float] | None]:
        return [self.embed(t) for t in texts]
