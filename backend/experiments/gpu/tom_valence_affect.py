"""Can the LLM's Theory-of-Mind valence drive appraisal? (ADR-002 follow-up)

The affect simulation shows the *mechanism* works when appraisal's goal
congruence comes from the user's expressed valence (oracle labels: mood tracks
the user, r = 0.36-0.98, bounded, trust separates hostile from positive) and
fails in V1, where it comes from the agent's own mood. What is not known is
whether a production estimator is good enough. The candidate already runs on
every turn: DecisionService._classify_intent_and_goal asks the fast LLM for
`inferred_valence`. This script measures it and replays the simulation on it.

Steps:
1. For every labelled message in evals/cognitive/affect_sim.MESSAGES, call
   the real classifier (real prompt, real model, neutral agent state) and
   record `inferred_valence`; report MAE, Pearson r and sign agreement vs the
   hand labels, plus latency per call.
2. Run the affect matrix with source="precomputed" on those values, next to
   the oracle and V1 rows, learning off (the shipped default).

Decision rule (written before running, per the research protocol): adopt the
ToM valence as appraisal input only if r >= 0.8 and sign agreement >= 0.9 on
non-neutral messages AND the simulated hostile-script trust ends <= 0.5.

Usage (Ollama box, from backend/):
  python -m experiments.gpu.tom_valence_affect --model llama3.2:3b \\
      --ollama-url http://127.0.0.1:11434 --repeats 3 --out results/tom_valence.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
import uuid

from evals.cognitive import affect_sim


async def infer(messages, model: str, url: str, repeats: int) -> dict:
    from app.cognitive.decision import DecisionService
    from app.cognitive.perception import CognitiveEvent
    from app.config import Config
    from app.llm.ollama_client import OllamaClient

    Config.LLM_FAST_MODEL = model
    service = DecisionService(llm_service=OllamaClient(base_url=url, model=model))
    state = {"emotion": "neutral", "mood": 0.0}
    rows = []
    for text, label in messages:
        values, latencies = [], []
        for _ in range(repeats):
            event = CognitiveEvent(str(uuid.uuid4()), "USER_MESSAGE", text, {})
            started = time.perf_counter()
            await service._classify_intent_and_goal(event, state)
            latencies.append((time.perf_counter() - started) * 1000.0)
            tom = event.metadata.get("tom_inferences")
            if tom is not None:
                values.append(float(tom["inferred_valence"]))
        rows.append(
            {
                "text": text,
                "label": label,
                "inferred": statistics.fmean(values) if values else None,
                "parse_failures": repeats - len(values),
                "p50_ms": statistics.median(latencies),
            }
        )
    return {"model": model, "rows": rows}


def score(rows) -> dict:
    ok = [r for r in rows if r["inferred"] is not None]
    labels = [r["label"] for r in ok]
    preds = [r["inferred"] for r in ok]
    nonneutral = [r for r in ok if abs(r["label"]) >= 0.3]
    return {
        "n": len(rows),
        "parse_failure_rate": sum(r["parse_failures"] > 0 for r in rows)
        / max(1, len(rows)),
        "mae": statistics.fmean(abs(p - lab) for p, lab in zip(preds, labels))
        if ok
        else None,
        "pearson_r": affect_sim._pearson(labels, preds),
        "sign_agreement_nonneutral": (
            sum((r["inferred"] > 0) == (r["label"] > 0) for r in nonneutral)
            / len(nonneutral)
            if nonneutral
            else None
        ),
        "p50_latency_ms": statistics.median(r["p50_ms"] for r in rows),
    }


def main(argv=None) -> int:
    logging.disable(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--turns", type=int, default=200)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    messages = sorted({m for group in affect_sim.MESSAGES.values() for m in group})
    inference = asyncio.run(infer(messages, args.model, args.ollama_url, args.repeats))
    valence_map = {
        r["text"]: (r["inferred"] if r["inferred"] is not None else 0.0)
        for r in inference["rows"]
    }
    sims = {}
    for script in affect_sim.SCRIPTS:
        for source in ("agent_mood", "oracle", "precomputed"):
            sims[f"{source}/{script}"] = [
                asyncio.run(
                    affect_sim.simulate(
                        script, source, args.turns, seed, valence_map=valence_map
                    )
                ).metrics()
                for seed in (0, 1, 2)
            ]
    report = {
        "suite": "tom-valence-affect",
        "inference": inference,
        "accuracy": score(inference["rows"]),
        "simulation": sims,
    }
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps(report["accuracy"], indent=1))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
