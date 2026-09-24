"""Re-run the memory-retrieval experiments with a REAL embedding model.

The cloud benchmark (`python -m evals.cognitive memory`) uses a synthetic,
seeded embedder because no model server exists there. Every retrieval
conclusion in docs/brain-research/03-memory-retrieval.md is therefore
conditional on that embedder. This script removes the condition: it embeds
the same scenario texts with the production model and scores the same arms.

Two backends:
  --backend ollama   the production path: Ollama /api/embed, model
                     nomic-embed-text (what MemoryStore.get_embedding calls).
  --backend st       sentence-transformers, any HF model id (needs torch).
  --backend synthetic  the cloud benchmark's seeded embedder -- a plumbing
                     smoke test that needs no model (results are NOT evidence).

Hypotheses under test (each is a column in the output):
  H-R1  hybrid beats V1 by >= 0.30 hit@3 on the summary regime (synthetic: +0.67).
  H-R2  the hybrid weight plateau (w_lex 1-2, w_act 0.15-0.3) holds.
  H-R3  nomic task prefixes ("search_query: " / "search_document: ") raise
        hit@3 -- production embeds raw text with no prefix today.

Usage (GPU/Ollama box, from backend/):
  pip install -r experiments/gpu/requirements.txt
  python -m experiments.gpu.real_embedding_retrieval --backend ollama \\
      --ollama-url http://127.0.0.1:11434 --seeds 1-20 \\
      --out results/real_embedding_retrieval.json
  python -m experiments.gpu.real_embedding_retrieval --backend ollama --prefix nomic \\
      --seeds 1-20 --out results/real_embedding_retrieval_prefixed.json

Output: the same JSON shape as `python -m evals.cognitive memory` plus
"embedding" (backend, model, prefix, dimension) and "hardware" blocks.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import sys
import time
from dataclasses import asdict

import numpy as np

from evals.cognitive import memory_experiments as mx
from evals.cognitive.embedder import PrecomputedEmbedder
from evals.cognitive.lab import run_lab
from evals.cognitive.metrics import ProbeResult
from evals.cognitive.scenarios import build_history

ARMS = [
    ("v1", "sqlite"),
    ("v1", "vec60"),
    ("cosine", "vec60"),
    ("v1_retuned:12:2", "vec60"),
    ("hybrid:1.5:0.2:none/pool", "vec60"),
    ("hybrid:0.0:0.2:none/pool", "vec60"),
    ("hybrid:1.5:0.0:none/pool", "vec60"),
] + [(h + "/pool", "vec60") for h in mx.HYBRID_WEIGHT_GRID]


def hardware() -> dict:
    info = {"python": platform.python_version(), "machine": platform.machine()}
    try:
        info["nvidia_smi"] = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            text=True,
            timeout=10,
        ).strip()
    except Exception as exc:  # no GPU is fine for the ollama backend
        info["nvidia_smi"] = f"unavailable: {exc}"
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = torch.cuda.is_available()
    except ImportError:
        info["torch"] = None
    return info


def _prefix(text: str, role: str, scheme: str | None) -> str:
    if scheme == "nomic":
        return ("search_query: " if role == "query" else "search_document: ") + text
    return text


def embed_ollama(
    texts: list[str], url: str, model: str, batch: int = 32
) -> list[list[float]]:
    import httpx

    out: list[list[float]] = []
    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            response = client.post(
                f"{url.rstrip('/')}/api/embed", json={"model": model, "input": chunk}
            )
            response.raise_for_status()
            vectors = response.json().get("embeddings")
            if not vectors or len(vectors) != len(chunk):
                raise RuntimeError(
                    f"Ollama returned {len(vectors or [])} vectors for {len(chunk)} texts"
                )
            out.extend(vectors)
    return out


_ST_MODEL_CACHE: dict[str, object] = {}


def embed_st(texts: list[str], model: str) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer

    # build_embedder() is called once per (regime, seed) -- up to 60 times for
    # the default sweep -- and each call used to reconstruct the model from
    # disk/GPU from scratch (Codex C2 audit, item 5). Cache it per process:
    # the model is identical across every call in one script invocation.
    encoder = _ST_MODEL_CACHE.get(model)
    if encoder is None:
        encoder = SentenceTransformer(model, trust_remote_code=True)
        _ST_MODEL_CACHE[model] = encoder
    return encoder.encode(
        texts, batch_size=64, normalize_embeddings=True, show_progress_bar=True
    ).tolist()


def build_embedder(scenario, args) -> PrecomputedEmbedder:
    docs = sorted({e.text for e in scenario.events})
    queries = sorted({p.query for p in scenario.probes})
    collision = set(docs) & set(queries)
    if collision:
        # PrecomputedEmbedder keys by raw text; a doc/query collision means
        # the query's (possibly differently-prefixed) vector silently
        # overwrites the document's. Verified untriggered across every tune
        # and held-out seed (tests/test_gpu_experiments.py), but fail loudly
        # rather than silently corrupt a real run if that ever regresses.
        raise ValueError(
            f"doc/query text collide, would corrupt PrecomputedEmbedder keying: {collision}"
        )
    raw = [_prefix(t, "doc", args.prefix) for t in docs] + [
        _prefix(q, "query", args.prefix) for q in queries
    ]
    if args.backend == "synthetic":
        from evals.cognitive.embedder import LatentTopicEmbedder
        from evals.cognitive.scenarios import register_embeddings

        synthetic = LatentTopicEmbedder("hard", seed=scenario.seed)
        register_embeddings(scenario, synthetic)
        vectors = [synthetic.embed(t) for t in docs + queries]
    elif args.backend == "ollama":
        vectors = embed_ollama(raw, args.ollama_url, args.model)
    else:
        vectors = embed_st(raw, args.model)
    # Keys are the unprefixed texts: the lab and harness look up what the
    # scenario stored / asked, while the model saw the prefixed form.
    return PrecomputedEmbedder(dict(zip(docs + queries, vectors)))


def parse_seeds(spec: str) -> list[int]:
    if "-" in spec:
        lo, hi = spec.split("-")
        return list(range(int(lo), int(hi) + 1))
    return [int(s) for s in spec.split(",")]


_HR3_ARMS = ["cosine@vec60", "hybrid:1.5:0.2:none/pool@vec60"]


def main(argv=None) -> int:
    logging.disable(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend", choices=("ollama", "st", "synthetic"), default="ollama"
    )
    parser.add_argument("--model", default="nomic-embed-text")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--prefix", choices=("none", "nomic"), default="none")
    parser.add_argument(
        "--compare-prefix",
        action="store_true",
        help=(
            "H-R3 needs a PAIRED delta (prefixed - unprefixed on the same "
            "seed/probe), which two separate --prefix runs cannot produce -- "
            "each only pairs its arms against v1@sqlite. This embeds every "
            "scenario both ways in one process and reports the paired "
            "bootstrap CI directly. Ignores --prefix."
        ),
    )
    parser.add_argument("--seeds", default="1-20")
    parser.add_argument(
        "--regimes", nargs="*", default=["verbatim", "unique", "summary"]
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    args.prefix = None if args.prefix == "none" else args.prefix

    seeds = parse_seeds(args.seeds)
    grouped: dict = {}
    prefix_pairs: dict[str, dict[str, list[ProbeResult]]] = {}
    dims = set()
    started = time.time()
    for regime in args.regimes:
        arms_out: dict[str, list[ProbeResult]] = {f"{p}@{g}": [] for p, g in ARMS}
        hr3_unprefixed: dict[str, list[ProbeResult]] = {a: [] for a in _HR3_ARMS}
        hr3_prefixed: dict[str, list[ProbeResult]] = {a: [] for a in _HR3_ARMS}
        for seed in seeds:
            scenario = build_history(seed, regime=regime)
            embedder = build_embedder(scenario, args)
            dims.add(len(next(iter(embedder._vectors.values()))))
            for policy_name, gen in ARMS:
                results = run_lab(
                    scenario, embedder, mx._policy(policy_name), mx.GENERATORS[gen]
                )
                arms_out[f"{policy_name}@{gen}"].extend(results)
            if args.compare_prefix:
                # Same scenario, same model, only the prefix differs -- this
                # is what makes the pairing valid (metrics.paired_delta pairs
                # on (scenario_seed, probe_key), both identical here).
                nomic_args = argparse.Namespace(**{**vars(args), "prefix": "nomic"})
                prefixed_embedder = build_embedder(scenario, nomic_args)
                for arm in _HR3_ARMS:
                    policy_name, gen = arm.rsplit("@", 1)
                    hr3_unprefixed[arm].extend(
                        run_lab(
                            scenario,
                            embedder,
                            mx._policy(policy_name),
                            mx.GENERATORS[gen],
                        )
                    )
                    hr3_prefixed[arm].extend(
                        run_lab(
                            scenario,
                            prefixed_embedder,
                            mx._policy(policy_name),
                            mx.GENERATORS[gen],
                        )
                    )
            print(
                f"[{regime}] seed {seed} done ({time.time() - started:.0f}s)",
                file=sys.stderr,
            )
        grouped[(f"{args.backend}:{args.model}", regime)] = arms_out
        if args.compare_prefix:
            prefix_pairs[regime] = {
                arm: mx.paired_delta(hr3_unprefixed[arm], hr3_prefixed[arm], "hit@3")
                for arm in _HR3_ARMS
            }
    report = {
        "suite": "memory-real-embedding",
        "git_sha": mx.git_sha(),
        "embedding": {
            "backend": args.backend,
            "model": args.model,
            "prefix": args.prefix,
            "dimension": sorted(dims),
        },
        "hardware": hardware(),
        "seeds": seeds,
        "seconds": round(time.time() - started, 1),
        "results": mx.summarize(grouped, "v1@sqlite"),
    }
    if args.compare_prefix:
        # H-R3's actual decision rule: prefixed - unprefixed hit@3, paired
        # per (seed, probe), CI excluding 0. Keyed by regime -> arm.
        report["hr3_prefix_paired_delta"] = prefix_pairs
    with open(args.out, "w") as fh:
        json.dump(
            report,
            fh,
            indent=1,
            default=lambda o: (
                asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)
            ),
        )
    print(args.out)
    # Headline to stdout so a terminal run is immediately readable.
    for cell, arms in report["results"].items():
        for arm in ("v1@sqlite", "cosine@vec60", "hybrid:1.5:0.2:none/pool@vec60"):
            agg = arms[arm]["aggregate"]["all"]["hit@3"]
            print(f"{cell:40s} {arm:34s} hit@3={agg['mean']:.3f} ci={agg['ci95']}")
    return 0


if __name__ == "__main__":
    np.set_printoptions(precision=3)
    raise SystemExit(main())
