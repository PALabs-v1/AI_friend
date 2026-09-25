"""Production-path scale loading, retrieval, decay and foreground measurements."""

from __future__ import annotations

import asyncio
import os
import platform
import random
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.config import Config
from app.state.memory_store import MemoryStore
from app.state.sqlite_fallback import SQLitePool

BACKENDS = ("sqlite", "postgres", "qdrant-pinned", "qdrant-latest", "neo4j")
SIZES = (1_000, 10_000, 100_000, 500_000, 1_000_000, 5_000_000)
DECAY_BATCH_SIZE = 5_000


def git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def working_tree_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return not result.stdout.strip()


def hardware() -> dict:
    facts = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }
    try:
        import psutil

        facts["ram_bytes"] = psutil.virtual_memory().total
        facts["cpu_model"] = platform.processor()
    except ImportError:
        try:
            if platform.system() == "Darwin":
                output = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=2,
                ).stdout.strip()
                facts["ram_bytes"] = int(output)
                cpu = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                )
                if cpu.returncode == 0:
                    facts["cpu_model"] = cpu.stdout.strip()
            else:
                facts["ram_bytes"] = None
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            facts["ram_bytes"] = None
        facts.setdefault("cpu_model", platform.processor())
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        facts["gpu"] = result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        facts["gpu"] = None
    return facts


def _result_content(result) -> str:
    if isinstance(result, dict):
        return str(result.get("content", ""))
    return str(getattr(result, "content", ""))


async def open_store(backend: str, data_dir: Path, database_url: str | None):
    if backend == "qdrant-latest" and not os.getenv("SCALE_QDRANT_IMAGE"):
        raise RuntimeError(
            "qdrant-latest requires SCALE_QDRANT_IMAGE set to the resolved stable image tag"
        )
    graph_db = None
    if backend == "postgres":
        if not database_url:
            raise RuntimeError(
                "postgres requires --database-url with initialized production schema"
            )
        import asyncpg

        database_url = database_url.format(
            size=data_dir.name.rsplit("-", maxsplit=1)[-1],
            corpus=data_dir.name.rsplit("-", maxsplit=1)[0],
        )
        pool = await asyncpg.create_pool(dsn=database_url, statement_cache_size=0)
    else:
        pool = SQLitePool(str(data_dir / "memory.sqlite3"))
    if backend in ("qdrant-pinned", "qdrant-latest"):
        variant = "PINNED" if backend == "qdrant-pinned" else "LATEST"
        Config.QDRANT_HOST = os.getenv(
            f"SCALE_QDRANT_{variant}_HOST",
            os.getenv("SCALE_QDRANT_HOST", "127.0.0.1"),
        )
        Config.QDRANT_PORT = int(
            os.getenv(
                f"SCALE_QDRANT_{variant}_PORT",
                os.getenv("SCALE_QDRANT_PORT", "6333"),
            )
        )
    else:
        Config.QDRANT_HOST = "127.0.0.1"
        Config.QDRANT_PORT = 0
    if backend == "neo4j":
        from app.state.graph_db import GraphDB

        try:
            graph_db = GraphDB()
            await graph_db.initialize()
            if not await graph_db.execute_query("RETURN 1"):
                raise RuntimeError("Neo4j did not return a readiness result")
        except Exception:
            await pool.close()
            raise
    if backend in ("qdrant-pinned", "qdrant-latest"):
        store = MemoryStore(pool, graph_db)
        if store.qdrant_store.client is None:
            await store._http_client.aclose()
            await pool.close()
            raise RuntimeError("selected Qdrant service is unavailable")
        store.qdrant_store.collection_name = (
            f"ai_friend_scale_{data_dir.name.replace('-', '_')}"
        )
        store.qdrant_store._ensure_collection_exists()
        if store.qdrant_store.client is None:
            await store._http_client.aclose()
            await pool.close()
            raise RuntimeError("could not create the scale-isolated Qdrant collection")
    else:
        # MemoryStore normally probes Qdrant during construction. Keep the
        # other backends offline unless Qdrant is the selected vector store.
        from app.state import semantic_recall_store

        original = semantic_recall_store.SemanticRecallStore
        semantic_recall_store.SemanticRecallStore = lambda: SimpleNamespace(
            client=None, collection_name="disabled-for-this-backend"
        )
        try:
            store = MemoryStore(pool, graph_db)
        finally:
            semantic_recall_store.SemanticRecallStore = original
    if backend == "postgres":
        await store.warm_retrieval_index()
    return store, pool, graph_db


async def load_memories(store, rows, matrix, *, room: str):
    started = time.perf_counter()
    for row, vector in zip(rows, matrix, strict=True):
        ok = await store.add_memory(
            content=row.content,
            raw_content=row.content,
            wing="scale",
            room=room,
            importance=0.5,
            source="lifesim",
            metadata={
                "scale_seed": row.seed,
                "scale_archetype": row.archetype,
                "scale_turn": row.turn,
                "scale_timestamp": row.timestamp,
            },
            current_time=datetime.fromisoformat(row.timestamp),
            embedding=vector.tolist(),
            record_type="episode",
        )
        if not ok:
            raise RuntimeError(f"MemoryStore.add_memory failed at {row.memory_id}")
    return time.perf_counter() - started


async def _raw_query_ms(store, vector, row_count: int, *, room: str) -> float:
    started = time.perf_counter()
    limit = min(60, max(row_count, 1))
    if store.qdrant_store.client:
        await asyncio.to_thread(
            store.qdrant_store.search_vector_memories,
            query_vector=vector,
            limit=limit,
            filter_dict={"wing": "scale", "room": room},
        )
    else:
        async with store.pool.acquire() as conn:
            if store.is_sqlite:
                await store._sqlite_vector_index.top_k(
                    conn, "scale", vector, limit, room=room
                )
            else:
                await conn.execute(f"SET hnsw.ef_search = {max(40, limit)}")
                await conn.fetch(
                    store._PG_SIMILARITY_SQL,
                    str(vector),
                    "scale",
                    room,
                    limit,
                )
    return (time.perf_counter() - started) * 1000


async def measure_store(store, rows, matrix, probes, *, room: str, query_embedder=None):
    contents = [row.content for row in rows]
    content_by_id = {row.memory_id: row.content for row in rows}
    probe_set = probes[: min(100, len(probes))]
    if query_embedder is not None:
        store.get_embedding = query_embedder
    index_started = time.perf_counter()
    await store.warm_retrieval_index("scale")
    index_build_seconds = time.perf_counter() - index_started
    index = getattr(store, "_sqlite_vector_index", None)
    indexed_rows = (
        len(index._wings.get("scale").ids)
        if index and "scale" in index._wings
        else None
    )
    samples: list[float] = []
    raw_samples: list[float] = []
    hits = 0
    measured = 0
    for probe in probe_set:
        query_vector = await store.get_embedding(probe.text)
        if not query_vector:
            continue
        raw_samples.append(
            await _raw_query_ms(store, query_vector, len(rows), room=room)
        )
        started = time.perf_counter()
        found = await store.search_memories(
            probe.text,
            wing="scale",
            room=room,
            limit=5,
            refresh_on_recall=False,
            current_time=datetime.now(UTC),
        )
        samples.append((time.perf_counter() - started) * 1000)
        returned = {_result_content(item) for item in found or []}
        expected = {
            content_by_id[memory_id]
            for memory_id in probe.support_memory_ids
            if memory_id in content_by_id
        }
        measured += 1
        hits += int(bool(returned & expected))

    decay_started = time.perf_counter()
    await _decay_in_batches(store, contents)
    decay_seconds = time.perf_counter() - decay_started

    no_load = []
    for probe in probe_set[: min(25, len(probe_set))]:
        store._l1_cache.clear()
        started = time.perf_counter()
        await store.search_memories(
            probe.text,
            wing="scale",
            room=room,
            limit=5,
            refresh_on_recall=False,
            current_time=datetime.now(UTC),
        )
        no_load.append((time.perf_counter() - started) * 1000)

    background = asyncio.create_task(_decay_in_batches(store, contents))
    foreground = []
    for probe in probe_set[: min(25, len(probe_set))]:
        store._l1_cache.clear()
        started = time.perf_counter()
        await store.search_memories(
            probe.text,
            wing="scale",
            room=room,
            limit=5,
            refresh_on_recall=False,
            current_time=datetime.now(UTC),
        )
        foreground.append((time.perf_counter() - started) * 1000)
    await background

    return {
        "retrieval_ms": _percentiles(samples),
        "raw_backend_query_ms": _percentiles(raw_samples),
        "recall": {
            "hit_at_5": hits / measured if measured else None,
            "probes": measured,
        },
        "decay_pass_seconds": decay_seconds,
        "decay_batch_size": DECAY_BATCH_SIZE,
        "index_build_seconds": index_build_seconds,
        "foreground_no_load_ms": _percentiles(no_load),
        "foreground_during_decay_ms": _percentiles(foreground),
        "foreground_delta_p95_ms": _percentile(foreground, 95)
        - _percentile(no_load, 95),
        "sqlite_scan_limit": Config.MEMORY_SQLITE_SCAN_LIMIT,
        "sqlite_indexed_rows_before_decay": indexed_rows,
        "reflex_retrieval_under_50ms": (
            _percentile(samples, 95) < 50 if samples else None
        ),
    }


async def _decay_in_batches(store, contents):
    for start in range(0, len(contents), DECAY_BATCH_SIZE):
        await store.apply_actr_decay(contents[start : start + DECAY_BATCH_SIZE])


async def load_graph_edges(graph_db, edges, *, namespace: str):
    started = time.perf_counter()
    for edge in edges:
        await graph_db.consolidate_relationship(
            f"{namespace}:{edge.seed}:{edge.source}",
            edge.relation,
            f"{namespace}:{edge.seed}:{edge.target}",
            properties={"category": "lifesim", "source_seed": edge.seed},
        )
    return time.perf_counter() - started


async def measure_graph(graph_db, edges, seed, *, namespace: str):
    if not graph_db:
        return {"status": "not-applicable", "reason": "backend has no Neo4j graph"}
    names = sorted(
        {
            f"{namespace}:{edge.seed}:{name}"
            for edge in edges
            for name in (edge.source, edge.target)
        }
    )
    if not names:
        return {"status": "not-measured", "reason": "lifesim produced no entity edges"}
    entity = random.Random(seed).choice(names)
    counts_result = await graph_db.execute_query(
        "MATCH (e:Entity) WHERE e.name STARTS WITH $prefix "
        "OPTIONAL MATCH (e)-[r]-() RETURN count(DISTINCT e) AS nodes, "
        "count(DISTINCT r) AS edges",
        {"prefix": namespace + ":"},
    )
    graph_counts = (
        {
            "nodes": int(counts_result[0]["nodes"]),
            "edges": int(counts_result[0]["edges"]),
        }
        if counts_result
        else {"nodes": 0, "edges": 0}
    )
    timings = {}
    counts = {}
    for hops in (1, 2):
        started = time.perf_counter()
        result = await graph_db.execute_query(
            "MATCH p=(e:Entity {name: $name})-[*1.." + str(hops) + "]-(n:Entity) "
            "WHERE length(p) = " + str(hops) + " RETURN count(p) AS paths",
            {"name": entity},
        )
        timings[f"{hops}_hop_ms"] = (time.perf_counter() - started) * 1000
        counts[f"{hops}_hop_paths"] = int(result[0]["paths"]) if result else 0
    return {
        "status": "measured",
        "entity": entity,
        **graph_counts,
        **timings,
        **counts,
    }


def _percentile(values, quantile):
    if not values:
        return None
    return float(np.percentile(values, quantile))


def _percentiles(values):
    return {f"p{q}": _percentile(values, q) for q in (50, 95, 99)}


def _current_rss_bytes():
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except ImportError:
        pass
    try:
        if platform.system() == "Linux":
            fields = Path(f"/proc/{os.getpid()}/statm").read_text().split()
            return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(os.getpid())],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return int(result.stdout.strip()) * 1024 if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return None


class RSSSampler:
    def __init__(self):
        self.initial = _current_rss_bytes()
        self.peak = self.initial
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self):
        self._thread.start()

    def _sample(self):
        while not self._stop.wait(0.1):
            current = _current_rss_bytes()
            if current is not None and (self.peak is None or current > self.peak):
                self.peak = current

    def close(self):
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(timeout=2)
        current = _current_rss_bytes()
        if current is not None and (self.peak is None or current > self.peak):
            self.peak = current


async def run_cell(
    backend: str,
    size: int,
    rows,
    probes,
    matrix,
    *,
    data_dir: Path,
    database_url: str | None = None,
    query_embedder=None,
    graph_edges=(),
    corpus_sha: str = "local",
    embedding_sha: str = "no-embedding",
):
    run_key = f"{corpus_sha[:12]}-{embedding_sha[:12]}-{size}"
    cell_dir = data_dir / backend / run_key
    cell_dir.mkdir(parents=True, exist_ok=True)
    room = f"p8-{run_key}"
    graph_namespace = f"scale-{run_key}"
    store = pool = graph_db = None
    rss = RSSSampler()
    rss.start()
    cpu_before = time.process_time()
    started = time.perf_counter()
    try:
        store, pool, graph_db = await open_store(backend, cell_dir, database_url)
        selected_seeds = {row.seed for row in rows}
        selected_edges = [edge for edge in graph_edges if edge.seed in selected_seeds]
        graph_build_seconds = 0.0
        if graph_db:
            graph_build_seconds = await load_graph_edges(
                graph_db, selected_edges, namespace=graph_namespace
            )
        build_seconds = await load_memories(store, rows, matrix, room=room)
        async with pool.acquire() as conn:
            if store.is_sqlite:
                row_count = await conn.fetch(
                    "SELECT count(*) AS n FROM memories WHERE wing = ? AND room = ?",
                    "scale",
                    room,
                )
            else:
                row_count = await conn.fetch(
                    "SELECT count(*) AS n FROM memories WHERE wing = $1 AND room = $2",
                    "scale",
                    room,
                )
        scoped_memory_rows_before_decay = int(row_count[0]["n"])
        measured = await measure_store(
            store, rows, matrix, probes, room=room, query_embedder=query_embedder
        )
        async with pool.acquire() as conn:
            if store.is_sqlite:
                row_count = await conn.fetch(
                    "SELECT count(*) AS n FROM memories WHERE wing = ? AND room = ?",
                    "scale",
                    room,
                )
            else:
                row_count = await conn.fetch(
                    "SELECT count(*) AS n FROM memories WHERE wing = $1 AND room = $2",
                    "scale",
                    room,
                )
        scoped_memory_rows_after_measurement = int(row_count[0]["n"])
        db_bytes = None
        if backend == "sqlite":
            db_path = cell_dir / "memory.sqlite3"
            db_bytes = db_path.stat().st_size if db_path.exists() else 0
        elif backend == "postgres":
            async with pool.acquire() as conn:
                db_bytes = int(
                    await conn.fetchval("SELECT pg_database_size(current_database())")
                )
        service_metrics = _docker_metrics(backend)
        if backend.startswith("qdrant-") and store.qdrant_store.client:
            try:
                info = store.qdrant_store.client.get_collection(
                    store.qdrant_store.collection_name
                )
                service_metrics["vector_points"] = info.points_count
                service_metrics["vector_segments"] = info.segments_count
            except Exception as exc:
                service_metrics["vector_store_error"] = f"{type(exc).__name__}: {exc}"
        rss.close()
        return {
            "status": "measured",
            "backend": backend,
            "size": size,
            "build_seconds": build_seconds,
            "graph_build_seconds": graph_build_seconds,
            "elapsed_seconds": time.perf_counter() - started,
            "cpu_seconds": time.process_time() - cpu_before,
            "rss_peak_bytes": rss.peak,
            "rss_before_bytes": rss.initial,
            "database_bytes": db_bytes,
            "scoped_memory_rows_before_decay": scoped_memory_rows_before_decay,
            "scoped_memory_rows_after_measurement": scoped_memory_rows_after_measurement,
            "disk_bytes": sum(
                p.stat().st_size for p in cell_dir.rglob("*") if p.is_file()
            ),
            "metrics": measured,
            "service_metrics": service_metrics,
            "graph_traversal": await measure_graph(
                graph_db, selected_edges, size, namespace=graph_namespace
            ),
            "image_tags": _image_tags(backend),
            "git_sha": git_sha(),
            "working_tree_clean": working_tree_clean(),
            "hardware": hardware(),
        }
    finally:
        rss.close()
        if store:
            await store._http_client.aclose()
        if graph_db:
            await graph_db.close()
        if pool:
            await pool.close()


def not_run_cell(backend: str, size: int, reason: str):
    return {"status": "not-run", "backend": backend, "size": size, "reason": reason}


def _docker_metrics(backend: str) -> dict:
    default_container = {
        "postgres": "postgres_db",
        "qdrant-pinned": "brain_vectors",
        "qdrant-latest": "brain_vectors",
        "neo4j": "brain_graph",
    }.get(backend)
    variant = "PINNED" if backend == "qdrant-pinned" else "LATEST"
    container = os.getenv(
        f"SCALE_QDRANT_{variant}_CONTAINER",
        os.getenv("SCALE_CONTAINER_NAME", default_container or ""),
    )
    store_path = {
        "postgres": "/var/lib/postgresql/data",
        "qdrant-pinned": "/qdrant/storage",
        "qdrant-latest": "/qdrant/storage",
        "neo4j": "/data",
    }.get(backend)
    if not container or not shutil.which("docker"):
        return {"status": "unavailable", "reason": "docker CLI/container not available"}
    try:
        result = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.CPUPerc}}\t{{.MemUsage}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        cpu, memory = result.stdout.strip().split("\t", maxsplit=1)
        details = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Image}}|{{.Config.Image}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        image_id, image_ref = details.split("|", maxsplit=1)
        storage_bytes = None
        if store_path:
            storage = subprocess.run(
                ["docker", "exec", container, "du", "-sk", store_path],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if storage.returncode == 0:
                storage_bytes = int(storage.stdout.split()[0]) * 1024
        return {
            "status": "measured",
            "container": container,
            "cpu_percent": cpu,
            "memory_usage": memory,
            "image_id": image_id,
            "image_ref": image_ref,
            "storage_bytes": storage_bytes,
        }
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
    ) as exc:
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}


def _image_tags(backend: str) -> dict:
    if backend == "postgres":
        return {"postgres": os.getenv("SCALE_POSTGRES_IMAGE", "pgvector/pgvector:pg16")}
    if backend == "neo4j":
        return {"neo4j": os.getenv("SCALE_NEO4J_IMAGE", "neo4j:5.26.0")}
    if backend == "qdrant-pinned":
        return {
            "qdrant": os.getenv("SCALE_QDRANT_PINNED_IMAGE", "qdrant/qdrant:v1.9.0")
        }
    if backend == "qdrant-latest":
        return {"qdrant": os.getenv("SCALE_QDRANT_IMAGE")}
    return {}
