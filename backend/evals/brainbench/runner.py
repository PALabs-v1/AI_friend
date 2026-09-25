"""Grid planning, isolated cell execution, and BrainBench result analysis."""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import hashlib
import importlib
import json
import multiprocessing
import os
import platform
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.config import AppSettings, Config
from evals.brainbench import stats
from evals.brainbench.adapters import build_cognitive_service
from evals.brainbench.stats import SuiteOutcome
from evals.lifesim.generate import build, parse_horizon
from evals.lifesim.personas import PANEL
from evals.lifesim.splits import SPLITS, split_of


@dataclass(frozen=True)
class SuiteSpec:
    function: Callable[..., Any]
    modes: frozenset[str]
    variants: dict[str, dict[str, Any]] = field(default_factory=lambda: {"default": {}})
    async_function: bool = True


@dataclass(frozen=True)
class _SuiteInvoker:
    module: str
    name: str

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return getattr(
            importlib.import_module(f"evals.brainbench.{self.module}"), self.name
        )(*args, **kwargs)


def _suite_function(module: str, name: str) -> Callable[..., Any]:
    return _SuiteInvoker(module, name)


SUITES: dict[str, SuiteSpec] = {
    "memory": SuiteSpec(
        _suite_function("memory_suite", "run_memory_suite_for_seed"),
        frozenset({"llm_augmented"}),
    ),
    "attention": SuiteSpec(
        _suite_function("attention_suite", "run_attention_suite_for_seed"),
        frozenset({"architecture_only"}),
    ),
    "affect": SuiteSpec(
        _suite_function("affect_suite", "run_affect_suite_for_seed"),
        frozenset({"llm_augmented"}),
    ),
    "trust": SuiteSpec(
        _suite_function("trust_suite", "run_trust_suite_for_seed"),
        frozenset({"architecture_only"}),
    ),
    "personality": SuiteSpec(
        _suite_function("personality_suite", "run_personality_suite_for_seed"),
        frozenset({"llm_augmented"}),
        {
            "review_queue": {"review_required": True},
            "direct_apply": {"review_required": False},
        },
    ),
    "proactive": SuiteSpec(
        _suite_function("proactive_suite", "run_proactive_suite_for_seed"),
        frozenset({"architecture_only"}),
        {
            f"{sync}/{order}": {"sync": sync, "tick_order": order}
            for sync in ("broadcast", "none")
            for order in ("brain_first", "subconscious_first")
        },
    ),
    "bargein": SuiteSpec(
        _suite_function("bargein_suite", "run_bargein_suite_for_seed"),
        frozenset({"architecture_only"}),
        async_function=False,
    ),
    "metacognition": SuiteSpec(
        _suite_function("metacognition_suite", "run_metacognition_suite_for_seed"),
        frozenset({"llm_augmented"}),
        {"surfacing": {"surfacing": True}, "no_surfacing": {"surfacing": False}},
    ),
    "resources": SuiteSpec(
        _suite_function("resources_suite", "run_resources_suite"),
        frozenset({"architecture_only", "llm_augmented"}),
    ),
}

SUITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "memory": {"progress_every": 100},
    "attention": {"progress_every": 100},
    "affect": {"progress_every": 25, "system2_timeout": 30.0},
    "trust": {"progress_every": 100, "background_timeout": 10.0},
    "personality": {"progress_every": 25, "background_timeout": 180.0},
    "proactive": {"max_ticks_per_gap": 7 * 24 * 60},
    "bargein": {"n_scenarios_per_family": 1, "n_events": 20, "timeout": 1.0},
    "metacognition": {
        "progress_every": 100,
        "background_timeout": 10.0,
        "outage_every": 5,
    },
    "resources": {"sample_every_turns": 10, "background_timeout": 10.0},
}


@dataclass(frozen=True)
class Cell:
    cell_id: str
    suite: str
    mode: str
    arm: str
    variant: str
    archetype: str
    seed: int
    horizon: str
    kwargs: dict[str, Any]

    def fields(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _cell_id(values: dict[str, Any]) -> str:
    parts = [
        str(values[k])
        for k in ("suite", "mode", "arm", "variant", "archetype", "seed", "horizon")
    ]
    safe = [
        re.sub(r"[^A-Za-z0-9._+-]+", "_", part).strip("._") or "x" for part in parts
    ]
    suffix = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:10]
    return "--".join(safe) + "--" + suffix


def parse_seeds(value: str) -> list[int]:
    seeds: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty item in --seeds")
        if "-" in part:
            pieces = part.split("-")
            if len(pieces) != 2 or not all(p.isdigit() for p in pieces):
                raise ValueError(f"invalid seed range {part!r}")
            start, end = map(int, pieces)
            if end < start:
                raise ValueError(f"descending seed range {part!r}")
            seeds.extend(range(start, end + 1))
        else:
            try:
                seeds.append(int(part))
            except ValueError as exc:
                raise ValueError(f"invalid seed {part!r}") from exc
    if not seeds:
        raise ValueError("at least one seed is required")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seed list contains duplicates")
    for seed in seeds:
        split = split_of(seed)
        if split != "dev":
            raise ValueError(
                f"seed {seed} belongs to {split} split; BrainBench runner accepts dev seeds only ({SPLITS['dev'].start}-{SPLITS['dev'].stop - 1})"
            )
    return seeds


def parse_archetypes(value: str) -> list[str]:
    archetypes = (
        list(PANEL)
        if value.strip().lower() == "all"
        else [x.strip() for x in value.split(",") if x.strip()]
    )
    if not archetypes:
        raise ValueError("at least one archetype is required")
    unknown = sorted(set(archetypes) - set(PANEL))
    if unknown:
        raise ValueError(
            f"unknown archetype(s): {', '.join(unknown)}; expected one of {', '.join(PANEL)}"
        )
    if len(set(archetypes)) != len(archetypes):
        raise ValueError("archetype list contains duplicates")
    return archetypes


def parse_suite_args(values: list[str] | None) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    for entry in values or []:
        try:
            suite, assignment = entry.split(".", 1)
            key, raw = assignment.split("=", 1)
            value = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid --suite-arg {entry!r}; use suite.key=<JSON value>"
            ) from exc
        if suite not in SUITES:
            raise ValueError(f"unknown suite in --suite-arg: {suite!r}")
        if not key:
            raise ValueError(f"empty suite argument key in {entry!r}")
        parsed.setdefault(suite, {})[key] = value
    return parsed


def build_plan(
    *,
    mode: str,
    arm: str,
    suites: str | list[str],
    archetypes: str | list[str],
    seeds: str | list[int],
    horizons: str | list[str],
    pairing: str = "zip",
    variants: str | list[str] | None = None,
    suite_args: dict[str, dict[str, Any]] | None = None,
) -> list[Cell]:
    if mode not in {"architecture_only", "llm_augmented"}:
        raise ValueError("--mode must be architecture_only or llm_augmented")
    names = (
        [name for name, spec in SUITES.items() if mode in spec.modes]
        if suites == "all"
        else (
            [x.strip() for x in suites.split(",") if x.strip()]
            if isinstance(suites, str)
            else list(suites)
        )
    )
    if not names or len(set(names)) != len(names):
        raise ValueError("suite list must be non-empty and contain no duplicates")
    unknown = sorted(set(names) - set(SUITES))
    if unknown:
        raise ValueError(
            f"unknown suite(s): {', '.join(unknown)}; known: {', '.join(SUITES)}"
        )
    illegal = [name for name in names if mode not in SUITES[name].modes]
    if illegal:
        raise ValueError(
            f"suites illegal in {mode}: {', '.join(illegal)}; legal suites: {', '.join(n for n, spec in SUITES.items() if mode in spec.modes)}"
        )
    for suite, overrides in (suite_args or {}).items():
        if suite not in SUITES:
            raise ValueError(f"unknown suite in --suite-arg: {suite!r}")
        unsupported = sorted(
            set(overrides)
            - set(SUITE_DEFAULTS.get(suite, {}))
            - set(SUITES[suite].variants.get("default", {}))
        )
        if suite == "personality":
            unsupported = sorted(
                set(overrides) - set(SUITE_DEFAULTS[suite]) - {"review_required"}
            )
        elif suite == "proactive":
            unsupported = sorted(
                set(overrides) - set(SUITE_DEFAULTS[suite]) - {"sync", "tick_order"}
            )
        elif suite == "metacognition":
            unsupported = sorted(
                set(overrides) - set(SUITE_DEFAULTS[suite]) - {"surfacing"}
            )
        if unsupported:
            raise ValueError(
                f"unsupported --suite-arg key(s) for {suite}: {', '.join(unsupported)}"
            )
    archetype_list = (
        parse_archetypes(archetypes)
        if isinstance(archetypes, str)
        else list(archetypes)
    )
    seed_list = parse_seeds(seeds) if isinstance(seeds, str) else list(seeds)
    for seed in seed_list:
        split = split_of(seed)
        if split != "dev":
            raise ValueError(f"seed {seed} belongs to {split} split; dev seeds only")
    if pairing == "zip":
        if len(archetype_list) != len(seed_list):
            raise ValueError(
                f"--pairing zip requires equal archetype and seed counts (got {len(archetype_list)} and {len(seed_list)})"
            )
        pairs = list(zip(archetype_list, seed_list, strict=True))
    elif pairing == "cross":
        pairs = [(a, s) for a in archetype_list for s in seed_list]
    else:
        raise ValueError("--pairing must be zip or cross")
    horizon_list = (
        [x.strip() for x in horizons.split(",") if x.strip()]
        if isinstance(horizons, str)
        else list(horizons)
    )
    if not horizon_list:
        raise ValueError("at least one horizon is required")
    for horizon in horizon_list:
        parse_horizon(horizon)
    requested_variants = (
        None
        if variants is None
        else (
            [x.strip() for x in variants.split(",") if x.strip()]
            if isinstance(variants, str)
            else list(variants)
        )
    )
    arm_record = __import__(
        "evals.brainbench.switchboard", fromlist=["resolve_arm"]
    ).resolve_arm(arm)
    planned: list[Cell] = []
    for suite in names:
        spec = SUITES[suite]
        variant_map = spec.variants
        selected = (
            list(variant_map)
            if requested_variants is None
            else [v for v in requested_variants if v in variant_map]
        )
        if requested_variants is not None and not selected:
            raise ValueError(
                f"no requested variants are valid for suite {suite!r}; valid: {', '.join(variant_map)}"
            )
        for variant in selected:
            kwargs = {
                **SUITE_DEFAULTS.get(suite, {}),
                **variant_map[variant],
                **(suite_args or {}).get(suite, {}),
            }
            for archetype, seed in pairs:
                for horizon in horizon_list:
                    values = {
                        "suite": suite,
                        "mode": mode,
                        "arm": arm_record.name,
                        "variant": variant,
                        "archetype": archetype,
                        "seed": seed,
                        "horizon": horizon,
                        "kwargs": kwargs,
                    }
                    planned.append(Cell(_cell_id(values), **values))
    return planned


def _json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL at {path}:{line_number}: {exc}"
                    ) from exc
    return rows


def _remove_cell_outcomes(path: Path, cell_id: str) -> None:
    if not path.exists():
        return
    temp = path.with_suffix(".jsonl.tmp")
    with path.open() as source, temp.open("w") as target:
        for line in source:
            if line.strip() and json.loads(line).get("cell_id") != cell_id:
                target.write(line)
        target.flush()
        os.fsync(target.fileno())
    temp.replace(path)


def _backend_git() -> tuple[str, bool]:
    backend = Path(__file__).resolve().parents[2]
    sha = subprocess.run(
        ["git", "-C", str(backend), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(backend), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    return sha, dirty


def _last_frame(exc: BaseException) -> dict[str, Any] | None:
    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return None
    frame = tb[-1]
    return {"file": frame.filename, "line": frame.lineno, "function": frame.name}


def _invoke(
    cell: Cell,
    run_dir: Path,
    *,
    llm_url: str,
    embeddings: str,
    spec_override: SuiteSpec | None = None,
) -> list[SuiteOutcome]:
    from evals.brainbench.switchboard import apply_arm, resolve_arm

    spec = spec_override or SUITES[cell.suite]
    arm = resolve_arm(cell.arm)
    if (
        cell.mode == "llm_augmented"
        and str(Config.LLM_PROVIDER or "ollama").lower() != "ollama"
    ):
        raise ValueError(
            "BrainBench llm_augmented requires the local Ollama provider; hosted providers are refused"
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    with apply_arm(arm):
        service = None
        if cell.suite != "bargein":
            llm = None
            if cell.mode == "llm_augmented":
                from app.llm import build_llm_client

                llm = build_llm_client(base_url=llm_url, model=Config.LLM_CHAT_MODEL)
            if cell.mode == "llm_augmented":
                with patch.object(Config, "OLLAMA_URL", llm_url):
                    service = build_cognitive_service(
                        cell.mode, run_dir, llm_service=llm
                    )
            else:
                service = build_cognitive_service(cell.mode, run_dir, llm_service=llm)
            if embeddings == "deterministic":
                from evals.brainbench.resources_suite import architecture_embedding

                service.memory_store.get_embedding = architecture_embedding
        args = dict(cell.kwargs)
        if cell.suite == "bargein":
            result = spec.function(cell.seed, cell.archetype, cell.horizon, **args)
        elif cell.suite == "resources":
            result = asyncio.run(
                spec.function(
                    build(cell.seed, cell.archetype, cell.horizon),
                    service,
                    persona_seed=cell.seed,
                    **args,
                )
            )
        elif cell.suite == "proactive":
            args["run_dir"] = run_dir
            result = asyncio.run(
                spec.function(cell.seed, cell.archetype, cell.horizon, service, **args)
            )
        else:
            result = spec.function(
                cell.seed, cell.archetype, cell.horizon, service, **args
            )
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
    return list(result)


def _process_cell(
    cell: Cell,
    run_dir: Path,
    llm_url: str,
    embeddings: str,
    spec_override: SuiteSpec | None = None,
) -> list[SuiteOutcome]:
    return _invoke(
        cell,
        run_dir,
        llm_url=llm_url,
        embeddings=embeddings,
        spec_override=spec_override,
    )


def _write_cell_outcomes(out: Path, cell: Cell, outcomes: list[SuiteOutcome]) -> None:
    outcome_path = out / "outcome-cells" / f"{cell.cell_id}.jsonl"
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    with outcome_path.open("w") as stream:
        for outcome in outcomes:
            row = dataclasses.asdict(outcome)
            row.update(
                {
                    "cell_id": cell.cell_id,
                    "suite": cell.suite,
                    "mode": cell.mode,
                    "arm": cell.arm,
                    "variant": cell.variant,
                    "archetype": cell.archetype,
                    "seed": cell.seed,
                    "persona_seed": outcome.persona_seed,
                    "horizon": cell.horizon,
                }
            )
            stream.write(_json_dump(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _rebuild_outcomes(out: Path) -> None:
    latest = {row["cell_id"]: row for row in _read_jsonl(out / "cells.jsonl")}
    path = out / "outcomes.jsonl"
    temp = path.with_suffix(".jsonl.tmp")
    with temp.open("w") as stream:
        for cell_id, record in latest.items():
            if record.get("status") != "ok":
                continue
            cell_path = out / "outcome-cells" / f"{cell_id}.jsonl"
            if cell_path.exists():
                stream.write(cell_path.read_text())
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def run_plan(
    cells: list[Cell],
    out: Path,
    *,
    llm_url: str | None = None,
    argv: list[str] | None = None,
    force_new_plan: bool = False,
    embeddings: str | None = None,
    allow_model_embeddings_in_architecture_only: bool = False,
    workers: int = 1,
) -> int:
    if workers < 1:
        raise ValueError("--workers must be at least 1")
    out = Path(out)
    mode = cells[0].mode if cells else None
    embeddings = embeddings or (
        "deterministic" if mode == "architecture_only" else "model"
    )
    if embeddings not in {"deterministic", "model"}:
        raise ValueError("--embeddings must be deterministic or model")
    if (
        embeddings == "model"
        and mode == "architecture_only"
        and not allow_model_embeddings_in_architecture_only
    ):
        raise ValueError(
            "model embeddings in architecture_only require --allow-model-embeddings-in-architecture-only"
        )
    out.mkdir(parents=True, exist_ok=True)
    plan_path = out / "plan.json"
    plan_data = {"cells": [c.fields() for c in cells], "embeddings": embeddings}
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan_data:
        if not force_new_plan:
            raise ValueError(
                "existing plan.json differs from requested grid; pass --force-new-plan to start a new run"
            )
        for name in (
            "cells.jsonl",
            "outcomes.jsonl",
            "progress.log",
            "manifest.json",
            "report.json",
            "report.md",
            "compare.json",
            "compare.md",
            "summaries.jsonl",
        ):
            (out / name).unlink(missing_ok=True)
        for name in ("cells", "outcome-cells"):
            directory = out / name
            if directory.exists():
                import shutil

                shutil.rmtree(directory)
    _write_json(plan_path, plan_data)
    sha, dirty = _backend_git()
    active_llm_url = llm_url or os.environ.get(
        "BRAINBENCH_LLM_URL", "http://100.88.246.46:11434"
    )
    started = datetime.now(UTC).isoformat()
    manifest_path = out / "manifest.json"
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest = {
        **existing,
        "git_sha": sha,
        "git_sha_history": list(
            dict.fromkeys(
                [
                    *existing.get("git_sha_history", []),
                    existing.get("git_sha", sha),
                    sha,
                ]
            )
        ),
        "dirty": dirty,
        "python": sys.version,
        "platform": platform.platform(),
        "argv": argv or sys.argv,
        "mode": cells[0].mode if cells else None,
        "arm": cells[0].arm if cells else None,
        "overrides": __import__(
            "evals.brainbench.switchboard", fromlist=["resolve_arm"]
        )
        .resolve_arm(cells[0].arm)
        .overrides
        if cells
        else {},
        "variants": sorted({c.variant for c in cells}),
        "embeddings": embeddings,
        "workers": workers,
        "llm_url": active_llm_url
        if any(c.mode == "llm_augmented" for c in cells)
        else None,
        "models": (
            {
                k: getattr(Config, k)
                for k in AppSettings.model_fields
                if k.startswith("LLM_") and "MODEL" in k
            }
            if any(c.mode == "llm_augmented" for c in cells)
            else {}
        ),
        "started_at": existing.get("started_at", started),
        "ended_at": None,
    }
    _write_json(manifest_path, manifest)
    if existing.get("git_sha") and existing["git_sha"] != sha:
        with (out / "progress.log").open("a") as stream:
            stream.write(
                f"{datetime.now(UTC).isoformat()} WARNING manifest git_sha changed: {existing['git_sha']} -> {sha}; cells retain per-cell provenance\n"
            )
    latest: dict[str, dict[str, Any]] = {}
    for record in _read_jsonl(out / "cells.jsonl"):
        latest[record["cell_id"]] = record
    records = []
    progress_path = out / "progress.log"
    pending = [
        cell for cell in cells if latest.get(cell.cell_id, {}).get("status") != "ok"
    ]
    errors = sum(row.get("status") == "error" for row in latest.values())

    def finish_cell(
        cell: Cell,
        outcomes: list[SuiteOutcome] | None,
        exc: BaseException | None,
        wall: float,
    ) -> None:
        nonlocal errors
        status = "error" if exc is not None else "ok"
        error_fields: dict[str, Any] = {}
        outcomes = outcomes or []
        if exc is not None:
            error_fields = {
                "exception_type": type(exc).__name__,
                "message": str(exc)[:500],
                "last_frame": _last_frame(exc),
            }
        _write_cell_outcomes(out, cell, outcomes)
        record = {
            **cell.fields(),
            "status": status,
            "wall_s": round(wall, 6),
            "n_outcomes": len(outcomes),
            "git_sha": sha,
            "embeddings": embeddings,
            **error_fields,
        }
        with (out / "cells.jsonl").open("a") as stream:
            stream.write(_json_dump(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        records.append(record)
        latest[cell.cell_id] = record
        errors = sum(row.get("status") == "error" for row in latest.values())
        finished_records = [latest[c.cell_id] for c in cells if c.cell_id in latest]
        finished_count = len(finished_records)
        total = len(cells)
        done_wall = [float(row["wall_s"]) for row in finished_records]
        mean = sum(done_wall) / len(done_wall) if done_wall else 0.0
        rate = 3600 / mean if mean else 0.0
        eta = max(0, total - finished_count) * mean / workers
        line = (
            f"{datetime.now(UTC).isoformat()} done={finished_count}/{total} percent={100 * finished_count / total if total else 100:.2f} "
            f"errors={errors} rate_cells_h={rate:.2f} eta_s={eta:.1f}\n"
        )
        with progress_path.open("a") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

    def run_one(
        cell: Cell,
    ) -> tuple[list[SuiteOutcome] | None, BaseException | None, float]:
        started_cell = time.monotonic()
        try:
            run_dir = out / "cells" / cell.cell_id
            if run_dir.exists():
                import shutil

                shutil.rmtree(run_dir)
            outcomes = _invoke(
                cell, run_dir, llm_url=active_llm_url, embeddings=embeddings
            )
        except KeyboardInterrupt:
            manifest["ended_at"] = datetime.now(UTC).isoformat()
            _write_json(manifest_path, manifest)
            raise
        except BaseException as exc:
            return None, exc, time.monotonic() - started_cell
        return outcomes, None, time.monotonic() - started_cell

    if workers == 1:
        for cell in pending:
            result, exc, wall = run_one(cell)
            finish_cell(cell, result, exc, wall)
    else:
        context = multiprocessing.get_context("spawn")
        remaining = iter(pending)
        while True:
            batch = list(__import__("itertools").islice(remaining, workers))
            if not batch:
                break
            starts = {cell.cell_id: time.monotonic() for cell in batch}
            executors = {}
            futures = {}
            for cell in batch:
                run_dir = out / "cells" / cell.cell_id
                if run_dir.exists():
                    import shutil

                    shutil.rmtree(run_dir)
                executor = concurrent.futures.ProcessPoolExecutor(
                    max_workers=1, mp_context=context, max_tasks_per_child=1
                )
                executors[cell.cell_id] = executor
                future = executor.submit(
                    _process_cell,
                    cell,
                    run_dir,
                    active_llm_url,
                    embeddings,
                    SUITES[cell.suite],
                )
                futures[future] = cell
            try:
                for future in concurrent.futures.as_completed(futures):
                    cell = futures[future]
                    try:
                        result = future.result()
                        exc = None
                    except BaseException as error:
                        result, exc = None, error
                    finish_cell(
                        cell, result, exc, time.monotonic() - starts[cell.cell_id]
                    )
            except KeyboardInterrupt:
                for future in futures:
                    future.cancel()
                manifest["ended_at"] = datetime.now(UTC).isoformat()
                _write_json(manifest_path, manifest)
                raise
            finally:
                for executor in executors.values():
                    executor.shutdown(wait=True, cancel_futures=True)
    manifest["ended_at"] = datetime.now(UTC).isoformat()
    manifest["dirty"] = _backend_git()[1]
    _write_json(manifest_path, manifest)
    _rebuild_outcomes(out)
    return (
        1 if errors or any(r.get("status") == "error" for r in latest.values()) else 0
    )


def _outcomes(path: Path) -> list[dict[str, Any]]:
    if (path / "outcome-cells").exists() and (path / "cells.jsonl").exists():
        _rebuild_outcomes(path)
        return _read_jsonl(path / "outcomes.jsonl")
    return _read_jsonl(path / "outcomes.jsonl")


def _latest_cells(path: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path / "cells.jsonl"):
        latest[row["cell_id"]] = row
    return list(latest.values())


def report_run(out: Path) -> tuple[dict[str, Any], str]:
    out = Path(out)
    rows = _outcomes(out)
    groups: dict[tuple[str, str, str], list[SuiteOutcome]] = {}
    missing: dict[tuple[str, str, str, str], int] = {}
    for row in rows:
        key = (row["suite"], row["variant"], row.get("horizon", "unknown"))
        metrics = row.get("metrics", {})
        for name, value in metrics.items():
            if value is None:
                missing[(key[0], key[1], key[2], name)] = (
                    missing.get((key[0], key[1], key[2], name), 0) + 1
                )
        valid_metrics = {
            name: value for name, value in metrics.items() if value is not None
        }
        outcome = SuiteOutcome(
            row["probe_key"],
            int(row["persona_seed"]),
            row["suite"],
            tuple(row.get("categories", [])),
            valid_metrics,
            row.get("mode", "architecture_only"),
            row.get("error"),
        )
        groups.setdefault(key, []).append(outcome)
    report: dict[str, Any] = {
        "groups": {},
        "errors": [r for r in _latest_cells(out) if r.get("status") == "error"],
    }
    md = [
        f"# BrainBench report: {out.name}",
        "",
        f"Errored cells: {len(report['errors'])}",
        "",
    ]
    for (suite, variant, horizon), outcomes in sorted(groups.items()):
        aggregate = stats.aggregate(outcomes).get("all", {"n": 0, "metrics": {}})
        metric_rows = {}
        md.extend(
            [
                f"## {suite} / {variant} / {horizon}",
                "",
                "| Metric | Mean | 95% CI | n | n_clusters | Missing |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for metric, summary in sorted(aggregate["metrics"].items()):
            count_clusters = len(
                {row.persona_seed for row in outcomes if metric in row.metrics}
            )
            missing_count = missing.get((suite, variant, horizon, metric), 0)
            metric_rows[metric] = {
                **summary,
                "n_clusters": count_clusters,
                "missing": missing_count,
            }
            md.append(
                f"| {metric} | {summary['mean']:.4f} | [{summary['ci95'][0]:.4f}, {summary['ci95'][1]:.4f}] | {summary['n']} | {count_clusters} | {missing_count} |"
            )
        report["groups"][f"{suite}/{variant}/{horizon}"] = {
            "n_outcomes": aggregate["n"],
            "metrics": metric_rows,
        }
        md.append("")
    summary_rows: list[dict[str, Any]] = []
    from evals.brainbench.summaries import SUMMARY_SCORERS, summarize_cell

    successful_cells = [row for row in _latest_cells(out) if row.get("status") == "ok"]
    simulation_cache: dict[tuple[int, str, str], Any] = {}
    pooled: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for cell in successful_cells:
        suite = cell["suite"]
        if suite not in SUMMARY_SCORERS:
            continue
        outcome_rows = [row for row in rows if row.get("cell_id") == cell["cell_id"]]
        outcomes = [
            SuiteOutcome(
                row["probe_key"],
                int(row.get("persona_seed", row.get("seed", cell["seed"]))),
                suite,
                tuple(row.get("categories", [])),
                row.get("metrics", {}),
                row.get("mode", cell.get("mode", "architecture_only")),
                row.get("error"),
            )
            for row in outcome_rows
        ]
        simulation = None
        if suite == "attention":
            cache_key = (int(cell["seed"]), cell["archetype"], cell["horizon"])
            if cache_key not in simulation_cache:
                simulation_cache[cache_key] = build(*cache_key)
            simulation = simulation_cache[cache_key]
        metrics, none_reasons = summarize_cell(suite, outcomes, simulation)
        summary = {
            "cell_id": cell["cell_id"],
            "suite": suite,
            "variant": cell["variant"],
            "horizon": cell["horizon"],
            "seed": cell["seed"],
            "archetype": cell["archetype"],
            "mode": cell["mode"],
            "metrics": metrics,
            "none_reasons": none_reasons,
        }
        summary_rows.append(summary)
        pooled.setdefault((suite, cell["variant"], cell["horizon"]), []).append(metrics)
    with (out / "summaries.jsonl").open("w") as stream:
        for row in summary_rows:
            stream.write(_json_dump(row) + "\n")
    report["summaries"] = {}
    md.extend(["## Suite-level summaries", ""])
    for key, cell_metrics in sorted(pooled.items()):
        suite, variant, horizon = key
        metric_names = sorted({name for row in cell_metrics for name in row})
        pooled_metrics: dict[str, Any] = {}
        md.extend(
            [
                f"### {suite} / {variant} / {horizon}",
                "",
                "| Metric | Mean | Min | Max | n_cells | n_none |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for name in metric_names:
            values = [row.get(name) for row in cell_metrics]
            numeric = [
                float(value)
                for value in values
                if isinstance(value, (int, float)) and value is not None
            ]
            item = {
                "mean": sum(numeric) / len(numeric) if numeric else None,
                "min": min(numeric) if numeric else None,
                "max": max(numeric) if numeric else None,
                "n_cells": len(numeric),
                "n_none": sum(value is None for value in values),
            }
            pooled_metrics[name] = item
            display = lambda value: "—" if value is None else f"{value:.4f}"
            md.append(
                f"| {name} | {display(item['mean'])} | {display(item['min'])} | {display(item['max'])} | {item['n_cells']} | {item['n_none']} |"
            )
        report["summaries"][f"{suite}/{variant}/{horizon}"] = pooled_metrics
        md.append("")
    report["summary_cells"] = len(summary_rows)
    if report["errors"]:
        md.extend(["## Errored cells", ""])
        for row in report["errors"]:
            md.append(
                f"- `{row['cell_id']}`: {row.get('exception_type')}: {row.get('message', '')}"
            )
        md.append("")
    _write_json(out / "report.json", report)
    (out / "report.md").write_text("\n".join(md))
    return report, "\n".join(md)


def compare_runs(baseline_out: Path, arm_out: Path) -> tuple[dict[str, Any], str]:
    baseline_out, arm_out = Path(baseline_out), Path(arm_out)
    baseline_manifest = json.loads((baseline_out / "manifest.json").read_text())
    arm_manifest = json.loads((arm_out / "manifest.json").read_text())
    if baseline_manifest.get("mode") != arm_manifest.get("mode"):
        raise ValueError(
            f"cannot compare modes {baseline_manifest.get('mode')!r} and {arm_manifest.get('mode')!r}"
        )
    warnings = []
    if baseline_manifest.get("git_sha") != arm_manifest.get("git_sha"):
        warnings.append("Git SHAs differ")
    if baseline_manifest.get("dirty") or arm_manifest.get("dirty"):
        warnings.append("one or both runs were dirty")
    left, right = _outcomes(baseline_out), _outcomes(arm_out)
    context = ("suite", "variant", "archetype", "seed", "horizon")
    left_groups: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    right_groups: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for source, target in ((left, left_groups), (right, right_groups)):
        for row in source:
            group_key = tuple(
                row.get(k, row.get("persona_seed") if k == "seed" else None)
                for k in context
            )
            target.setdefault(group_key, {})[row["probe_key"]] = row
    grouped: dict[
        tuple[str, str, str], dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]
    ] = {}
    for key in set(left_groups) & set(right_groups):
        arows, brows = left_groups[key], right_groups[key]
        for probe in set(arows) & set(brows):
            a, b = arows[probe], brows[probe]
            for metric in set(a.get("metrics", {})) & set(b.get("metrics", {})):
                av, bv = a["metrics"][metric], b["metrics"][metric]
                if av is None or bv is None:
                    continue
                grouped.setdefault((key[0], key[1], key[4]), {}).setdefault(
                    metric, []
                ).append((a, b))
    comparisons: dict[str, Any] = {}
    unpaired: dict[tuple[str, str, str], dict[str, int]] = {}
    cell_identity = ("suite", "variant", "archetype", "seed", "horizon")

    def cells_by_group(path: Path) -> dict[tuple[str, str, str], set[tuple[Any, ...]]]:
        grouped_cells: dict[tuple[str, str, str], set[tuple[Any, ...]]] = {}
        for row in _latest_cells(path):
            if row.get("status") != "ok":
                continue
            group = (row["suite"], row["variant"], row.get("horizon", "unknown"))
            grouped_cells.setdefault(group, set()).add(
                tuple(row.get(k) for k in cell_identity)
            )
        return grouped_cells

    left_cells, right_cells = cells_by_group(baseline_out), cells_by_group(arm_out)

    def probes_by_group(
        source: list[dict[str, Any]],
    ) -> dict[tuple[str, str, str], set[tuple[Any, ...]]]:
        index: dict[tuple[str, str, str], set[tuple[Any, ...]]] = {}
        for row in source:
            group = (row["suite"], row["variant"], row.get("horizon", "unknown"))
            index.setdefault(group, set()).add(
                tuple(row.get(k) for k in cell_identity) + (row["probe_key"],)
            )
        return index

    left_probes, right_probes = probes_by_group(left), probes_by_group(right)
    for group in (
        set(left_cells) | set(right_cells) | set(left_probes) | set(right_probes)
    ):
        common_cells = left_cells.get(group, set()) & right_cells.get(group, set())
        left_common_probes = {
            probe
            for probe in left_probes.get(group, set())
            if probe[:-1] in common_cells
        }
        right_common_probes = {
            probe
            for probe in right_probes.get(group, set())
            if probe[:-1] in common_cells
        }
        unpaired[group] = {
            "baseline_only_cells": len(
                left_cells.get(group, set()) - right_cells.get(group, set())
            ),
            "arm_only_cells": len(
                right_cells.get(group, set()) - left_cells.get(group, set())
            ),
            "baseline_only_probes": len(left_common_probes - right_common_probes),
            "arm_only_probes": len(right_common_probes - left_common_probes),
        }
    family_pvalues: dict[str, dict[str, float]] = {}
    md = [f"# BrainBench comparison: {baseline_out.name} vs {arm_out.name}", ""]
    if warnings:
        md.extend(["Warnings: " + "; ".join(warnings), ""])
    all_compare_groups = set(grouped) | set(unpaired)
    for suite, variant, horizon in sorted(all_compare_groups):
        metrics = grouped.get((suite, variant, horizon), {})
        raw: dict[str, Any] = {}
        for metric, pairs in sorted(metrics.items()):
            a = [
                SuiteOutcome(
                    f"{p[0].get('archetype')}|{p[0].get('horizon')}|{p[0]['probe_key']}",
                    int(p[0]["persona_seed"]),
                    suite,
                    tuple(p[0].get("categories", [])),
                    {metric: p[0]["metrics"][metric]},
                )
                for p in pairs
            ]
            b = [
                SuiteOutcome(
                    f"{p[1].get('archetype')}|{p[1].get('horizon')}|{p[1]['probe_key']}",
                    int(p[1]["persona_seed"]),
                    suite,
                    tuple(p[1].get("categories", [])),
                    {metric: p[1]["metrics"][metric]},
                )
                for p in pairs
            ]
            result = stats.paired_delta(a, b, metric)
            # Keep the named primitives explicit here so future stats changes do not silently alter the report contract.
            differences = [
                p[1]["metrics"][metric] - p[0]["metrics"][metric] for p in pairs
            ]
            clusters = [int(p[0]["persona_seed"]) for p in pairs]
            result["p_value"] = round(stats.bootstrap_p_value(differences, clusters), 4)
            result["cliffs_delta"] = round(
                stats.cliffs_delta(
                    [p[0]["metrics"][metric] for p in pairs],
                    [p[1]["metrics"][metric] for p in pairs],
                ),
                4,
            )
            raw[metric] = result
            family_pvalues.setdefault(suite, {})[f"{variant}/{horizon}/{metric}"] = (
                result.get("p_value", 1.0)
            )
        group_name = f"{suite}/{variant}/{horizon}"
        comparisons[group_name] = {
            "metrics": raw,
            "holm": {},
            "unpaired": unpaired.get((suite, variant, horizon), {}),
        }
    family_corrections = {
        suite: stats.holm_correction(pvalues)
        for suite, pvalues in family_pvalues.items()
    }
    for group_name, group in comparisons.items():
        suite, variant, _horizon = group_name.split("/", 2)
        group["holm"] = {
            metric: family_corrections[suite][f"{variant}/{_horizon}/{metric}"]
            for metric in group["metrics"]
        }
        md.extend(
            [
                f"## {suite} / {variant} / {_horizon}",
                "",
                "| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |",
                "|---|---:|---:|---:|---:|---|---:|",
            ]
        )
        if not group["metrics"]:
            md.extend(["No paired metric rows.", ""])
        for metric, value in group["metrics"].items():
            decision = group["holm"][metric]
            ci = value.get("ci95", [None, None])
            md.append(
                f"| {metric} | {value.get('delta', 0):.4f} | [{ci[0]:.4f}, {ci[1]:.4f}] | {value['n']} | {value['p_value']:.4f} | {decision['significant']} | {value['cliffs_delta']:.4f} |"
            )
        unmatched = group["unpaired"]
        md.extend(
            [
                "",
                "| Unpaired rows | Count |",
                "|---|---:|",
                f"| Baseline-only cells | {unmatched.get('baseline_only_cells', 0)} |",
                f"| Arm-only cells | {unmatched.get('arm_only_cells', 0)} |",
                f"| Baseline-only probes | {unmatched.get('baseline_only_probes', 0)} |",
                f"| Arm-only probes | {unmatched.get('arm_only_probes', 0)} |",
            ]
        )
        md.append("")
    result = {
        "baseline": str(baseline_out),
        "arm": str(arm_out),
        "mode": baseline_manifest.get("mode"),
        "warnings": warnings,
        "groups": comparisons,
        "unpaired": {
            f"{suite}/{variant}/{horizon}": counts
            for (suite, variant, horizon), counts in unpaired.items()
        },
    }
    _write_json(arm_out / "compare.json", result)
    (arm_out / "compare.md").write_text("\n".join(md))
    return result, "\n".join(md)
