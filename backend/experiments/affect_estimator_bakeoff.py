"""Common local bake-off for user-text valence estimators (ADR-002)."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path

from app.cognitive.user_valence import build_estimator
from evals.cognitive import affect_sim

TOM_MODELS = (
    "vader",
    "cardiffnlp/twitter-roberta-base-sentiment-latest",
    "j-hartmann/emotion-english-distilroberta-base",
    "SamLowe/roberta-base-go_emotions",
    "ensemble:vader,cardiffnlp/twitter-roberta-base-sentiment-latest",
    "ensemble:vader,j-hartmann/emotion-english-distilroberta-base,SamLowe/roberta-base-go_emotions",
)


def _sign_agreement(gold: list[float], predicted: list[float]) -> float | None:
    selected = [(g, p) for g, p in zip(gold, predicted, strict=True) if abs(g) >= 0.3]
    if not selected:
        return None
    return sum(p != 0 and (g > 0) == (p > 0) for g, p in selected) / len(selected)


def _score(gold: list[float], predicted: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(gold),
        "pearson_r": affect_sim._pearson(gold, predicted),
        "sign_agreement_nonneutral": _sign_agreement(gold, predicted),
    }


async def _measure(estimator, texts: list[str]) -> tuple[dict[str, float], list[float]]:
    values: dict[str, float] = {}
    latencies: list[float] = []
    for text in texts:
        started = time.perf_counter()
        try:
            values[text] = await estimator.estimate(text)
        except Exception:
            values[text] = float("nan")
        latencies.append((time.perf_counter() - started) * 1000.0)
    return values, latencies


def _latency(latencies: list[float]) -> dict[str, float | None]:
    ordered = sorted(latencies)
    if not ordered:
        return {"p50_ms": None, "p95_ms": None}
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(
            ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))], 3
        ),
    }


def _failure_rate(values: list[float]) -> float:
    return sum(not math.isfinite(value) for value in values) / max(1, len(values))


def _dataset_report(
    rows,
    values: dict[str, float],
    latencies: list[float],
    *,
    label_index: int = 1,
):
    gold = [row[label_index] for row in rows]
    raw_predictions = [values.get(row[0], float("nan")) for row in rows]
    # Production treats failed estimates as no signal; its numeric fallback is
    # neutral. Score that same 0.0 rather than improving correlation by
    # dropping the hard rows. Keep the raw failures visible in failure_rate.
    predictions = [
        predicted if math.isfinite(predicted) else 0.0 for predicted in raw_predictions
    ]
    return {
        **_score(gold, predictions),
        **_latency(latencies),
        "failure_rate": _failure_rate(raw_predictions),
    }


def _lifesim_reports(rows, values, latencies):
    return (
        _dataset_report(rows, values, latencies, label_index=1),
        _dataset_report(rows, values, latencies, label_index=2),
    )


def _passes_adr002(tom_report, expressed_report, hostile_trust: list[float]) -> bool:
    return (
        all(
            score["pearson_r"] is not None
            and score["pearson_r"] >= 0.8
            and score["sign_agreement_nonneutral"] is not None
            and score["sign_agreement_nonneutral"] >= 0.9
            for score in (tom_report, expressed_report)
        )
        and bool(hostile_trust)
        and all(value <= 0.5 for value in hostile_trust)
    )


async def _measure_dataset(estimator, rows):
    return await _measure(estimator, [row[0] for row in rows])


def _candidate_report(name: str, tom_rows, life_rows, model: str, url: str) -> dict:
    init_started = time.perf_counter()
    estimator = build_estimator(name, ollama_model=model, ollama_url=url)
    prepare = getattr(estimator, "prepare", None)
    if prepare is not None:
        prepare()
    initialization_ms = (time.perf_counter() - init_started) * 1000.0
    tom_texts = [text for text, _label in tom_rows]

    async def run():
        tom_values, tom_times = await _measure_dataset(estimator, tom_rows)
        life_values, life_times = await _measure_dataset(estimator, life_rows)
        return tom_values, tom_times, life_values, life_times

    tom_values, tom_times, life_values, life_times = asyncio.run(run())
    tom_report = _dataset_report(tom_rows, tom_values, tom_times)
    life_expressed_report, life_event_report = _lifesim_reports(
        life_rows, life_values, life_times
    )
    hostile_map = {
        text: tom_values.get(text, 0.0)
        if math.isfinite(tom_values.get(text, 0.0))
        else 0.0
        for text in tom_texts
    }
    hostile_results = [
        asyncio.run(
            affect_sim.simulate(
                "hostile", "precomputed", 200, seed, valence_map=hostile_map
            )
        )
        for seed in (0, 1, 2)
    ]
    hostile_trust_by_seed = [
        {
            "seed": seed,
            "final_trust": round(result.metrics()["final_trust"], 4),
        }
        for seed, result in zip((0, 1, 2), hostile_results, strict=True)
    ]
    hostile_trust = [row["final_trust"] for row in hostile_trust_by_seed]
    return {
        "estimator": name,
        "initialization_ms": round(initialization_ms, 3),
        "tom": tom_report,
        "lifesim_dev_expressed_valence": life_expressed_report,
        "lifesim_dev_event_user_valence": life_event_report,
        "hostile_final_trust_by_seed": hostile_trust_by_seed,
        "hostile_final_trust_mean": round(statistics.fmean(hostile_trust), 4),
        "adr_002_pass": _passes_adr002(
            tom_report, life_expressed_report, hostile_trust
        ),
    }


def _parse_range(value: str) -> list[int]:
    if "-" in value:
        start, end = map(int, value.split("-", 1))
        if end < start:
            raise ValueError("seed range end must be >= start")
        return list(range(start, end + 1))
    return [int(item) for item in value.split(",") if item]


def _lifesim_rows(turns, annotations) -> list[tuple[str, float, float]]:
    rows = []
    for turn, annotation in zip(turns, annotations, strict=True):
        if turn.turn_id != annotation.turn_id:
            raise ValueError("lifesim turn and oracle annotation are misaligned")
        # Oracle labels are read only after generation and never enter estimation.
        rows.append(
            (
                turn.text,
                float(annotation.expressed_valence),
                float(annotation.user_valence),
            )
        )
    return rows


def bakeoff(
    *,
    model: str,
    url: str,
    seeds: list[int],
    archetypes: list[str],
    horizon: str,
    candidates: list[str] | None = None,
) -> dict:
    from evals.lifesim.generate import build
    from evals.lifesim.splits import split_of

    if any(split_of(seed) != "dev" for seed in seeds):
        raise ValueError("lifesim bake-off accepts dev seeds only (1000-1099)")
    tom_rows = sorted(
        {message for group in affect_sim.MESSAGES.values() for message in group}
    )
    life_rows: list[tuple[str, float, float]] = []
    life_meta = {"seeds": seeds, "archetypes": archetypes, "horizon": horizon}
    for seed in seeds:
        for archetype in archetypes:
            _sim, turns, annotations, _probes, _answers = build(
                seed, archetype, horizon
            )
            # Oracle labels are used only for scoring after text generation.
            life_rows.extend(_lifesim_rows(turns, annotations))
    candidates = candidates or list(TOM_MODELS)
    return {
        "suite": "affect-estimator-bakeoff",
        "adr_002_rule": "r >= 0.8 AND sign agreement >= 0.9 on non-neutral messages AND hostile final trust <= 0.5",
        "lifesim_verdict_label": "expressed_valence",
        "lifesim_comparison_label": "user_valence (event valence)",
        "llm": {"model": model, "url": url},
        "lifesim_sample": life_meta,
        "mapping": {
            "cardiffnlp/twitter-roberta-base-sentiment-latest": "P(positive) - P(negative), neutral maps to 0",
            "j-hartmann/emotion-english-distilroberta-base": "probability-weighted label scores: joy/love=+1; anger/disgust/fear/sadness=-1; neutral/surprise=0",
            "SamLowe/roberta-base-go_emotions": "probability-weighted label scores: positive emotion labels=+1; negative emotion labels=-1; unlisted labels=0",
        },
        "results": [
            _candidate_report(name, tom_rows, life_rows, model, url)
            for name in candidates
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--lifesim-seeds", default="1000-1001")
    parser.add_argument("--archetypes", default="all")
    parser.add_argument("--horizon", default="1m")
    parser.add_argument("--candidates", default="all")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    from evals.lifesim.personas import PANEL

    archetypes = list(PANEL) if args.archetypes == "all" else args.archetypes.split(",")
    result = bakeoff(
        model=args.model,
        url=args.ollama_url,
        seeds=_parse_range(args.lifesim_seeds),
        archetypes=archetypes,
        horizon=args.horizon,
        candidates=None if args.candidates == "all" else args.candidates.split(","),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["results"], indent=2))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
