from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.brainbench import runner
from evals.brainbench.runner import SuiteSpec
from evals.brainbench.stats import SuiteOutcome


def _outcome(seed: int, value: float | None, *, probe: str = "p") -> SuiteOutcome:
    return SuiteOutcome(
        probe, seed, "bargein", ("basic",), {"score": value, "optional": None}
    )


def _spawn_fake(seed, archetype, horizon, **kwargs):
    if seed == 1000 and os.environ.get("BRAINBENCH_TEST_HARD_EXIT") == "1":
        os._exit(3)
    return [_outcome(seed, float(seed), probe=f"{archetype}:{horizon}")]


def _slow_first_fake(seed, archetype, horizon, **kwargs):
    import time

    time.sleep(3.0 if seed == 1000 else 0.1)
    return [_outcome(seed, float(seed), probe=f"{archetype}:{horizon}")]


def test_parallel_pool_keeps_workers_busy_behind_a_slow_cell(tmp_path, monkeypatch):
    # Fixed batches of `workers` made every worker wait for the batch's
    # slowest cell; a rolling pool starts the next cell as soon as one ends.
    monkeypatch.setitem(
        runner.SUITES,
        "bargein",
        SuiteSpec(
            _slow_first_fake, frozenset({"architecture_only"}), async_function=False
        ),
    )
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional,chatty_student,forgetful_retiree,volatile_creative",
        seeds="1000-1003",
        horizons="1w",
    )
    out = tmp_path / "out"
    assert runner.run_plan(plan, out, workers=2) == 0
    order = [
        json.loads(line)["seed"]
        for line in (out / "cells.jsonl").read_text().splitlines()
    ]
    # Batching would finish 1002 and 1003 only after the slow 1000.
    assert order[-1] == 1000, order


@pytest.mark.parametrize("workers", [1, 2])
def test_cell_root_holds_working_dirs_and_frees_them(tmp_path, monkeypatch, workers):
    monkeypatch.setitem(
        runner.SUITES,
        "bargein",
        SuiteSpec(_spawn_fake, frozenset({"architecture_only"}), async_function=False),
    )
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional,chatty_student",
        seeds="1000,1001",
        horizons="1w",
    )
    out, scratch = tmp_path / "out", tmp_path / "shm"
    assert runner.run_plan(plan, out, workers=workers, cell_root=scratch) == 0
    assert len(runner._outcomes(out)) == 2  # results kept on disk
    assert not (out / "cells").exists()  # working dirs were not under out
    assert list(scratch.iterdir()) == []  # and were freed after each cell
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["cell_root"] == str(scratch)


def test_plan_zip_cross_variants_and_cell_ids():
    zipped = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="proactive",
        archetypes="steady_professional,chatty_student",
        seeds="1000,1001",
        horizons="1w,100t",
        pairing="zip",
        variants="none/brain_first",
    )
    assert len(zipped) == 4
    assert {(c.archetype, c.seed) for c in zipped} == {
        ("steady_professional", 1000),
        ("chatty_student", 1001),
    }
    assert all(
        c.kwargs
        == {
            "max_ticks_per_gap": 7 * 24 * 60,
            "sync": "none",
            "tick_order": "brain_first",
        }
        for c in zipped
    )
    crossed = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional,chatty_student",
        seeds="1000,1001",
        horizons="1w",
        pairing="cross",
    )
    assert len(crossed) == 4
    assert len({c.cell_id for c in crossed}) == 4


@pytest.mark.parametrize("seed", [999, 1100, 2000, 3000, 9000])
def test_plan_refuses_non_dev_seeds(seed):
    with pytest.raises(ValueError, match="split"):
        runner.build_plan(
            mode="architecture_only",
            arm="baseline",
            suites="bargein",
            archetypes="steady_professional",
            seeds=[seed],
            horizons="1w",
        )


def test_plan_refuses_illegal_mode_and_horizon_before_execution():
    with pytest.raises(ValueError, match="illegal"):
        runner.build_plan(
            mode="architecture_only",
            arm="baseline",
            suites="memory",
            archetypes="steady_professional",
            seeds="1000",
            horizons="1w",
        )
    with pytest.raises(ValueError, match="horizon"):
        runner.build_plan(
            mode="architecture_only",
            arm="baseline",
            suites="bargein",
            archetypes="steady_professional",
            seeds="1000",
            horizons="soon",
        )


def test_all_selects_only_suites_legal_in_mode():
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="all",
        archetypes="steady_professional",
        seeds="1000",
        horizons="1w",
    )
    assert {cell.suite for cell in plan} == {
        "attention",
        "trust",
        "proactive",
        "bargein",
        "resources",
    }


def _write_run(out: Path, arm: str, mode: str, rows: list[dict]):
    out.mkdir()
    (out / "manifest.json").write_text(
        json.dumps({"mode": mode, "git_sha": "same", "dirty": False, "arm": arm})
    )
    (out / "outcomes.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (out / "cells.jsonl").write_text("")


def test_report_none_metrics_are_missing_and_compare_pairs_contextually(tmp_path):
    rows = []
    for seed, value in ((1000, 1.0), (1001, None)):
        row = json.loads(
            json.dumps(
                {
                    **_outcome(seed, value).__dict__,
                    "cell_id": str(seed),
                    "arm": "baseline",
                    "variant": "default",
                    "archetype": "steady_professional",
                    "seed": seed,
                    "horizon": "1w",
                }
            )
        )
        rows.append(row)
    baseline = tmp_path / "baseline"
    arm = tmp_path / "arm"
    _write_run(baseline, "baseline", "architecture_only", rows)
    changed = []
    for row in rows:
        clone = dict(row)
        clone["arm"] = "v1-ranker"
        clone["metrics"] = {
            "score": None if row["metrics"]["score"] is None else 2.0,
            "optional": None,
        }
        changed.append(clone)
    _write_run(arm, "v1-ranker", "architecture_only", changed)
    report, markdown = runner.report_run(baseline)
    score = report["groups"]["bargein/default/1w"]["metrics"]["score"]
    assert score["n"] == 1 and score["n_clusters"] == 1 and score["missing"] == 1
    assert "Mean" in markdown and "0.0000" not in markdown
    comparison, _ = runner.compare_runs(baseline, arm)
    metric = comparison["groups"]["bargein/default/1w"]["metrics"]["score"]
    assert metric["n"] == 1 and metric["delta"] == 1.0
    (arm / "manifest.json").write_text(
        json.dumps({"mode": "llm_augmented", "git_sha": "same", "dirty": False})
    )
    with pytest.raises(ValueError, match="modes"):
        runner.compare_runs(baseline, arm)


def test_resume_retries_errors_skips_success_and_deduplicates_outcomes(
    tmp_path, monkeypatch
):
    calls: list[int] = []
    fail = {1001}

    def fake(seed, archetype, horizon, **kwargs):
        calls.append(seed)
        if seed in fail:
            raise RuntimeError("temporary failure")
        return [_outcome(seed, float(seed - 1000))]

    monkeypatch.setitem(
        runner.SUITES,
        "bargein",
        SuiteSpec(fake, frozenset({"architecture_only"}), async_function=False),
    )
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional,chatty_student",
        seeds="1000,1001",
        horizons="1w",
        pairing="zip",
    )
    out = tmp_path / "run"
    assert runner.run_plan(plan, out) == 1
    fail.clear()
    assert runner.run_plan(plan, out) == 0
    assert calls == [1000, 1001, 1001]
    assert len((out / "outcomes.jsonl").read_text().splitlines()) == 2
    records = [
        json.loads(line) for line in (out / "cells.jsonl").read_text().splitlines()
    ]
    latest = {record["cell_id"]: record for record in records}
    assert len(latest) == 2 and all(
        record["status"] == "ok" for record in latest.values()
    )
    assert json.loads((out / "manifest.json").read_text())["ended_at"]
    assert len((out / "progress.log").read_text().splitlines()) == 3
    records_before = (out / "cells.jsonl").read_text()
    assert runner.run_plan(plan, out) == 0
    assert (out / "cells.jsonl").read_text() == records_before


def test_plan_mismatch_requires_explicit_force(tmp_path, monkeypatch):
    monkeypatch.setitem(
        runner.SUITES,
        "bargein",
        SuiteSpec(
            lambda *a, **k: [], frozenset({"architecture_only"}), async_function=False
        ),
    )
    first = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional",
        seeds="1000",
        horizons="1w",
    )
    second = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional",
        seeds="1000",
        horizons="2w",
    )
    out = tmp_path / "run"
    runner.run_plan(first, out)
    with pytest.raises(ValueError, match="force-new-plan"):
        runner.run_plan(second, out)


def test_llm_url_routes_chat_and_embedding_service_construction(tmp_path, monkeypatch):
    from app.config import Config

    calls = {}

    def fake_client(*, base_url, model=None):
        calls["chat_url"] = base_url
        return object()

    def fake_builder(mode, run_dir, *, llm_service=None):
        calls["embedding_url"] = Config.OLLAMA_URL
        assert llm_service is not None
        return SimpleNamespace(mode=mode)

    async def fake_resources(
        simulation, service, *, persona_seed, sample_every_turns, background_timeout
    ):
        return [
            SuiteOutcome(
                "probe", persona_seed, "resources", (), {"latency": 1.0}, service.mode
            )
        ]

    monkeypatch.setattr(Config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(runner, "build_cognitive_service", fake_builder)
    monkeypatch.setattr("app.llm.build_llm_client", fake_client)
    monkeypatch.setitem(
        runner.SUITES,
        "resources",
        SuiteSpec(fake_resources, frozenset({"architecture_only", "llm_augmented"})),
    )
    cells = runner.build_plan(
        mode="llm_augmented",
        arm="baseline",
        suites="resources",
        archetypes="steady_professional",
        seeds="1000",
        horizons="1w",
    )
    endpoint = "http://gpu.example:11434"
    assert runner.run_plan(cells, tmp_path / "llm", llm_url=endpoint) == 0
    assert calls == {"chat_url": endpoint, "embedding_url": endpoint}


def test_cli_real_architecture_only_bargein_cell_and_report(tmp_path, capsys):
    out = tmp_path / "smoke"
    status = __import__("evals.brainbench.__main__", fromlist=["main"]).main(
        [
            "run",
            "--mode",
            "architecture_only",
            "--suites",
            "bargein",
            "--archetypes",
            "steady_professional",
            "--seeds",
            "1000",
            "--horizons",
            "1w",
            "--out",
            str(out),
            "--suite-arg",
            "bargein.n_events=4",
        ]
    )
    assert status == 0
    records = [
        json.loads(line) for line in (out / "cells.jsonl").read_text().splitlines()
    ]
    assert (
        len(records) == 1
        and records[0]["status"] == "ok"
        and records[0]["n_outcomes"] > 0
    )
    report_status = __import__("evals.brainbench.__main__", fromlist=["main"]).main(
        ["report", str(out)]
    )
    assert report_status == 0
    assert (out / "report.json").exists() and "# BrainBench report" in (
        out / "report.md"
    ).read_text()


def test_embeddings_default_to_async_architecture_patch(tmp_path, monkeypatch):
    calls = {"real": 0, "patched": 0}

    async def real_embedding(_text):
        calls["real"] += 1
        return [1.0]

    async def suite(
        _simulation, service, *, persona_seed, sample_every_turns, background_timeout
    ):
        await service.memory_store.get_embedding("sample")
        return [_outcome(persona_seed, 1.0)]

    async def fake_embedding(_text):
        calls["patched"] += 1
        return [1.0]

    memory = SimpleNamespace(get_embedding=real_embedding)
    monkeypatch.setattr(
        runner,
        "build_cognitive_service",
        lambda *a, **k: SimpleNamespace(mode="architecture_only", memory_store=memory),
    )
    monkeypatch.setattr(
        "evals.brainbench.resources_suite.architecture_embedding", fake_embedding
    )
    monkeypatch.setitem(
        runner.SUITES, "resources", SuiteSpec(suite, frozenset({"architecture_only"}))
    )
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="resources",
        archetypes="steady_professional",
        seeds="1000",
        horizons="1w",
    )
    out = tmp_path / "embeddings"
    assert runner.run_plan(plan, out) == 0
    assert calls == {"real": 0, "patched": 1}
    assert (
        json.loads((out / "manifest.json").read_text())["embeddings"] == "deterministic"
    )
    record = json.loads((out / "cells.jsonl").read_text().splitlines()[0])
    assert record["embeddings"] == "deterministic"


def test_first_attempt_does_not_rewrite_aggregate_outcomes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        runner, "_remove_cell_outcomes", lambda *args: calls.append(args)
    )
    monkeypatch.setitem(
        runner.SUITES,
        "bargein",
        SuiteSpec(_spawn_fake, frozenset({"architecture_only"}), async_function=False),
    )
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional",
        seeds="1000",
        horizons="1w",
    )
    assert runner.run_plan(plan, tmp_path / "first") == 0
    assert calls == []


def test_resume_records_git_sha_history_and_warns(tmp_path, monkeypatch):
    monkeypatch.setitem(
        runner.SUITES,
        "bargein",
        SuiteSpec(_spawn_fake, frozenset({"architecture_only"}), async_function=False),
    )
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional",
        seeds="1000",
        horizons="1w",
    )
    shas = iter(("a" * 40, "a" * 40, "b" * 40, "b" * 40))
    monkeypatch.setattr(runner, "_backend_git", lambda: (next(shas), False))
    out = tmp_path / "provenance"
    assert runner.run_plan(plan, out) == 0
    assert runner.run_plan(plan, out) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    record = json.loads((out / "cells.jsonl").read_text().splitlines()[0])
    assert manifest["git_sha_history"] == ["a" * 40, "b" * 40]
    assert record["git_sha"] == "a" * 40
    assert "WARNING manifest git_sha changed" in (out / "progress.log").read_text()


def test_report_keeps_horizons_separate(tmp_path):
    out = tmp_path / "horizons"
    rows = []
    for horizon, seed in (("1w", 1000), ("10y", 1001)):
        rows.append(
            {
                **_outcome(seed, float(seed)).__dict__,
                "cell_id": str(seed),
                "arm": "baseline",
                "variant": "default",
                "archetype": "steady_professional",
                "seed": seed,
                "persona_seed": seed,
                "horizon": horizon,
            }
        )
    _write_run(out, "baseline", "architecture_only", rows)
    report, _ = runner.report_run(out)
    assert set(report["groups"]) == {"bargein/default/1w", "bargein/default/10y"}


def test_compare_reports_unpaired_cells_and_probes(tmp_path):
    baseline, arm = tmp_path / "baseline-unpaired", tmp_path / "arm-unpaired"

    def row(cell, seed, probe, arm_name):
        return {
            **_outcome(seed, 1.0, probe=probe).__dict__,
            "cell_id": cell,
            "arm": arm_name,
            "variant": "default",
            "archetype": "steady_professional",
            "seed": seed,
            "persona_seed": seed,
            "horizon": "1w",
        }

    left = [
        row("shared", 1000, "a", "baseline"),
        row("shared", 1000, "b", "baseline"),
        row("left", 1001, "c", "baseline"),
    ]
    right = [
        row("shared", 1000, "a", "test"),
        row("shared", 1000, "d", "test"),
        row("right", 1002, "e", "test"),
    ]
    _write_run(baseline, "baseline", "architecture_only", left)
    _write_run(arm, "test", "architecture_only", right)
    (baseline / "cells.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "cell_id": c,
                    "suite": "bargein",
                    "variant": "default",
                    "seed": seed,
                    "archetype": "steady_professional",
                    "horizon": "1w",
                    "status": "ok",
                }
            )
            for c, seed in (("shared", 1000), ("left", 1001))
        )
        + "\n"
    )
    (arm / "cells.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "cell_id": c,
                    "suite": "bargein",
                    "variant": "default",
                    "seed": seed,
                    "archetype": "steady_professional",
                    "horizon": "1w",
                    "status": "ok",
                }
            )
            for c, seed in (("shared", 1000), ("right", 1002))
        )
        + "\n"
    )
    comparison, markdown = runner.compare_runs(baseline, arm)
    unpaired = comparison["unpaired"]["bargein/default/1w"]
    assert unpaired == {
        "baseline_only_cells": 1,
        "arm_only_cells": 1,
        "baseline_only_probes": 1,
        "arm_only_probes": 1,
    }
    assert "Baseline-only probes" in markdown and "Arm-only cells" in markdown


def test_all_suites_have_registered_summary_and_empty_trust_has_reason():
    from evals.brainbench.summaries import SUMMARY_FUNCTIONS, summarize_cell

    assert set(SUMMARY_FUNCTIONS) == set(runner.SUITES)
    metrics, reasons = summarize_cell("trust", [], None)
    assert metrics["hostility_response.mean_trust_delta"] is None
    assert "hostile robot-directed" in reasons["hostility_response.mean_trust_delta"]


def test_real_tiny_trust_summary_matches_public_scorers(tmp_path):
    from evals.brainbench import trust_suite
    from evals.brainbench.summaries import summarize_cell

    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="trust",
        archetypes="steady_professional",
        seeds="1000",
        horizons="100t",
    )
    assert runner.run_plan(plan, tmp_path / "trust-summary") == 0
    rows = runner._outcomes(tmp_path / "trust-summary")
    outcomes = [
        SuiteOutcome(
            row["probe_key"],
            row["persona_seed"],
            row["suite"],
            tuple(row["categories"]),
            row["metrics"],
            row["mode"],
        )
        for row in rows
    ]
    summary, reasons = summarize_cell("trust", outcomes)
    with pytest.raises(ValueError, match="hostile robot-directed"):
        trust_suite.hostility_response(outcomes)
    direct_competence = trust_suite.competence_warmth_separation(outcomes)
    direct_drift = trust_suite.background_drift(outcomes)
    assert summary["hostility_response.mean_trust_delta"] is None
    assert "hostile robot-directed" in reasons["hostility_response.mean_trust_delta"]
    assert (
        summary["competence_warmth_separation.competence_signal"]
        == direct_competence["competence_signal"]
    )
    for component, turns in direct_drift["turns_to_ceiling"].items():
        assert summary[f"background_drift.turns_to_ceiling.{component}"] == turns


def test_parallel_spawn_matches_serial_and_hard_exit_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setitem(
        runner.SUITES,
        "bargein",
        SuiteSpec(_spawn_fake, frozenset({"architecture_only"}), async_function=False),
    )
    plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional,chatty_student",
        seeds="1000,1001",
        horizons="1w",
    )
    serial, parallel = tmp_path / "serial", tmp_path / "parallel"
    assert runner.run_plan(plan, serial, workers=1) == 0
    assert runner.run_plan(plan, parallel, workers=2) == 0

    def signatures(path):
        return {
            (row["probe_key"], json.dumps(row["metrics"], sort_keys=True))
            for row in runner._outcomes(path)
        }

    assert signatures(serial) == signatures(parallel)

    monkeypatch.setenv("BRAINBENCH_TEST_HARD_EXIT", "1")
    crash_plan = runner.build_plan(
        mode="architecture_only",
        arm="baseline",
        suites="bargein",
        archetypes="steady_professional,chatty_student,volatile_creative",
        seeds="1000-1002",
        horizons="1w",
    )
    crashed = tmp_path / "crashed"
    assert runner.run_plan(crash_plan, crashed, workers=2) == 1
    records = [
        json.loads(line) for line in (crashed / "cells.jsonl").read_text().splitlines()
    ]
    assert any(row["seed"] == 1000 and row["status"] == "error" for row in records)
    assert any(row["seed"] == 1001 and row["status"] == "ok" for row in records)
    assert any(row["seed"] == 1002 and row["status"] == "ok" for row in records)
