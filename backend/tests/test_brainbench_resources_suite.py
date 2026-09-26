"""Scoring, filesystem inventory, and real architecture-only resource replay tests."""

from __future__ import annotations

import math
import os
import sqlite3
import time
from pathlib import Path

import pytest

from app.config import Config
from evals.brainbench.adapters import build_cognitive_service
from evals.brainbench.resources_suite import (
    _sqlite_snapshot,
    architecture_embedding,
    deterministic_embedding,
    growth_curves,
    latency_percentiles,
    rss_mib,
    run_resources_suite,
    unbounded_structures,
)
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build


def _outcome(
    key: str,
    categories: tuple[str, ...],
    metrics: dict[str, float],
) -> SuiteOutcome:
    return SuiteOutcome(key, 1000, "resources", categories, metrics)


def test_latency_percentiles_interpolate_and_group_by_simulated_month():
    outcomes = [
        _outcome(
            str(index),
            ("turn",),
            {
                "elapsed_days": float(index - 1),
                "turn_latency_ms": float(index),
                "foreground_latency_ms": float(index * 2),
            },
        )
        for index in range(1, 5)
    ]

    result = latency_percentiles(outcomes)

    assert result["overall"]["turn_latency_ms"] == {
        "p50": 2.5,
        "p95": pytest.approx(3.85),
        "p99": pytest.approx(3.97),
        "max": 4.0,
        "n": 4,
    }
    assert result["month_1"]["foreground_latency_ms"]["p50"] == 5.0
    assert all(
        "p99" in result["overall"][name]
        for name in ("turn_latency_ms", "foreground_latency_ms")
    )


@pytest.mark.parametrize(
    ("platform", "value", "expected"),
    [("darwin", 2 * 1024 * 1024, 2.0), ("linux", 2048, 2.0)],
)
def test_rss_normalization_per_platform(monkeypatch, platform, value, expected):
    monkeypatch.setattr("evals.brainbench.resources_suite.sys.platform", platform)
    assert rss_mib(value) == expected


def test_growth_curves_classify_linear_flat_and_log_series():
    turn_counts = list(range(1, 8))
    linear = [float(value * 4) for value in turn_counts]
    flat = [7.0] * len(turn_counts)
    log_series = [10.0 * math.log1p(value) for value in turn_counts]

    def run(values: list[float]) -> str:
        outcomes = [
            _outcome(
                f"sample:{index}",
                ("sample",),
                {
                    "turn_index": float(index),
                    "elapsed_days": float(index * 2),
                    "db_rows": value,
                },
            )
            for index, value in zip(turn_counts, values, strict=True)
        ]
        return growth_curves(outcomes)["db_rows"]["shape"]

    assert run(linear) == "linear"
    assert run(flat) == "sublinear"
    assert run(log_series) == "sublinear"


def test_unbounded_structures_reports_uncapped_growth_in_last_third():
    outcomes = [
        _outcome(
            f"sample:{index}",
            ("sample",),
            {"turn_index": float(index), "structure:leaky_registry": float(index)},
        )
        for index in range(1, 7)
    ]
    assert unbounded_structures(outcomes) == {
        "bounded_by_cap": {},
        "still_increasing": {
            "leaky_registry": {"first": 5.0, "last": 6.0, "tail_slope": 1.0}
        },
        "still_increasing_sqlite_tables": {},
    }


def test_unbounded_structures_reports_linear_unpruned_sqlite_tables():
    outcomes = [
        _outcome(
            f"sample:{index}",
            ("sample",),
            {
                "turn_index": float(index),
                "rows:workspace.db:workspace_transitions": float(index * 5),
            },
        )
        for index in range(1, 7)
    ]

    assert unbounded_structures(outcomes) == {
        "bounded_by_cap": {},
        "still_increasing": {},
        "still_increasing_sqlite_tables": {
            "workspace.db:workspace_transitions": {
                "first": 5.0,
                "last": 30.0,
                "tail_slope_rows_per_turn": 5.0,
                "pruning": "no production DELETE, prune, or trim path found",
            }
        },
    }


def test_sqlite_snapshot_discovers_tables_and_counts_production_dimension_vectors(
    tmp_path: Path,
):
    nested = tmp_path / "state"
    nested.mkdir()
    database = nested / "sample.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories (id TEXT, embedding TEXT)")
        connection.execute("CREATE TABLE workspace_transitions (id INTEGER)")
        connection.executemany(
            "INSERT INTO memories VALUES (?, ?)",
            [
                ("valid", "[" + ",".join(["0.0"] * 768) + "]"),
                ("wrong_dimension", "[0.0, 1.0]"),
                ("missing", None),
            ],
        )
        connection.execute("INSERT INTO workspace_transitions VALUES (1)")

    snapshot = _sqlite_snapshot(tmp_path)

    assert snapshot["rows:state/sample.db:memories"] == 3.0
    assert snapshot["rows:state/sample.db:workspace_transitions"] == 1.0
    assert snapshot["stored_vectors:state/sample.db:memories"] == 1.0
    assert snapshot["db_bytes:state/sample.db"] == database.stat().st_size


def test_architecture_embedding_is_deterministic_and_production_dimension():
    first = deterministic_embedding("same memory text")
    second = deterministic_embedding("same memory text")

    assert first == second
    assert len(first) == 768
    assert all(math.isfinite(value) for value in first)
    assert sum(value * value for value in first) == pytest.approx(1.0)
    assert first != deterministic_embedding("different memory text")


def _disable_network_clients(monkeypatch) -> None:
    """Keep the architecture-only integration replay entirely local."""
    monkeypatch.setattr(
        "app.state.semantic_recall_store.QdrantClient", lambda **_kwargs: None
    )

    def disabled_redis(**_kwargs):
        raise ConnectionError("Redis is disabled in the offline BrainBench test")

    monkeypatch.setattr("app.state.agent_state.redis.Redis", disabled_redis)
    monkeypatch.setattr("app.state.working_memory_store.redis.Redis", disabled_redis)


@pytest.mark.asyncio
async def test_real_architecture_only_resources_replay(tmp_path: Path, monkeypatch):
    # Dev split only: a 1m private_minimalist baseline keeps this real replay
    # small while exercising normal interaction and reflection paths.
    simulation = build(1015, "private_minimalist", "1m")
    sim, turns, *_ = simulation
    _disable_network_clients(monkeypatch)
    service = build_cognitive_service("architecture_only", tmp_path / "one_month")
    monkeypatch.setattr(service.memory_store, "get_embedding", architecture_embedding)
    started = time.perf_counter()
    try:
        outcomes = await run_resources_suite(
            simulation, service, persona_seed=sim.seed, sample_every_turns=5
        )
        one_month_seconds = time.perf_counter() - started

        turn_rows = [row for row in outcomes if "turn" in row.categories]
        sample_rows = [row for row in outcomes if "sample" in row.categories]
        assert len(turn_rows) == len(turns)
        assert sample_rows
        assert all(row.mode == "architecture_only" for row in outcomes)
        assert all(
            "architecture overhead only: no model time, placeholder memory content"
            in row.categories
            for row in outcomes
        )
        assert all(row.suite == "resources" for row in outcomes)
        assert all(
            math.isfinite(value) for row in outcomes for value in row.metrics.values()
        )
        assert all("vram_mib" not in row.metrics for row in sample_rows)
        assert all(row.metrics["vram_applicable"] == 0.0 for row in sample_rows)
        assert sample_rows[-1].metrics["turn_index"] == float(len(turns))
        # Reviewer regression: a synchronous embedding stub made every
        # add_memory fail inside a caught TypeError, so this stayed 0 for a
        # whole six-month replay. Reflection stores NullLLM's identical "{}"
        # summary each turn and dedup merges them, so exactly one row is the
        # correct architecture_only result; zero means writes are failing.
        assert sample_rows[-1].metrics["rows:memory.db:memories"] == 1.0
        scores = {
            "latency_percentiles": latency_percentiles(outcomes),
            "growth_curves": growth_curves(outcomes),
            "unbounded_structures": unbounded_structures(outcomes),
        }
        print(
            "BrainBench resources real-run:",
            {
                "seed": sim.seed,
                "archetype": sim.archetype,
                "horizon": sim.horizon.label,
                "turns": len(turns),
                "sample_points": len(sample_rows),
                "elapsed_seconds": round(one_month_seconds, 3),
                "scores": scores,
            },
        )

        if one_month_seconds < 60.0:
            six_month_simulation = build(1015, "private_minimalist", "6m")
            six_month_sim, six_month_turns, *_ = six_month_simulation
            six_month_service = build_cognitive_service(
                "architecture_only", tmp_path / "six_months"
            )
            monkeypatch.setattr(
                six_month_service.memory_store, "get_embedding", architecture_embedding
            )
            six_started = time.perf_counter()
            try:
                six_outcomes = await run_resources_suite(
                    six_month_simulation,
                    six_month_service,
                    persona_seed=six_month_sim.seed,
                    sample_every_turns=10,
                )
                six_seconds = time.perf_counter() - six_started
                assert len(
                    [row for row in six_outcomes if "turn" in row.categories]
                ) == len(six_month_turns)
                print(
                    "BrainBench resources six-month real-run:",
                    {
                        "seed": six_month_sim.seed,
                        "archetype": six_month_sim.archetype,
                        "horizon": six_month_sim.horizon.label,
                        "turns": len(six_month_turns),
                        "elapsed_seconds": round(six_seconds, 3),
                        "scores": {
                            "latency_percentiles": latency_percentiles(six_outcomes),
                            "growth_curves": growth_curves(six_outcomes),
                            "unbounded_structures": unbounded_structures(six_outcomes),
                        },
                    },
                )
            finally:
                six_month_service.cognitive.close()
                await six_month_service.memory_store.close()
    finally:
        service.cognitive.close()
        await service.memory_store.close()


# Match the memory suite's guarded remote fixture: model runs use home-gpu by
# default and can target another host only through an explicit URL override.
BRAINBENCH_LLM_URL = os.environ.get("BRAINBENCH_LLM_URL", "http://100.88.246.46:11434")


@pytest.mark.asyncio
async def test_real_llm_augmented_lifesim_replay(
    tmp_path: Path, ollama_tags, monkeypatch
):
    from app.llm import build_llm_client

    assert (Config.LLM_PROVIDER or "ollama").lower() == "ollama"
    assert isinstance(ollama_tags.get("models", []), list)
    # MemoryStore captures Config.OLLAMA_URL in its constructor. Keep its real
    # asynchronous embedding client pointed at the same remote host as chat.
    monkeypatch.setattr(Config, "OLLAMA_URL", BRAINBENCH_LLM_URL)
    service = build_cognitive_service(
        "llm_augmented",
        tmp_path,
        llm_service=build_llm_client(
            base_url=BRAINBENCH_LLM_URL,
            model=Config.LLM_CHAT_MODEL,
        ),
    )
    assert service.memory_store.ollama_base_url == BRAINBENCH_LLM_URL.rstrip("/")
    simulation = build(1015, "private_minimalist", "1m")
    sim, turns, *_ = simulation
    started = time.perf_counter()
    try:
        outcomes = await run_resources_suite(
            simulation,
            service,
            persona_seed=sim.seed,
            sample_every_turns=5,
        )
        elapsed_seconds = time.perf_counter() - started
        turn_rows = [row for row in outcomes if "turn" in row.categories]
        sample_rows = [row for row in outcomes if "sample" in row.categories]
        assert len(turn_rows) == len(turns)
        assert sample_rows
        assert all(row.mode == "llm_augmented" for row in outcomes)
        assert all(
            "llm_augmented: real model time and memory growth" in row.categories
            for row in outcomes
        )
        assert all(
            math.isfinite(value) for row in outcomes for value in row.metrics.values()
        )
        assert all("vram_mib" not in row.metrics for row in sample_rows)
        assert all(row.metrics["vram_applicable"] == 0.0 for row in sample_rows)
        assert any(
            metric == "rows:memory.db:memories" and value > 0
            for row in sample_rows
            for metric, value in row.metrics.items()
        ), "llm_augmented replay should store real episodic memories"
        scores = {
            "latency_percentiles": latency_percentiles(outcomes),
            "growth_curves": growth_curves(outcomes),
            "unbounded_structures": unbounded_structures(outcomes),
        }
        for score_name, score in scores.items():
            print(f"BrainBench resources llm_augmented {score_name}:", score)
        print(
            "BrainBench resources llm_augmented run:",
            {
                "seed": sim.seed,
                "archetype": sim.archetype,
                "horizon": sim.horizon.label,
                "turns": len(turns),
                "sample_points": len(sample_rows),
                "elapsed_seconds": round(elapsed_seconds, 3),
            },
        )
    finally:
        service.cognitive.close()
        await service.memory_store.close()
