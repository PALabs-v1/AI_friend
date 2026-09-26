"""Per-persona runtime and storage growth measurements for BrainBench.

The suite accepts both BrainBench modes. ``architecture_only`` is
"architecture overhead only: no model time, placeholder memory content": it
patches ``get_embedding`` with the async ``architecture_embedding`` and reports
Mac CPU/RSS/SQLite costs. Every reflection stores NullLLM's identical ``"{}"``
summary (DR-037), which production's dedup correctly merges into one row, so
memory row and vector counts stay flat by construction here: memory growth is
only measurable in ``llm_augmented``. Nothing here claims model latency.
``llm_augmented`` uses the
service's real model and embedding clients, so ``turn_latency_ms`` includes
model time and memory growth reflects real consolidation. VRAM is not
applicable and is absent from metrics.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import resource
import sqlite3
import struct
import sys
import time
from pathlib import Path
from typing import Any

from app import clock
from app.cognitive.tom import MAX_KNOWN_CONCEPTS
from evals.brainbench.adapters import BrainBenchService
from evals.brainbench.stats import SuiteOutcome

logger = logging.getLogger(__name__)

_EMBEDDING_DIMENSION = 768
_SECONDS_PER_SIMULATED_DAY = 86_400.0
_DAYS_PER_SIMULATED_MONTH = 30.4375


def deterministic_embedding(
    text: str, dimension: int = _EMBEDDING_DIMENSION
) -> list[float]:
    """Create a stable, normalized vector of the requested production dimension."""
    if dimension <= 0:
        raise ValueError("embedding dimension must be positive")
    raw = hashlib.shake_256(text.encode("utf-8")).digest(dimension * 2)
    values = [value[0] / 32767.5 - 1.0 for value in struct.iter_unpack("<H", raw)]
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else [0.0] * dimension


async def architecture_embedding(text: str) -> list[float]:
    """Async drop-in for ``MemoryStore.get_embedding``.

    ``MemoryStore.add_memory`` awaits ``get_embedding``. Patching it with the
    synchronous ``deterministic_embedding`` raised ``TypeError`` inside
    ``add_memory``, which catches it and returns ``False``, so a whole
    six-month replay stored zero memory rows while every growth curve looked
    healthy. Always patch with this coroutine.
    """
    return deterministic_embedding(text)


def rss_mib(max_rss: float, platform: str | None = None) -> float:
    """Normalize getrusage's peak-RSS value to MiB (macOS bytes, Linux KiB)."""
    platform = sys.platform if platform is None else platform
    divisor = 1024.0 * 1024.0 if platform == "darwin" else 1024.0
    return float(max_rss) / divisor


def _sqlite_files(run_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in run_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
    )


def _sqlite_snapshot(run_dir: Path) -> dict[str, float]:
    """Return database file bytes, row counts, and stored embedding counts."""
    result: dict[str, float] = {}
    for database in _sqlite_files(run_dir):
        relative = database.relative_to(run_dir).as_posix()
        result[f"db_bytes:{relative}"] = float(database.stat().st_size)
        try:
            connection = sqlite3.connect(
                f"file:{database}?mode=ro", uri=True, timeout=2
            )
            try:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for (table,) in tables:
                    quoted = '"' + table.replace('"', '""') + '"'
                    result[f"rows:{relative}:{table}"] = float(
                        connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[
                            0
                        ]
                    )
                    columns = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({quoted})")
                    }
                    if "embedding" in columns:
                        count = 0
                        for (embedding,) in connection.execute(
                            f"SELECT embedding FROM {quoted} WHERE embedding IS NOT NULL"
                        ):
                            try:
                                vector = (
                                    json.loads(embedding)
                                    if isinstance(embedding, str)
                                    else embedding
                                )
                            except (TypeError, json.JSONDecodeError):
                                continue
                            if (
                                isinstance(vector, list)
                                and len(vector) == _EMBEDDING_DIMENSION
                            ):
                                count += 1
                        result[f"stored_vectors:{relative}:{table}"] = float(count)
            finally:
                connection.close()
        except sqlite3.Error as exc:
            logger.warning(
                "BrainBench resources could not inspect %s: %s", database, exc
            )
    return result


def _run_directory_bytes(run_dir: Path) -> float:
    return float(
        sum(path.stat().st_size for path in run_dir.rglob("*") if path.is_file())
    )


def _collection_lengths(
    service: BrainBenchService,
) -> tuple[dict[str, float], dict[str, int]]:
    """Measure known mutable collections and report their production caps."""
    cognitive = service.cognitive
    memory_store = service.memory_store
    collections: dict[str, Any] = {
        "cognitive.surfaced_memories": cognitive.surfaced_memories,
        "identity.history": cognitive.identity.history,
        "memory.l1_cache": memory_store._l1_cache,
        "memory.goal_buffer.concepts": memory_store.goal_buffer.concepts,
        "state.active_goals": getattr(
            cognitive.state.current_state, "active_goals", None
        ),
        "state.user_mental_model.known_concepts": getattr(
            getattr(cognitive.state.current_state, "user_mental_model", None),
            "known_concepts",
            None,
        ),
        "state.user_mental_model.implied_goals": getattr(
            getattr(cognitive.state.current_state, "user_mental_model", None),
            "implied_goals",
            None,
        ),
    }
    governor = getattr(cognitive.pipeline, "learning_governor", None)
    proposals = getattr(governor, "_proposals", None)
    if proposals is not None:
        collections["learning_governor.proposals"] = proposals
    index = getattr(memory_store, "_sqlite_vector_index", None)
    wings = getattr(index, "_wings", {})
    if isinstance(wings, dict):
        for wing, entry in wings.items():
            ids = getattr(entry, "ids", None)
            if ids is not None:
                collections[f"memory.vector_index.{wing}"] = ids

    caps = {
        "cognitive.surfaced_memories": 5,
        "memory.l1_cache": int(memory_store._l1_cache_max),
        "memory.goal_buffer.concepts": int(memory_store.goal_buffer.capacity),
        "state.user_mental_model.known_concepts": MAX_KNOWN_CONCEPTS,
    }
    if index is not None:
        for key in collections:
            if key.startswith("memory.vector_index."):
                caps[key] = int(index.scan_limit)

    lengths = {
        name: float(len(value))
        for name, value in collections.items()
        if value is not None and isinstance(value, (list, dict, set, tuple))
    }
    return lengths, caps


def _sample_metrics(service: BrainBenchService, run_dir: Path) -> dict[str, float]:
    process_usage = resource.getrusage(resource.RUSAGE_SELF)
    metrics = {
        "rss_peak_mib": rss_mib(process_usage.ru_maxrss),
        "cpu_user_seconds": float(process_usage.ru_utime),
        "cpu_system_seconds": float(process_usage.ru_stime),
        "cpu_total_seconds": float(process_usage.ru_utime + process_usage.ru_stime),
        "run_directory_bytes": _run_directory_bytes(run_dir),
        "vram_applicable": 0.0,
        "graph_db_counts_applicable": 0.0,
    }
    metrics.update(_sqlite_snapshot(run_dir))
    index = getattr(service.memory_store, "_sqlite_vector_index", None)
    wings = getattr(index, "_wings", None)
    if isinstance(wings, dict):
        metrics["vector_index_vectors"] = float(
            sum(len(getattr(wing, "ids", ())) for wing in wings.values())
        )
    lengths, caps = _collection_lengths(service)
    metrics.update({f"structure:{name}": size for name, size in lengths.items()})
    metrics.update({f"structure_cap:{name}": float(cap) for name, cap in caps.items()})
    return metrics


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def latency_percentiles(outcomes: list[SuiteOutcome]) -> dict[str, Any]:
    """P50/P95/P99/max per latency, overall and grouped by simulated month."""
    turns = [outcome for outcome in outcomes if "turn" in outcome.categories]
    groups: dict[str, list[SuiteOutcome]] = {"overall": turns}
    month_numbers = sorted(
        {
            int(outcome.metrics["elapsed_days"] // _DAYS_PER_SIMULATED_MONTH)
            for outcome in turns
            if "elapsed_days" in outcome.metrics
        }
    )
    for month in month_numbers:
        groups[f"month_{month + 1}"] = [
            outcome
            for outcome in turns
            if int(outcome.metrics.get("elapsed_days", -1) // _DAYS_PER_SIMULATED_MONTH)
            == month
        ]
    result: dict[str, Any] = {}
    for group_name, rows in groups.items():
        metrics: dict[str, Any] = {}
        for metric in ("turn_latency_ms", "foreground_latency_ms"):
            values = [row.metrics[metric] for row in rows if metric in row.metrics]
            if values:
                metrics[metric] = {
                    "p50": _percentile(values, 50),
                    "p95": _percentile(values, 95),
                    "p99": _percentile(values, 99),
                    "max": max(values),
                    "n": len(values),
                }
        result[group_name] = metrics
    return result


def _r_squared(xs: list[float], ys: list[float]) -> tuple[float, float]:
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    slope = (
        sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
        / denominator
        if denominator
        else 0.0
    )
    intercept = y_mean - slope * x_mean
    total = sum((y - y_mean) ** 2 for y in ys)
    residual = sum(
        (y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys, strict=True)
    )
    return (1.0 - residual / total if total else 1.0), slope


def _growth_shape(xs: list[float], ys: list[float]) -> str | None:
    """Classify turn-count growth: linear fit, log fit, or persistent growth.

    A positive linear fit with R² >= .90 is ``linear``. A positive log1p fit
    that beats the linear fit by .05 is ``sublinear``. Other rising series are
    ``unbounded`` when their final third still has a positive least-squares
    slope; flat or non-rising series are ``sublinear``. These are descriptive
    finite-run labels, not extrapolation guarantees.
    """
    if len(xs) < 2:
        return None
    linear_r2, linear_slope = _r_squared(xs, ys)
    if len(xs) >= 4:
        midpoint = len(xs) // 2
        early_slope = _r_squared(xs[: midpoint + 1], ys[: midpoint + 1])[1]
        late_slope = _r_squared(xs[midpoint:], ys[midpoint:])[1]
        if early_slope > 0 and 0 <= late_slope < early_slope * 0.7:
            return "sublinear"
    log_xs = [math.log1p(x) for x in xs]
    log_r2, log_slope = _r_squared(log_xs, ys)
    if log_slope > 0 and log_r2 >= linear_r2 + 0.05:
        return "sublinear"
    if linear_slope > 0 and linear_r2 >= 0.90:
        return "linear"
    third_start = max(0, (2 * len(xs)) // 3)
    tail_slope = (
        _r_squared(xs[third_start:], ys[third_start:])[1]
        if len(xs) - third_start > 1
        else 0.0
    )
    return "unbounded" if ys[-1] > ys[0] and tail_slope > 0 else "sublinear"


def growth_curves(outcomes: list[SuiteOutcome]) -> dict[str, dict[str, Any]]:
    """Summarize sample-point metrics and their finite-run growth shape."""
    samples = [outcome for outcome in outcomes if "sample" in outcome.categories]
    metric_names = sorted(
        {
            name
            for outcome in samples
            for name, value in outcome.metrics.items()
            if name not in {"turn_index", "elapsed_days"}
            and not name.startswith("structure_cap:")
            and math.isfinite(value)
        }
    )
    curves: dict[str, dict[str, Any]] = {}
    for name in metric_names:
        rows = [outcome for outcome in samples if name in outcome.metrics]
        values = [row.metrics[name] for row in rows]
        days = [row.metrics.get("elapsed_days", 0.0) for row in rows]
        turns = [row.metrics.get("turn_index", float(i)) for i, row in enumerate(rows)]
        _, slope_per_day = _r_squared(days, values) if len(values) > 1 else (1.0, 0.0)
        curves[name] = {
            "first": values[0],
            "last": values[-1],
            "slope_per_simulated_day": slope_per_day,
            "shape": _growth_shape(turns, values),
            "n": len(values),
        }
    return curves


def unbounded_structures(outcomes: list[SuiteOutcome]) -> dict[str, dict[str, Any]]:
    """Report unbounded collections and known unpruned SQLite tables.

    SQLite pruning was checked in production for ``workspace_transitions``:
    ``WorkspaceStore`` inserts and lists these rows, but no DELETE, prune, or
    trim path exists. Other SQLite tables are only included here once that
    production audit establishes the absence of pruning.
    """
    samples = [outcome for outcome in outcomes if "sample" in outcome.categories]
    if len(samples) < 3:
        return {}
    start = (2 * len(samples)) // 3
    tail = samples[start:]
    caps = {
        metric.removeprefix("structure_cap:"): int(value)
        for row in samples
        for metric, value in row.metrics.items()
        if metric.startswith("structure_cap:")
    }
    still_increasing = {}
    bounded_by_cap = {}
    for metric in sorted(
        {key for row in tail for key in row.metrics if key.startswith("structure:")}
    ):
        name = metric.removeprefix("structure:")
        if name in caps:
            bounded_by_cap[name] = {
                "cap": caps[name],
                "first": tail[0].metrics.get(metric),
                "last": tail[-1].metrics.get(metric),
            }
            continue
        values = [row.metrics[metric] for row in tail if metric in row.metrics]
        if len(values) >= 2:
            slope = _r_squared(list(range(len(values))), values)[1]
            if values[-1] > values[0] and slope > 0:
                still_increasing[name] = {
                    "first": values[0],
                    "last": values[-1],
                    "tail_slope": slope,
                }
    unpruned_sqlite_tables = {"workspace_transitions"}
    sqlite_table_growth: dict[str, dict[str, Any]] = {}
    for metric in sorted(
        {key for row in samples for key in row.metrics if key.startswith("rows:")}
    ):
        database_and_table = metric.removeprefix("rows:")
        database, separator, table = database_and_table.rpartition(":")
        if not separator or table not in unpruned_sqlite_tables:
            continue
        rows = [row for row in samples if metric in row.metrics]
        xs = [
            row.metrics.get("turn_index", float(index))
            for index, row in enumerate(rows)
        ]
        ys = [row.metrics[metric] for row in rows]
        if _growth_shape(xs, ys) != "linear":
            continue
        start = (2 * len(rows)) // 3
        tail_slope = (
            _r_squared(xs[start:], ys[start:])[1] if len(xs) - start > 1 else 0.0
        )
        sqlite_table_growth[f"{database}:{table}"] = {
            "first": ys[0],
            "last": ys[-1],
            "tail_slope_rows_per_turn": tail_slope,
            "pruning": "no production DELETE, prune, or trim path found",
        }
    return {
        "bounded_by_cap": bounded_by_cap,
        "still_increasing": still_increasing,
        "still_increasing_sqlite_tables": sqlite_table_growth,
    }


async def _await_turn_background(
    service: BrainBenchService, *, seed: int, turn_id: str, timeout: float
) -> int:
    tasks = [
        service.cognitive.pipeline._system2_task,
        service.cognitive.last_reflection_task,
        getattr(service.cognitive, "_retrieval_warmup_task", None),
    ]
    tasks.extend(getattr(service.cognitive.state, "_background_tasks", ()))
    tasks.extend(getattr(service.memory_store, "_background_tasks", ()))
    timed_out = 0
    seen: set[int] = set()
    for task in tasks:
        if task is None or id(task) in seen or task.done():
            continue
        seen.add(id(task))
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except TimeoutError:
            timed_out += 1
            logger.warning(
                "BrainBench resources background task timed out seed=%s turn=%s",
                seed,
                turn_id,
            )
        except Exception as exc:
            logger.warning(
                "BrainBench resources background task failed seed=%s turn=%s: %s",
                seed,
                turn_id,
                exc,
            )
    return timed_out


async def run_resources_suite(
    simulation: tuple[Any, list[Any], list[Any], list[Any], list[Any]],
    service: BrainBenchService,
    *,
    persona_seed: int | None = None,
    sample_every_turns: int = 10,
    background_timeout: float = 10.0,
) -> list[SuiteOutcome]:
    """Replay either BrainBench mode and record turn latency and resource samples.

    In ``architecture_only`` this is architecture overhead only: no model time,
    and memory rows carry placeholder ``"{}"`` content. In ``llm_augmented``,
    turn latency includes real model calls and storage metrics include real
    memory growth.
    """
    sim, turns, _annotations, _probes, _answers = simulation
    if service.mode not in {"architecture_only", "llm_augmented"}:
        raise ValueError(f"unknown BrainBench mode: {service.mode!r}")
    if sample_every_turns <= 0:
        raise ValueError("sample_every_turns must be positive")
    if background_timeout <= 0:
        raise ValueError("background_timeout must be positive")

    try:
        database_rows = service.memory_store.pool.connection.conn.execute(
            "PRAGMA database_list"
        ).fetchall()
        database_path = next((row[2] for row in database_rows if row[2]), None)
    except (AttributeError, sqlite3.Error):
        database_path = None
    if not database_path or database_path == ":memory:":
        raise ValueError(
            "resources suite requires the adapter's file-backed SQLite run directory"
        )
    run_dir = Path(database_path).resolve().parent
    seed = sim.seed if persona_seed is None else persona_seed
    outcomes: list[SuiteOutcome] = []
    mode_category = (
        "architecture overhead only: no model time, placeholder memory content"
        if service.mode == "architecture_only"
        else "llm_augmented: real model time and memory growth"
    )
    sim_clock = clock.ManualClock(sim.start)
    last_sampled_turn = 0

    with clock.use_clock(sim_clock):
        for index, turn in enumerate(turns, start=1):
            sim_clock.set(turn.t)
            started = time.perf_counter()
            foreground_started = started
            raw_event = {
                "id": turn.turn_id,
                "type": "USER_MESSAGE",
                "content": turn.text,
                "metadata": {},
            }
            outputs = [
                output async for output in service.cognitive.process_event(raw_event)
            ]
            foreground_ms = (time.perf_counter() - foreground_started) * 1000.0
            errors = [output for output in outputs if output.get("type") == "error"]
            if errors:
                raise RuntimeError(
                    f"BrainBench resources turn {turn.turn_id} failed: {errors!r}"
                )
            # User-facing response latency ends with the completed stream.
            # Background consolidation is awaited below so storage samples are
            # complete, but it must not inflate the latency users experience.
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timed_out = await _await_turn_background(
                service, seed=seed, turn_id=turn.turn_id, timeout=background_timeout
            )
            elapsed_days = (
                turn.t - sim.start
            ).total_seconds() / _SECONDS_PER_SIMULATED_DAY
            metrics = {
                "turn_index": float(index),
                "elapsed_days": elapsed_days,
                "turn_latency_ms": elapsed_ms,
                "foreground_latency_ms": foreground_ms,
                "background_task_timeouts": float(timed_out),
            }
            telemetry = next(
                (
                    output.get("data")
                    for output in outputs
                    if output.get("type") == "pipeline_telemetry"
                ),
                None,
            )
            if isinstance(telemetry, dict):
                for name, value in telemetry.items():
                    if isinstance(value, (int, float)) and math.isfinite(value):
                        metrics[f"stage:{name}"] = float(value)
            outcomes.append(
                SuiteOutcome(
                    turn.turn_id,
                    seed,
                    "resources",
                    ("turn", mode_category),
                    metrics,
                    service.mode,
                )
            )

            if index % sample_every_turns == 0 or index == len(turns):
                sample_metrics = _sample_metrics(service, run_dir)
                sample_metrics.update(
                    {"turn_index": float(index), "elapsed_days": elapsed_days}
                )
                outcomes.append(
                    SuiteOutcome(
                        f"sample:{index}",
                        seed,
                        "resources",
                        ("sample", mode_category),
                        sample_metrics,
                        service.mode,
                    )
                )
                last_sampled_turn = index
            if index % 100 == 0:
                logger.info(
                    "BrainBench resources replay seed=%s turns=%d/%d",
                    seed,
                    index,
                    len(turns),
                )

    if turns and last_sampled_turn != len(turns):
        sample_metrics = _sample_metrics(service, run_dir)
        final_turn = turns[-1]
        sample_metrics.update(
            {
                "turn_index": float(len(turns)),
                "elapsed_days": (final_turn.t - sim.start).total_seconds()
                / _SECONDS_PER_SIMULATED_DAY,
            }
        )
        outcomes.append(
            SuiteOutcome(
                f"sample:{len(turns)}",
                seed,
                "resources",
                ("sample", mode_category),
                sample_metrics,
                service.mode,
            )
        )
    return outcomes
