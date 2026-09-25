"""Resumable, content-addressed Ollama embedding cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float] | None]: ...


class ProductionEmbedder:
    """Use MemoryStore's production `/api/embed` batching and no-prefix policy."""

    def __init__(self, ollama_url: str = "http://127.0.0.1:11434"):
        from app.state import semantic_recall_store
        from app.state.memory_store import MemoryStore
        from app.state.sqlite_fallback import SQLitePool

        original = semantic_recall_store.SemanticRecallStore
        semantic_recall_store.SemanticRecallStore = lambda: SimpleNamespace(
            client=None, collection_name="disabled-for-embedding"
        )
        try:
            self._store = MemoryStore(
                SQLitePool(":memory:"), graph_db=None, ollama_base_url=ollama_url
            )
        finally:
            semantic_recall_store.SemanticRecallStore = original

    async def embed(self, texts: list[str]) -> list[list[float] | None]:
        return await self._store.get_embeddings(texts)

    async def close(self) -> None:
        await self._store._http_client.aclose()


def ollama_model_digest(model: str, ollama_url: str) -> str:
    import httpx

    response = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=10)
    response.raise_for_status()
    for entry in response.json().get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            digest = entry.get("digest")
            if digest:
                return str(digest)
            raise ValueError(f"Ollama model {model!r} has no digest in /api/tags")
    raise ValueError(f"Ollama model {model!r} is not present in /api/tags")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_paths(data_dir: Path, corpus_sha: str, model_digest: str):
    stem = f"embeddings-{corpus_sha[:12]}-{hashlib.sha256(model_digest.encode()).hexdigest()[:12]}"
    return data_dir / f"{stem}.npy", data_dir / f"{stem}.manifest.json"


def load_verified_cache(array_path: Path, manifest_path: Path, *, expected: dict):
    manifest = json.loads(manifest_path.read_text())
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"embedding cache mismatch for {key}")
    if manifest.get("sha256") != sha256_file(array_path):
        raise ValueError("embedding cache sha256 verification failed")
    array = np.load(array_path, mmap_mode="r", allow_pickle=False)
    if len(array) != expected["count"]:
        raise ValueError("embedding cache count verification failed")
    return array, manifest


def _progress(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _gpu_snapshot():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return [part.strip() for part in result.stdout.strip().split(",")]


async def embed_once(
    texts: list[str],
    *,
    data_dir: Path,
    corpus_sha: str,
    model_digest: str,
    embedder: Embedder,
    batch_size: int = 32,
    workers: int = 2,
    progress_path: Path | None = None,
):
    """Create or resume one matrix. The final cache is immutable and verified."""
    if batch_size <= 0 or workers <= 0:
        raise ValueError("batch_size and workers must be positive")
    data_dir.mkdir(parents=True, exist_ok=True)
    expected = {
        "model": "nomic-embed-text",
        "model_digest": model_digest,
        "count": len(texts),
        "corpus_sha256": corpus_sha,
        "prefix_policy": "none",
    }
    array_path, manifest_path = cache_paths(data_dir, corpus_sha, model_digest)
    if array_path.exists() or manifest_path.exists():
        if array_path.exists() and not manifest_path.exists():
            array = np.load(array_path, mmap_mode="r", allow_pickle=False)
            if (
                array.ndim != 2
                or len(array) != len(texts)
                or not np.isfinite(array).all()
            ):
                raise ValueError(
                    "incomplete final embedding cache; remove it to rebuild"
                )
            recovered = {**expected, "sha256": sha256_file(array_path)}
            manifest_path.write_text(
                json.dumps(recovered, indent=2, sort_keys=True) + "\n"
            )
        elif not array_path.exists() or not manifest_path.exists():
            raise ValueError("incomplete final embedding cache; remove it to rebuild")
        return load_verified_cache(array_path, manifest_path, expected=expected)

    partial_path = array_path.with_suffix(".partial.npy")
    state_path = array_path.with_suffix(".progress.json")
    partial_manifest = state_path.with_suffix(".manifest.json")
    offset = 0
    if partial_path.exists() or state_path.exists() or partial_manifest.exists():
        if not state_path.exists() or not partial_manifest.exists():
            raise ValueError(
                "incomplete embedding resume state; remove partial cache files"
            )
        state_manifest = json.loads(partial_manifest.read_text())
        if state_manifest != expected:
            raise ValueError("embedding resume cache mismatch")
        offset = int(json.loads(state_path.read_text()).get("completed", 0))
        if not 0 <= offset <= len(texts) or offset % batch_size:
            raise ValueError("embedding resume offset is invalid for this batch size")
        if partial_path.exists():
            matrix = np.load(partial_path, mmap_mode="r+", allow_pickle=False)
            if matrix.shape[0] != len(texts):
                raise ValueError("embedding partial cache count mismatch")
        elif offset:
            raise ValueError("embedding partial matrix missing for completed rows")
        else:
            matrix = None
    else:
        _write_json_atomic(partial_manifest, expected)
        _write_json_atomic(state_path, {"completed": 0})
        matrix = None

    started = time.monotonic()
    next_index = offset
    semaphore = asyncio.Semaphore(workers)

    async def run_batch(start: int):
        async with semaphore:
            vectors = await embedder.embed(texts[start : start + batch_size])
            if len(vectors) != min(batch_size, len(texts) - start) or any(
                not v for v in vectors
            ):
                raise RuntimeError(
                    f"embedder returned invalid vectors for batch at {start}"
                )
            return start, np.asarray(vectors, dtype=np.float32)

    # Commit vector data before its checkpoint so interruption can replay an
    # unfinished wave without claiming unpersisted rows.
    while next_index < len(texts):
        starts = list(
            range(
                next_index,
                min(len(texts), next_index + batch_size * workers),
                batch_size,
            )
        )
        results = await asyncio.gather(*(run_batch(start) for start in starts))
        results.sort(key=lambda item: item[0])
        for start, vectors in results:
            if matrix is None:
                matrix = np.lib.format.open_memmap(
                    partial_path,
                    mode="w+",
                    dtype=np.float32,
                    shape=(len(texts), vectors.shape[1]),
                )
            if vectors.ndim != 2 or vectors.shape[1] != matrix.shape[1]:
                raise ValueError("embedder returned inconsistent vector dimensions")
            matrix[start : start + len(vectors)] = vectors
        next_index = min(len(texts), next_index + len(starts) * batch_size)
        if matrix is not None:
            matrix.flush()
        with partial_path.open("rb") as handle:
            os.fsync(handle.fileno())
        _write_json_atomic(state_path, {"completed": next_index})
        elapsed = max(time.monotonic() - started, 1e-9)
        rate = (next_index - offset) / elapsed
        remaining = len(texts) - next_index
        _progress(
            progress_path or data_dir / "embedding-progress.jsonl",
            {
                "completed": next_index,
                "total": len(texts),
                "rate_rows_per_second": rate,
                "eta_seconds": remaining / rate if rate else None,
                "gpu": _gpu_snapshot(),
            },
        )
    if matrix is None:
        raise ValueError("cannot embed an empty corpus")
    del matrix
    os.replace(partial_path, array_path)
    manifest = {**expected, "sha256": sha256_file(array_path)}
    _write_json_atomic(manifest_path, manifest)
    state_path.unlink(missing_ok=True)
    partial_manifest.unlink(missing_ok=True)
    return load_verified_cache(array_path, manifest_path, expected=expected)
