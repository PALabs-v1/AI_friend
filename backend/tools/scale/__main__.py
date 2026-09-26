"""Build a lifesim corpus, embed once, and measure production memory backends.

Run from ``backend/``; use a dedicated data directory for each backend run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tools.scale.corpus import build_corpus, write_jsonl
from tools.scale.embedding_cache import (
    ProductionEmbedder,
    cache_paths,
    embed_once,
    ollama_model_digest,
)
from tools.scale.report import budget_breakpoints, write_report
from tools.scale.runner import (
    BACKENDS,
    SIZES,
    git_sha,
    hardware,
    not_run_cell,
    run_cell,
    working_tree_clean,
)


class DeterministicStubEmbedder:
    """No-network embedder used only by the local 200-row pipeline check."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        seed = int.from_bytes(
            hashlib.sha256(text.casefold().encode()).digest()[:8], "big"
        )
        rng = np.random.default_rng(seed)
        vector = rng.standard_normal(768).astype(np.float32)
        vector /= np.linalg.norm(vector)
        return vector.tolist()


def _parse_ints(raw: str) -> list[int]:
    values = [int(value.strip()) for value in raw.split(",") if value.strip()]
    if not values:
        raise argparse.ArgumentTypeError("provide at least one integer")
    return values


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=_parse_ints, default=list(SIZES))
    parser.add_argument(
        "--backends", type=lambda value: value.split(","), default=list(BACKENDS)
    )
    parser.add_argument("--seeds", type=_parse_ints, default=list(range(1000, 1100)))
    parser.add_argument("--horizon", default="10y")
    parser.add_argument("--data-dir", type=Path, default=Path("/tmp/aif-scale"))
    parser.add_argument(
        "--report-dir", type=Path, default=Path("/tmp/aif-scale/reports")
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--stub-embeddings",
        action="store_true",
        help="use deterministic non-production vectors for local checks",
    )
    parser.add_argument("--retrieval-budget-ms", type=float, default=300.0)
    return parser


async def run(args) -> dict:
    if any(backend not in BACKENDS for backend in args.backends):
        raise ValueError(f"backends must be selected from: {', '.join(BACKENDS)}")
    if any(size <= 0 for size in args.sizes):
        raise ValueError("sizes must be positive")
    if (
        "postgres" in args.backends
        and len(set(args.sizes)) > 1
        and args.database_url
        and "{size}" not in args.database_url
    ):
        raise ValueError(
            "Postgres multi-size runs require --database-url with a {size} database-name placeholder"
        )
    size_max = max(args.sizes)
    rows, probes, graph_edges = build_corpus(
        size_max, args.seeds, horizon=args.horizon, allow_partial=True
    )
    if not rows:
        raise ValueError(
            "lifesim produced no distinct memory rows from the requested seeds"
        )
    data_dir = args.data_dir
    corpus_sha = write_jsonl(data_dir / "corpus.jsonl", rows)
    probe_sha = write_jsonl(data_dir / "probes.jsonl", probes)
    graph_sha = write_jsonl(data_dir / "graph-edges.jsonl", graph_edges)

    if args.stub_embeddings:
        embedder = DeterministicStubEmbedder()
        model_digest = "deterministic-stub-not-nomic"
    else:
        model_digest = ollama_model_digest("nomic-embed-text", args.ollama_url)
        embedder = ProductionEmbedder(args.ollama_url)
    array_path, _ = cache_paths(data_dir, corpus_sha, model_digest)
    cache_hit = array_path.exists()
    embedding_started = time.perf_counter()
    embedding_cpu_started = time.process_time()
    progress_path = data_dir / f"embedding-progress-{corpus_sha[:12]}.jsonl"
    try:
        matrix, embedding_manifest = await embed_once(
            [row.content for row in rows],
            data_dir=data_dir,
            corpus_sha=corpus_sha,
            model_digest=model_digest,
            embedder=embedder,
            progress_path=progress_path,
        )
    finally:
        close = getattr(embedder, "close", None)
        if close:
            await close()

    cells = []
    for size in args.sizes:
        if size > len(rows):
            for backend in BACKENDS:
                cells.append(
                    not_run_cell(
                        backend,
                        size,
                        f"dev corpus produced {len(rows):,} unique memories; {size:,} required",
                    )
                )
            continue
        size_rows = rows[:size]
        used_memory_ids = {row.memory_id for row in size_rows}
        size_probes = [
            probe
            for probe in probes
            if used_memory_ids.intersection(probe.support_memory_ids)
        ]
        for backend in BACKENDS:
            if backend not in args.backends:
                cells.append(not_run_cell(backend, size, "not selected for this run"))
                continue
            try:
                query_embedder = None
                if args.stub_embeddings:

                    async def query_embedder(text):
                        return DeterministicStubEmbedder.vector(text)

                cell = await run_cell(
                    backend,
                    size,
                    size_rows,
                    size_probes,
                    matrix[:size],
                    data_dir=data_dir,
                    database_url=args.database_url,
                    query_embedder=query_embedder,
                    graph_edges=graph_edges,
                    corpus_sha=corpus_sha,
                    embedding_sha=embedding_manifest["sha256"],
                )
                cell["embedding_model_digest"] = model_digest
                cells.append(cell)
            except Exception as exc:
                cells.append(
                    not_run_cell(
                        backend,
                        size,
                        f"service unavailable or setup failed: {type(exc).__name__}: {exc}",
                    )
                )

    report = {
        "schema_version": 1,
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "git_sha": git_sha(),
        "working_tree_clean": working_tree_clean(),
        "hardware": hardware(),
        "corpus": {
            "requested_memories": size_max,
            "actual_memories": len(rows),
            "unique_texts": len(rows),
            "dev_seeds": sorted({row.seed for row in rows}),
            "dev_seed_pool": args.seeds,
            "archetypes": sorted({row.archetype for row in rows}),
            "horizon": args.horizon,
            "sha256": corpus_sha,
            "probe_count": len(probes),
            "probe_sha256": probe_sha,
            "lifesim_graph_edges": len(graph_edges),
            "graph_edges_sha256": graph_sha,
        },
        "embedding": {
            "model": embedding_manifest["model"],
            "model_digest": model_digest,
            "prefix_policy": "none",
            "cache_sha256": embedding_manifest["sha256"],
            "cache_path": str(args.data_dir),
            "stub": args.stub_embeddings,
            "cache_hit": cache_hit,
            "elapsed_seconds": time.perf_counter() - embedding_started,
            "cpu_seconds": time.process_time() - embedding_cpu_started,
            "rows_per_second": (
                len(rows) / (time.perf_counter() - embedding_started)
                if not cache_hit and time.perf_counter() > embedding_started
                else None
            ),
            "gpu_progress_samples": _gpu_progress_samples(progress_path),
        },
        "retrieval_budget_ms": args.retrieval_budget_ms,
        "cells": cells,
        "budget_breakpoints": budget_breakpoints(cells, args.retrieval_budget_ms),
    }
    return report


def _gpu_progress_samples(path: Path):
    if not path.exists():
        return []
    samples = []
    for line in path.read_text().splitlines():
        try:
            gpu = json.loads(line).get("gpu")
        except json.JSONDecodeError:
            continue
        if gpu is not None:
            samples.append(gpu)
    return samples


def main():
    parser = _parser()
    args = parser.parse_args()
    report = asyncio.run(run(args))
    stem = f"scale-{report['run_id']}"
    write_report(
        report, args.report_dir / f"{stem}.json", args.report_dir / f"{stem}.md"
    )
    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "cells": len(report["cells"]),
                "report": str(args.report_dir / f"{stem}.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
