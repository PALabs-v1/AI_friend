from __future__ import annotations

import json

import pytest

from tools.scale.__main__ import DeterministicStubEmbedder
from tools.scale.corpus import build_corpus, write_jsonl
from tools.scale.embedding_cache import embed_once, load_verified_cache
from tools.scale.report import budget_breakpoints, report_markdown, write_report
from tools.scale.runner import run_cell


@pytest.mark.asyncio
async def test_scale_pipeline_200_is_deterministic_offline(tmp_path):
    rows, probes, edges = build_corpus(200, [1000, 1001], horizon="1000t")
    again, again_probes, again_edges = build_corpus(200, [1000, 1001], horizon="1000t")
    assert rows == again
    assert probes == again_probes
    assert edges == again_edges
    assert all(1000 <= row.seed <= 1099 for row in rows)
    assert len({row.content.casefold() for row in rows}) == 200
    assert len({row.archetype for row in rows}) == 2
    assert probes

    corpus_sha = write_jsonl(tmp_path / "corpus.jsonl", rows)
    matrix, manifest = await embed_once(
        [row.content for row in rows],
        data_dir=tmp_path / "cache",
        corpus_sha=corpus_sha,
        model_digest="deterministic-stub-not-nomic",
        embedder=DeterministicStubEmbedder(),
        batch_size=20,
        workers=2,
    )
    assert matrix.shape == (200, 768)
    assert manifest["prefix_policy"] == "none"
    assert manifest["count"] == 200

    async def query_embedder(text):
        return DeterministicStubEmbedder.vector(text)

    cell = await run_cell(
        "sqlite",
        200,
        rows,
        probes,
        matrix,
        data_dir=tmp_path / "db",
        query_embedder=query_embedder,
        graph_edges=edges,
    )
    assert cell["status"] == "measured"
    assert cell["metrics"]["retrieval_ms"]["p95"] >= 0
    assert cell["metrics"]["raw_backend_query_ms"]["p95"] >= 0
    assert cell["metrics"]["recall"]["probes"] > 0
    assert cell["metrics"]["sqlite_indexed_rows_before_decay"] == 200
    assert cell["metrics"]["foreground_during_decay_ms"]["p95"] >= 0


@pytest.mark.asyncio
async def test_embedding_cache_resumes_after_interrupted_batch(tmp_path):
    class FailingEmbedder:
        def __init__(self):
            self.calls = 0

        async def embed(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated interruption")
            return [DeterministicStubEmbedder.vector(text) for text in texts]

    class CountingEmbedder:
        def __init__(self):
            self.calls = 0

        async def embed(self, texts):
            self.calls += 1
            return [DeterministicStubEmbedder.vector(text) for text in texts]

    texts = [f"memory {index}" for index in range(40)]
    options = {
        "data_dir": tmp_path,
        "corpus_sha": "corpus-digest",
        "model_digest": "model-digest",
        "batch_size": 10,
        "workers": 1,
    }
    with pytest.raises(RuntimeError, match="simulated interruption"):
        await embed_once(texts, embedder=FailingEmbedder(), **options)
    embedder = CountingEmbedder()
    matrix, _ = await embed_once(texts, embedder=embedder, **options)
    assert matrix.shape == (40, 768)
    assert embedder.calls == 3


@pytest.mark.asyncio
async def test_embedding_cache_resumes_if_first_batch_is_interrupted(tmp_path):
    class FailingEmbedder:
        async def embed(self, texts):
            raise RuntimeError("offline during first batch")

    texts = [f"memory {index}" for index in range(20)]
    options = {
        "data_dir": tmp_path,
        "corpus_sha": "first-batch-corpus",
        "model_digest": "model-digest",
        "batch_size": 10,
        "workers": 1,
    }
    with pytest.raises(RuntimeError, match="offline during first batch"):
        await embed_once(texts, embedder=FailingEmbedder(), **options)
    matrix, _ = await embed_once(
        texts,
        embedder=DeterministicStubEmbedder(),
        **options,
    )
    assert matrix.shape == (20, 768)


@pytest.mark.asyncio
async def test_embedding_cache_rejects_mismatched_manifest(tmp_path):
    texts = ["one", "two"]
    expected_corpus = "c" * 64
    matrix, _ = await embed_once(
        texts,
        data_dir=tmp_path,
        corpus_sha=expected_corpus,
        model_digest="model-digest",
        embedder=DeterministicStubEmbedder(),
        batch_size=2,
        workers=1,
    )
    array_path = next(tmp_path.glob("*.npy"))
    manifest_path = next(tmp_path.glob("*.manifest.json"))
    assert matrix.shape == (2, 768)
    with pytest.raises(ValueError, match="model_digest"):
        load_verified_cache(
            array_path,
            manifest_path,
            expected={
                "model": "nomic-embed-text",
                "model_digest": "wrong-model",
                "count": 2,
                "corpus_sha256": expected_corpus,
                "prefix_policy": "none",
            },
        )


def test_report_shape_includes_matrix_budget_and_context(tmp_path):
    cells = [
        {
            "backend": "sqlite",
            "size": 200,
            "status": "measured",
            "build_seconds": 1.25,
            "metrics": {
                "retrieval_ms": {"p50": 2.0, "p95": 4.0, "p99": 5.0},
                "raw_backend_query_ms": {"p50": 1.0, "p95": 2.0, "p99": 3.0},
                "recall": {"hit_at_5": 0.75, "probes": 4},
                "foreground_no_load_ms": {"p95": 4.0},
                "foreground_during_decay_ms": {"p95": 5.0},
            },
        },
        {
            "backend": "postgres",
            "size": 200,
            "status": "not-run",
            "reason": "no service",
        },
    ]
    report = {
        "git_sha": "abc123",
        "working_tree_clean": True,
        "hardware": {"machine": "test"},
        "embedding": {"model": "nomic-embed-text", "model_digest": "digest"},
        "retrieval_budget_ms": 3.0,
        "cells": cells,
        "budget_breakpoints": budget_breakpoints(cells, 3.0),
    }
    json_path, markdown_path = tmp_path / "run.json", tmp_path / "run.md"
    write_report(report, json_path, markdown_path)
    loaded = json.loads(json_path.read_text())
    assert len(loaded["cells"]) == 2
    assert loaded["budget_breakpoints"]["sqlite"].endswith("200 memories")
    assert "no service" in markdown_path.read_text()
    assert "2.00/4.00/5.00" in report_markdown(report)
